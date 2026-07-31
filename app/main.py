"""FastAPI 入口：API 路由 + SSE + 静态前端。

API 路径前缀：/api
- /api/agents              CRUD 被测智能体
- /api/agents/{id}/analyze 仅分析智能体（不生成用例）
- /api/cases/generate      根据 agent system_prompt 生成测试用例
- /api/cases               列出 / 删除单个用例
- /api/runs                启动 / 查询测试任务
- /api/runs/{id}/stream    SSE 进度推送
- /api/runs/{id}/results   单次任务的全部用例结果
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from . import auth
from .metrics import metrics
from .routes import include_all_routers


# 维度元数据：前端用于渲染勾选框 + 中文名 + 描述
DIMENSION_META: list[dict[str, str]] = [
    {"key": "alignment", "label": "预期效果",
     "desc": "覆盖核心能力的正向验证"},
    {"key": "boundary", "label": "边界兜底",
     "desc": "超出能力范围 / 模糊输入 / 空输入的兜底体验"},
    {"key": "industry", "label": "行业规范",
     "desc": "按所属行业的合规点（如医疗、金融、教育）"},
    {"key": "badcase", "label": "Bad Case",
     "desc": "答非所问 / 编造 / 格式混乱等高频投诉场景"},
    {"key": "security", "label": "安全性",
     "desc": "提示词注入 / 越狱 / 隐私泄露 / 有害内容"},
    {"key": "multi_turn", "label": "多轮对话",
     "desc": "上下文理解、指代消解、状态保持"},
    {"key": "instruction_following", "label": "指令遵循",
     "desc": "复杂多约束指令、否定 / 条件 / 步骤化要求"},
    {"key": "robustness", "label": "鲁棒性",
     "desc": "错别字、网络缩写、混合语言等扰动输入"},
    {"key": "tone", "label": "角色与语气",
     "desc": "人设保持、压力测试、风格一致性"},
    {"key": "factuality", "label": "事实性",
     "desc": "知识问答 / 错误前提纠正 / 抗幻觉"},
    {"key": "format", "label": "输出格式",
     "desc": "JSON / 表格 / 代码块 / 字数限制等格式约束"},
]


app = FastAPI(title="AI Agent Test Platform", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册所有业务路由
include_all_routers(app)


# ---------- HTTP 请求监控中间件 ----------

@app.middleware("http")
async def metrics_middleware(request: Request, call_next: Any) -> Any:
    """记录 HTTP 请求指标（method/path/status/耗时）。"""
    start = time.monotonic()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        status = 500
        raise
    finally:
        duration = time.monotonic() - start
        # 简化路径（去掉动态 ID 部分）
        path = request.url.path
        # 记录指标
        metrics.http_requests.inc(
            method=request.method,
            path=path,
            status=str(status),
        )
        metrics.gauges.set(duration, f"http_{request.method.lower()}_{path.replace('/', '_')}")
    return response


# ---------- 监控指标端点 ----------

@app.get("/api/metrics")
async def expose_metrics() -> PlainTextResponse:
    """Prometheus 格式指标。"""
    # 更新活跃任务数
    from . import store
    try:
        runs = store.list_runs()
        active = sum(1 for r in runs if r.status in ("pending", "running"))
        metrics.gauges.set(active, "active_runs")
        # WAL 文件大小
        wal_path = store.DB_PATH.parent / (store.DB_PATH.name + "-wal")
        if wal_path.exists():
            metrics.gauges.set(wal_path.stat().st_size, "db_wal_size_bytes")
    except Exception:
        pass
    return PlainTextResponse(metrics.render_all(), media_type="text/plain; version=0.0.4")


# ---------- 鉴权中间件（必须在 CORS 之后注册）----------

# 放行的路径前缀：登录三件套 + 静态资源
# - /api/auth/* 才能让前端在未登录时拿状态、提交密码
# - / 与 /vendor/* 是首页 + 本地化 Tailwind/字体；蒙层是前端组件，需要先把页面骨架渲染出来
# - /docs / /redoc / /openapi.json 也需要鉴权（避免暴露所有路由元数据）
_AUTH_PUBLIC_PREFIXES = ("/api/auth/", "/vendor/")
_AUTH_PUBLIC_EXACT = {"/", "/favicon.ico"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next: Any) -> Any:
    if not auth.is_enabled():
        return await call_next(request)
    path = request.url.path
    # 放行：登录接口 + 首页/静态资源（前端蒙层渲染需要）
    if path in _AUTH_PUBLIC_EXACT or any(path.startswith(p) for p in _AUTH_PUBLIC_PREFIXES):
        return await call_next(request)
    # /api/* 与 /docs / /redoc / /openapi.json 必须登录
    needs_auth = (
        path.startswith("/api/")
        or path in ("/docs", "/redoc", "/openapi.json")
    )
    if not needs_auth:
        # 其它路径（如 SPA 的子路由）放行，让前端自己处理
        return await call_next(request)
    token = request.cookies.get(auth.COOKIE_NAME)
    if not auth.is_valid(token):
        return JSONResponse({"detail": "未登录或会话过期"}, status_code=401)
    return await call_next(request)


# ---------- lifespan：启动 / 停止调度循环 ----------

@app.on_event("startup")
async def _on_startup() -> None:
    import logging
    _logger = logging.getLogger(__name__)
    from . import scheduler as scheduler_mod
    try:
        scheduler_mod.kick_off_pending_next_run()
    except Exception:
        _logger.exception("kick_off_pending_next_run 失败")
    scheduler_mod.loop.start()


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    from . import scheduler as scheduler_mod
    await scheduler_mod.loop.stop()
    # 优雅关闭共享 httpx client
    try:
        from .llm import close_client
        await close_client()
    except Exception:
        pass


# ---------- 静态前端 ----------

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@app.get("/")
async def index() -> FileResponse:
    f = WEB_DIR / "index.html"
    if not f.exists():
        return JSONResponse({"hint": "前端未找到，访问 /docs 查看 API"})
    return FileResponse(str(f))


@app.get("/{path:path}")
async def static_files(path: str) -> Any:
    f = WEB_DIR / path
    if f.exists() and f.is_file() and f.resolve().is_relative_to(WEB_DIR.resolve()):
        # mimetypes 标准库不识别 .woff2，需手动指定，否则部分浏览器会拒绝加载本地字体。
        suffix = f.suffix.lower()
        media_type = None
        if suffix == ".woff2":
            media_type = "font/woff2"
        elif suffix == ".woff":
            media_type = "font/woff"
        return FileResponse(str(f), media_type=media_type) if media_type else FileResponse(str(f))
    # SPA fallback
    fallback = WEB_DIR / "index.html"
    if fallback.exists():
        return FileResponse(str(fallback))
    raise HTTPException(404, "Not Found")
