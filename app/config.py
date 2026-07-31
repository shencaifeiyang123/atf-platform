"""运行时配置：从环境变量加载 LLM / 存储 / 并发参数。

支持运行时覆盖：data/runtime_config.json 若存在，会覆盖 .env 的 LLM 配置，
便于在前端「配置」弹窗中动态修改 base_url / api_key / 模型，不必改 .env。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 加载 .env（若存在）
load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float = 0.7

    @property
    def ok(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass
class JudgeEnsembleConfig:
    """G-Eval + 多 Judge 集成评审配置。

    - n: 同一条用例的 judge 调用次数（>=1）。N=1 等同旧行为；推荐奇数（3 / 5）方便投票
    - temperature_var: 在 base temperature 上做 ±var 抖动，N>1 时让多次调用产出有差异，
      避免「自我重复」导致虚假一致
    - use_geval: True 时使用 G-Eval CoT 提示词（先推理再打分），更稳定但多消耗 tokens
    - pass_threshold: 通过阈值（1.0-5.0），综合评分 >= 此值才判定通过。默认 3.5
    - strict_mode: 严格模式，True 时要求规则层和 LLM 层都通过；False 时只要 LLM 层通过即可
    """
    n: int = 3
    temperature_var: float = 0.2
    use_geval: bool = True
    pass_threshold: float = 3.5
    strict_mode: bool = True


@dataclass
class Settings:
    data_dir: Path
    port: int
    max_concurrency: int
    generator_llm: LLMConfig
    judge_llm: LLMConfig
    judge_ensemble: JudgeEnsembleConfig
    # 模型单价表：key=模型名，value={"input_price_per_1m": float, "output_price_per_1m": float}
    # 单位：美元 / 百万 token（与主流 API 定价对齐）
    model_prices: dict[str, dict[str, float]]
    # 单密码鉴权：空字符串 = 鉴权关闭（dev 模式）；非空 = 启用
    # 故意不进 runtime_config（密码不应通过 API 改），只能改 .env
    auth_password: str


_RUNTIME_CONFIG_FILE: Path  # 在 _load 中赋值


def _safe_float(key: str, default: str) -> float:
    val = os.getenv(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        print(f"[config] 警告: 环境变量 {key}={val!r} 不是有效数字，使用默认值 {default}")
        return float(default)


def _safe_int(key: str, default: str) -> int:
    val = os.getenv(key, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        print(f"[config] 警告: 环境变量 {key}={val!r} 不是有效整数，使用默认值 {default}")
        return int(default)


def _load() -> Settings:
    global _RUNTIME_CONFIG_FILE
    data_dir = Path(os.getenv("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    _RUNTIME_CONFIG_FILE = data_dir / "runtime_config.json"

    gen = LLMConfig(
        base_url=os.getenv("LLM_BASE_URL", "").rstrip("/"),
        api_key=os.getenv("LLM_API_KEY", ""),
        model=os.getenv("LLM_MODEL", ""),
        temperature=_safe_float("LLM_TEMPERATURE", "0.7"),
    )
    judge = LLMConfig(
        base_url=os.getenv("JUDGE_BASE_URL", "").rstrip("/") or gen.base_url,
        api_key=os.getenv("JUDGE_API_KEY", "") or gen.api_key,
        model=os.getenv("JUDGE_MODEL", "") or gen.model,
        temperature=_safe_float("JUDGE_TEMPERATURE", "0.2"),
    )
    ensemble = JudgeEnsembleConfig(
        n=_safe_int("JUDGE_ENSEMBLE_N", "3"),
        temperature_var=_safe_float("JUDGE_TEMPERATURE_VAR", "0.2"),
        use_geval=os.getenv("JUDGE_USE_GEVAL", "true").lower() in ("1", "true", "yes"),
        pass_threshold=_safe_float("JUDGE_PASS_THRESHOLD", "3.5"),
        strict_mode=os.getenv("JUDGE_STRICT_MODE", "true").lower() in ("1", "true", "yes"),
    )
    # 默认模型单价表（主流模型参考价，单位：美元/百万 token）
    default_prices = {
        "qwen-plus": {"input_price_per_1m": 0.8, "output_price_per_1m": 2.4},
        "qwen-turbo": {"input_price_per_1m": 0.3, "output_price_per_1m": 0.6},
        "qwen-max": {"input_price_per_1m": 40.0, "output_price_per_1m": 120.0},
        "deepseek-chat": {"input_price_per_1m": 0.14, "output_price_per_1m": 0.28},
        "gpt-4o": {"input_price_per_1m": 2.5, "output_price_per_1m": 10.0},
        "gpt-4o-mini": {"input_price_per_1m": 0.15, "output_price_per_1m": 0.6},
        "claude-3-5-sonnet-20241022": {"input_price_per_1m": 3.0, "output_price_per_1m": 15.0},
        "claude-3-5-haiku-20241022": {"input_price_per_1m": 0.8, "output_price_per_1m": 4.0},
    }
    s = Settings(
        data_dir=data_dir,
        port=_safe_int("PORT", "8000"),
        max_concurrency=_safe_int("MAX_CONCURRENCY", "5"),
        generator_llm=gen,
        judge_llm=judge,
        judge_ensemble=ensemble,
        model_prices=default_prices,
        auth_password=os.getenv("ATF_PASSWORD", "").strip(),
    )
    # 应用运行时覆盖
    _apply_runtime_overrides(s)
    return s


def _apply_runtime_overrides(s: Settings) -> None:
    """从 data/runtime_config.json 读取，覆盖 LLM 配置 + 模型单价表。"""
    if not _RUNTIME_CONFIG_FILE.exists():
        return
    try:
        data = json.loads(_RUNTIME_CONFIG_FILE.read_text("utf-8"))
    except Exception:
        logger.warning("runtime_config.json 解析失败，已忽略", exc_info=True)
        return
    g = data.get("generator_llm") or {}
    j = data.get("judge_llm") or {}
    if g:
        if g.get("base_url"):
            s.generator_llm.base_url = g["base_url"].rstrip("/")
        if g.get("api_key"):
            s.generator_llm.api_key = g["api_key"]
        if g.get("model"):
            s.generator_llm.model = g["model"]
        if g.get("temperature") is not None:
            try:
                s.generator_llm.temperature = float(g["temperature"])
            except (TypeError, ValueError):
                pass
    if j:
        if j.get("base_url"):
            s.judge_llm.base_url = j["base_url"].rstrip("/")
        if j.get("api_key"):
            s.judge_llm.api_key = j["api_key"]
        if j.get("model"):
            s.judge_llm.model = j["model"]
        if j.get("temperature") is not None:
            try:
                s.judge_llm.temperature = float(j["temperature"])
            except (TypeError, ValueError):
                pass
    # 模型单价表覆盖
    if "model_prices" in data and isinstance(data["model_prices"], dict):
        s.model_prices = data["model_prices"]


def get_runtime_config() -> dict[str, Any]:
    """返回当前生效的 LLM 配置（不暴露完整 api_key，只返回掩码）+ 模型单价表。"""
    def _mask(k: str) -> str:
        if not k:
            return ""
        if len(k) <= 8:
            return "*" * len(k)
        return k[:4] + "*" * (len(k) - 8) + k[-4:]

    return {
        "generator_llm": {
            "base_url": settings.generator_llm.base_url,
            "api_key_masked": _mask(settings.generator_llm.api_key),
            "api_key_set": bool(settings.generator_llm.api_key),
            "model": settings.generator_llm.model,
            "temperature": settings.generator_llm.temperature,
        },
        "judge_llm": {
            "base_url": settings.judge_llm.base_url,
            "api_key_masked": _mask(settings.judge_llm.api_key),
            "api_key_set": bool(settings.judge_llm.api_key),
            "model": settings.judge_llm.model,
            "temperature": settings.judge_llm.temperature,
        },
        "max_concurrency": settings.max_concurrency,
        "model_prices": settings.model_prices,
    }


def save_runtime_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存运行时配置到 data/runtime_config.json，立即生效（in-place 修改 settings）。

    payload 形如：
    {
      "generator_llm": {"base_url": "...", "api_key": "...", "model": "...", "temperature": 0.7},
      "judge_llm":     {"base_url": "...", "api_key": "...", "model": "...", "temperature": 0.2},
      "model_prices":  {"qwen-plus": {"input_price_per_1m": 0.8, "output_price_per_1m": 2.4}, ...}
    }

    api_key 为空字符串时不修改既有值；不传字段同样不修改。
    """
    # 读旧的 runtime_config，做合并（避免清空 api_key）
    old: dict[str, Any] = {}
    if _RUNTIME_CONFIG_FILE.exists():
        try:
            old = json.loads(_RUNTIME_CONFIG_FILE.read_text("utf-8"))
        except Exception:
            old = {}

    merged: dict[str, Any] = {
        "generator_llm": dict(old.get("generator_llm") or {}),
        "judge_llm": dict(old.get("judge_llm") or {}),
        "model_prices": dict(old.get("model_prices") or {}),
    }

    for section in ("generator_llm", "judge_llm"):
        new_section = payload.get(section) or {}
        for key in ("base_url", "model", "temperature"):
            if key in new_section and new_section[key] not in (None, ""):
                merged[section][key] = new_section[key]
        # api_key：仅当传入非空时才更新
        if "api_key" in new_section and new_section["api_key"]:
            merged[section]["api_key"] = new_section["api_key"]

    # 模型单价表：完整替换（前端会发送完整表）
    if "model_prices" in payload and isinstance(payload["model_prices"], dict):
        merged["model_prices"] = payload["model_prices"]

    _RUNTIME_CONFIG_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 立即应用到全局 settings
    _apply_runtime_overrides(settings)
    return get_runtime_config()


def reset_runtime_config() -> dict[str, Any]:
    """删除运行时覆盖文件，回退到 .env 配置（需要重启进程才能完全恢复 .env 值，
    但本函数会清空 in-memory 中由运行时写入的字段——
    最简单做法是直接重新加载 .env 后再覆盖一次）。"""
    if _RUNTIME_CONFIG_FILE.exists():
        _RUNTIME_CONFIG_FILE.unlink()
    # 重新从 env 构建一份再赋值
    fresh = _load()
    settings.generator_llm = fresh.generator_llm
    settings.judge_llm = fresh.judge_llm
    settings.max_concurrency = fresh.max_concurrency
    return get_runtime_config()


settings = _load()
