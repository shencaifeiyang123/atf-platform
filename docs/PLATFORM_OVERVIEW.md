# AI 智能体批量测试平台 · 特性与使用指南

> 单进程 FastAPI + 单页 HTML + SQLite。`python run.py` 即跑。
> 输入一个智能体的 system prompt，平台帮你完成：
> **分析定位 → 自动生成测试用例 → 调用被测智能体跑测 → LLM 自动评分 → 可视化报告 → 一键反向优化提示词** 的完整闭环。

---

## 一、平台核心特性

### 1. 智能体接入（被测对象）

| 适配器 | 适用场景 | 关键配置 |
|---|---|---|
| OpenAI 兼容 | 通义、DeepSeek、Moonshot、本地模型等 | `base_url` / `api_key` / `model` |
| 阿里百炼 | 已在百炼上构建的「智能体应用」 | `api_key` / `app_id` / `endpoint` |
| 扣子 Coze | 已在 Coze 上发布的 Bot | `api_key` / `bot_id` / `endpoint` |

- **业务变量（variables）**：任意嵌套 JSON，调用时会替换消息中的 `{{key}}` 占位符；
  - 百炼侧透传到 `biz_params`
  - Coze 侧透传到 `custom_variables`

---

### 2. 智能体分析（一次分析，多次复用）

把 system prompt 提炼成结构化摘要：
- **核心定位（core_value）**
- **核心能力（capabilities）**
- **能力边界（boundaries）**
- **用户画像（user_profile）**
- **业务参数使用方式（variables_usage）**：当智能体配置了 `variables` 时，分析会额外输出每个参数的预期取值方式与应触发的行为，供后续用例生成专门验证业务参数是否被正确使用

分析结果落库，下次生成用例直接复用，避免重复花 token。
也可在「智能体详情 → 🔍 重新分析」强制刷新。

---

### 3. 用例生成（两套互补模式）

#### A. 维度驱动（推荐做系统化覆盖）
勾选维度 + 设每维度条数，内置 **11 个维度**：

| key | 中文名 | 说明 |
|---|---|---|
| `alignment` | 预期效果 | 覆盖核心能力的正向验证 |
| `boundary` | 边界兜底 | 超出能力范围 / 模糊输入 / 空输入 |
| `industry` | 行业规范 | 按所属行业的合规点 |
| `badcase` | Bad Case | 答非所问 / 编造 / 格式混乱等高频投诉 |
| `security` | 安全性 | 提示词注入 / 越狱 / 隐私泄露 / 有害内容 |
| `multi_turn` | 多轮对话 | 上下文理解 / 指代消解 / 状态保持 |
| `instruction_following` | 指令遵循 | 复杂多约束 / 否定 / 条件 / 步骤化要求 |
| `robustness` | 鲁棒性 | 错别字 / 网络缩写 / 混合语言等扰动 |
| `tone` | 角色与语气 | 人设保持 / 压力测试 / 风格一致性 |
| `factuality` | 事实性 | 知识问答 / 错误前提纠正 / 抗幻觉 |
| `format` | 输出格式 | JSON / 表格 / 代码块 / 字数限制 |

#### B. PD 风格（适合定向回归）
- 自由填写「测试要点」+ 选 P 级别（`p0` / `p1` / `p1_p2` / `p1_p3` / `p1_p5` / `all` / `p0~p6`）+ 生成数量
- 可选「**使用智能体分析结果**」开关，把分析摘要注入到生成 prompt
- 生成的用例统一归到 `alignment` 维度，P 级别写入 `sub_type` 便于统计

#### 开场设置（两种模式共用）
- **用户先开场**（默认）：`turns[0]` 即用户首问；可填「用户开场内容」固定首问
- **AI 先开场**：执行时由智能体主动问候，`turns` 是用户的回应
- **混合 / 默认**：生成期混搭，落到 case 上规整为 ai/user 之一

#### 业务参数（variables）注入
- 智能体配置的 `variables`（JSON）会在分析与生成阶段一并注入到 LLM Prompt：
  - **分析阶段**：`_ANALYSIS_PROMPT` 通过 `{variables_section}` 占位符注入 variables JSON，并要求 LLM 输出 `variables_usage` 字段
  - **维度驱动生成**：当 `agent.variables` 非空时，自动在每条用例的生成指令后追加"业务参数验证"段落，引导覆盖必填校验 / 错误值兜底 / 跨参数组合
  - **PD 风格生成**：variables JSON 直接注入到 `build_pd_prompt` 的 Prompt 中；若同时开启「使用智能体分析结果」，分析块还会包含 `variables_usage` 摘要
- 用例 `turns` 文本中可用 `{{key}}` 占位符引用 variables，调用时由适配器自动替换（百炼透传 `biz_params`，Coze 透传 `custom_variables`）

#### 批次（batch）管理
- 每次生成的用例共享同一 `batch_id`，前端按批次折叠分组
- 批次标签自动带时间戳和模式标记，例如 `05-17 11:38 维度驱动 · 预期效果,边界兜底`
- 支持「全选本批次」+ 一键批量删除

---

### 4. 批量测试执行

- **并发**：asyncio + 信号量，1-20 可调
- **进度**：SSE 实时推送，前端进度条 + 实时打分
- **用例组选择**：启动时下拉选择某个 batch，不选则默认跑最新批次
- **任务列表**：显示创建 / 开始 / 完成时间和耗时
- **任务详情**：每条结果显示维度徽章 / sub_type / 标题 / 完整对话 + 评审意见

---

### 5. 双层 + 集成评估

| 层 | 作用 | 说明 |
|---|---|---|
| 规则层 | 字符串硬匹配 | 解析 `pass_criteria` 里的「必须包含 / 不应包含 / 字数 < N」等模式 |
| **G-Eval + Ensemble** | 语义打分 | 默认 3 次调用 + temperature 抖动，CoT 推理后打分；输出 `agreement` 一致性指标（0-1） |

最终 `passed = rule_ok AND judge_passed`；规则不通过时分数最多 3 分。
- 评审 LLM 可单独配置（建议用更强的模型）
- 关键环境变量：`JUDGE_ENSEMBLE_N` / `JUDGE_TEMPERATURE_VAR` / `JUDGE_USE_GEVAL`

---

### 6. 测试报告

- **顶部 KPI**：综合评分（0-100） / 通过率 / 总用例 / 失败+错误
- **SVG 雷达图**：各维度得分一目了然
- **维度详情**：横向进度条 + 分数 + 通过/总数
- **维度 Tab 切换**：查看用例明细，按 失败 → 错误 → 通过 排序
- **一键导出**：Markdown / JSON

---

### 7. 提示词反向优化

- 基于最近一次（或指定）任务的失败用例 + 维度统计
- 让评审 LLM 给出 **优化版 system_prompt + 主要改动说明**
- 支持指定：
  - **优化方向**（如「增强真人感」「提升异议处理」）
  - **优化前提**（约束，如「保持输出格式不变」）
- 一键应用到智能体

---

### 8. 测试模板管理

> 前端入口位于导航栏「测试Prompt」Tab（原"测试模板"），用于管理下列各类模板。

| 模板类型 | 用途 |
|---|---|
| `dimension` | 维度生成 Prompt（注入 `{core_value}` / `{capabilities}` / `{boundaries}` / `{user_profile}` / `{industry}` / `{industry_rules}` / `{good_cases}` / `{bad_cases}` / `{n}` / `{system_prompt}` 等占位符） |
| `system_prompt` | 系统级 Prompt（如「能力提取」） |
| `industry_rule` | 行业规则（一行一条文本） |
| `good_case` | 沉淀的优秀用例 |
| `bad_case` | 高频 Bad Case 反例 |

- 内置 **6 个行业** 默认规则：教育 / 金融 / 医疗 / 客服 / 电商 / 通用
- 一键「初始化默认模板」即可获得开箱即用的模板集合

---

### 9. 运行时配置

- 「⚙️ 配置」弹窗里直接改 LLM 的 `base_url` / `api_key` / `model` / `temperature`
- 写入 `data/runtime_config.json` 立即生效，**无需重启**
- 一键「恢复 .env 默认」回退

---

## 二、典型使用流程

```
1) 启动服务
   cd agent_test_framework/platform
   pip install -r requirements.txt
   cp .env.example .env       # 至少填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
   python run.py              # 默认端口看 .env 里的 PORT

2) 浏览器打开 http://127.0.0.1:<PORT>

3) 「智能体」页 → + 新建智能体
   - 名称、行业、system prompt
   - 选接入方式（百炼 / OpenAI / Coze）填密钥
   - 业务参数（可选 JSON）

4) 「测试用例」页 → 选中该智能体 → ✨ 生成用例
   方式 A：维度驱动 → 勾选维度 + 设每维度数量 → 开始生成（后台）
   方式 B：PD 风格   → 写测试要点 + 选 P 级别 + 数量 → 开始生成（后台）
   生成期间右下角浮窗显示进度，可继续操作其他页面

5) ▶ 开始批量测试 → 选用例组 + 并发数 → 进入任务详情看 SSE 进度

6) 任务完成 → 「测试报告」按钮
   雷达图、维度分、失败用例分析

7) 「智能体」页 → 💡 提示词优化
   基于失败用例自动生成优化版 system_prompt → 复制 / 一键应用

8)（迭代）回到第 4 步重新跑测，对比改进效果
```

---

## 三、API 速查

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 健康 + LLM 配置状态 |
| GET | `/api/dimensions` | 支持的测试维度元数据 |
| GET / POST | `/api/agents` | 智能体 CRUD |
| GET / PUT / DELETE | `/api/agents/{id}` | 单个智能体 |
| GET | `/api/agents_overview` | 智能体卡片视图（含用例统计 / 最近任务） |
| GET | `/api/agents/{id}/analysis` | 读取已保存的分析结果 |
| POST | `/api/agents/{id}/analyze?force=true` | 强制重新分析 |
| POST | `/api/cases/generate_async` | 维度驱动生成（异步） |
| POST | `/api/cases/generate_pd_async` | PD 风格生成（异步，支持 `use_analysis`） |
| GET | `/api/cases/generation_jobs/{job_id}` | 查询生成任务进度 |
| GET | `/api/agents/{id}/cases` | 用例列表 |
| GET | `/api/agents/{id}/batches` | 用例组（批次）列表 |
| POST / PUT / DELETE | `/api/cases` `/api/cases/{id}` | 用例 CRUD |
| POST | `/api/cases/batch_delete` | 批量删除 |
| POST | `/api/runs` | 启动批量测试 |
| GET | `/api/runs` | 任务列表 |
| GET | `/api/runs/{id}/stream` | SSE 进度流 |
| GET | `/api/runs/{id}/results` | 用例结果（附维度元信息） |
| GET | `/api/runs/{id}/report?format=json\|md` | 聚合测试报告 |
| DELETE | `/api/runs/{id}` | 删除任务及全部结果 |
| POST | `/api/agents/{id}/optimize_prompt` | 提示词反向优化 |
| GET / PUT / DELETE | `/api/config` | 运行时 LLM 配置 |
| GET / POST / PUT / DELETE | `/api/templates` `/api/templates/{id}` | 模板 CRUD |
| POST | `/api/templates/init` | 一键初始化默认模板 |

完整 OpenAPI 文档：启动后访问 `/docs`。

---

## 四、注意事项 & 常见坑

1. **DATA_DIR 是相对路径**：必须从 `agent_test_framework/platform/` 目录启动（或在 .env 里写绝对路径）；否则会在错误位置创建一个空 SQLite 库，让你以为「数据丢了」。
2. **LLM 用量**：维度驱动每条用例 1 次生成 + N 次评审（默认 N=3）。批量跑测前留意 token 消耗。
3. **百炼 / Coze 的 variables**：会自动透传到平台原生字段（`biz_params` / `custom_variables`），比 OpenAI 适配器多一层能力，注意 system prompt 里的占位符要和 variables 的 key 对得上。
4. **数据备份**：所有业务数据落在 `data/platform.db`（SQLite 文件），备份只需复制这一个文件；`runtime_config.json` 是运行时 LLM 配置。
5. **后台生成任务**：异步生成任务记录在内存里（`_GEN_JOBS`），进程重启会丢；只影响进度浮窗，**已生成的用例已落库不会丢**。
6. **任务删除时正在运行**：可以直接删，后台 asyncio 任务会在下次写库时发现 run 已不存在并自然结束。

---

## 五、技术栈一览

| 层 | 选型 | 说明 |
|---|---|---|
| 后端 | FastAPI + uvicorn | 路由 / SSE / 异步并发 |
| 存储 | SQLite（WAL 模式） | 单文件零依赖 |
| 数据模型 | Pydantic v2 | 请求 / 响应 / 实体一体化 |
| 前端 | 单页 HTML + Tailwind CDN + 原生 JS | 无构建步骤，按 `index.html` 即可改 |
| LLM 客户端 | httpx + OpenAI 兼容协议 | 同时支持百炼 / Coze 适配 |
| 并发 | asyncio.Semaphore | 信号量限流 |

---

## 六、灵感来源

- **AgentTester**（`agent-tester-main/`）—— 借鉴「分析智能体 → 按维度设计用例」的提示词体系与多维度分类思路
- **TwinTalk**（`TwinTalk-main/`）—— 借鉴单次运行的事件驱动结构（runner + bus + SSE）和简洁的数据模型
- 本平台用 Python + FastAPI 重写，聚焦「围绕提示词的自动化测试」这一核心目标，不依赖 Next.js 技术栈，部署更轻
