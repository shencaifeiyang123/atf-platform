"""Pydantic 数据模型：请求 / 响应 / 数据库实体的统一定义。"""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

# ---------- 被测智能体 ----------

AdapterType = Literal["openai", "bailian", "coze"]


class AgentUnderTest(BaseModel):
    """被测智能体配置。"""
    id: Optional[str] = None
    name: str
    description: str = ""
    # 智能体的「提示词/预期行为」——生成测试用例的关键输入
    system_prompt: str = Field(..., description="智能体的 system prompt 或预期行为描述")
    industry: str = "通用"
    # 接入方式（默认百炼，方便国内用户）
    adapter: AdapterType = "bailian"
    # 各 adapter 专用配置（OpenAI: base_url/api_key/model；Bailian: api_key/app_id；Coze: api_key/bot_id/endpoint）
    config: dict[str, Any] = Field(default_factory=dict)
    # 业务参数：支持任意 JSON（嵌套对象/数组），会替换消息中的 {{key}} 占位符；
    # 对 Coze/百炼还会透传到它们的 variables 字段
    variables: dict[str, Any] = Field(default_factory=dict)
    # 智能体分析结果缓存（首次生成用例时填入，后续复用，避免重复调用 LLM）
    # 包含 core_value / capabilities / boundaries / user_profile 等字段
    analysis: dict[str, Any] = Field(default_factory=dict)
    analysis_at: Optional[int] = None  # 分析完成的时间戳
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


# ---------- 测试用例 ----------

TestDimension = Literal[
    "alignment",              # 预期能力验证
    "boundary",               # 边界/兜底
    "industry",               # 行业规范
    "badcase",                # 高频 Bad Case
    "security",               # 安全性
    "multi_turn",             # 多轮对话与上下文
    "instruction_following",  # 复杂指令遵循
    "robustness",             # 鲁棒性（噪声/扰动输入）
    "tone",                   # 角色与语气一致性
    "factuality",             # 事实准确性 / 抗幻觉
    "format",                 # 输出格式规范
]


class TestTurn(BaseModel):
    role: Literal["user"] = "user"
    content: str


# 开场设置：控制对话由谁先说话
# - ai      智能体先开场（runner 会先发起一条触发消息让 AI 主动问候）
# - user    用户先开场（沿用 turns[0] 作为开场，常规情况）
# - default 智能体的默认行为，等同于 user 但语义上"不干预"
# - mixed   仅用于生成期：让 LLM 在不同用例间混合 ai/user 两种开场；落到 TestCase 上的值
#           会被规整为 ai 或 user 之一，不会保留 mixed
OpeningMode = Literal["ai", "user", "mixed", "default"]


# 对话模式：
# - scripted 脚本模式（默认）：按 turns 列表逐条发送预设的用户消息
# - dynamic  动态模式：用 LLM 扮演用户与被测智能体多轮对话，由 user_persona+user_goal 驱动
DialogueMode = Literal["scripted", "dynamic"]


class TestCase(BaseModel):
    id: Optional[str] = None
    agent_id: str
    dimension: TestDimension
    sub_type: str = ""
    title: str = ""
    # 开场设置：控制本条用例「谁先说话」
    opening_mode: OpeningMode = "default"
    # 多轮：用户消息列表（dynamic 模式下可为空，runner 会通过 LLM 生成）
    turns: list[TestTurn] = Field(default_factory=list)
    # 期望的智能体表现（自然语言 + 机器校验）
    expectation: str = ""
    pass_criteria: list[str] = Field(default_factory=list)
    weight: int = 3
    # ---- 对话模式（动态对话 MVP） ----
    dialogue_mode: DialogueMode = "scripted"
    # dynamic 模式下：模拟用户的人设描述（年龄/性格/语气/背景等）
    user_persona: str = ""
    # dynamic 模式下：模拟用户本轮想达成的目标（任务/疑问/情绪诉求）
    user_goal: str = ""
    # dynamic 模式下：单条用例最大轮数（防失控）；默认 6 轮
    max_turns: int = 6
    # dynamic 模式下：当被测智能体回复包含任一关键词时提前结束（早停）
    termination_keywords: list[str] = Field(default_factory=list)
    # 批次标识：同一次生成的用例共享同一个 batch_id
    batch_id: str = ""
    # 批次显示标签：如 "2026-05-16 09:30 维度驱动 (7条)" 或 "手动创建"
    batch_label: str = ""
    created_at: Optional[int] = None


# ---------- 测试运行 ----------

RunStatus = Literal["pending", "running", "completed", "failed", "canceled"]


class TokenUsage(BaseModel):
    """单次 LLM 调用的 token 用量。"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""


class MetricScore(BaseModel):
    """单个评估指标的得分。"""
    name: str                    # 指标名：relevance / coherence / safety / ...
    score: float = 0.0           # 1-5 分
    reasoning: str = ""          # G-Eval CoT 推理过程


class JudgeRun(BaseModel):
    """单次 judge 调用的原始结果（ensemble 中的一票）。"""
    passed: bool = False
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    comment: str = ""
    metrics: list[MetricScore] = Field(default_factory=list)
    temperature: float = 0.0     # 本次调用使用的 temperature


class CaseResult(BaseModel):
    case_id: str
    status: Literal["pending", "running", "passed", "failed", "error"] = "pending"
    # 对话轨迹
    transcript: list[dict[str, Any]] = Field(default_factory=list)
    # 评估（最终聚合结果，兼容旧逻辑）
    score: float = 0.0
    passed: bool = False
    reasons: list[str] = Field(default_factory=list)
    judge_comment: str = ""
    error: str = ""
    # G-Eval + Ensemble 扩展
    metrics: list[MetricScore] = Field(default_factory=list)
    judge_runs: list[JudgeRun] = Field(default_factory=list)
    agreement: float = 1.0       # 评审一致性 0-1（1=完全一致）
    # Token 用量（adapter 调用 + judge 调用累计）
    token_usage: list[TokenUsage] = Field(default_factory=list)


class TestRun(BaseModel):
    id: Optional[str] = None
    agent_id: str
    name: str = ""
    status: RunStatus = "pending"
    total: int = 0
    finished: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    average_score: float = 0.0
    summary: str = ""
    error: str = ""
    created_at: Optional[int] = None
    started_at: Optional[int] = None
    finished_at: Optional[int] = None
    # Token 用量 + 成本（adapter 调用 + judge 调用累计）
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    # 触发来源：定时任务触发的 run 会带上 schedule_id；手动触发为空
    schedule_id: str = ""


# ---------- API 请求体 ----------

class GenerateRequest(BaseModel):
    agent_id: str
    dimensions: list[TestDimension] = Field(
        default_factory=lambda: ["alignment", "boundary", "industry", "badcase", "security"]
    )
    # 默认每个维度生成多少条
    cases_per_dim: int = 3
    # 可选：按维度精细化指定数量，覆盖 cases_per_dim
    # 例如 {"alignment": 5, "security": 2}
    cases_per_dim_map: dict[str, int] = Field(default_factory=dict)
    # 开场设置（生成期）：控制生成的用例「谁先说话」
    # - default：不强制，让模型按维度自由发挥（多数维度等同 user）
    # - user：用户主动发起对话（generator 给出 turns[0]=用户消息）
    # - ai：智能体先开场（生成的用例不预填 user 消息，由 runner 触发 AI 问候）
    # - mixed：在每个维度内尽量混合 ai / user 两种开场，输出时各自规整为 ai 或 user
    opening_mode: OpeningMode = "default"
    # 用户开场文本（可选）：当 opening_mode="user" 时，若提供此字段，
    # 则强制所有生成的用例第一条用户消息为该文本
    user_opening_text: str = ""


class RunRequest(BaseModel):
    agent_id: str
    name: str = ""
    case_ids: Optional[list[str]] = None  # 为空则跑该 agent 的全部用例
    batch_id: Optional[str] = None        # 指定批次：仅跑该批次内的用例；与 case_ids 同时存在时取交集
    concurrency: int = 8


# ---------- 模板管理 ----------

# 模板类型：
# - dimension     维度生成 Prompt（受 agent-tester 启发）
# - system_prompt 系统级 Prompt（能力提取 / 用户画像 / 自审等）
# - industry_rule 行业合规规则（一行一条文本）
# - good_case     人工沉淀的优秀用例
# - bad_case      高频 Bad Case 反例
TemplateType = Literal["dimension", "system_prompt", "industry_rule", "good_case", "bad_case"]


class Template(BaseModel):
    id: Optional[str] = None
    type: TemplateType
    dimension: str = ""           # 关联维度 key（system_prompt 不填）
    industry: str = ""            # 仅 industry_rule 使用
    name: str
    content: str
    description: str = ""
    is_active: bool = True
    sort_order: int = 0
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class TemplateUpsert(BaseModel):
    """创建 / 更新模板的请求体（部分字段可空，PUT 走部分更新）。"""
    type: Optional[TemplateType] = None
    dimension: Optional[str] = None
    industry: Optional[str] = None
    name: Optional[str] = None
    content: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    sort_order: Optional[int] = None


# ---------- Prompt-Debugger 风格生成请求 ----------

class PromptDebuggerGenerateRequest(BaseModel):
    """基于 prompt-debugger 风格的测试用例生成请求。
    
    特点：
    - 用户输入「测试要点」而非选择维度
    - 按 P0-P6 级别分级生成
    - 输出文本格式用例，后端解析为结构化数据
    - 支持 AI 开场 / 用户开场 / 混合开场
    """
    agent_id: str
    # 测试要点：用户自然语言描述想测什么（留空则用默认值）
    test_points: str = ""
    # 用例级别：p0 / p1 / p1_p2 / p1_p3 / p1_p5 / all
    test_case_level: str = "p1_p2"
    # 开场方式：ai_first / user_first / mixed
    opening_style: str = "mixed"
    # 用户开场文本（可选）：当 opening_style="user_first" 时，
    # 若提供此字段，则强制所有生成的用例第一条用户消息为该文本（如 "//init"、"/start" 等触发指令）
    user_opening_text: str = ""
    # 生成数量
    generate_count: int = 10
    # 是否注入智能体分析结果（core_value / capabilities / boundaries / user_profile）到生成 prompt
    use_analysis: bool = False


# ---------- 动态对话生成请求 ----------

class DynamicGenerateRequest(BaseModel):
    """生成 dialogue_mode='dynamic' 的测试用例。

    LLM 会根据 system_prompt 自动构造虚拟用户的 persona / goal / max_turns
    等字段，运行期由 UserSimulator 驱动多轮对话。
    """
    agent_id: str
    # 生成数量（1-30）
    generate_count: int = 8
    # 开场方式：user_first / ai_first / mixed
    opening_style: str = "mixed"
    # 是否注入智能体分析结果到生成 prompt
    use_analysis: bool = False
    # 额外提示（可选）：例如「重点测试退费场景」等用户意图的补充
    user_hint: str = ""
    # 选定的测试维度（可多选）。LLM 会在这些维度间均衡分布生成用例；
    # 不传或为空则按 ["alignment"] 处理，与之前默认行为一致。
    dimensions: list[str] = []


# ---------- 定时任务（Schedules） ----------

# 触发器类型：
# - interval：每隔 N 分钟一次（最小 5 分钟，避免堆积）
# - daily：每天 HH:MM
# - weekly：每周指定 weekday + HH:MM（weekday：0=周一，6=周日，与 datetime.weekday() 一致）
ScheduleTriggerType = Literal["interval", "daily", "weekly"]


# 用例选择模式：
# - all：触发时取该 agent 全部用例（动态，新建的也会被纳入）
# - dimensions：取指定维度的全部用例
# - ids：固定用例 id 列表（创建 schedule 时快照）
ScheduleSelectorMode = Literal["all", "dimensions", "ids"]


# 重叠策略：
# - skip：上次还没跑完就跳过本次（v1 仅支持此项）
# - queue：排队（v1 不实现，预留）
ScheduleOverlap = Literal["skip", "queue"]


class ScheduleTrigger(BaseModel):
    """触发器配置；按 type 解读不同字段。"""
    type: ScheduleTriggerType
    # interval：分钟数，>= 5
    minutes: int = 0
    # daily / weekly：HH:MM（24 小时制，本地时区）
    hour: int = 0
    minute: int = 0
    # weekly：0=周一 ... 6=周日
    weekday: int = 0


class ScheduleSelector(BaseModel):
    """用例选择器；按 mode 解读不同字段。"""
    mode: ScheduleSelectorMode = "all"
    # dimensions 模式：维度 key 列表
    dimensions: list[str] = Field(default_factory=list)
    # ids 模式：用例 id 列表（快照）
    ids: list[str] = Field(default_factory=list)


class Schedule(BaseModel):
    """定时任务实体。"""
    id: Optional[str] = None
    name: str
    agent_id: str
    trigger: ScheduleTrigger
    selector: ScheduleSelector = Field(default_factory=ScheduleSelector)
    concurrency: int = 5
    enabled: bool = True
    on_overlap: ScheduleOverlap = "skip"
    # 调度元数据（运行期写入，前端只读）
    next_run_at: Optional[int] = None
    last_run_id: str = ""
    last_run_at: Optional[int] = None
    last_run_status: str = ""
    created_at: Optional[int] = None
    updated_at: Optional[int] = None


class ScheduleUpsert(BaseModel):
    """创建 / 修改 schedule 的请求体；id / 调度元数据由后端管理。"""
    name: Optional[str] = None
    agent_id: Optional[str] = None
    trigger: Optional[ScheduleTrigger] = None
    selector: Optional[ScheduleSelector] = None
    concurrency: Optional[int] = None
    enabled: Optional[bool] = None
    on_overlap: Optional[ScheduleOverlap] = None

