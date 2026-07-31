"""定时任务相关路由：/api/schedules/*。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import scheduler as scheduler_mod
from .. import store
from ..models import Schedule, ScheduleSelector, ScheduleTrigger, ScheduleUpsert

router = APIRouter()


def _schedule_to_dict(s: Schedule, agent_name: str = "") -> dict[str, Any]:
    """Schedule -> dict，附带 agent_name 给前端展示用。"""
    d = s.model_dump()
    d["agent_name"] = agent_name
    return d


def _validate_trigger(t: ScheduleTrigger) -> None:
    if t.type == "interval":
        if t.minutes < scheduler_mod.MIN_INTERVAL_MIN:
            raise HTTPException(400, f"interval 最小 {scheduler_mod.MIN_INTERVAL_MIN} 分钟")
    elif t.type == "daily":
        if not (0 <= t.hour <= 23 and 0 <= t.minute <= 59):
            raise HTTPException(400, "daily 触发的 hour/minute 不合法")
    elif t.type == "weekly":
        if not (0 <= t.weekday <= 6):
            raise HTTPException(400, "weekly 的 weekday 范围 0-6（0=周一，6=周日）")
        if not (0 <= t.hour <= 23 and 0 <= t.minute <= 59):
            raise HTTPException(400, "weekly 触发的 hour/minute 不合法")
    else:
        raise HTTPException(400, f"未知触发类型：{t.type}")


def _validate_selector(s: ScheduleSelector, agent_id: str) -> None:
    if s.mode == "dimensions" and not s.dimensions:
        raise HTTPException(400, "selector.mode=dimensions 时 dimensions 不能为空")
    if s.mode == "ids":
        if not s.ids:
            raise HTTPException(400, "selector.mode=ids 时 ids 不能为空")
        all_ids = {c.id for c in store.list_cases(agent_id)}
        missing = [i for i in s.ids if i not in all_ids]
        if missing:
            raise HTTPException(400, f"用例不存在：{missing[:3]}{'...' if len(missing) > 3 else ''}")


@router.get("/schedules")
async def list_schedules() -> list[dict[str, Any]]:
    name_map = {a.id: a.name for a in store.list_agents()}
    return [_schedule_to_dict(s, name_map.get(s.agent_id, "")) for s in store.list_schedules()]


@router.get("/schedules/{schedule_id}")
async def get_schedule(schedule_id: str) -> dict[str, Any]:
    s = store.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, "schedule 不存在")
    a = store.get_agent(s.agent_id)
    return _schedule_to_dict(s, a.name if a else "")


@router.post("/schedules")
async def create_schedule(payload: Schedule) -> dict[str, Any]:
    if not payload.name:
        raise HTTPException(400, "name 不能为空")
    if not store.get_agent(payload.agent_id):
        raise HTTPException(404, "agent 不存在")
    _validate_trigger(payload.trigger)
    _validate_selector(payload.selector, payload.agent_id)
    payload.id = None  # 强制后端生成
    # 启用时立刻算出 next_run_at
    if payload.enabled:
        payload.next_run_at = scheduler_mod.compute_next(
            payload.trigger.model_dump(), store.now_ms()
        )
    saved = store.create_schedule(payload)
    a = store.get_agent(saved.agent_id)
    return _schedule_to_dict(saved, a.name if a else "")


@router.put("/schedules/{schedule_id}")
async def update_schedule(schedule_id: str, payload: ScheduleUpsert) -> dict[str, Any]:
    cur = store.get_schedule(schedule_id)
    if not cur:
        raise HTTPException(404, "schedule 不存在")

    fields: dict[str, Any] = {}
    if payload.name is not None:
        if not payload.name:
            raise HTTPException(400, "name 不能为空")
        fields["name"] = payload.name
    if payload.agent_id is not None:
        if not store.get_agent(payload.agent_id):
            raise HTTPException(404, "agent 不存在")
        fields["agent_id"] = payload.agent_id
    if payload.trigger is not None:
        _validate_trigger(payload.trigger)
        fields["trigger"] = payload.trigger
    if payload.selector is not None:
        target_agent = payload.agent_id or cur.agent_id
        _validate_selector(payload.selector, target_agent)
        fields["selector"] = payload.selector
    if payload.concurrency is not None:
        fields["concurrency"] = max(1, int(payload.concurrency))
    if payload.enabled is not None:
        fields["enabled"] = bool(payload.enabled)
    if payload.on_overlap is not None:
        fields["on_overlap"] = payload.on_overlap

    # trigger 变 / 启用状态切换 → 重算 next_run_at
    new_enabled = fields.get("enabled", cur.enabled)
    new_trigger = fields.get("trigger", cur.trigger)
    if (
        "trigger" in fields
        or ("enabled" in fields and fields["enabled"] != cur.enabled)
    ):
        if new_enabled:
            trigger_dict = (
                new_trigger.model_dump() if hasattr(new_trigger, "model_dump") else new_trigger
            )
            fields["next_run_at"] = scheduler_mod.compute_next(trigger_dict, store.now_ms())
        else:
            fields["next_run_at"] = None

    updated = store.update_schedule(schedule_id, fields)
    a = store.get_agent(updated.agent_id) if updated else None
    return _schedule_to_dict(updated, a.name if a else "")


@router.delete("/schedules/{schedule_id}")
async def delete_schedule(schedule_id: str) -> dict[str, Any]:
    if not store.get_schedule(schedule_id):
        raise HTTPException(404, "schedule 不存在")
    store.delete_schedule(schedule_id)
    return {"ok": True}


@router.post("/schedules/{schedule_id}/run-now")
async def run_schedule_now(schedule_id: str) -> dict[str, Any]:
    """立刻触发一次（不影响 next_run_at 的常规节奏，仅用于人工验证）。"""
    s = store.get_schedule(schedule_id)
    if not s:
        raise HTTPException(404, "schedule 不存在")
    try:
        scheduler_mod.fire(s)
    except Exception as e:
        raise HTTPException(500, f"触发失败：{e}")
    # 拉一遍最新状态返回
    s2 = store.get_schedule(schedule_id)
    a = store.get_agent(s2.agent_id) if s2 else None
    return _schedule_to_dict(s2 or s, a.name if a else "")
