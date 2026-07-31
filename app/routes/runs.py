"""测试任务相关路由：/api/runs/*。"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from .. import store
from ..models import RunRequest, TestRun
from ..runner import bus, start_run

router = APIRouter()

# 维度元数据：用于结果中的维度中文名映射
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


def _results_with_case_meta(run_id: str) -> list[dict[str, Any]]:
    """把 case 表里的 dimension / sub_type / title / weight 一并塞进每条 result。"""
    rows = store.list_case_results_with_meta(run_id)
    dim_label_map = {d["key"]: d["label"] for d in DIMENSION_META}
    for d in rows:
        d["dimension_label"] = dim_label_map.get(d.get("dimension") or "", "")
    return rows


@router.get("/runs")
async def list_runs(
    agent_id: Optional[str] = None,
    schedule_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """返回任务列表；附带 agent_name 字段，便于前端按智能体名称模糊搜索。"""
    runs = store.list_runs(agent_id=agent_id, schedule_id=schedule_id)
    name_map = {a.id: a.name for a in store.list_agents()}
    out: list[dict[str, Any]] = []
    for r in runs:
        d = r.model_dump()
        d["agent_name"] = name_map.get(r.agent_id, "")
        out.append(d)
    return out


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404, "run 不存在")
    return r.model_dump()


@router.delete("/runs/{run_id}")
async def delete_run(run_id: str) -> dict[str, Any]:
    """删除测试任务（含全部用例结果）。"""
    r = store.get_run(run_id)
    if not r:
        raise HTTPException(404, "run 不存在")
    store.delete_run(run_id)
    return {"ok": True}


@router.post("/runs")
async def create_run(req: RunRequest) -> dict[str, Any]:
    a = store.get_agent(req.agent_id)
    if not a:
        raise HTTPException(404, "agent 不存在")

    all_cases = store.list_cases(req.agent_id)
    cases = all_cases
    # 同时传 case_ids 和 batch_id 时取交集；只传一个则按该条件过滤
    if req.case_ids and req.batch_id:
        id_set = set(req.case_ids)
        cases = [c for c in all_cases if c.id in id_set and c.batch_id == req.batch_id]
    elif req.case_ids:
        cases = [c for c in all_cases if c.id in set(req.case_ids)]
    elif req.batch_id:
        cases = [c for c in all_cases if c.batch_id == req.batch_id]
    else:
        # 默认：使用最新批次的用例（list_batches 已按 created_at 降序）
        batches = store.list_batches(req.agent_id)
        if batches:
            latest_bid = batches[0].get("batch_id") or ""
            if latest_bid:
                cases = [c for c in all_cases if c.batch_id == latest_bid]
            # latest_bid 为空表示「未分组」批次，全部跑
    if not cases:
        raise HTTPException(400, "没有可执行的测试用例，请先生成")

    run = TestRun(
        agent_id=req.agent_id,
        name=req.name or f"{a.name} - 批量测试",
        total=len(cases),
    )
    run = store.create_run(run)
    from ..config import settings
    start_run(run.id or "", a, cases, max(1, min(req.concurrency, settings.max_concurrency)))
    return run.model_dump()


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str) -> EventSourceResponse:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run 不存在")

    queue = bus.subscribe(run_id)

    async def event_gen():
        # 立即推一份当前快照
        snapshot = {
            "type": "snapshot",
            "run": run.model_dump(),
            "results": _results_with_case_meta(run_id),
        }
        yield {"event": "message", "data": json.dumps(snapshot, ensure_ascii=False)}

        # 已结束的任务直接关掉
        if run.status in ("completed", "failed", "canceled"):
            yield {"event": "message", "data": json.dumps({"type": "end"}, ensure_ascii=False)}
            return

        # 心跳：每 15 秒推一个 SSE 注释帧，防止中间代理因空闲断开连接
        last_sent = asyncio.get_event_loop().time()
        HEARTBEAT_INTERVAL = 15

        def _heartbeat_if_needed():
            """若超过 HEARTBEAT_INTERVAL 没推过数据，先发心跳再推业务帧。"""
            nonlocal last_sent
            now = asyncio.get_event_loop().time()
            if now - last_sent >= HEARTBEAT_INTERVAL:
                last_sent = now
                return ": heartbeat\n\n"
            last_sent = now
            return ""

        try:
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    last_sent = asyncio.get_event_loop().time()
                    continue

                hb = _heartbeat_if_needed()
                if hb:
                    yield hb
                yield {"event": "message", "data": json.dumps(evt, ensure_ascii=False)}
                if evt.get("type") == "end":
                    return
        finally:
            bus.unsubscribe(run_id, queue)

    return EventSourceResponse(event_gen())


@router.get("/runs/{run_id}/results")
async def get_run_results(run_id: str) -> list[dict[str, Any]]:
    """返回某次测试任务的全部用例结果，并附加每条用例所属的维度等元信息。"""
    return _results_with_case_meta(run_id)


@router.get("/runs/{run_id}/report")
async def get_run_report(run_id: str, format: str = "json") -> Any:
    """聚合测试报告：综合得分 / 通过率 / 维度统计 / 用例明细。"""
    from ..config import settings

    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    agent = store.get_agent(run.agent_id)
    if not agent:
        raise HTTPException(404, "agent not found")

    results = store.list_case_results(run_id)
    # 用例信息：用 case_id 作为索引；只取本 run 涉及到的，避免拉全量
    case_ids = [r.case_id for r in results]
    cases_by_id: dict[str, Any] = {}
    for cid in case_ids:
        c = store.get_case(cid)
        if c is not None:
            cases_by_id[cid] = c

    total = len(results)
    passed_list = [r for r in results if r.passed]
    failed_list = [r for r in results if not r.passed and r.status != "error"]
    error_list = [r for r in results if r.status == "error"]
    pass_rate = round(len(passed_list) / total * 100) if total else 0
    # 评估器返回 1-5 分；报告统一展示为 0-100（×20）以适配前端的颜色阈值与进度条
    SCORE_SCALE = 20
    avg_score = round(sum(r.score for r in results) / total * SCORE_SCALE) if total else 0

    # 按维度聚合
    dim_label_map = {d["key"]: d["label"] for d in DIMENSION_META}
    by_dim: dict[str, dict[str, Any]] = {}
    for r in results:
        c = cases_by_id.get(r.case_id)
        dim = (c.dimension if c else "") or "unknown"
        bucket = by_dim.setdefault(dim, {
            "dim": dim,
            "label": dim_label_map.get(dim, dim),
            "total": 0,
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "score_sum": 0.0,
        })
        bucket["total"] += 1
        if r.status == "error":
            bucket["errors"] += 1
        elif r.passed:
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
        bucket["score_sum"] += r.score

    dim_stats = []
    for d in by_dim.values():
        n = d["total"] or 1
        dim_stats.append({
            "dim": d["dim"],
            "label": d["label"],
            "total": d["total"],
            "passed": d["passed"],
            "failed": d["failed"],
            "errors": d["errors"],
            # 同样转 0-100 分制
            "score": round(d["score_sum"] / n * SCORE_SCALE),
            "pass_rate": round(d["passed"] / n * 100),
        })
    # 维度顺序按 DIMENSION_META 顺序，不在表里的追加到末尾
    order_keys = [d["key"] for d in DIMENSION_META]
    dim_stats.sort(key=lambda x: (
        order_keys.index(x["dim"]) if x["dim"] in order_keys else 999,
        x["dim"],
    ))

    # 失败用例排前面，错误其次，通过最后
    def _rank(r):
        if r.status == "error":
            return 1
        if not r.passed:
            return 0
        return 2

    sorted_results = sorted(results, key=_rank)
    cases_payload = []
    for r in sorted_results:
        c = cases_by_id.get(r.case_id)
        last_user = ""
        turns_payload: list[dict[str, Any]] = []
        if c:
            for t in c.turns:
                turns_payload.append({"role": t.role, "content": t.content})
                if t.role == "user":
                    last_user = t.content
        cases_payload.append({
            "case_id": r.case_id,
            "title": (c.title if c else "") or last_user[:60],
            "dimension": (c.dimension if c else "") or "",
            "dimension_label": dim_label_map.get((c.dimension if c else "") or "", ""),
            "sub_type": (c.sub_type if c else "") or "",
            "weight": (c.weight if c else 0),
            "expectation": (c.expectation if c else "") or "",
            "pass_criteria": (c.pass_criteria if c else []) or [],
            "turns": turns_payload,
            "last_user_message": last_user,
            "status": r.status,
            "passed": r.passed,
            "score": r.score,
            "judge_comment": r.judge_comment or "",
            "reasons": r.reasons or [],
            "error": r.error or "",
            "transcript": r.transcript or [],
        })

    payload: dict[str, Any] = {
        "run": run.model_dump(),
        "agent": {
            "id": agent.id,
            "name": agent.name,
            "industry": agent.industry,
            "adapter": agent.adapter,
        },
        "summary": {
            "total": total,
            "passed": len(passed_list),
            "failed": len(failed_list),
            "errors": len(error_list),
            "pass_rate": pass_rate,
            "avg_score": avg_score,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        },
        "dimensions": dim_stats,
        "cases": cases_payload,
    }

    if format == "md":
        from fastapi.responses import PlainTextResponse

        lines: list[str] = []
        lines.append(f"# 测试报告 · {agent.name}")
        lines.append("")
        lines.append(f"- 任务名称：{run.name or '(未命名)'}")
        lines.append(f"- 智能体：{agent.name}（{agent.adapter} / {agent.industry}）")
        lines.append(f"- 综合评分：**{avg_score}** 通过率：**{pass_rate}%** 总用例：{total} 失败：{len(failed_list)} 错误：{len(error_list)}")
        if run.summary:
            lines.append(f"- 摘要：{run.summary}")
        lines.append("")
        lines.append("## 各维度得分")
        lines.append("")
        lines.append("| 维度 | 总数 | 通过 | 失败 | 错误 | 平均分 | 通过率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for d in dim_stats:
            lines.append(
                f"| {d['label']} | {d['total']} | {d['passed']} | {d['failed']} | {d['errors']} | {d['score']} | {d['pass_rate']}% |"
            )
        lines.append("")
        lines.append("## 用例明细（失败优先）")
        for c in cases_payload:
            mark = "✓" if c["passed"] else ("!" if c["status"] == "error" else "✗")
            lines.append("")
            lines.append(f"### {mark} [{c['dimension_label'] or c['dimension']}] {c['title'] or '(无标题)'}")
            lines.append(f"- 得分：{c['score']} 状态：{c['status']}")
            if c["last_user_message"]:
                lines.append(f"- 用户：{c['last_user_message']}")
            if c["judge_comment"]:
                lines.append(f"- 评审：{c['judge_comment']}")
            if c["reasons"]:
                lines.append("- 原因：")
                for x in c["reasons"]:
                    lines.append(f"  - {x}")
            if c["error"]:
                lines.append(f"- 错误：{c['error']}")
        return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")

    return payload
