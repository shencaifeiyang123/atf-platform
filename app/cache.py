"""简单的内存 TTL 缓存层。

用于缓存不频繁变化的数据（智能体分析结果、模板元数据等），
减少重复查询数据库或调用 LLM 的开销。

使用方式：
    from .cache import cache
    result = cache.get("agent_analysis:" + agent_id)
    if result is None:
        result = await analyze_agent(agent)
        cache.set("agent_analysis:" + agent_id, result, ttl=3600)
"""
from __future__ import annotations

import time
from typing import Any, Optional


class _TTLCache:
    """带过期时间的内存缓存。线程不安全（仅在 asyncio 事件循环中使用）。"""

    def __init__(self, max_size: int = 256) -> None:
        self._store: dict[str, tuple[Any, float]] = {}  # key -> (value, expire_at)
        self._max_size = max_size

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值。若已过期或不存在则返回 None，并自动清理。"""
        if key not in self._store:
            from .metrics import metrics
            metrics.cache_misses.inc(key=key[:50])
            return None
        value, expire_at = self._store[key]
        if time.monotonic() > expire_at:
            del self._store[key]
            from .metrics import metrics
            metrics.cache_misses.inc(key=key[:50])
            return None
        from .metrics import metrics
        metrics.cache_hits.inc(key=key[:50])
        return value

    def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """设置缓存值。ttl 单位为秒，默认 5 分钟。"""
        # LRU 淘汰：若超出容量，删除最旧的一个
        if len(self._store) >= self._max_size:
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest_key]
        self._store[key] = (value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        """主动删除某个缓存键。"""
        self._store.pop(key, None)

    def clear(self) -> None:
        """清空所有缓存。"""
        self._store.clear()

    def cleanup(self) -> int:
        """清理所有过期条目，返回清理数量。"""
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._store.items() if now > exp]
        for k in expired:
            del self._store[k]
        return len(expired)

    @property
    def size(self) -> int:
        return len(self._store)


# 全局缓存实例
cache = _TTLCache(max_size=256)
