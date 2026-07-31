"""鉴权相关路由：/api/auth/*。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import auth

router = APIRouter()


class LoginReq(BaseModel):
    password: str


def _client_ip(request: Request) -> str:
    """取客户端 IP，优先看 X-Forwarded-For（反代场景）。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_https(request: Request) -> bool:
    """判断当前请求是否经 HTTPS（同时考虑反代 X-Forwarded-Proto）。"""
    if request.url.scheme == "https":
        return True
    return request.headers.get("x-forwarded-proto", "").lower() == "https"


@router.get("/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    """返回 {enabled, authenticated}，前端据此决定是否弹登录蒙层。"""
    if not auth.is_enabled():
        return {"enabled": False, "authenticated": False}
    token = request.cookies.get(auth.COOKIE_NAME)
    return {"enabled": True, "authenticated": auth.is_valid(token)}


@router.post("/auth/login")
async def auth_login(req: LoginReq, request: Request) -> JSONResponse:
    """密码正确则下发 atf_sid cookie。"""
    if not auth.is_enabled():
        # 鉴权关闭时直接返回成功，避免前端误以为需要登录
        return JSONResponse({"ok": True, "enabled": False})
    ip = _client_ip(request)
    locked, retry_after_s = auth.is_locked(ip)
    if locked:
        return JSONResponse(
            {"detail": f"登录失败次数过多，请在 {retry_after_s} 秒后重试"},
            status_code=429,
        )
    if not auth.verify_password(req.password):
        auth.record_login_fail(ip)
        return JSONResponse({"detail": "密码错误"}, status_code=401)
    auth.clear_login_fails(ip)
    token = auth.create_session()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        max_age=auth.COOKIE_MAX_AGE_S,
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
        path="/",
    )
    return resp


@router.post("/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    token = request.cookies.get(auth.COOKIE_NAME)
    if token:
        auth.revoke_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp
