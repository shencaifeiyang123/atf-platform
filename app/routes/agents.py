"""智能体相关路由：/api/agents/* + /api/agent_defaults。"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request

from .. import store
from ..cache import cache
from ..models import AgentUnderTest

router = APIRouter()

# ---- 百炼导入进度（内存共享状态） ----
_bailian_progress: dict[str, Any] = {"phase": "idle", "message": ""}
_bailian_lock = threading.Lock()


def _bailian_progress_cb(prog):
    """进度回调：更新全局状态供前端轮询。"""
    with _bailian_lock:
        _bailian_progress.update({
            "phase": prog.phase,
            "message": prog.message,
            "total": prog.total,
            "current": prog.current,
            "imported": prog.imported,
            "skipped": prog.skipped,
            "errors": prog.errors,
            "agents": prog.agents,
        })


@router.get("/agents")
async def list_agents() -> list[dict[str, Any]]:
    return [a.model_dump() for a in store.list_agents()]


@router.get("/agents_overview")
async def agents_overview() -> list[dict[str, Any]]:
    """智能体卡片视图：附带用例数 / 维度分布 / 最近一次任务概要。"""
    return store.agents_overview_data()


@router.post("/agents")
async def create_agent(agent: AgentUnderTest) -> dict[str, Any]:
    return store.create_agent(agent).model_dump()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    a = store.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    return a.model_dump()


@router.put("/agents/{agent_id}")
async def update_agent(agent_id: str, agent: AgentUnderTest) -> dict[str, Any]:
    if not store.get_agent(agent_id):
        raise HTTPException(404, "agent 不存在")
    agent.id = agent_id
    return store.update_agent(agent).model_dump()


@router.delete("/agents/{agent_id}")
async def delete_agent(agent_id: str) -> dict[str, Any]:
    store.delete_agent(agent_id)
    cache.delete(f"agent_analysis:{agent_id}")
    return {"ok": True}


@router.get("/agents/{agent_id}/analysis")
async def get_analysis(agent_id: str) -> dict[str, Any]:
    """读取已保存的分析结果（优先缓存，再从数据库）。"""
    # 尝试缓存
    cached = cache.get(f"agent_analysis:{agent_id}")
    if cached is not None:
        return {"analysis": cached, "analysis_at": cached.get("_analysis_at"), "from_cache": True}
    # 从数据库读
    a = store.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    if a.analysis:
        # 写入缓存（1 小时）
        cache.set(f"agent_analysis:{agent_id}", {**a.analysis, "_analysis_at": a.analysis_at}, ttl=3600)
    return {"analysis": a.analysis or {}, "analysis_at": a.analysis_at, "from_cache": False}


@router.put("/agents/{agent_id}/analysis")
async def update_analysis(agent_id: str, request: Request) -> dict[str, Any]:
    """手动编辑并保存分析结果（前端编辑器提交）。"""
    a = store.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    body = await request.json()
    analysis = body.get("analysis")
    if analysis is None or not isinstance(analysis, dict):
        raise HTTPException(400, "请求体需包含 analysis 字段（JSON 对象）")
    store.update_agent_analysis(agent_id, analysis)
    a2 = store.get_agent(agent_id)
    cache.set(f"agent_analysis:{agent_id}", {**a2.analysis, "_analysis_at": a2.analysis_at}, ttl=3600)
    return {"analysis": a2.analysis or {}, "analysis_at": a2.analysis_at}


@router.post("/agents/{agent_id}/analyze")
async def analyze(agent_id: str, force: bool = False) -> dict[str, Any]:
    """分析智能体并把结果持久化到数据库。"""
    from ..generator import analyze_agent

    a = store.get_agent(agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    if not force and a.analysis:
        return {"analysis": a.analysis, "analysis_at": a.analysis_at, "cached": True}
    try:
        analysis = await analyze_agent(a)
    except Exception as e:
        raise HTTPException(500, f"分析失败：{e}")
    store.update_agent_analysis(agent_id, analysis)
    a2 = store.get_agent(agent_id)
    cache.set(f"agent_analysis:{agent_id}", {**analysis, "_analysis_at": a2.analysis_at if a2 else None}, ttl=3600)
    return {"analysis": analysis, "analysis_at": a2.analysis_at if a2 else None, "cached": False}


@router.get("/agent_defaults")
async def agent_defaults() -> dict[str, Any]:
    """新建智能体弹窗的字段预填值。"""
    return {
        "api_key": os.getenv("AGENT_DEFAULT_API_KEY", "") or "",
    }


@router.post("/agents/import_bailian")
async def import_bailian(
    request: Request,
    api_key: Optional[str] = None,
    debug_port: int = 9222,
    max_pages: int = 10,
) -> dict[str, Any]:
    """从百炼平台批量导入智能体（后台线程执行，前端轮询进度）。"""
    from ..bailian_importer import HAS_SELENIUM, import_from_bailian

    if not HAS_SELENIUM:
        raise HTTPException(
            400,
            "未安装 selenium 依赖，请执行: pip install selenium>=4.0.0",
        )

    with _bailian_lock:
        if _bailian_progress.get("phase") not in ("idle", "done", "error"):
            raise HTTPException(409, "导入任务正在进行中，请稍后重试")
        # 重置进度
        _bailian_progress.clear()
        _bailian_progress.update({"phase": "connecting", "message": "正在启动导入任务..."})

    api_key = api_key or os.getenv("BAILIAN_API_KEY", "")

    def _run():
        try:
            import_from_bailian(
                debug_port=debug_port,
                max_pages=max_pages,
                api_key=api_key,
                progress_cb=_bailian_progress_cb,
            )
        except Exception as e:
            _bailian_progress_cb(type("P", (), {
                "phase": "error",
                "message": f"导入异常: {e}",
                "total": 0, "current": 0,
                "imported": 0, "skipped": 0, "errors": 0,
                "agents": [],
            })())

    threading.Thread(target=_run, daemon=True).start()
    return {"ok": True, "message": "导入任务已启动，请轮询 /api/agents/import_bailian/progress 查看进度"}


@router.get("/agents/import_bailian/progress")
async def import_bailian_progress() -> dict[str, Any]:
    """查询百炼导入进度。"""
    with _bailian_lock:
        return dict(_bailian_progress)


@router.post("/agents/import_bailian/reset")
async def import_bailian_reset() -> dict[str, Any]:
    """重置导入状态（允许重新启动）。"""
    with _bailian_lock:
        _bailian_progress.clear()
        _bailian_progress.update({"phase": "idle", "message": ""})
    return {"ok": True}
