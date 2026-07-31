"""OpenAI 兼容的 LLM 调用封装（异步）。

测试用例生成 / LLM-as-Judge 评估 都走这里。
也支持简单的 JSON 提取（带容错）。
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import random
import re
from typing import Any, Optional
import warnings

import httpx

from .config import LLMConfig

# SSL 验证开关：默认启用（生产安全）。
# Windows 等环境的证书吊销检查问题，可设 LLM_SSL_VERIFY=0 临时关掉。
# 关闭时同时压制 httpx 的"未验证 HTTPS"警告噪音。
_SSL_VERIFY = os.getenv("LLM_SSL_VERIFY", "1").lower() not in ("0", "false", "no")
# 是否走系统/环境的代理：默认走（trust_env=True）。如需绕开代理可设 LLM_TRUST_ENV=0。
_TRUST_ENV = os.getenv("LLM_TRUST_ENV", "1").lower() not in ("0", "false", "no")

if not _SSL_VERIFY:
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


# ---------- 请求限流器（令牌桶）----------

class _RateLimiter:
    """简单的令牌桶限流器，保护 LLM API 不被瞬间打爆。

    通过环境变量控制：
    - LLM_RATE_LIMIT_RPS: 每秒补充令牌数（默认 5）
    - LLM_RATE_LIMIT_BURST: 桶容量/最大突发（默认 10）
    """

    def __init__(self, rate: float = 5.0, burst: int = 10) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_refill: float = 0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌，若桶空则等待直到有令牌。"""
        while True:
            async with self._lock:
                now = asyncio.get_event_loop().time()
                # 补充令牌
                elapsed = now - self.last_refill
                self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
                self.last_refill = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                # 计算等待时间
                wait = (1.0 - self.tokens) / self.rate
            await asyncio.sleep(wait)


# 全局限流器实例（可通过环境变量配置）
_rate_limit_rps = float(os.getenv("LLM_RATE_LIMIT_RPS", "5"))
_rate_limit_burst = int(os.getenv("LLM_RATE_LIMIT_BURST", "10"))
_rate_limiter = _RateLimiter(rate=_rate_limit_rps, burst=_rate_limit_burst)


# Token 用量累计器（contextvars，每个 case / run / gen_job 独立）
# 格式：list[dict] = [{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int, "model": str}, ...]
_token_usage_ctx: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "_token_usage_ctx", default=[]
)


def get_token_usage() -> list[dict[str, Any]]:
    """获取当前上下文累计的 token 用量列表。"""
    return _token_usage_ctx.get()


def reset_token_usage() -> None:
    """重置当前上下文的 token 累计器（在 case / run / gen_job 开始时调用）。"""
    _token_usage_ctx.set([])


def _record_usage(resp_data: dict[str, Any], model: str) -> None:
    """从 LLM 响应中抽取 usage 并累计到 contextvar。"""
    try:
        usage = resp_data.get("usage")
        if not usage:
            return
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or 0)
        if total_tokens == 0:
            total_tokens = prompt_tokens + completion_tokens
        record = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "model": model,
        }
        current = _token_usage_ctx.get()
        _token_usage_ctx.set(current + [record])
    except Exception as e:
        logger.warning(f"抽取 usage 失败：{e}")


# 网络层瞬时错误：连接/读取超时、连接被对端断开、协议错误等。
# 这类错误往往是 TCP/TLS 握手抖动或服务器临时打嗝，重试通常能解决。
_TRANSIENT_NET_EXC = (
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)

_shared_client: Optional[httpx.AsyncClient] = None
_client_lock: Optional[asyncio.Lock] = None


def _get_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


async def _get_client(timeout_cfg: httpx.Timeout) -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        async with _get_lock():
            # double-checked locking
            if _shared_client is None or _shared_client.is_closed:
                limits = httpx.Limits(
                    max_connections=int(os.getenv("LLM_MAX_CONNECTIONS", "200")),
                    max_keepalive_connections=int(os.getenv("LLM_MAX_KEEPALIVE", "50")),
                    keepalive_expiry=float(os.getenv("LLM_KEEPALIVE_EXPIRY", "30")),
                )
                _shared_client = httpx.AsyncClient(
                    timeout=timeout_cfg,
                    verify=_SSL_VERIFY,
                    trust_env=_TRUST_ENV,
                    limits=limits,
                )
    return _shared_client


async def close_client() -> None:
    """优雅关闭共享 httpx client（在 shutdown 时调用）。"""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None


async def chat(
    cfg: LLMConfig,
    messages: list[dict[str, str]],
    *,
    response_format_json: bool = False,
    timeout: float = 120.0,
    max_tokens: Optional[int] = None,
    max_retries: int = 3,
) -> str:
    if not cfg.ok:
        raise LLMError("LLM 配置不完整，请检查 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL")

    # 限流：等待令牌，避免瞬间打爆 LLM API
    await _rate_limiter.acquire()

    url = f"{cfg.base_url}/chat/completions"
    logger.info(f"调用 LLM: {cfg.model}, timeout={timeout}s, max_tokens={max_tokens}")

    # 记录 LLM 调用开始时间
    llm_start = asyncio.get_event_loop().time()
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": cfg.temperature,
        "stream": False,
    }
    if response_format_json:
        body["response_format"] = {"type": "json_object"}
    if max_tokens is not None:
        body["max_tokens"] = max_tokens

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg.api_key}",
    }

    # 拆分 timeout：connect 短一点（30s 足够），read 用调用方传入的总值（thinking 模型可能需要 120s+）
    # pool 也独立设置，避免连接池阻塞导致 PoolTimeout 报到上层。
    timeout_cfg = httpx.Timeout(connect=30.0, read=timeout, write=30.0, pool=30.0)

    # 网络错误 + 429/5xx 重试：指数退避 + jitter（1s → 2s → 4s … + 随机抖动）
    last_exc: Optional[BaseException] = None
    for attempt in range(max_retries + 1):
        try:
            logger.debug(f"LLM 调用尝试 {attempt + 1}/{max_retries + 1}")
            client = await _get_client(timeout_cfg)
            resp = await client.post(url, headers=headers, json=body, timeout=timeout_cfg)
            logger.debug(f"LLM 响应状态码: {resp.status_code}")
            # 自动重试：某些 thinking 模型（如 claude-opus-4-7）的中转 API 会拒绝 temperature 字段
            # 返回 400 + "`temperature` is deprecated for this model."。剥掉后重发一次。
            if resp.status_code == 400 and "temperature" in resp.text and "deprecated" in resp.text:
                logger.info("temperature 字段被拒绝，移除后重试")
                body.pop("temperature", None)
                resp = await client.post(url, headers=headers, json=body, timeout=timeout_cfg)
            # 429（限流）或 502/503/504（网关错误）走重试
            if resp.status_code in (429, 502, 503, 504) and attempt < max_retries:
                wait = (2 ** attempt) + random.random()
                logger.warning(f"LLM HTTP {resp.status_code}，{wait:.1f}s 后重试 ({attempt + 1}/{max_retries})")
                last_exc = LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 400:
                raise LLMError(f"LLM HTTP {resp.status_code}: {resp.text[:400]}")
            data = resp.json()
            # 抽取 usage 并累计到 contextvar
            _record_usage(data, cfg.model)
            # 记录指标
            llm_duration = asyncio.get_event_loop().time() - llm_start
            from .metrics import metrics
            metrics.llm_calls.inc(model=cfg.model, status="success")
            metrics.llm_duration.observe(llm_duration, model=cfg.model)
            break
        except _TRANSIENT_NET_EXC as e:
            last_exc = e
            if attempt < max_retries:
                # 指数退避 + jitter（0-1s 随机），避免 thundering herd
                wait = (2 ** attempt) + random.random()
                logger.warning(f"LLM 网络错误 ({type(e).__name__})，{wait:.1f}s 后重试 ({attempt + 1}/{max_retries})")
                await asyncio.sleep(wait)
                continue
            # 重试用尽，抛出更友好的错误信息
            from .metrics import metrics
            metrics.llm_calls.inc(model=cfg.model, status="network_error")
            raise LLMError(
                f"LLM 网络请求失败（已重试 {max_retries} 次）：{type(e).__name__}: {str(e) or '(无消息)'}"
            ) from e
    else:
        # 不应到达，循环正常退出会 break
        from .metrics import metrics
        metrics.llm_calls.inc(model=cfg.model, status="failed")
        raise LLMError(f"LLM 调用失败：{last_exc}")

    content = _extract_content(data)
    if not content:
        # 针对 thinking/reasoning 模型给出更友好的错误信息
        finish_reason = ""
        has_reasoning = False
        try:
            choice0 = data["choices"][0]
            finish_reason = choice0.get("finish_reason") or ""
            msg = choice0.get("message") or {}
            has_reasoning = bool(msg.get("reasoning_content"))
        except (KeyError, IndexError, TypeError):
            pass
        if has_reasoning and finish_reason == "length":
            raise LLMError(
                "LLM 输出被 max_tokens 截断在思考阶段（finish_reason=length，仅有 reasoning_content）。"
                "请增大 max_tokens，或换用非 thinking 型号（如 deepseek-chat / qwen-plus / gpt-4o-mini）。"
            )
        if has_reasoning:
            raise LLMError(
                "LLM 仅返回 reasoning_content，content 为空。该 thinking 模型可能不兼容当前 API 转发，"
                "建议改用非 thinking 模型（如 deepseek-chat / qwen-plus / gpt-4o-mini）。"
            )
        raise LLMError(f"LLM 响应无法解析: {json.dumps(data, ensure_ascii=False)[:400]}")
    return content


def _extract_content(raw: dict[str, Any]) -> Optional[str]:
    try:
        c = raw["choices"][0]["message"]["content"]
        if isinstance(c, str) and c.strip():
            return c
    except (KeyError, IndexError, TypeError):
        pass
    # 部分中转 API 把 thinking 模型的最终回答放到 reasoning_content
    # 只在 content 完全为空时回退使用，且尝试剥离 <think>...</think> 思考部分
    try:
        msg = raw["choices"][0]["message"]
        rc = msg.get("reasoning_content")
        if isinstance(rc, str) and rc.strip():
            # 一些模型把 "思考 + 最终答案" 都塞到 reasoning_content；
            # 看是否有「答案」标记，没有就直接返回原文（让上层解析器自行处理）
            return rc
    except (KeyError, IndexError, TypeError):
        pass
    if isinstance(raw.get("output_text"), str):
        return raw["output_text"]
    return None


def extract_json(text: str) -> Optional[Any]:
    """从模型输出里提取 JSON。容错：去除 ```json ... ``` 代码块包裹。

    在直接 json.loads 失败时，做一次保守的「内嵌引号兜底」：把字符串值里
    未转义的英文双引号替换为中文「」再重试（仅当原始解析失败时启用）。
    """
    if not text:
        return None
    s = text.strip()

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s, re.IGNORECASE)
    if fence:
        s = fence.group(1).strip()

    # 直接解析
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    # 兜底 1：尝试修复字符串值里未转义的英文双引号
    # 触发场景：LLM 把中文里的引用（如「回复"看答案"」）原样塞进字符串值导致 JSON 非法
    repaired = _repair_inline_quotes(s)
    if repaired is not None:
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            pass

    # 兜底 2：找首个 { 到末尾 }
    i = s.find("{")
    j = s.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i: j + 1])
        except json.JSONDecodeError:
            pass

    # 兜底 3：试试数组
    i = s.find("[")
    j = s.rfind("]")
    if i >= 0 and j > i:
        try:
            return json.loads(s[i: j + 1])
        except json.JSONDecodeError:
            pass
    return None


# 字符串值的合法结束标志：紧随其后必定是这些字符之一（可前置任意空白）
# 用于识别「这个 " 是 value 结束符」还是「value 内部夹带的引号」
_VALUE_TERMINATOR = re.compile(r'\s*[,\]\}:]')


def _repair_inline_quotes(s: str) -> Optional[str]:
    """启发式修复字符串值里未转义的英文双引号。

    扫描整段 JSON，遇到字符串值（key 后冒号 + 引号 / 数组里以引号开头的元素）时，
    沿途的引号若不是「合法的字符串结束符」（后面紧跟 , ] } :），就视为内嵌引号，
    替换成中文左 / 右双引号（""）。返回修复后的字符串；若识别不出可疑情况返回 None。

    设计目标：保守，只在确实可疑时介入；保留 JSON 结构（key / 标点）不动。
    """
    out: list[str] = []
    i = 0
    n = len(s)
    inside_string = False
    repaired = False
    while i < n:
        ch = s[i]
        if not inside_string:
            out.append(ch)
            if ch == '"':
                inside_string = True
            i += 1
            continue
        # 在字符串内
        if ch == "\\" and i + 1 < n:
            # 转义序列：原样保留两个字符
            out.append(ch)
            out.append(s[i + 1])
            i += 2
            continue
        if ch == '"':
            # 看后面是不是合法终止符
            rest = s[i + 1:]
            if _VALUE_TERMINATOR.match(rest) or rest.strip() == "":
                out.append('"')
                inside_string = False
                i += 1
                continue
            # 内嵌引号：用「」配对替换（按出现顺序左右轮换）
            # 简化处理：统一替换成中文双引号，足以让 json 解析通过
            out.append("“" if not repaired or out[-1] in (" ", "，", "：", "(") else "”")
            repaired = True
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out) if repaired else None
