"""单密码 + cookie session 鉴权（in-memory）。

设计要点：
- 密码来自 .env 的 ATF_PASSWORD；为空时鉴权关闭（dev 友好）
- session 存进程内 dict[token -> created_at_ms]，重启清空（强制重登一次）
- 7 天过期，无滑动续期、无「记住我」
- 单 IP 5 次错密锁 5 分钟（防暴力枚举）
- 模块只导出纯函数；middleware 在 main.py 里挂
"""
from __future__ import annotations

import hmac
import secrets
import time
from collections import deque
from typing import Deque

from .config import settings


# ---------- 常量 ----------

SESSION_TTL_MS = 7 * 24 * 3600 * 1000        # 7 天
COOKIE_NAME = "atf_sid"
COOKIE_MAX_AGE_S = 7 * 24 * 3600

# 限速：单 IP 在 RATE_WINDOW_MS 内累计 RATE_MAX_FAILS 次失败 → 锁 RATE_LOCK_MS
RATE_MAX_FAILS = 5
RATE_WINDOW_MS = 5 * 60 * 1000               # 5 分钟内的失败计数才有效
RATE_LOCK_MS = 5 * 60 * 1000                 # 锁 5 分钟


# ---------- 内存状态 ----------

# token -> created_at_ms
_sessions: dict[str, int] = {}
# ip -> 最近若干次失败的时间戳（旧的从左侧滚出）
_login_fails: dict[str, Deque[int]] = {}
# ip -> 锁定到期时间戳（绝对毫秒）
_login_lock_until: dict[str, int] = {}


# ---------- 工具 ----------

def _now_ms() -> int:
    return int(time.time() * 1000)


# ---------- 公开 API ----------

def is_enabled() -> bool:
    """密码非空 = 启用鉴权。"""
    return bool(settings.auth_password)


def verify_password(pw: str) -> bool:
    """常量时间比对，避免时序侧信道泄露密码长度/内容。"""
    if not is_enabled():
        return False
    expected = settings.auth_password
    return hmac.compare_digest(pw or "", expected)


def create_session() -> str:
    """生成新 token 并登记。"""
    token = secrets.token_urlsafe(32)
    _sessions[token] = _now_ms()
    return token


def revoke_session(token: str) -> None:
    _sessions.pop(token, None)


def is_valid(token: str | None) -> bool:
    """检查 token 是否存在且未过期；过期顺手清掉。"""
    if not token:
        return False
    created = _sessions.get(token)
    if created is None:
        return False
    if _now_ms() - created > SESSION_TTL_MS:
        _sessions.pop(token, None)
        return False
    return True


# ---------- 限速 ----------

def _gc_fails(ip: str, now: int) -> None:
    """剔除窗口外的失败记录。"""
    dq = _login_fails.get(ip)
    if not dq:
        return
    while dq and now - dq[0] > RATE_WINDOW_MS:
        dq.popleft()
    if not dq:
        _login_fails.pop(ip, None)


def is_locked(ip: str) -> tuple[bool, int]:
    """返回 (是否锁定, 剩余秒数)。"""
    now = _now_ms()
    until = _login_lock_until.get(ip)
    if until is None:
        return False, 0
    if now >= until:
        _login_lock_until.pop(ip, None)
        return False, 0
    return True, max(1, (until - now) // 1000)


def record_login_fail(ip: str) -> None:
    """记一次失败；累计达阈值则上锁。"""
    now = _now_ms()
    _gc_fails(ip, now)
    dq = _login_fails.setdefault(ip, deque())
    dq.append(now)
    if len(dq) >= RATE_MAX_FAILS:
        _login_lock_until[ip] = now + RATE_LOCK_MS
        # 上锁后清空计数，下个窗口重新开始
        _login_fails.pop(ip, None)


def clear_login_fails(ip: str) -> None:
    """登录成功时调用，清掉历史失败计数与锁。"""
    _login_fails.pop(ip, None)
    _login_lock_until.pop(ip, None)
