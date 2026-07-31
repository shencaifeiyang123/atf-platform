"""结构化日志配置。

将日志输出格式化为 JSON（方便 ELK/Loki 等日志收集系统解析），
同时保留人类可读的控制台输出（开发环境）。

环境变量：
- LOG_FORMAT: "json" 或 "console"（默认 console）
- LOG_LEVEL: DEBUG/INFO/WARNING/ERROR（默认 INFO）
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class _JsonFormatter(logging.Formatter):
    """将日志记录格式化为 JSON 一行。"""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        # 异常信息
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # 额外字段
        if hasattr(record, "extra"):
            log_entry.update(record.extra)
        return json.dumps(log_entry, ensure_ascii=False)


class _ConsoleFormatter(logging.Formatter):
    """人类可读的控制台格式（开发环境）。"""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
        msg = f"{ts} [{record.levelname:<7}] {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[0] is not None:
            msg += "\n" + self.formatException(record.exc_info)
        return msg


def setup_logging() -> None:
    """配置全局日志格式。"""
    fmt = os.getenv("LOG_FORMAT", "console").lower()
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    if fmt == "json":
        formatter = _JsonFormatter()
    else:
        formatter = _ConsoleFormatter()

    # 配置根 logger
    root = logging.getLogger()
    root.setLevel(level)
    # 移除默认 handler
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    # 压制第三方库的噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
