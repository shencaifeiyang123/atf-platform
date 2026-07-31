"""Prompt-Debugger 风格的测试用例生成器。

灵感来自 D:\\AI_TEST\\prompt-debugger-main 项目，提供与现有 generator.py 不同的生成路径：

- 用户用自然语言描述「测试要点」而非选择维度
- 按 P0-P6 优先级分级生成（P0=致命错误 / P1=核心流程 / ...）
- LLM 输出文本格式（用例X：【级别】标题 + 多轮 AI/用户 对话）
- 后端解析为结构化 TestCase 列表入库

与现有维度驱动方式互补：
- 维度方式：覆盖面广、自动按维度均衡，适合系统化全面测试
- PD 风格：聚焦特定测试要点、按优先级集中产出，适合定向回归
"""
from __future__ import annotations

import re
import math
from typing import Any

from .config import settings
from .llm import chat
from .models import AgentUnderTest, TestCase, TestTurn


# ---------- 级别配置 ----------

def _level_config(level: str, count: int) -> dict[str, str]:
    """根据级别返回对应描述与数量分布。逻辑与 prompt-debugger 一致。"""
    n = max(1, int(count))

    def _distribute(weights: list[float]) -> list[int]:
        """按权重分配 n 个名额，确保总和 == n。"""
        raw = [int(n * w) for w in weights]
        remainder = n - sum(raw)
        for i in range(remainder):
            raw[i] += 1
        return raw

    if level == "p1_p3":
        d = _distribute([0.5, 0.3, 0.2])
        counts_str = f"P1用例{d[0]}个，P2用例{d[1]}个，P3用例{d[2]}个"
    elif level == "p1_p5":
        d = _distribute([0.3, 0.25, 0.2, 0.15, 0.1])
        counts_str = f"P1:{d[0]}个，P2:{d[1]}个，P3:{d[2]}个，P4:{d[3]}个，P5:{d[4]}个"
    elif level == "all":
        d = _distribute([0.05, 0.25, 0.2, 0.2, 0.15, 0.1, 0.05])
        counts_str = (f"P0:{d[0]}个，P1:{d[1]}个，P2:{d[2]}个，P3:{d[3]}个，"
                      f"P4:{d[4]}个，P5:{d[5]}个，P6:{d[6]}个")
    elif level == "p1_p2":
        d = _distribute([0.6, 0.4])
        counts_str = f"P1用例{d[0]}个，P2用例{d[1]}个"
    elif level == "p0":
        counts_str = f"共{n}个P0用例"
    elif level == "p1":
        counts_str = f"共{n}个P1用例"
    else:
        d = _distribute([0.6, 0.4])
        counts_str = f"P1用例{d[0]}个，P2用例{d[1]}个"

    desc_map = {
        "p0": "P0(致命错误)用例",
        "p1": "P1(核心流程)用例",
        "p1_p2": "P1-P2(核心+重要)用例",
        "p1_p3": "P1-P3(核心+重要+常规)用例",
        "p1_p5": "P1-P5全覆盖用例",
        "all": "P0-P6完整覆盖用例",
    }
    return {"desc": desc_map.get(level, "P1-P2(核心+重要)用例"), "counts": counts_str}


def _opening_style_text(style: str) -> str:
    return {
        "ai_first": "AI先说话（每条用例第一句必须是 AI: 开头）",
        "user_first": "用户先说话（每条用例第一句必须是 用户: 开头）",
        "mixed": "混合（部分用例 AI 先开口，部分用户先开口）",
    }.get(style, "混合（部分用例 AI 先开口，部分用户先开口）")


# ---------- 提示词构造 ----------

def _format_analysis_block(analysis: dict[str, Any] | None) -> str:
    """把智能体分析结果格式化成可注入到 prompt 的中文摘要片段。

    若 analysis 为空 / 缺关键字段，返回空串，调用方负责跳过整段。
    """
    if not analysis or not isinstance(analysis, dict):
        return ""
    import json as _json

    def _short(obj: Any, limit: int = 1200) -> str:
        try:
            s = _json.dumps(obj, ensure_ascii=False)
        except Exception:
            s = str(obj)
        return s[:limit]

    core_value = str(analysis.get("core_value") or "").strip()
    capabilities = analysis.get("capabilities") or []
    boundaries = analysis.get("boundaries") or []
    user_profile = analysis.get("user_profile") or {}
    variables_usage = analysis.get("variables_usage") or []

    if not (core_value or capabilities or boundaries or user_profile or variables_usage):
        return ""

    lines: list[str] = ["**智能体分析摘要**（请结合此摘要设计用例，覆盖核心能力、规避能力边界、贴近用户画像）："]
    if core_value:
        lines.append(f"- 核心定位:{core_value}")
    if capabilities:
        lines.append(f"- 核心能力:{_short(capabilities)}")
    if boundaries:
        lines.append(f"- 能力边界:{_short(boundaries)}")
    if user_profile:
        lines.append(f"- 用户画像:{_short(user_profile)}")
    if variables_usage:
        lines.append(f"- 业务参数预期用途:{_short(variables_usage)}")
    return "\n".join(lines)


def build_pd_prompt(
    agent: AgentUnderTest,
    test_points: str,
    test_case_level: str,
    opening_style: str,
    generate_count: int,
    analysis: dict[str, Any] | None = None,
) -> str:
    """构造发给 LLM 的生成提示词。

    analysis 非空时会把核心定位 / 能力 / 边界 / 用户画像追加为「智能体分析摘要」段落。
    """
    cfg = _level_config(test_case_level, generate_count)
    final_points = (test_points or "").strip() or "基于提示词生成典型的对话场景，包括：积极场景、拒绝场景、常规场景等"

    # 控制 system_prompt 长度避免超限
    sp = (agent.system_prompt or "").strip()
    if len(sp) > 6000:
        sp = sp[:6000] + "\n...(后续内容已省略)"

    analysis_block = _format_analysis_block(analysis)
    analysis_section = f"\n{analysis_block}\n" if analysis_block else ""

    # 业务参数（variables）注入：让 LLM 直接看到调用时会传入的参数键值
    variables = getattr(agent, "variables", None) or {}
    variables_section = ""
    if variables and isinstance(variables, dict) and len(variables) > 0:
        import json as _json
        try:
            vars_text = _json.dumps(variables, ensure_ascii=False, indent=2)
        except Exception:
            vars_text = str(variables)
        variables_section = (
            "\n**业务参数（调用时会注入到智能体上下文，请围绕这些参数设计验证用例）**：\n"
            f"{vars_text}\n"
            "请在本批用例中安排至少 1-2 条用例，验证智能体是否正确使用了这些参数（如基于参数提供个性化回答、引用参数值等）。\n"
        )

    return f"""基于以下提示词和测试要点，生成{cfg['desc']}。

**系统提示词**：
{sp}
{analysis_section}{variables_section}
**测试要点**：
{final_points}

**严格要求**（每一条都必须遵守）：
1. 必须生成{cfg['counts']}，一条都不能少。
2. 用例格式（每条用例必须遵守）：
   用例X：【级别】标题
   AI:开场白
   用户:回应
   AI:回应
   用户:回应
   ...

3. 开场方式：{_opening_style_text(opening_style)}
4. 每条用例独立完整、覆盖不同场景。
5. 【重要】直接输出用例正文。**禁止**输出任何思考过程、规划、英文分析、Let me think、I'll generate 等前置说明；**禁止**用 ``` 代码块包裹；**禁止**在用例之间插入"---"或解释文字。
6. 第一条直接以「用例1：」开头；最后一条结尾就是该用例的最后一行对话，不要再写「以上是全部用例」之类的总结。

参考输出样例（仅展示格式，内容请按上方测试要点生成，不要照抄）：
用例1：【P1】用户首次问候
AI:你好，我是XX助手，有什么可以帮你？
用户:介绍一下你的功能
AI:我可以为你做……

用例2：【P1】用户提出常见需求
AI:请问需要哪方面的帮助？
用户:我想了解XX
AI:好的，关于XX……

现在请直接输出{cfg['counts']}的全部用例正文："""


# ---------- 文本解析 ----------

# 标题级别提取：【P0】xxx / 【P1】xxx
_LEVEL_RE = re.compile(r"【\s*([Pp][0-6])\s*】")


def parse_pd_text(text: str) -> list[dict[str, Any]]:
    """把 LLM 文本输出解析成结构化用例列表。

    输入示例：
        用例1：【P1】开场询问产品信息
        AI:你好，有什么可以帮你？
        用户:介绍一下你们的产品
        AI:我们提供 ...

    输出元素：
        {
          "title": "开场询问产品信息",
          "level": "P1",                     # 可能为空
          "opening_role": "ai" | "user",     # 第一条是谁
          "messages": [                      # role+content 的有序列表（可能 ai/user 都有）
              {"role": "assistant", "content": "你好..."},
              {"role": "user", "content": "介绍一下..."},
              ...
          ]
        }
    """
    if not text:
        return []

    # 跳过开头的思考过程：找到第一个「用例N：」前的内容直接丢弃
    first_match = re.search(r"^用例\s*\d+\s*[：:]", text, flags=re.MULTILINE)
    if first_match:
        text = text[first_match.start():]

    # 去掉可能存在的 ``` 代码块包裹
    text = re.sub(r"^```(?:\w+)?\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)

    # 用「用例N：」作为分隔（保留分隔符所在行）
    parts = re.split(r"(?=^用例\s*\d+\s*[：:])", text, flags=re.MULTILINE)
    cases: list[dict[str, Any]] = []
    for part in parts:
        part = part.strip()
        if not part or not re.match(r"^用例\s*\d+\s*[：:]", part):
            continue

        lines = [ln for ln in part.splitlines() if ln.strip()]
        if not lines:
            continue

        # 标题行
        title_line = re.sub(r"^用例\s*\d+\s*[：:]", "", lines[0]).strip()
        title_line = title_line.replace("**", "").strip()
        # 抽取级别
        m = _LEVEL_RE.search(title_line)
        level = m.group(1).upper() if m else ""
        title_clean = _LEVEL_RE.sub("", title_line).strip()
        title_clean = title_clean.lstrip("：:- ").strip() or "(未命名)"

        # 解析对话
        messages: list[dict[str, str]] = []
        for raw in lines[1:]:
            ln = raw.strip()
            # 兼容 "AI:" / "AI：" / "用户:" / "用户：" / "我:" / "我："
            mm = re.match(r"^(AI|Ai|ai|用户|我)\s*[：:]\s*(.*)$", ln)
            if not mm:
                # 续行：把内容附加到上一条
                if messages:
                    messages[-1]["content"] = (messages[-1]["content"] + "\n" + ln).strip()
                continue
            role_raw, content = mm.group(1), mm.group(2).strip()
            role = "assistant" if role_raw.lower() == "ai" else "user"
            if content:
                messages.append({"role": role, "content": content})

        if not messages:
            continue

        opening_role = "ai" if messages[0]["role"] == "assistant" else "user"
        cases.append({
            "title": title_clean,
            "level": level,
            "opening_role": opening_role,
            "messages": messages,
        })
    return cases


# ---------- 转换为 TestCase ----------

# P 级别 -> 权重映射（P0/P1 高权重，P5/P6 低权重）
_LEVEL_WEIGHT = {"P0": 5, "P1": 5, "P2": 4, "P3": 3, "P4": 2, "P5": 2, "P6": 1}


def to_test_cases(
    agent_id: str,
    parsed: list[dict[str, Any]],
    *,
    force_opening_mode: str = "",
    user_opening_text: str = "",
) -> list[TestCase]:
    """把解析结果转换成 TestCase 入库格式。

    维度统一打到 alignment（PD 风格不分维度，便于和现有维度统计融合）。
    sub_type 写入级别（P0/P1/...）方便筛选。
    AI 开场用例使用 opening_mode=ai；turns 仅保留 user 角色文本（runner 兼容设计）。

    - force_opening_mode: "user"/"ai"/"" — 强制覆盖每条用例的开场角色（来自 opening_style 配置）。
      传 ""（默认）时尊重 LLM 解析出来的 opening_role。
    - user_opening_text: 当强制 user 开场且填写此字段时，把每条用例的第一条 user 消息替换为该文本。
    """
    out: list[TestCase] = []
    for c in parsed:
        msgs = c.get("messages") or []
        if not msgs:
            continue

        # 强制覆盖优先于 LLM 解析结果：opening_style=user_first/ai_first 时统一规范
        if force_opening_mode in ("user", "ai"):
            opening = force_opening_mode
        else:
            opening = c.get("opening_role") or "user"
        # turns 只能放 user 消息（与现有 TestTurn 设计一致）
        # - 用户开场：保留所有 user 消息，按顺序排列
        # - AI 开场：跳过首条 AI，把后续的 user 消息按顺序保留
        user_msgs = [m["content"].strip() for m in msgs if m["role"] == "user" and m["content"].strip()]
        if opening == "ai" and not user_msgs:
            # AI 开场但完全没有用户回应：构造一条占位
            user_msgs = ["（请按你的设定主动开场）"]
        if opening == "user" and not user_msgs:
            continue

        # 显式开场文本处理（如 //init / /start 这类初始化指令）：
        # 语义：在每条用例最前面追加这条触发指令，先让智能体初始化，再走原本的对话剧本。
        # - user 开场：作为 turns[0] 由 runner 直接发出，原 user 消息变为 turns[1:]
        # - ai 开场：作为 turns[0] 触发词（runner 用它让 AI 主动开场），原 user 消息变为 turns[1:]
        # 两种模式都用 insert（而非 replace），避免 LLM 生成的剧本被吞掉只剩一句 //init。
        if user_opening_text and opening in ("user", "ai"):
            user_msgs.insert(0, user_opening_text)

        turns = [TestTurn(role="user", content=t) for t in user_msgs]

        # 期望行为：把全部对话拼接，便于人工查看与评估
        transcript = "\n".join(f"{'AI' if m['role']=='assistant' else '用户'}: {m['content']}" for m in msgs)
        expectation = (
            f"对照以下完整参考对话执行：\n{transcript}\n"
            "智能体的回复应在主题、立场、语气上与参考对话中的 AI 回复保持一致。"
        )

        level = c.get("level") or ""
        weight = _LEVEL_WEIGHT.get(level, 3)

        out.append(TestCase(
            agent_id=agent_id,
            dimension="alignment",                      # 统一归到 alignment
            sub_type=level or "PD",
            title=str(c.get("title") or "")[:200],
            opening_mode=("ai" if opening == "ai" else "user"),
            turns=turns,
            expectation=expectation,
            pass_criteria=[],                           # PD 风格不强制 pass_criteria
            weight=weight,
        ))
    return out


# ---------- 主入口 ----------

async def generate_pd_cases(
    agent: AgentUnderTest,
    test_points: str = "",
    test_case_level: str = "p1_p2",
    opening_style: str = "mixed",
    generate_count: int = 10,
    analysis: dict[str, Any] | None = None,
    user_opening_text: str = "",
) -> tuple[str, list[TestCase]]:
    """完整生成流程：构造 prompt → 调 LLM → 解析 → 转 TestCase。

    返回 (raw_text, cases)。raw_text 是 LLM 原始输出，方便前端展示与人工审阅。

    - analysis：可选的智能体分析结果（core_value / capabilities / boundaries / user_profile），
      非空时会作为「智能体分析摘要」注入到 prompt。
    
    **自动重试补足**：若首次生成数量不足，自动追加请求补齐（最多重试 2 次）。
    """
    prompt = build_pd_prompt(
        agent, test_points, test_case_level, opening_style, generate_count,
        analysis=analysis,
    )

    # 把 opening_style 映射成 to_test_cases 的强制开场角色
    # ai_first → 强制 ai；user_first → 强制 user；mixed → 不强制（""）
    force_opening = {"ai_first": "ai", "user_first": "user"}.get(opening_style, "")

    all_raw_texts: list[str] = []
    all_cases: list[TestCase] = []
    max_retries = 2

    # 按用例数动态分配 token 上限：单条经验值约 1500 tokens（含 emoji/HTML 排版的极端 case），
    # 给 thinking 模型再留 30% buffer；上限 32000 防止极端情况下打爆。
    # 同时 timeout 设为 300s，覆盖 thinking 模型 + 4sapi 中转排队的最坏情况。
    target_count = max(1, int(generate_count))
    dynamic_max_tokens = min(32000, max(8000, target_count * 2000))

    for attempt in range(max_retries + 1):
        text = await chat(
            settings.generator_llm,
            [
                {"role": "system", "content": "你是测试用例生成专家，生成高质量的对话测试用例。"},
                {"role": "user", "content": prompt},
            ],
            response_format_json=False,
            max_tokens=dynamic_max_tokens,
            timeout=300.0,
        )
        
        all_raw_texts.append(text)
        parsed = parse_pd_text(text)
        new_cases = to_test_cases(
            agent.id or "",
            parsed,
            force_opening_mode=force_opening,
            user_opening_text=user_opening_text,
        )
        all_cases.extend(new_cases)
        
        # 达到目标数量或已是最后一次尝试，结束
        if len(all_cases) >= generate_count or attempt >= max_retries:
            break
        
        # 数量不足，构造补足 prompt
        shortage = generate_count - len(all_cases)
        prompt = f"""刚才已生成 {len(all_cases)} 条用例，还需要 {shortage} 条。

请继续生成剩余的 {shortage} 条用例，编号从「用例{len(all_cases) + 1}：」开始。

**要求**：
1. 格式与之前一致：用例X：【级别】标题 + AI:/用户: 对话
2. 直接输出用例正文，不要思考过程、不要代码块包裹
3. 覆盖不同场景，与已生成的用例不重复

现在请输出剩余 {shortage} 条用例："""
    
    # 合并所有 raw_text（用分隔符标注多次调用）
    combined_raw = "\n\n".join(f"=== 第 {i+1} 次生成 ===\n{t}" for i, t in enumerate(all_raw_texts)) if len(all_raw_texts) > 1 else all_raw_texts[0]
    return combined_raw, all_cases[:generate_count]  # 截取到目标数量
