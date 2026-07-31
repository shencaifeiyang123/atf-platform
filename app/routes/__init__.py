"""路由聚合模块：将所有子路由注册到 FastAPI 应用。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

from . import agents, cases, runs, schedules, auth, system


def include_all_routers(app: "FastAPI") -> None:
    """将所有路由模块注册到 app，统一使用 /api 前缀。"""
    app.include_router(auth.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(cases.router, prefix="/api")
    app.include_router(runs.router, prefix="/api")
    app.include_router(schedules.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
