"""测试标准一致性检查器。

根据失败用例分析报告，添加对 pass_criteria 的自动验证，
检测标准中的矛盾、模糊性和不明确性。
"""
from __future__ import annotations

import re
from typing import Any

from .models import TestCase


class CriteriaIssue:
    """标准问题描述"""
    def __init__(
        self,
        severity: str,  # "error" | "warning" | "info"
        message: str,
        criterion: str = "",
        suggestion: str = "",
    ):
        self.severity = severity
        self.message = message
        self.criterion = criterion
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "criterion": self.criterion,
            "suggestion": self.suggestion,
        }


def validate_criteria(case: TestCase) -> list[CriteriaIssue]:
    """验证测试用例的通过标准是否明确、一致、可执行。

    检查项：
    1. 标准自相矛盾（同一事物既要求又禁止）
    2. 标准过于模糊（"合理""适当""较好"等主观词）
    3. 计分公式未明确定义
    4. 举例与要求不一致
    5. 标准为空或过少
    6. 用例不可达（标准要求 AI 感知系统级异常，但用户输入侧未提供该上下文）
    """
    issues: list[CriteriaIssue] = []

    if not case.pass_criteria:
        issues.append(CriteriaIssue(
            severity="warning",
            message="通过标准为空，评判将完全依赖 LLM 主观判断",
            suggestion="建议至少添加 2-3 条明确的验证标准",
        ))
        return issues

    if len(case.pass_criteria) < 2:
        issues.append(CriteriaIssue(
            severity="info",
            message=f"通过标准较少（{len(case.pass_criteria)} 条），可能不够全面",
            suggestion="建议添加更多具体标准以提高评判准确性",
        ))

    # 检查每条标准
    for idx, criterion in enumerate(case.pass_criteria, 1):
        c = criterion.strip()
        if not c:
            continue

        # 1. 检查模糊词汇
        vague_words = ["合理", "适当", "较好", "比较", "相对", "一定程度", "基本", "大概", "可能"]
        found_vague = [w for w in vague_words if w in c]
        if found_vague:
            issues.append(CriteriaIssue(
                severity="warning",
                message=f"标准 #{idx} 包含模糊词汇：{', '.join(found_vague)}",
                criterion=c,
                suggestion="建议用具体的数值、关键词或行为描述替代主观评价",
            ))

        # 2. 检查是否包含"例如"但未明确要求
        if "例如" in c or "如" in c:
            # 检查是否有明确的量化要求
            if not any(kw in c for kw in ["必须", "应", "至少", "不超过", "包含", "不包含"]):
                issues.append(CriteriaIssue(
                    severity="warning",
                    message=f"标准 #{idx} 仅举例但未明确要求",
                    criterion=c,
                    suggestion="建议明确说明：举例是'必须满足其一'还是'仅供参考'",
                ))

    # 3. 检查标准间的矛盾
    issues.extend(_check_contradictions(case.pass_criteria))

    # 4. 检查计分相关标准的明确性
    if any(kw in case.expectation.lower() or any(kw in c.lower() for c in case.pass_criteria)
           for kw in ["计分", "积分", "得分", "分数"]):
        issues.extend(_check_scoring_clarity(case))

    # 5. 检查难度相关标准的一致性
    if any(kw in case.expectation.lower() or any(kw in c.lower() for c in case.pass_criteria)
           for kw in ["难度", "简单", "复杂", "容易", "困难"]):
        issues.extend(_check_difficulty_consistency(case))

    # 6. 检查用例是否可达：标准要求 AI 感知系统级异常状态，
    #    但用户输入侧（turns / user_persona / user_goal / expectation）都没提供该上下文
    issues.extend(_check_unreachable_state(case))

    return issues


def _check_contradictions(criteria: list[str]) -> list[CriteriaIssue]:
    """检查标准间的矛盾。"""
    issues: list[CriteriaIssue] = []

    # 提取"包含"和"不包含"的内容
    includes: list[tuple[int, str, str]] = []  # (index, criterion, target)
    excludes: list[tuple[int, str, str]] = []

    include_patterns = [
        r'(?:必须|应)?包含\s*[：:]?\s*[""\'"]?(.+?)[""\'"]?(?:$|，|。)',
    ]
    exclude_patterns = [
        r'不(?:应|应该|得|要|能)?包含\s*[：:]?\s*[""\'"]?(.+?)[""\'"]?(?:$|，|。)',
    ]

    for idx, c in enumerate(criteria, 1):
        for pat in include_patterns:
            m = re.search(pat, c)
            if m:
                includes.append((idx, c, m.group(1).strip()))

        for pat in exclude_patterns:
            m = re.search(pat, c)
            if m:
                excludes.append((idx, c, m.group(1).strip()))

    # 检查是否有相同内容既要包含又要排除
    # 注意：只检查不同索引的标准之间的矛盾
    for inc_idx, inc_c, inc_target in includes:
        for exc_idx, exc_c, exc_target in excludes:
            # 跳过同一条标准（不可能既包含又排除）
            if inc_idx == exc_idx:
                continue
            if inc_target == exc_target or inc_target in exc_target or exc_target in inc_target:
                issues.append(CriteriaIssue(
                    severity="error",
                    message=f"标准 #{inc_idx} 和 #{exc_idx} 存在矛盾",
                    criterion=f"#{inc_idx}: {inc_c}\n#{exc_idx}: {exc_c}",
                    suggestion=f"'{inc_target}' 和 '{exc_target}' 不能同时要求包含和排除",
                ))

    return issues


def _check_scoring_clarity(case: TestCase) -> list[CriteriaIssue]:
    """检查计分标准的明确性。"""
    issues: list[CriteriaIssue] = []

    # 检查是否有明确的计分公式
    has_formula = False
    for c in case.pass_criteria:
        # 查找数学公式：包含 +, -, ×, *, =, 括号等
        if re.search(r'[+\-×*=()]\s*\d+|第\d+题.*\d+分', c):
            has_formula = True
            break

    if not has_formula:
        # 检查是否提到计分但没有公式
        scoring_mentioned = any(
            kw in c.lower() for c in case.pass_criteria
            for kw in ["计分", "积分", "得分", "分数", "连胜"]
        )
        if scoring_mentioned:
            issues.append(CriteriaIssue(
                severity="warning",
                message="提到计分但未明确定义计算公式",
                suggestion="建议添加明确的数学公式，如：'第N题得分 = (前累计+1) × 连胜数'",
            ))

    return issues


def _check_difficulty_consistency(case: TestCase) -> list[CriteriaIssue]:
    """检查难度相关标准的一致性。"""
    issues: list[CriteriaIssue] = []

    # 提取所有提到的具体例子
    examples: list[str] = []
    for c in case.pass_criteria + [case.expectation]:
        # 匹配引号内的内容或"如XXX"
        # 使用简单的引号匹配
        matches = re.findall(r'["\']([^"\']+)["\']', c)
        examples.extend([m.strip() for m in matches if m.strip()])

        # 匹配"如XXX"模式
        matches2 = re.findall(r'如[「『]?([^」』，。、]+)[」』]?', c)
        examples.extend([m.strip() for m in matches2 if m.strip()])

    # 检查是否在标准中既说某个例子简单，又在失败原因中可能说它复杂
    # 这需要结合 expectation 和 pass_criteria 一起看
    simple_keywords = ["简单", "容易", "常见", "基础"]
    complex_keywords = ["复杂", "困难", "高级", "深奥"]

    for example in examples:
        simple_mentioned = any(
            example in c and any(kw in c for kw in simple_keywords)
            for c in case.pass_criteria + [case.expectation]
        )
        complex_mentioned = any(
            example in c and any(kw in c for kw in complex_keywords)
            for c in case.pass_criteria + [case.expectation]
        )

        if simple_mentioned and complex_mentioned:
            issues.append(CriteriaIssue(
                severity="error",
                message=f"'{example}' 在标准中既被描述为简单又被描述为复杂",
                suggestion="请明确该示例的难度定位，避免自相矛盾",
            ))

    return issues


def _check_unreachable_state(case: TestCase) -> list[CriteriaIssue]:
    """检测"用例不可达"：标准要求 AI 感知某种系统级异常状态，
    但用户输入侧未在消息流中体现该状态，导致 AI 永远无从察觉。

    典型反例（来自 run_17b84225355f #4）：
        pass_criteria: ["AI明确提示数据缺失", ...]
        user_goal:     "想正常参与活动，但系统数据有问题"   ← 只在测试者视角声明
        turns / 首条用户消息: "老师好，我准备好了！"        ← AI 看不到任何异常信号

    判定逻辑：
      1. pass_criteria 中存在"AI 应检测/识别/提示某种异常状态"的措辞
      2. 但被测 AI 实际能看到的内容（首条 user 消息 / turns 的 user content）
         里没有任何与该异常相关的关键词
      → 报 warning（system_prompt 仍可能注入异常上下文，所以不报 error）
    """
    issues: list[CriteriaIssue] = []

    # 1. 在 pass_criteria 里查找"AI 感知系统异常"的措辞
    #    用 (动作) + (对象关键词) 的组合，避免误伤"AI不应包含敏感词"这种正常规则
    state_action_kw = ["检测到", "识别到", "发现", "察觉", "提示", "告知", "说明", "提醒"]
    state_object_kw = [
        "缺失", "为空", "异常", "未配置", "不完备", "不完整", "无效",
        "失败", "错误", "故障", "超时", "不可用", "未就绪",
    ]

    triggered: list[tuple[int, str, str]] = []  # (idx, criterion, matched_object_kw)
    for idx, c in enumerate(case.pass_criteria, 1):
        for obj in state_object_kw:
            if obj not in c:
                continue
            # 仅在同条标准里同时出现"动作 + 对象"才算
            if not any(act in c for act in state_action_kw):
                continue
            triggered.append((idx, c, obj))
            break  # 每条标准命中一次即可

    if not triggered:
        return issues

    # 2. 收集 AI 端能看到的用户输入文本
    #    - scripted: turns 里的 user content
    #    - dynamic:  user_simulator 在首轮通常会照搬/改写 user_goal，但 goal 文本本身
    #                只给模拟器看，不会原样发给 AI；这里只信 turns 里的实际消息
    visible_to_ai = " ".join(
        (t.content or "") for t in case.turns if (t.content or "").strip()
    )

    # 3. 对每条触发的标准，看用户消息里是否提到该异常关键词
    for idx, c, matched_obj in triggered:
        if matched_obj in visible_to_ai:
            continue  # 用户消息里直接提到了，AI 能看到，可达
        # 也宽松匹配一下别名：缺失/为空/未配置 都属于"缺数据"语义
        loose_aliases = {
            "缺失": ["缺失", "没有", "找不到", "为空", "空的"],
            "为空": ["为空", "空的", "缺失", "没有"],
            "异常": ["异常", "出错", "报错", "不对劲"],
            "未配置": ["未配置", "没配", "缺失"],
            "不完备": ["不完备", "不完整", "缺失"],
            "不完整": ["不完整", "不完备", "缺失"],
            "无效": ["无效", "失效", "不对"],
            "失败": ["失败", "出错"],
            "错误": ["错误", "报错", "出错"],
            "故障": ["故障", "坏了", "不能用"],
            "超时": ["超时", "卡住"],
            "不可用": ["不可用", "用不了", "坏了"],
            "未就绪": ["未就绪", "没准备", "还没好"],
        }
        aliases = loose_aliases.get(matched_obj, [matched_obj])
        if any(a in visible_to_ai for a in aliases):
            continue

        # 给 dynamic 模式更明确的修复建议
        if case.dialogue_mode == "dynamic":
            suggestion = (
                f"该用例要求 AI 感知\"{matched_obj}\"状态，但 turns 中无任何用户消息会暴露该状态。"
                "请在 turns 中加一条首发用户消息显式描述异常（如\"老师，刚才系统提示数据加载失败\"），"
                "或确认被测智能体的 system_prompt / 上下文注入机制会把异常信号传给它，否则 AI 无从触发该分支。"
            )
        else:
            suggestion = (
                f"该用例要求 AI 感知\"{matched_obj}\"状态，但 turns 中的用户消息未提及该异常。"
                "请补充一条用户消息显式描述异常情境，或确认被测智能体能从 system_prompt / 工具调用结果中感知该状态。"
            )

        issues.append(CriteriaIssue(
            severity="warning",
            message=f"标准 #{idx} 可能不可达：要求 AI 感知\"{matched_obj}\"，但用户消息未提及该状态",
            criterion=c,
            suggestion=suggestion,
        ))

    return issues


def format_validation_report(issues: list[CriteriaIssue]) -> str:
    """格式化验证报告为可读文本。"""
    if not issues:
        return "✅ 通过标准验证通过，未发现明显问题"

    lines = ["⚠️ 通过标准验证发现以下问题：\n"]

    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    if errors:
        lines.append("🔴 错误（必须修复）：")
        for i, issue in enumerate(errors, 1):
            lines.append(f"  {i}. {issue.message}")
            if issue.criterion:
                lines.append(f"     相关标准: {issue.criterion[:100]}...")
            if issue.suggestion:
                lines.append(f"     建议: {issue.suggestion}")
        lines.append("")

    if warnings:
        lines.append("🟡 警告（建议修复）：")
        for i, issue in enumerate(warnings, 1):
            lines.append(f"  {i}. {issue.message}")
            if issue.suggestion:
                lines.append(f"     建议: {issue.suggestion}")
        lines.append("")

    if infos:
        lines.append("ℹ️ 提示：")
        for i, issue in enumerate(infos, 1):
            lines.append(f"  {i}. {issue.message}")
        lines.append("")

    return "\n".join(lines)
