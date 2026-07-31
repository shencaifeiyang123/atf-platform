"""轻量级 Prometheus 风格指标收集器（无外部依赖）。

提供内存中的计数器、直方图和 Gauge，通过 /api/metrics 端点
暴露 Prometheus 文本格式，方便 Grafana 等监控系统采集。

指标列表：
- platform_http_requests_total: HTTP 请求总数（带 method/path/status 标签）
- platform_llm_calls_total: LLM 调用总数（带 model/status 标签）
- platform_llm_call_duration_seconds: LLM 调用耗时直方图
- platform_cache_hits_total: 缓存命中数
- platform_cache_misses_total: 缓存未命中数
- platform_active_runs: 当前活跃任务数
- platform_db_wal_size_bytes: WAL 文件大小
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any


class _Counter:
    def __init__(self) -> None:
        self._values: dict[tuple[str, ...], float] = defaultdict(float)

    def inc(self, value: float = 1.0, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        self._values[key] += value

    def render(self, name: str, help_text: str) -> str:
        lines = [f"# HELP {name} {help_text}", f"# TYPE {name} counter"]
        for labels, value in self._values.items():
            label_str = ",".join(f'{k}="{v}"' for k, v in labels)
            lines.append(f"{name}{{{label_str}}} {value}")
        return "\n".join(lines)


class _Histogram:
    def __init__(self, buckets: list[float] = None) -> None:
        self._buckets = buckets or [0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0]
        self._sum: float = 0.0
        self._count: int = 0
        self._bucket_counts: dict[tuple[str, ...], list[int]] = defaultdict(lambda: [0] * len(self._buckets))

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(sorted(labels.items()))
        self._sum += value
        self._count += 1
        buckets = self._bucket_counts[key]
        for i, threshold in enumerate(self._buckets):
            if value <= threshold:
                for j in range(i, len(buckets)):
                    buckets[j] += 1
                break
        else:
            for i in range(len(buckets)):
                buckets[i] += 1

    def render(self, name: str, help_text: str) -> str:
        lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
        # 合并所有标签的桶计数
        all_labels: dict[str, str] = {}
        for labels in self._bucket_counts:
            for k, v in labels:
                all_labels[k] = v
        for i, threshold in enumerate(self._buckets):
            label_str = ",".join(f'{k}="{v}"' for k, v in sorted(all_labels.items()))
            label_str += f',le="{threshold}"' if label_str else f'le="{threshold}"'
            total = sum(buckets[i] for buckets in self._bucket_counts.values())
            lines.append(f"{name}_bucket{{{label_str}}} {total}")
        lines.append(f"{name}_sum {self._sum:.3f}")
        lines.append(f"{name}_count {self._count}")
        return "\n".join(lines)


class _Gauge:
    def __init__(self) -> None:
        self._values: dict[str, float] = {}

    def set(self, value: float, name: str) -> None:
        self._values[name] = value

    def inc(self, name: str, value: float = 1.0) -> None:
        self._values[name] = self._values.get(name, 0) + value

    def dec(self, name: str, value: float = 1.0) -> None:
        self._values[name] = self._values.get(name, 0) - value

    def render(self, help_text: str = "") -> str:
        lines = []
        if help_text:
            lines.append(f"# HELP platform_gauge {help_text}")
            lines.append("# TYPE platform_gauge gauge")
        for name, value in self._values.items():
            lines.append(f"platform_gauge{{name=\"{name}\"}} {value}")
        return "\n".join(lines)


class _MetricsRegistry:
    """全局指标注册表。"""

    def __init__(self) -> None:
        self.http_requests = _Counter()
        self.llm_calls = _Counter()
        self.llm_duration = _Histogram()
        self.cache_hits = _Counter()
        self.cache_misses = _Counter()
        self.gauges = _Gauge()

    def render_all(self) -> str:
        """输出 Prometheus 文本格式。"""
        parts = [
            self.http_requests.render("platform_http_requests_total", "HTTP 请求总数"),
            self.llm_calls.render("platform_llm_calls_total", "LLM 调用总数"),
            self.llm_duration.render("platform_llm_call_duration_seconds", "LLM 调用耗时"),
            self.cache_hits.render("platform_cache_hits_total", "缓存命中数"),
            self.cache_misses.render("platform_cache_misses_total", "缓存未命中数"),
            self.gauges.render("平台运行指标"),
        ]
        return "\n\n".join(p for p in parts if p) + "\n"


# 全局单例
metrics = _MetricsRegistry()
