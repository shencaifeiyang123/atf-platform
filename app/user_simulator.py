"""用户模拟器：用 LLM 扮演真实用户，与被测智能体多轮对话。

设计灵感来自 DeepEval 的 ConversationSimulator / promptfoo 的 conversation provider：
- 给定一个「人设 + 目标」，模拟器在每一轮根据已有对话历史生成下一条用户消息
- 当目标已达成 / 被拒绝 / 想离开时，模拟器输出 [END] 主动结束
- runner 侧再加一层 max_turns 硬限位，防止失控

只暴露 next_user_message(history) -> (text, ended) 这一个接口，runner 不必关心 prompt 细节。
"""
from __future__ import annotations

from typing import Any

from .config import settings
from .llm import LLMError, chat


_SYSTEM_TPL = """你正在扮演一位真实用户，与一个 AI 智能体进行对话测试。

【你的人设】
{persona}

【你本轮想要达成的目标 / 你的诉求】
{goal}

【对话规则】
- 始终用第一人称发言，符合人设的语气、用词、知识水平和情绪状态。
- 每次只发一条用户消息（一两句话），不要替 AI 继续说话，不要解释你在做测试。
- 收到 AI 的回复后，根据它的内容决定下一步：追问 / 反驳 / 提供更多信息 / 表达情绪 / 切换话题 / 继续执行目标中描述的行为。
- **重要**：严格按照【目标】中描述的具体行为执行。如果目标说"会说'今天天气真好'"，你必须在某一轮真的说出这句话；如果目标说"会发送'???'"，你必须真的发送这些符号。不要只是"想到"或"打算"，而是要实际执行。
- **结束条件**：只有在以下情况才输出 [END] 结束对话：
  1. 你已经完整执行了目标中描述的所有行为（不是"打算做"，而是"已经做了"）
  2. AI 已多次拒绝且你确认无法绕过
  3. 你已经感到不耐烦想离开（但要先尝试至少 3-5 轮对话）
- 不要使用任何 Markdown、JSON、代码块或前缀（如「用户：」「我：」），直接给出你想说的话即可。
- 不要暴露你是 AI、是测试程序，也不要透露你的人设和目标。
"""


class UserSimulator:
    """每个测试用例独占一个实例，内部无状态（历史由 runner 传入）。"""

    def __init__(self, persona: str, goal: str):
        self.system = _SYSTEM_TPL.format(
            persona=(persona or "通用用户，普通用语，期望得到有用的帮助").strip(),
            goal=(goal or "正常使用该智能体，看它能否解决你的问题").strip(),
        )

    async def next_user_message(self, history: list[dict[str, Any]]) -> tuple[str, bool]:
        """根据已有对话生成下一条用户消息。

        - history: 与被测智能体的真实对话轨迹，元素为 {role: 'user'|'assistant', content: str, ...}。
          注意从模拟器视角看，被测的 assistant = 模拟器的"对方"，因此需要做一次角色翻转。
        - 返回 (text, ended)。ended=True 表示模拟器主动结束（输出了 [END]）。
        """
        cfg = settings.generator_llm
        msgs: list[dict[str, str]] = [{"role": "system", "content": self.system}]
        # 视角翻转：被测对话中的 user(模拟器自己) ↔ assistant(被测)
        # 翻转后 LLM 才会把"被测的话"当成对方发言，自然续接下一条用户消息。
        # 只保留最近 20 轮，防止超出模型上下文窗口
        recent = history[-20:] if len(history) > 20 else history
        for h in recent:
            role = h.get("role")
            content = h.get("content") or ""
            if role == "user":
                msgs.append({"role": "assistant", "content": content})
            elif role == "assistant":
                msgs.append({"role": "user", "content": content})
            # 其他角色（如 system）忽略
        # 如果历史里第一条就是模拟器自己（role=user），LLM 会缺少触发上下文，补一条 user 引导。
        # 但绝大多数情况下首轮已经由被测先发话或由 runner 用一句 "你好" 触发，这里无需特殊处理。

        try:
            text = await chat(cfg, msgs, timeout=60.0, max_tokens=400)
        except LLMError:
            # 模拟器出错时让 runner 自然结束本轮，不抛到上层影响整个 run
            return "", True

        text = (text or "").strip()
        ended = False
        if "[END]" in text:
            ended = True
            text = text.replace("[END]", "").strip()
        # 模拟器空输出也视为结束
        if not text:
            ended = True
        return text, ended
