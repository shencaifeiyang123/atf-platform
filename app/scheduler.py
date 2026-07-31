"""定时任务调度引擎（进程内）。

设计要点：
- 30 秒 tick：查询 list_schedules_due，到点的逐个 _fire
- compute_next：interval / daily / weekly 三种触发器，纯函数（无 IO，方便单测）
- 重叠保护（on_overlap=skip）：上次 run 还在 running 时跳过本次，但仍把 next_run_at
  推到下一次窗口，避免堆积过期任务后一次性触发
- 防御：agent 不存在 → 自动禁用并落日志；selector 解析后没有用例 → 跳过本次但更新 next_run_at
- 重启后续跑：lifespan 启动时会调用 _kick_off_pending_next_run 填充缺失的 next_run_at；
  本身 next_run_at 已落库的任务直接被 tick 接管即可
- fire-and-forget：调度器自身在事件循环里以 task 形式跑，不阻塞 API
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

from . import store
from .config import settings
from .models import AgentUnderTest, Schedule, TestCase, TestRun
from .runner import start_run


# 调度循环间隔（秒）。30s 在「不漏触发」与「不空转」之间折中。
TICK_INTERVAL_S = 30
# interval 触发器最小分钟数（防误填 0/1 把系统打爆）
MIN_INTERVAL_MIN = 5
# WAL checkpoint 间隔（tick 次数）。30s × 120 = 3600s = 1 小时
WAL_CHECKPOINT_EVERY = 120


# ---------- 触发器：计算下一次触发时间（毫秒）----------

def compute_next(trigger_dict: dict, now_ms: int) -> Optional[int]:
    """根据触发器配置算出下一次触发时间戳（毫秒，本地时区）。

    trigger_dict 是 ScheduleTrigger.model_dump()，避免 import 循环。
    返回 None 表示触发器配置非法。
    """
    ttype = trigger_dict.get("type") or ""
    now = datetime.fromtimestamp(now_ms / 1000)

    if ttype == "interval":
        minutes = max(MIN_INTERVAL_MIN, int(trigger_dict.get("minutes") or 0))
        nxt = now + timedelta(minutes=minutes)
        return int(nxt.timestamp() * 1000)

    if ttype == "daily":
        hour = _clamp_int(trigger_dict.get("hour"), 0, 23, default=0)
        minute = _clamp_int(trigger_dict.get("minute"), 0, 59, default=0)
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return int(candidate.timestamp() * 1000)

    if ttype == "weekly":
        hour = _clamp_int(trigger_dict.get("hour"), 0, 23, default=0)
        minute = _clamp_int(trigger_dict.get("minute"), 0, 59, default=0)
        weekday = _clamp_int(trigger_dict.get("weekday"), 0, 6, default=0)
        # datetime.weekday(): 0=周一 ... 6=周日，与我们的约定一致
        days_ahead = (weekday - now.weekday()) % 7
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        if candidate <= now:
            candidate += timedelta(days=7)
        return int(candidate.timestamp() * 1000)

    return None


def _clamp_int(v, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


# ---------- 用例解析 ----------

def _resolve_cases(agent_id: str, selector_dict: dict) -> list[TestCase]:
    """根据 selector 模式从 store 里挑出本次要跑的用例。

    - all：该 agent 全部用例（动态）
    - dimensions：维度匹配
    - ids：固定 id 列表（已删的会被自动过滤掉）
    """
    mode = selector_dict.get("mode") or "all"
    all_cases = store.list_cases(agent_id)
    if mode == "all":
        return all_cases
    if mode == "dimensions":
        dims = set(selector_dict.get("dimensions") or [])
        if not dims:
            return []
        return [c for c in all_cases if c.dimension in dims]
    if mode == "ids":
        wanted = set(selector_dict.get("ids") or [])
        if not wanted:
            return []
        return [c for c in all_cases if c.id in wanted]
    return []


# ---------- 单次触发 ----------

def _is_run_active(run_id: str) -> bool:
    """上次 run 是否还在跑。空 run_id / 已结束 / 不存在都视为不活跃。"""
    if not run_id:
        return False
    r = store.get_run(run_id)
    if r is None:
        return False
    return r.status in ("pending", "running")


def _bump_next_run_at(schedule: Schedule, *, status: str = "", run_id: str = "") -> None:
    """计算并写回 next_run_at 与上次执行的元数据。"""
    nxt = compute_next(schedule.trigger.model_dump(), store.now_ms())
    fields: dict = {"next_run_at": nxt}
    if status:
        fields["last_run_status"] = status
    if run_id:
        fields["last_run_id"] = run_id
    fields["last_run_at"] = store.now_ms()
    store.update_schedule(schedule.id or "", fields)


def fire(schedule: Schedule) -> None:
    """触发一次定时任务。已在事件循环里调用（runner.start_run 依赖这一点）。"""
    sid = schedule.id or ""

    # 1. agent 必须存在
    a = store.get_agent(schedule.agent_id)
    if a is None:
        print(f"[scheduler] schedule {sid} 引用的 agent={schedule.agent_id} 已不存在，自动禁用")
        store.update_schedule(sid, {"enabled": False, "last_run_status": "agent_missing"})
        return

    # 2. 重叠保护
    if (schedule.on_overlap or "skip") == "skip" and _is_run_active(schedule.last_run_id):
        print(f"[scheduler] schedule {sid} 上次 run={schedule.last_run_id} 还在跑，本次跳过")
        _bump_next_run_at(schedule, status="skipped_overlap")
        return

    # 3. 解析用例
    cases = _resolve_cases(schedule.agent_id, schedule.selector.model_dump())
    if not cases:
        print(f"[scheduler] schedule {sid} 没有匹配的用例，跳过")
        _bump_next_run_at(schedule, status="skipped_empty")
        return

    # 4. 建 run 并启动
    name_prefix = schedule.name or "定时任务"
    run = TestRun(
        agent_id=schedule.agent_id,
        name=f"{name_prefix}（定时）",
        total=len(cases),
        schedule_id=sid,
    )
    run = store.create_run(run)
    concurrency = max(1, min(int(schedule.concurrency or 5), settings.max_concurrency))
    start_run(run.id or "", a, cases, concurrency)
    print(f"[scheduler] schedule {sid} 触发 run={run.id} cases={len(cases)}")

    # 5. 推 next_run_at + 记元数据
    _bump_next_run_at(schedule, status="dispatched", run_id=run.id or "")


# ---------- 启动期回填 ----------

def kick_off_pending_next_run() -> int:
    """启动期：给所有 enabled 但 next_run_at 缺失的 schedule 补一个 next_run_at。

    返回补回的数量。create_schedule 后若没立刻设置 next_run_at（例如手动 enable）
    会被这里捞起来。
    """
    fixed = 0
    for s in store.list_schedules():
        if not s.enabled or s.next_run_at:
            continue
        nxt = compute_next(s.trigger.model_dump(), store.now_ms())
        if nxt is None:
            continue
        store.update_schedule(s.id or "", {"next_run_at": nxt})
        fixed += 1
    if fixed:
        print(f"[scheduler] 启动时补 next_run_at：{fixed} 条")
    return fixed


# ---------- 主循环 ----------

class SchedulerLoop:
    """以 asyncio task 跑的调度循环。lifespan 起停。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None

    async def _tick(self) -> None:
        try:
            due = store.list_schedules_due(store.now_ms())
        except Exception as e:
            print(f"[scheduler] list_schedules_due 失败：{e}")
            return
        for s in due:
            try:
                fire(s)
            except Exception as e:
                # 单条失败不阻塞其他 schedule
                print(f"[scheduler] fire schedule {s.id} 失败：{e}")
                # 失败也要推进 next_run_at，避免 hot loop
                try:
                    _bump_next_run_at(s, status=f"error: {str(e)[:60]}")
                except Exception:
                    pass

    async def _run(self) -> None:
        assert self._stop_event is not None
        # 启动时立刻 tick 一次（系统重启后到点的不用再等 30 秒）
        await self._tick()
        ticks_since_checkpoint = 0
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=TICK_INTERVAL_S
                )
                # wait 因 stop 触发 → 退出
                break
            except asyncio.TimeoutError:
                pass
            await self._tick()
            # 定时 WAL checkpoint：把 WAL 文件刷回主 DB 并释放空间
            ticks_since_checkpoint += 1
            if ticks_since_checkpoint >= WAL_CHECKPOINT_EVERY:
                ticks_since_checkpoint = 0
                try:
                    result = store.wal_checkpoint()
                    if result["checkpointed"] > 0:
                        print(f"[scheduler] WAL checkpoint: {result['checkpointed']} 页刷回")
                except Exception as e:
                    print(f"[scheduler] WAL checkpoint 失败：{e}")

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="scheduler-loop")
        print(f"[scheduler] 启动调度循环（tick={TICK_INTERVAL_S}s）")

    async def stop(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        self._task = None
        self._stop_event = None
        print("[scheduler] 调度循环已停止")


# 全局单例（main.py 在 lifespan 里 start/stop）
loop = SchedulerLoop()
