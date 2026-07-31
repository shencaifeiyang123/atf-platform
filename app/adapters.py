"""被测智能体的接入适配器（异步、非流式）。

支持三种协议：
- openai：任何 OpenAI 兼容 chat completions 接口（含 DeepSeek/通义/Moonshot 等）
- bailian：阿里百炼 智能体应用
- coze：扣子（国内/国际，通过 endpoint 区分）

每个 session 通过 `send(messages)` 发出本轮用户消息列表，返回助手回复字符串。
session 内部维护 session_id / conversation_id 等状态。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

from .models import AgentUnderTest


_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z_][\w\.\-]*)\s*\}\}")


def _stringify(v: Any) -> str:
    """把任意 JSON 值转成字符串用于模板替换。dict/list 用 JSON，其他走 str。"""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def render_vars(text: str, variables: dict[str, Any]) -> str:
    """把文本里的 {{key}} 占位符替换为 variables[key]；缺失则保留原样。

    变量可以是任意 JSON 值（嵌套对象/数组），渲染时序列化为 JSON 字符串。
    """
    if not text or not variables:
        return text or ""

    def _sub(m: re.Match) -> str:
        k = m.group(1)
        if k in variables:
            return _stringify(variables[k])
        return m.group(0)

    return _VAR_PATTERN.sub(_sub, text)


def render_messages(msgs: list[dict[str, str]], variables: dict[str, Any]) -> list[dict[str, str]]:
    if not variables:
        return msgs
    return [{**m, "content": render_vars(m.get("content", ""), variables)} for m in msgs]


class AgentSession(ABC):
    """与被测智能体的一次对话会话。多轮间复用同一个 session 以保持上下文。"""

    def __init__(self, agent: AgentUnderTest):
        self.agent = agent
        self.variables: dict[str, Any] = dict(agent.variables or {})

    @abstractmethod
    async def send(self, messages: list[dict[str, str]]) -> str:
        """传入完整 messages 列表（含历史），返回最后一轮 assistant 回复。"""

    async def close(self) -> None:  # 可选清理
        return None


class OpenAISession(AgentSession):
    """OpenAI 兼容接口。"""

    def __init__(self, agent: AgentUnderTest):
        super().__init__(agent)
        cfg = agent.config or {}
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        self.api_key = cfg.get("api_key") or ""
        self.model = cfg.get("model") or ""
        self.temperature = float(cfg.get("temperature") or 0.7)
        if not self.base_url or not self.api_key or not self.model:
            raise ValueError("OpenAI 适配器需要 config: base_url / api_key / model")

    async def send(self, messages: list[dict[str, str]]) -> str:
        # 渲染 {{var}} 占位符
        msgs = render_messages(list(messages), self.variables)
        # 注入被测智能体自己的 system_prompt（如果用户没在 messages 里加过）
        if self.agent.system_prompt and (not msgs or msgs[0].get("role") != "system"):
            sys_content = render_vars(self.agent.system_prompt, self.variables)
            msgs = [{"role": "system", "content": sys_content}] + msgs

        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": msgs,
            "temperature": self.temperature,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json=body,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"OpenAI 适配器 HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            try:
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                raise RuntimeError(f"OpenAI 响应格式异常: {json.dumps(data)[:300]}")


class BailianSession(AgentSession):
    """阿里百炼智能体应用。
    POST {endpoint}/api/v1/apps/{app_id}/completion
    """

    def __init__(self, agent: AgentUnderTest):
        super().__init__(agent)
        cfg = agent.config or {}
        self.api_key = cfg.get("api_key") or ""
        self.app_id = cfg.get("app_id") or ""
        self.endpoint = (cfg.get("endpoint") or "https://dashscope.aliyuncs.com").rstrip("/")
        if not self.api_key or not self.app_id:
            raise ValueError("百炼适配器需要 config: api_key / app_id")
        self.session_id: Optional[str] = None

    async def send(self, messages: list[dict[str, str]]) -> str:
        # 百炼自身维护历史，只取最后一条 user 消息发送
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        if not last_user:
            raise RuntimeError("百炼适配器：messages 中没有 user 消息")
        last_user = render_vars(last_user, self.variables)

        url = f"{self.endpoint}/api/v1/apps/{self.app_id}/completion"
        body: dict[str, Any] = {
            "input": {"prompt": last_user},
            "parameters": {},
        }
        if self.variables:
            # 百炼智能体应用通过 biz_params 注入自定义参数；嵌套对象保留为 dict，原子值转字符串
            body["input"]["biz_params"] = {
                k: v if isinstance(v, (dict, list)) else str(v)
                for k, v in self.variables.items()
            }
        if self.session_id:
            body["input"]["session_id"] = self.session_id

        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json=body,
            )
            if r.status_code >= 400:
                raise RuntimeError(f"百炼 HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()

        out = data.get("output") or {}
        text = out.get("text") or ""
        if not text:
            # 错误码
            code = data.get("code") or out.get("code") or ""
            msg = data.get("message") or out.get("message") or "空内容"
            raise RuntimeError(f"百炼返回错误：{code} {msg}")
        sid = out.get("session_id")
        if isinstance(sid, str) and sid:
            self.session_id = sid
        return text


class CozeSession(AgentSession):
    """扣子（Coze）智能体。使用 v3/chat 接口（轮询）。

    config: api_key, bot_id, endpoint（默认 https://api.coze.cn）
    """

    def __init__(self, agent: AgentUnderTest):
        super().__init__(agent)
        cfg = agent.config or {}
        self.api_key = cfg.get("api_key") or ""
        self.bot_id = cfg.get("bot_id") or ""
        self.endpoint = (cfg.get("endpoint") or "https://api.coze.cn").rstrip("/")
        if not self.api_key or not self.bot_id:
            raise ValueError("Coze 适配器需要 config: api_key / bot_id")
        self.user_id = cfg.get("user_id") or f"tester_{uuid.uuid4().hex[:8]}"
        self.conversation_id: Optional[str] = None

    async def send(self, messages: list[dict[str, str]]) -> str:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content") or ""
                break
        if not last_user:
            raise RuntimeError("Coze 适配器：messages 中没有 user 消息")
        last_user = render_vars(last_user, self.variables)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [
                {"role": "user", "content": last_user, "content_type": "text"}
            ],
        }
        if self.variables:
            # Coze 的 Bot 变量：只接受字符串值
            body["custom_variables"] = {k: str(v) for k, v in self.variables.items()}
        if self.conversation_id:
            body["conversation_id"] = self.conversation_id

        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{self.endpoint}/v3/chat", headers=headers, json=body)
            if r.status_code >= 400:
                raise RuntimeError(f"Coze HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            chat = (data or {}).get("data") or {}
            chat_id = chat.get("id")
            conv_id = chat.get("conversation_id")
            if conv_id:
                self.conversation_id = conv_id
            if not chat_id:
                raise RuntimeError(f"Coze 响应无 chat id: {json.dumps(data)[:300]}")

            # 轮询直到 completed
            deadline = time.time() + 90
            while time.time() < deadline:
                rr = await client.get(
                    f"{self.endpoint}/v3/chat/retrieve",
                    headers=headers,
                    params={"chat_id": chat_id, "conversation_id": conv_id},
                )
                rdata = rr.json().get("data") or {}
                status = rdata.get("status")
                if status in ("completed", "failed", "requires_action"):
                    break
                await _sleep(0.8)
            else:
                raise RuntimeError("Coze 对话超时未完成")

            if status == "failed":
                last_error = rdata.get("last_error") or {}
                raise RuntimeError(f"Coze 对话失败: {last_error.get('msg', status)}")
            if status == "requires_action":
                raise RuntimeError("Coze 对话需要额外操作 (requires_action)，当前不支持")

            # 拉消息列表
            mr = await client.get(
                f"{self.endpoint}/v3/chat/message/list",
                headers=headers,
                params={"chat_id": chat_id, "conversation_id": conv_id},
            )
            mdata = mr.json().get("data") or []
            # 取最近的一条 assistant answer
            for msg in reversed(mdata):
                if msg.get("role") == "assistant" and msg.get("type") == "answer":
                    return msg.get("content") or ""
            raise RuntimeError("Coze 未找到 assistant 回复")


async def _sleep(s: float) -> None:
    import asyncio
    await asyncio.sleep(s)


def create_session(agent: AgentUnderTest) -> AgentSession:
    if agent.adapter == "openai":
        return OpenAISession(agent)
    if agent.adapter == "bailian":
        return BailianSession(agent)
    if agent.adapter == "coze":
        return CozeSession(agent)
    raise ValueError(f"不支持的适配器类型: {agent.adapter}")
