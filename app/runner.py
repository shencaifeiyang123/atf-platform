"""任务编排器：批量执行测试用例。

- 受 TwinTalk runner.js 启发，使用 asyncio 事件循环 + 信号量控制并发
- 每条用例独立 session（保证上下文不串）
- 通过 in-memory event bus 把进度推给 SSE
- 任务结果实时落库
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from typing import Any

from . import llm, store
from .adapters import create_session
from .config import settings
from .cost import calculate_cost
from .evaluator import evaluate
from .models import AgentUnderTest, CaseResult, TestCase, TestRun, TokenUsage
from .user_simulator import UserSimulator


# ---------- 事件总线 ----------

class RunBus:
    """每个 run 一个 asyncio.Queue 列表，SSE 客户端订阅时获取一份。"""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subs[run_id].append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        if q in self._subs.get(run_id, []):
            self._subs[run_id].remove(q)

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        for q in list(self._subs.get(run_id, [])):
            if q.full():
                try:
                    q.get_nowait()
                except Exception:
                    pass
            try:
                q.put_nowait(event)
            except Exception:
                pass

    def close(self, run_id: str) -> None:
        for q in self._subs.get(run_id, []):
            try:
                q.put_nowait({"type": "end"})
            except Exception:
                pass
        self._subs.pop(run_id, None)


bus = RunBus()


# ---------- 单条用例执行 ----------

# ai 开场触发语：用一条最朴素的 user 消息让 AI 主动开口，
# 大多数对话型智能体在收到它时会按自身角色发起开场白。
_AI_OPENING_TRIGGER = "你好"


def _agent_with_user_context(agent: AgentUnderTest, case: TestCase) -> AgentUnderTest:
    """dynamic 模式下，把虚拟用户的 persona / goal 注入被测智能体的 system_prompt。

    动机：测试用例里指定了「10 岁三年级小学生」这种用户画像后，被测 AI 也应当
    知道对方是谁（否则它就只能从 simulator 的措辞里猜，公平性不足）。
    做法：克隆一份 agent，在原 system_prompt 末尾追加一段「当前对话用户」说明。
    仅 dynamic 模式生效；scripted 模式原样返回。
    """
    if (case.dialogue_mode or "scripted") != "dynamic":
        return agent
    persona = (case.user_persona or "").strip()
    goal = (case.user_goal or "").strip()
    if not persona and not goal:
        return agent

    extra_lines = ["", "---", "【当前对话用户画像（测试场景信息）】"]
    if persona:
        extra_lines.append(f"- 用户身份：{persona}")
    if goal:
        extra_lines.append(f"- 用户当前诉求：{goal}")
    extra_lines.append("请基于以上用户画像调整你的回复风格、用词复杂度和内容深度。")

    cloned = deepcopy(agent)
    base = (cloned.system_prompt or "").rstrip()
    cloned.system_prompt = base + "\n" + "\n".join(extra_lines)
    return cloned


async def _run_one_case(agent: AgentUnderTest, case: TestCase) -> CaseResult:
    # 重置 token 累计器（每条 case 独立统计）
    llm.reset_token_usage()

    transcript: list[dict[str, Any]] = []
    # dynamic 模式下把虚拟用户画像注入 system_prompt（要求被测 AI 也知道对方是谁）
    effective_agent = _agent_with_user_context(agent, case)
    session = create_session(effective_agent)
    try:
        # 开场设置：opening_mode == "ai" 时，先发一条触发消息让 AI 主动开场，
        # 这条触发会被记录到 transcript 第一项（role=user, trigger=True），
        # 评估器可识别该标记从而判断 AI 开场的得体性。
        if (case.opening_mode or "").lower() == "ai":
            # 优先用 case.turns[0] 作为触发词（来自 user_opening_text 配置，如 //init / /start），
            # 没有时回退到默认「你好」
            trigger_text = (
                (case.turns[0].content if case.turns else "") or _AI_OPENING_TRIGGER
            )
            transcript.append({
                "role": "user",
                "content": trigger_text,
                "trigger": True,
            })
            try:
                first_reply = await session.send(
                    [{"role": "user", "content": trigger_text}]
                )
            except Exception as e:
                return CaseResult(
                    case_id=case.id or "",
                    status="error",
                    transcript=transcript,
                    passed=False,
                    score=0.0,
                    error=f"调用被测智能体失败（AI 开场）：{e}",
                )
            transcript.append({"role": "assistant", "content": first_reply})

        if (case.dialogue_mode or "scripted") == "dynamic":
            # 动态对话：每轮由 UserSimulator 根据已有 transcript 生成下一条用户消息。
            # ai 开场已在上方完成首轮，dynamic 直接接着续聊；user/default 开场时若 turns 提供了首条则先发出去。
            sim = UserSimulator(case.user_persona, case.user_goal)
            opening_used_turn0 = (case.opening_mode or "").lower() == "ai"
            if not opening_used_turn0 and case.turns:
                # 用配置的首句话作为开场（例如 "//init"），后续由模拟器接管
                first_user = case.turns[0].content
                transcript.append({"role": "user", "content": first_user})
                try:
                    reply = await session.send(
                        [{"role": "user", "content": first_user}]
                    )
                except Exception as e:
                    return CaseResult(
                        case_id=case.id or "",
                        status="error",
                        transcript=transcript,
                        passed=False,
                        score=0.0,
                        error=f"调用被测智能体失败（动态对话首轮）：{e}",
                    )
                transcript.append({"role": "assistant", "content": reply})

            max_turns = max(1, int(case.max_turns or 6))
            term_kw = [k for k in (case.termination_keywords or []) if k]
            for _ in range(max_turns):
                # 早停：上一条 assistant 命中 termination_keywords
                if term_kw and transcript and transcript[-1].get("role") == "assistant":
                    last_reply = transcript[-1].get("content") or ""
                    if any(kw in last_reply for kw in term_kw):
                        break
                user_text, ended = await sim.next_user_message(transcript)
                if not user_text:
                    break
                transcript.append({
                    "role": "user",
                    "content": user_text,
                    "simulated": True,  # 标记此条为模拟用户生成，便于报告区分
                })
                msgs = [{"role": m["role"], "content": m["content"]} for m in transcript]
                try:
                    reply = await session.send(msgs)
                except Exception as e:
                    return CaseResult(
                        case_id=case.id or "",
                        status="error",
                        transcript=transcript,
                        passed=False,
                        score=0.0,
                        error=f"调用被测智能体失败（动态对话）：{e}",
                    )
                transcript.append({"role": "assistant", "content": reply})
                if ended:
                    break
        else:
            # 脚本模式：原逻辑
            # ai 开场时 turns[0] 已被当作触发词消费，后续轮次从 turns[1:] 开始；
            # user 开场时正常从 turns[0] 开始。
            remaining_turns = case.turns[1:] if (case.opening_mode or "").lower() == "ai" else case.turns
            for turn in remaining_turns:
                transcript.append({"role": "user", "content": turn.content})
                # 给 adapter 的消息列表里去掉自定义字段（trigger 等），仅保留 role/content
                msgs = [{"role": m["role"], "content": m["content"]} for m in transcript]
                try:
                    reply = await session.send(msgs)
                except Exception as e:
                    return CaseResult(
                        case_id=case.id or "",
                        status="error",
                        transcript=transcript,
                        passed=False,
                        score=0.0,
                        error=f"调用被测智能体失败：{e}",
                    )
                transcript.append({"role": "assistant", "content": reply})
    finally:
        try:
            await session.close()
        except Exception:
            pass

    try:
        result = await evaluate(case, transcript)
        # 收集本次 case 的 token 用量（adapter 调用 + judge 调用累计）
        usage_records = llm.get_token_usage()
        result.token_usage = [TokenUsage(**u) for u in usage_records]
        return result
    except Exception as e:
        return CaseResult(
            case_id=case.id or "",
            status="error",
            transcript=transcript,
            passed=False,
            score=0.0,
            error=f"评估异常：{e}",
        )


# ---------- 批量执行 ----------

async def execute_run(run_id: str, agent: AgentUnderTest, cases: list[TestCase], concurrency: int) -> None:
    run = store.get_run(run_id)
    if run is None:
        return
    run.status = "running"
    run.total = len(cases)
    run.started_at = store.now_ms()
    store.update_run(run)
    bus.publish(run_id, {"type": "status", "status": "running", "total": run.total})

    sem = asyncio.Semaphore(max(1, concurrency))
    scores: list[float] = []
    counter_lock = asyncio.Lock()

    async def runner(c: TestCase) -> None:
        async with sem:
            bus.publish(run_id, {"type": "case_start", "case_id": c.id, "title": c.title})
            result = await _run_one_case(agent, c)
            store.save_case_result(run_id, result)

            async with counter_lock:
                r = store.get_run(run_id)
                if r is None:
                    return
                r.finished += 1
                if result.status == "passed":
                    r.passed += 1
                elif result.status == "error":
                    r.errors += 1
                else:
                    r.failed += 1
                scores.append(result.score)
                r.average_score = round(sum(scores) / len(scores), 2) if scores else 0.0
                # 累计 token 用量
                for u in result.token_usage:
                    r.tokens_in += u.prompt_tokens
                    r.tokens_out += u.completion_tokens
                # 计算成本（每条 case 完成后累加）
                r.cost_usd += calculate_cost(result.token_usage, settings.model_prices)
                store.update_run(r)

            bus.publish(run_id, {
                "type": "case_done",
                "case_id": c.id,
                "status": result.status,
                "passed": result.passed,
                "score": result.score,
                "reasons": result.reasons,
                "judge_comment": result.judge_comment,
                "error": result.error,
                "progress": {
                    "finished": r.finished, "total": r.total,
                    "passed": r.passed, "failed": r.failed, "errors": r.errors,
                    "average_score": r.average_score,
                },
            })

    try:
        await asyncio.gather(*(runner(c) for c in cases))
        r = store.get_run(run_id)
        if r:
            r.status = "completed"
            r.finished_at = store.now_ms()
            r.summary = _summary(r)
            store.update_run(r)
            bus.publish(run_id, {"type": "status", "status": "completed", "summary": r.summary})
    except Exception as e:
        r = store.get_run(run_id)
        if r:
            r.status = "failed"
            r.error = str(e)
            r.finished_at = store.now_ms()
            store.update_run(r)
        bus.publish(run_id, {"type": "status", "status": "failed", "error": str(e)})
    finally:
        bus.close(run_id)


def _summary(r: TestRun) -> str:
    if r.total == 0:
        return "无用例"
    rate = r.passed / r.total * 100
    # 评估器返回 1-5 分，报告展示时统一转为 0-100（×20）
    avg_score_display = round(r.average_score * 20, 1)
    return (
        f"共 {r.total} 条，通过 {r.passed}，失败 {r.failed}，错误 {r.errors}；"
        f"通过率 {rate:.1f}%，平均分 {avg_score_display}/100。"
    )


def start_run(run_id: str, agent: AgentUnderTest, cases: list[TestCase], concurrency: int) -> None:
    """fire-and-forget。调用方必须已在事件循环中（FastAPI handler 里）。"""
    asyncio.create_task(execute_run(run_id, agent, cases, concurrency))
