"""测试用例相关路由：/api/cases/* + /api/agents/{agent_id}/cases + /api/agents/{agent_id}/batches。"""
from __future__ import annotations

import asyncio
import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException

from .. import llm, store
from ..config import settings
from ..cost import calculate_cost
from ..generator import generate_all
from ..models import (
    AgentUnderTest,
    DynamicGenerateRequest,
    GenerateRequest,
    PromptDebuggerGenerateRequest,
    TestCase,
    TokenUsage,
)
from ..pd_generator import generate_pd_cases

router = APIRouter()

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


# ---- 辅助函数 ----

def _dims_to_chinese(dim_keys: list[str]) -> str:
    """将维度英文 key 列表转为中文 label 拼接字符串，用于 batch_label 展示。"""
    dim_label_map = {d["key"]: d["label"] for d in DIMENSION_META}
    labels = [dim_label_map.get(k, k) for k in dim_keys[:3]]
    text = ",".join(labels)
    if len(dim_keys) > 3:
        text += "..."
    return text


def _make_batch_label(mode: str, extra: str = "") -> str:
    """生成批次显示标签，例如：'05-16 10:04 维度驱动 · 预期效果,边界兜底'。"""
    ts = datetime.datetime.now().strftime("%m-%d %H:%M")
    label_map = {
        "dimension": "维度驱动",
        "prompt_debugger": "PD 风格",
        "dynamic": "动态对话",
        "manual": "手动创建",
    }
    base = label_map.get(mode, mode or "生成")
    return f"{ts} {base}" + (f" · {extra}" if extra else "")


# ---- 异步生成任务管理 ----

_GEN_JOBS: dict[str, dict[str, Any]] = {}
_GEN_JOBS_MAX_AGE_MS = 3600_000  # 1 hour


def _cleanup_gen_jobs() -> None:
    # 内存里的过期项删掉；DB 里同步清理
    now = store.now_ms()
    expired = [k for k, v in _GEN_JOBS.items()
               if v.get("finished_at") and now - v["finished_at"] > _GEN_JOBS_MAX_AGE_MS]
    for k in expired:
        del _GEN_JOBS[k]
    try:
        store.cleanup_gen_jobs(_GEN_JOBS_MAX_AGE_MS)
    except Exception as e:
        print(f"[gen_job] cleanup_gen_jobs DB failed: {e}")


def _gen_persist(job_id: str, fields: dict[str, Any]) -> None:
    """把字段同时刷到内存 _GEN_JOBS 和 DB。让 DB 状态始终和内存一致。"""
    job = _GEN_JOBS.get(job_id)
    if job is not None:
        job.update(fields)
    try:
        # DB 只接受白名单字段；其它字段（如 traceback / mode）只放内存
        db_fields = {k: v for k, v in fields.items()
                     if k in {"generated", "status", "error", "raw_text", "analysis", "finished_at"}}
        if db_fields:
            store.update_gen_job(job_id, db_fields)
    except Exception as e:
        print(f"[gen_job {job_id}] persist failed: {e}")


def _gen_new_job(
    agent_id: str,
    agent_name: str,
    planned: int,
    *,
    mode: str = "",
    params: Optional[dict[str, Any]] = None,
) -> str:
    """新建一个生成任务。在内存与 DB 双写。"""
    _cleanup_gen_jobs()
    job_id = "gj_" + __import__("uuid").uuid4().hex[:12]
    started_at = store.now_ms()
    _GEN_JOBS[job_id] = {
        "id": job_id,
        "mode": mode,
        "agent_id": agent_id,
        "agent_name": agent_name,
        "status": "running",   # running / done / error
        "planned": planned,
        "generated": 0,
        "error": "",
        "raw_text": "",
        "analysis": None,
        "params": dict(params or {}),
        "started_at": started_at,
        "finished_at": None,
    }
    try:
        store.create_gen_job(
            job_id=job_id, mode=mode, agent_id=agent_id, agent_name=agent_name,
            planned=planned, params=params or {},
        )
    except Exception as e:
        print(f"[gen_job {job_id}] create_gen_job DB failed: {e}")
    return job_id


async def _run_gen_job(job_id: str, agent: AgentUnderTest, req: GenerateRequest) -> None:
    job = _GEN_JOBS.get(job_id)
    if job is None:
        return
    try:
        # 重置 token 累计器（每个 gen_job 独立统计）
        llm.reset_token_usage()
        print(f"[gen_job {job_id}] 开始生成，维度: {req.dimensions}, 每维度: {req.cases_per_dim}")
        analysis, cases = await generate_all(
            agent,
            req.dimensions,
            cases_per_dim=req.cases_per_dim,
            cases_per_dim_map=req.cases_per_dim_map,
            analysis=agent.analysis or None,  # 复用已有分析结果
            opening_mode=req.opening_mode,
            user_opening_text=req.user_opening_text,
        )
        print(f"[gen_job {job_id}] generate_all 返回: analysis={bool(analysis)}, cases={len(cases)}")
        # 生成批次标签（小组名使用中文维度名称）
        dims_text = _dims_to_chinese(req.dimensions)
        batch_label = _make_batch_label("dimension", dims_text)
        saved = store.save_cases(cases, batch_label=batch_label)
        print(f"[gen_job {job_id}] 保存成功: {len(saved)} 条")
        # 首次分析完成后回写数据库
        if not agent.analysis and analysis:
            store.update_agent_analysis(agent.id or "", analysis)
        # 收集 token 用量并计算成本
        usage_records = llm.get_token_usage()
        token_usage = [TokenUsage(**u) for u in usage_records]
        cost_usd = calculate_cost(token_usage, settings.model_prices)
        tokens_in = sum(u.prompt_tokens for u in token_usage)
        tokens_out = sum(u.completion_tokens for u in token_usage)
        _gen_persist(job_id, {
            "status": "done",
            "generated": len(saved),
            "analysis": analysis,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
        })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        # 兜底文案：空异常时把类型名也带上，避免前端拿到空串无从下手
        msg = (str(e).strip() or f"{type(e).__name__}: <无消息>")
        _gen_persist(job_id, {"status": "error", "error": msg})
        if job is not None:
            job["traceback"] = tb
        print(f"[gen_job {job_id}] failed:\n{tb}")   # 直接打到 stdout，便于在终端排查
    finally:
        _gen_persist(job_id, {"finished_at": store.now_ms()})


async def _run_pd_gen_job(job_id: str, agent: AgentUnderTest, req: PromptDebuggerGenerateRequest) -> None:
    """异步执行 PD 风格生成。"""
    job = _GEN_JOBS.get(job_id)
    if job is None:
        return
    try:
        # 重置 token 累计器
        llm.reset_token_usage()
        # 仅当前端勾选「使用分析结果」时才读取已缓存的 analysis；不在此处主动调用 analyze_agent
        analysis = (agent.analysis or None) if req.use_analysis else None
        raw_text, cases = await generate_pd_cases(
            agent,
            test_points=req.test_points,
            test_case_level=req.test_case_level,
            opening_style=req.opening_style,
            generate_count=req.generate_count,
            analysis=analysis,
            user_opening_text=req.user_opening_text,
        )
        # 截取测试要点的前 16 字作为批次副标题
        extra = (req.test_points or "").strip().splitlines()[0][:16] if req.test_points else req.test_case_level
        batch_label = _make_batch_label("prompt_debugger", extra)
        saved = store.save_cases(cases, batch_label=batch_label)
        # 收集 token 用量并计算成本
        usage_records = llm.get_token_usage()
        token_usage = [TokenUsage(**u) for u in usage_records]
        cost_usd = calculate_cost(token_usage, settings.model_prices)
        tokens_in = sum(u.prompt_tokens for u in token_usage)
        tokens_out = sum(u.completion_tokens for u in token_usage)
        # 若数量为 0，前端要能给出明确提示（LLM 返回了文本但解析不出任何用例）
        if len(saved) == 0:
            _gen_persist(job_id, {
                "status": "error", "generated": 0, "raw_text": raw_text,
                "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
                "error": (
                    "LLM 已返回文本，但解析后 0 条用例落库。"
                    "可能是模型未按「用例N：【级别】标题」格式输出，"
                    "或仅返回了思考过程被截断。请查看 raw_text 字段确认。"
                ),
            })
        else:
            _gen_persist(job_id, {
                "status": "done", "generated": len(saved), "raw_text": raw_text,
                "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
            })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        msg = (str(e).strip() or f"{type(e).__name__}: <无消息>")
        _gen_persist(job_id, {"status": "error", "error": msg})
        if job is not None:
            job["traceback"] = tb
        print(f"[pd_gen_job {job_id}] failed:\n{tb}")
    finally:
        _gen_persist(job_id, {"finished_at": store.now_ms()})


async def _run_dynamic_gen_job(
    job_id: str, agent: AgentUnderTest, req: DynamicGenerateRequest
) -> None:
    """异步执行动态对话用例生成。"""
    job = _GEN_JOBS.get(job_id)
    if job is None:
        return
    try:
        # 重置 token 累计器
        llm.reset_token_usage()
        from ..dynamic_generator import generate_dynamic_cases
        # 用户勾选「使用智能体分析结果」时：若 agent 还没分析过，先 analyze_agent 一次
        # 并把结果回写到智能体上，让前端在「智能体分析结果」处能直接看到
        analysis = (agent.analysis or None) if req.use_analysis else None
        if req.use_analysis and not analysis:
            try:
                from ..generator import analyze_agent
                analysis = await analyze_agent(agent)
                if analysis:
                    store.update_agent_analysis(agent.id or "", analysis)
                    agent.analysis = analysis  # 让本协程内后续逻辑也能拿到
            except Exception as ae:
                # 分析失败不阻塞用例生成（生成器在 analysis=None 时仍可工作）
                print(f"[dynamic_gen_job {job_id}] analyze_agent 失败，按无分析继续：{ae}")
                analysis = None
        raw_text, cases = await generate_dynamic_cases(
            agent,
            generate_count=req.generate_count,
            opening_style=req.opening_style,
            analysis=analysis,
            user_hint=req.user_hint,
            dimensions=req.dimensions,
        )
        # 截取 user_hint 前 16 字作为副标签；没填就用「N 条」标识
        extra = (req.user_hint or "").strip().splitlines()[0][:16] if req.user_hint else f"{req.generate_count} 条"

        batch_label = _make_batch_label("dynamic", extra)
        saved = store.save_cases(cases, batch_label=batch_label)
        # 收集 token 用量并计算成本
        usage_records = llm.get_token_usage()
        token_usage = [TokenUsage(**u) for u in usage_records]
        cost_usd = calculate_cost(token_usage, settings.model_prices)
        tokens_in = sum(u.prompt_tokens for u in token_usage)
        tokens_out = sum(u.completion_tokens for u in token_usage)
        if len(saved) == 0:
            _gen_persist(job_id, {
                "status": "error", "generated": 0, "raw_text": raw_text,
                "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
                "error": (
                    "LLM 已返回，但解析后 0 条用例落库。"
                    "请查看 raw_text 字段确认输出是否合规。"
                ),
            })
        else:
            _gen_persist(job_id, {
                "status": "done", "generated": len(saved), "raw_text": raw_text,
                "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
            })
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        msg = (str(e).strip() or f"{type(e).__name__}: <无消息>")
        # 把模型原始输出（如能拿到）也存到 job 里，方便前端排查
        rt = getattr(e, "raw_text", "")
        rt = rt if isinstance(rt, str) else ""
        _gen_persist(job_id, {"status": "error", "error": msg, "raw_text": rt})
        if job is not None:
            job["traceback"] = tb
        print(f"[dynamic_gen_job {job_id}] failed:\n{tb}")
    finally:
        _gen_persist(job_id, {"finished_at": store.now_ms()})


# ---- 路由 ----

@router.get("/agents/{agent_id}/cases")
async def list_cases(agent_id: str) -> list[dict[str, Any]]:
    return [c.model_dump() for c in store.list_cases(agent_id)]


@router.post("/cases/generate")
async def generate_cases(req: GenerateRequest) -> dict[str, Any]:
    """同步生成（兼容老调用）。耗时较长，建议改用 /api/cases/generate_async。"""
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    try:
        analysis, cases = await generate_all(
            a,
            req.dimensions,
            cases_per_dim=req.cases_per_dim,
            cases_per_dim_map=req.cases_per_dim_map,
            analysis=a.analysis or None,  # 已分析过则复用，避免重复 LLM
            opening_mode=req.opening_mode,
            user_opening_text=req.user_opening_text,
        )
    except Exception as e:
        raise HTTPException(500, f"生成失败：{e}")
    # 首次分析完成后写回数据库，下次生成 / 切换页面都能秒读
    if not a.analysis and analysis:
        store.update_agent_analysis(req.agent_id, analysis)
    # 生成批次标签（小组名使用中文维度名称）
    dims_text = _dims_to_chinese(req.dimensions)
    batch_label = _make_batch_label("dimension", dims_text)
    saved = store.save_cases(cases, batch_label=batch_label)
    return {
        "analysis": analysis,
        "generated": len(saved),
        "cases": [c.model_dump() for c in saved],
    }


@router.post("/cases/generate_async")
async def generate_cases_async(req: GenerateRequest) -> dict[str, Any]:
    """异步触发生成。立即返回 job_id，前端可关闭弹窗后通过 /api/cases/generation_jobs/{id} 轮询。"""
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")

    # 计算计划生成的总数（用于前端进度展示）
    planned = 0
    for d in req.dimensions:
        n = req.cases_per_dim_map.get(d, req.cases_per_dim) if req.cases_per_dim_map else req.cases_per_dim
        try:
            n = int(n)
        except Exception:
            n = req.cases_per_dim
        planned += max(0, min(20, n))

    job_id = _gen_new_job(
        a.id or "", a.name, planned,
        mode="dimension",
        params={
            "dimensions": list(req.dimensions or []),
            "cases_per_dim": req.cases_per_dim,
            "cases_per_dim_map": dict(req.cases_per_dim_map or {}),
            "opening_mode": req.opening_mode,
            "user_opening_text": req.user_opening_text,
        },
    )
    asyncio.create_task(_run_gen_job(job_id, a, req))
    return {"job_id": job_id, "planned": planned}


@router.get("/cases/generation_jobs/{job_id}")
async def get_generation_job(job_id: str) -> dict[str, Any]:
    # 优先内存（带 traceback 等运行时附加字段），落库的也回退查 DB（重启后内存已空）
    job = _GEN_JOBS.get(job_id) or store.get_gen_job(job_id)
    if not job:
        raise HTTPException(404, "job 不存在或已被清理")
    return job


@router.get("/cases/generation_jobs")
async def list_generation_jobs(active_only: bool = False) -> list[dict[str, Any]]:
    # 直接从 DB 读，重启后也能完整看到历史
    return store.list_gen_jobs(active_only=active_only)


@router.post("/cases/generate_pd")
async def generate_cases_pd(req: PromptDebuggerGenerateRequest) -> dict[str, Any]:
    """同步生成（PD 风格）。耗时较长，可改用 /api/cases/generate_pd_async。"""
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    try:
        # 仅当前端勾选「使用分析结果」时才注入已缓存的 analysis
        analysis = (a.analysis or None) if req.use_analysis else None
        raw_text, cases = await generate_pd_cases(
            a,
            test_points=req.test_points,
            test_case_level=req.test_case_level,
            opening_style=req.opening_style,
            generate_count=req.generate_count,
            analysis=analysis,
            user_opening_text=req.user_opening_text,
        )
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[generate_pd] 同步生成失败:\n{tb}")
        msg = str(e).strip() or f"{type(e).__name__}: <无消息>"
        raise HTTPException(500, f"生成失败：{msg}")
    extra = (req.test_points or "").strip().splitlines()[0][:16] if req.test_points else req.test_case_level
    batch_label = _make_batch_label("prompt_debugger", extra)
    saved = store.save_cases(cases, batch_label=batch_label)
    return {
        "raw_text": raw_text,
        "generated": len(saved),
        "cases": [c.model_dump() for c in saved],
    }


@router.post("/cases/generate_pd_async")
async def generate_cases_pd_async(req: PromptDebuggerGenerateRequest) -> dict[str, Any]:
    """异步触发 PD 风格生成。立即返回 job_id，前端可关闭弹窗后通过 /api/cases/generation_jobs/{id} 轮询。"""
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")

    # PD 风格不区分维度，planned 直接用 generate_count
    planned = max(1, min(50, req.generate_count))
    job_id = _gen_new_job(
        a.id or "", a.name, planned,
        mode="prompt_debugger",
        params={
            "test_points": req.test_points or "",
            "test_case_level": req.test_case_level,
            "opening_style": req.opening_style,
            "user_opening_text": req.user_opening_text,
            "use_analysis": req.use_analysis,
            "generate_count": req.generate_count,
        },
    )
    asyncio.create_task(_run_pd_gen_job(job_id, a, req))
    return {"job_id": job_id, "planned": planned}


@router.post("/cases/generate_dynamic")
async def generate_cases_dynamic(req: DynamicGenerateRequest) -> dict[str, Any]:
    """同步生成动态对话用例。耗时较长，建议改用 /api/cases/generate_dynamic_async。"""
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    try:
        from ..dynamic_generator import generate_dynamic_cases
        analysis = (a.analysis or None) if req.use_analysis else None
        raw_text, cases = await generate_dynamic_cases(
            a,
            generate_count=req.generate_count,
            opening_style=req.opening_style,
            analysis=analysis,
            user_hint=req.user_hint,
            dimensions=req.dimensions,
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[generate_dynamic] 同步生成失败:\n{tb}")
        msg = str(e).strip() or f"{type(e).__name__}: <无消息>"
        raise HTTPException(500, f"生成失败：{msg}")
    extra = (req.user_hint or "").strip().splitlines()[0][:16] if req.user_hint else f"{req.generate_count} 条"
    batch_label = _make_batch_label("dynamic", extra)
    saved = store.save_cases(cases, batch_label=batch_label)
    return {
        "raw_text": raw_text,
        "generated": len(saved),
        "cases": [c.model_dump() for c in saved],
    }


@router.post("/cases/generate_dynamic_async")
async def generate_cases_dynamic_async(req: DynamicGenerateRequest) -> dict[str, Any]:
    """异步触发动态对话用例生成。立即返回 job_id，前端可关闭弹窗后通过 /api/cases/generation_jobs/{id} 轮询。"""
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")
    planned = max(1, min(30, req.generate_count))
    job_id = _gen_new_job(
        a.id or "", a.name, planned,
        mode="dynamic",
        params={
            "dimensions": list(req.dimensions or []),
            "generate_count": req.generate_count,
            "opening_style": req.opening_style,
            "user_hint": req.user_hint or "",
            "use_analysis": req.use_analysis,
        },
    )
    asyncio.create_task(_run_dynamic_gen_job(job_id, a, req))
    return {"job_id": job_id, "planned": planned}


@router.delete("/cases/{case_id}")
async def delete_case(case_id: str) -> dict[str, Any]:
    store.delete_case(case_id)
    return {"ok": True}


@router.post("/cases/batch_delete")
async def batch_delete_cases(payload: dict[str, Any]) -> dict[str, Any]:
    """批量删除测试用例。"""
    ids = payload.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(400, "ids 必须是数组")
    if not ids:
        return {"ok": True, "deleted": 0, "skipped": []}

    deleted = 0
    skipped: list[str] = []
    for cid in ids:
        if not isinstance(cid, str) or not cid:
            continue
        try:
            store.delete_case(cid)
            deleted += 1
        except Exception:
            skipped.append(cid)

    return {"ok": True, "deleted": deleted, "skipped": skipped}


@router.get("/cases/{case_id}")
async def get_case(case_id: str) -> dict[str, Any]:
    c = store.get_case(case_id)
    if not c:
        raise HTTPException(404, "用例不存在")
    return c.model_dump()


@router.put("/cases/{case_id}")
async def update_case(case_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """部分更新单条用例。"""
    if not store.get_case(case_id):
        raise HTTPException(404, "用例不存在")
    fields = {k: v for k, v in body.items()
              if k in {"dimension", "sub_type", "title", "turns", "expectation", "pass_criteria",
                       "weight", "opening_mode", "dialogue_mode", "user_persona", "user_goal",
                       "max_turns", "termination_keywords"}}
    updated = store.update_case(case_id, fields)
    if not updated:
        raise HTTPException(500, "更新失败")
    return updated.model_dump()


@router.post("/cases")
async def create_case(c: TestCase) -> dict[str, Any]:
    """手工新增一条用例。"""
    if not c.agent_id or not store.get_agent(c.agent_id):
        raise HTTPException(400, "agent_id 不存在")
    if not c.turns:
        raise HTTPException(400, "turns 不能为空")
    # 手动创建的用例若未指定 batch_label，则标记为「手动创建」
    if not c.batch_label:
        c.batch_label = _make_batch_label("manual")
    saved = store.save_cases([c])
    return saved[0].model_dump()


@router.post("/cases/validate")
async def validate_case_criteria(c: TestCase) -> dict[str, Any]:
    """验证测试用例的通过标准是否明确、一致、可执行。"""
    from ..criteria_validator import validate_criteria, format_validation_report

    issues = validate_criteria(c)

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]

    return {
        "valid": len(errors) == 0,
        "issues": [i.to_dict() for i in issues],
        "report": format_validation_report(issues),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "suggestions": [i.suggestion for i in issues if i.suggestion],
    }


@router.get("/agents/{agent_id}/batches")
async def list_batches(agent_id: str) -> list[dict[str, Any]]:
    """列出某智能体下所有批次（按创建时间倒序）。"""
    return store.list_batches(agent_id)
