"""最小冒烟测试：不依赖外部 LLM，验证数据层 + 规则评估 + 路由加载。"""
import os
import sys
import tempfile
from pathlib import Path

# Windows 控制台默认 GBK 编码，强制 UTF-8 避免中文断言/输出乱码
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 使用临时目录隔离测试数据库
_test_data_dir = tempfile.mkdtemp(prefix="platform_smoke_")
os.environ["DATA_DIR"] = _test_data_dir

# 1) 路由加载
from app import main
assert len(main.app.routes) > 10, "FastAPI 路由未正确注册"
print(f"[OK] FastAPI 路由数量: {len(main.app.routes)}")

# 2) 规则层
from app.evaluator import apply_rules

ok, reasons, warnings = apply_rules("你好世界", ['必须包含"你好"', '不应包含"再见"', "字数 < 100"])
assert ok, f"应通过但失败: {reasons}"
assert len(warnings) == 0, f"不应有警告: {warnings}"
print(f"[OK] 规则通过: {ok}")

ok2, reasons2, warnings2 = apply_rules("我想和你说再见", ['必须包含"你好"', '不应包含"再见"'])
assert not ok2 and len(reasons2) == 2, f"应失败但: {reasons2}"
print(f"[OK] 规则失败并给出原因: {reasons2}")

# 测试字数警告（软性提示）
ok3, reasons3, warnings3 = apply_rules("短", ['必须包含"短"', "字数 > 10"])
assert ok3, f"关键词匹配应通过: {reasons3}"
assert len(warnings3) == 1 and "字数偏少" in warnings3[0], f"应有字数警告: {warnings3}"
print(f"[OK] 字数警告（软性提示）: {len(warnings3)} 条")

# 3) 数据层（写 -> 读 -> 删）
from app import store
from app.models import AgentUnderTest, TestCase, TestTurn, TestRun, CaseResult

agent = store.create_agent(AgentUnderTest(
    name="烟雾测试助手",
    system_prompt="你是一个测试 agent",
    industry="通用",
    adapter="openai",
    config={"base_url": "http://x", "api_key": "x", "model": "x"},
))
print(f"[OK] 创建 agent: {agent.id}")

cases = store.save_cases([TestCase(
    agent_id=agent.id,
    dimension="alignment",
    title="hi",
    turns=[TestTurn(content="你好")],
    expectation="友好回复",
    pass_criteria=["包含'你好'"],
    weight=3,
)])
assert cases[0].id
print(f"[OK] 创建 case: {cases[0].id}")

run = store.create_run(TestRun(agent_id=agent.id, name="smoke"))
store.save_case_result(run.id, CaseResult(
    case_id=cases[0].id, status="passed", passed=True, score=5.0,
    transcript=[{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好呀"}],
))
results = store.list_case_results(run.id)
assert len(results) == 1 and results[0].passed
print(f"[OK] 任务结果落库：{len(results)} 条")

# 清理
store.delete_agent(agent.id)

import shutil
shutil.rmtree(_test_data_dir, ignore_errors=True)
print("[OK] 清理完成。烟雾测试通过")
