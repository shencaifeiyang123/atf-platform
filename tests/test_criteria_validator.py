"""测试标准验证器功能。

运行方式：
    python tests/test_criteria_validator.py
"""
import sys
import io
from pathlib import Path

# 设置 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到 Python 路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.criteria_validator import validate_criteria, format_validation_report
from app.models import TestCase


def test_contradictory_criteria():
    """测试：标准自相矛盾"""
    print("\n" + "="*80)
    print("测试1：标准自相矛盾")
    print("="*80)

    case = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="测试矛盾检测",
        pass_criteria=[
            "必须包含：'你好'",
            "不应包含：'你好'",
        ],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues = validate_criteria(case)
    print(format_validation_report(issues))

    errors = [i for i in issues if i.severity == "error"]
    assert len(errors) > 0, "应该检测到矛盾错误"
    print("✅ 通过：成功检测到标准矛盾")


def test_vague_criteria():
    """测试：标准过于模糊"""
    print("\n" + "="*80)
    print("测试2：标准过于模糊")
    print("="*80)

    case = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="测试模糊词检测",
        pass_criteria=[
            "回答应该比较合理",
            "语气要适当友好",
            "内容相对完整",
        ],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues = validate_criteria(case)
    print(format_validation_report(issues))

    warnings = [i for i in issues if i.severity == "warning"]
    assert len(warnings) >= 3, "应该检测到至少3个模糊词警告"
    print("✅ 通过：成功检测到模糊词汇")


def test_scoring_clarity():
    """测试：计分公式明确性"""
    print("\n" + "="*80)
    print("测试3：计分公式明确性")
    print("="*80)

    # 情况1：提到计分但未给出公式
    case1 = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="测试计分功能",
        pass_criteria=[
            "第1题答对后正确计分",
            "连胜倍增规则正确实现",
        ],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues1 = validate_criteria(case1)
    print("情况1：提到计分但未给出公式")
    print(format_validation_report(issues1))

    warnings1 = [i for i in issues1 if "计分" in i.message and "公式" in i.message]
    assert len(warnings1) > 0, "应该检测到缺少公式的警告"

    # 情况2：给出了明确公式
    case2 = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="测试计分功能",
        pass_criteria=[
            "第1题答对得1分，总分1",
            "第2题答对得分：(1+1)×2=4分，总分5",
        ],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues2 = validate_criteria(case2)
    print("\n情况2：给出了明确公式")
    print(format_validation_report(issues2))

    warnings2 = [i for i in issues2 if "计分" in i.message and "公式" in i.message]
    assert len(warnings2) == 0, "有明确公式时不应该警告"

    print("✅ 通过：成功检测计分公式明确性")


def test_difficulty_consistency():
    """测试：难度标准一致性（用例#3的问题）"""
    print("\n" + "="*80)
    print("测试4：难度标准一致性")
    print("="*80)

    case = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="AI应在连续'过'后出更简单的成语",
        pass_criteria=[
            "第3题的成语明显比前面简单（如'守株待兔''画蛇添足'等）",
            "难度调整后的成语仍符合儿童教育标准",
        ],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues = validate_criteria(case)
    print(format_validation_report(issues))

    # 注意：这个测试可能不会检测到错误，因为标准本身没有明显矛盾
    # 只是举例不够明确。这需要人工审查或更复杂的语义分析
    print("ℹ️  提示：此类问题需要人工审查标准的语义一致性")


def test_empty_criteria():
    """测试：标准为空或过少"""
    print("\n" + "="*80)
    print("测试5：标准为空或过少")
    print("="*80)

    case1 = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="测试空标准",
        pass_criteria=[],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues1 = validate_criteria(case1)
    print("情况1：标准为空")
    print(format_validation_report(issues1))

    warnings1 = [i for i in issues1 if i.severity == "warning"]
    assert len(warnings1) > 0, "应该检测到标准为空的警告"

    case2 = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="测试标准过少",
        pass_criteria=["只有一条标准"],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues2 = validate_criteria(case2)
    print("\n情况2：标准过少")
    print(format_validation_report(issues2))

    infos2 = [i for i in issues2 if i.severity == "info"]
    assert len(infos2) > 0, "应该检测到标准过少的提示"

    print("✅ 通过：成功检测标准数量问题")


def test_good_criteria():
    """测试：良好的标准（应该通过验证）"""
    print("\n" + "="*80)
    print("测试6：良好的标准")
    print("="*80)

    case = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="AI应正确实现计分功能",
        pass_criteria=[
            "首轮回复必须以'👋 哈喽'开头",
            "第1题答对显示'本题得1分'，总分1",
            "第2题答对显示'(1+1)×2=4分'，总分5",
            "不包含：错误的计算结果",
        ],
        turns=[{"role": "user", "content": "测试"}],
    )

    issues = validate_criteria(case)
    print(format_validation_report(issues))

    errors = [i for i in issues if i.severity == "error"]
    assert len(errors) == 0, "良好的标准不应该有错误"

    print("✅ 通过：良好的标准验证通过")


def test_unreachable_state_dynamic():
    """测试：dynamic 用例要求 AI 感知系统异常，但 turns 没暴露异常上下文 → 应报警"""
    print("\n" + "="*80)
    print("测试7：用例不可达（dynamic 模式 + 无异常上下文）")
    print("="*80)

    # 复刻 run_17b84225355f #4 "缺失核心数据：level_config为空" 的形态
    case = TestCase(
        agent_id="test",
        dimension="boundary",
        sub_type="dynamic_boundary",
        title="缺失核心数据：level_config为空",
        dialogue_mode="dynamic",
        user_persona="正常学生，准备开始学习",
        user_goal="想正常参与活动，但系统数据有问题",
        expectation="AI应检测到level_config缺失，友好提示用户数据不完备",
        pass_criteria=[
            "AI明确提示数据缺失",
            "不尝试脑补或兜底生成场景",
            "给出具体的解决建议",
        ],
        turns=[],  # dynamic 模式常见：没有预置 turns
    )

    issues = validate_criteria(case)
    print(format_validation_report(issues))

    unreachable = [i for i in issues if "不可达" in i.message]
    assert len(unreachable) > 0, "应该检测到用例不可达"
    print("✅ 通过：成功检测到 dynamic 不可达用例")


def test_unreachable_state_user_mentioned():
    """测试：用户消息已显式暴露异常 → 不应报不可达"""
    print("\n" + "="*80)
    print("测试8：用例可达（用户消息已暴露异常）")
    print("="*80)

    case = TestCase(
        agent_id="test",
        dimension="boundary",
        expectation="AI应检测到数据异常并友好提示",
        pass_criteria=[
            "AI明确提示数据缺失",
            "给出具体的解决建议",
        ],
        turns=[{"role": "user", "content": "老师，我打开页面发现题目数据缺失，看不到题目了"}],
    )

    issues = validate_criteria(case)
    print(format_validation_report(issues))

    unreachable = [i for i in issues if "不可达" in i.message]
    assert len(unreachable) == 0, "用户已提及异常时不应判为不可达"
    print("✅ 通过：用户消息已暴露异常时未误报")


def test_unreachable_state_no_state_keyword():
    """测试：标准里没有"动作+异常对象"组合 → 不应触发不可达检测"""
    print("\n" + "="*80)
    print("测试9：非异常感知类标准不触发不可达检测")
    print("="*80)

    case = TestCase(
        agent_id="test",
        dimension="alignment",
        expectation="AI应礼貌问候并引导",
        pass_criteria=[
            "AI使用友好语气",
            "包含欢迎语",
            "引导用户进入下一步",
        ],
        turns=[{"role": "user", "content": "你好"}],
    )

    issues = validate_criteria(case)
    unreachable = [i for i in issues if "不可达" in i.message]
    assert len(unreachable) == 0, "正常用例不应触发不可达检测"
    print("✅ 通过：正常用例未误报不可达")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("标准验证器测试套件")
    print("="*80)

    try:
        test_contradictory_criteria()
        test_vague_criteria()
        test_scoring_clarity()
        test_difficulty_consistency()
        test_empty_criteria()
        test_good_criteria()
        test_unreachable_state_dynamic()
        test_unreachable_state_user_mentioned()
        test_unreachable_state_no_state_keyword()

        print("\n" + "="*80)
        print("✅ 所有测试通过！")
        print("="*80)

    except AssertionError as e:
        print(f"\n❌ 测试失败：{e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 测试出错：{e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
