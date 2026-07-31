# ATF · Agent Test Framework

> AI 智能体自动化测试平台 — **分析 → 生成 → 执行 → 评分 → 报告 → 优化** 一站式闭环。

一个用 **FastAPI + 单页 HTML + SQLite** 实现的轻量级智能体测试框架。输入被测智能体的 system prompt，平台自动完成提示词分析、测试用例生成、批量执行、LLM 自动评分和可视化报告。`python run.py` 即跑，零外部依赖。

## 核心特性

| 能力 | 说明 |
|------|------|
| 🧠 **智能体分析** | 用 LLM 将 system prompt 解析为「核心定位 / 能力 / 边界 / 用户画像 / 业务参数」结构化摘要，结果缓存复用 |
| ✨ **用例自动生成** | 支持 **维度驱动**（11 个维度系统化覆盖）和 **PD 风格**（测试要点 + P 级别定向回归）两种模式 |
| 🤖 **多平台接入** | 被测智能体支持 OpenAI 兼容 / 阿里百炼 / 扣子 Coze 三种适配器，透传业务变量 |
| ⚡ **批量并行执行** | asyncio + 信号量控制并发（1-20 可调），SSE 实时推送进度 |
| 🧪 **G-Eval + Ensemble 评估** | 规则层硬匹配 + LLM 多次投票评分（CoT 推理），输出一致性指标 |
| 📊 **可视化报告** | 综合评分、通过率、SVG 雷达图、维度明细、失败用例排序，支持 Markdown/JSON 导出 |
| 💡 **提示词反向优化** | 基于失败用例让 LLM 给出优化版 system_prompt + 改动说明，一键应用 |
| ⏰ **定时任务** | Cron 式定时执行测试，支持并发配置和跳过重叠 |
| 📝 **模板管理** | 维度 Prompt / 系统 Prompt / 行业规则 / Good/Bad Case 全可配置，内置 6 个行业默认模板 |
| 🔐 **单密码鉴权** | 可选密码保护，5 次失败锁定 5 分钟，session 7 天有效 |
| 💾 **零外部依赖** | SQLite 文件库，前端纯 HTML，`git clone` 即跑，数据备份只需复制一个文件 |

## 目录结构

```
platform/
├── app/
│   ├── main.py              # FastAPI 入口 + SSE + 静态文件
│   ├── config.py            # 环境变量加载
│   ├── models.py            # Pydantic 数据模型
│   ├── store.py             # SQLite 持久化（WAL 模式）
│   ├── llm.py               # OpenAI 兼容 LLM 客户端
│   ├── adapters.py          # 被测智能体适配器（OpenAI / 百炼 / Coze）
│   ├── generator.py         # 维度驱动用例生成器
│   ├── pd_generator.py      # PD 风格用例生成器
│   ├── dynamic_generator.py # 动态对话生成器
│   ├── evaluator.py         # 规则 + G-Eval + Ensemble 双层评估
│   ├── runner.py            # 并发执行 + 事件总线
│   ├── scheduler.py         # 定时任务调度
│   ├── bailian_importer.py  # 百炼平台批量导入
│   ├── metrics.py           # HTTP 指标中间件
│   ├── auth.py              # 密码鉴权
│   └── routes/              # API 路由（agents / cases / runs / schedules / system / auth）
├── web/
│   ├── index.html           # 单页前端（暗色 IDE 风格 UI）
│   ├── js/                  # 前端逻辑模块
│   └── vendor/              # Tailwind CDN + JetBrains Mono 字体
├── spider/                  # 百炼页面爬虫（可选，需 selenium）
├── scripts/                 # 批量导入 / 替换工具脚本
├── docs/                    # 详细文档
├── tests/                   # 冒烟测试 & 单元测试
├── requirements.txt
├── .env.example
└── run.py                   # 启动入口
```

## 快速开始

### 1. 安装依赖

```bash
cd platform
pip install -r requirements.txt
```

> 如需「从百炼平台批量导入」功能，额外安装：`pip install selenium`

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少配置以下三项：
#   LLM_BASE_URL  —— OpenAI 兼容接口地址
#   LLM_API_KEY   —— API Key
#   LLM_MODEL     —— 模型名（推荐 qwen-plus / gpt-4o-mini 等高性价比模型）
```

评估模型（Judge）可单独配置更强的模型，留空则复用生成模型。详见 [.env.example](.env.example) 中的完整注释。

### 3. 启动

```bash
python run.py
# 打开 http://127.0.0.1:8866
```

也可使用 `start.bat`（Windows）或设置自定义 `PORT`。

### 4. 使用流程

1. **新建被测智能体**：填写名称、行业、system prompt，选择接入方式（OpenAI / 百炼 / Coze）和密钥
2. **分析智能体**：点击「🔍 分析」，LLM 将 system prompt 解析为结构化摘要（能力/边界/用户画像）
3. **生成测试用例**：
   - **维度驱动**：勾选需要覆盖的维度，设置每维度条数
   - **PD 风格**：填写测试要点 + P 级别 + 数量，适合定向回归
4. **批量测试**：点击「▶ 开始批量测试」，选择用例批次和并发数，实时查看 SSE 进度
5. **查看报告**：任务完成后查看综合评分、雷达图、维度明细、失败用例详情
6. **优化提示词**：基于失败用例一键生成优化版 system_prompt，迭代改进

## 测试维度（11 个）

| key | 中文名 | 说明 |
|-----|--------|------|
| `alignment` | 预期效果 | 覆盖核心能力的正向验证 |
| `boundary` | 边界兜底 | 超出能力范围 / 模糊输入 / 空输入 / 特殊格式 |
| `industry` | 行业规范 | 按所属行业的合规点（医疗/金融/教育等） |
| `badcase` | Bad Case | 答非所问 / 过度拒绝 / 幻觉 / 格式混乱 |
| `security` | 安全性 | 提示词注入 / 越狱 / 隐私泄露 / 有害内容 |
| `multi_turn` | 多轮对话 | 上下文理解 / 指代消解 / 状态保持 |
| `instruction_following` | 指令遵循 | 复杂多约束 / 否定 / 条件 / 步骤化要求 |
| `robustness` | 鲁棒性 | 错别字 / 网络缩写 / 混合语言等扰动输入 |
| `tone` | 角色与语气 | 人设保持 / 压力测试 / 风格一致性 |
| `factuality` | 事实性 | 知识问答 / 错误前提纠正 / 抗幻觉 |
| `format` | 输出格式 | JSON / 表格 / 代码块 / 字数限制等格式约束 |

## 评估机制

每条用例结果由两层判定：

1. **规则层**：解析 `pass_criteria` 中的「必须包含 xxx / 不包含 xxx / 字数 < N」等模式做字符串硬匹配
2. **G-Eval + Ensemble 层**：默认 3 次 LLM 调用 + temperature 抖动，CoT 推理后打分（1-5 分），投票决定通过/失败，输出 `agreement` 一致性指标

最终 `passed = rule_ok AND judge_passed`（可通过 `JUDGE_STRICT_MODE` 切换为仅 LLM）。规则不通过时分数上限为 3 分。

### 评估配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `JUDGE_ENSEMBLE_N` | 3 | 评审次数（推荐奇数 3/5） |
| `JUDGE_TEMPERATURE_VAR` | 0.2 | 多次调用的 temperature 抖动幅度 |
| `JUDGE_USE_GEVAL` | true | 是否使用 G-Eval CoT 提示词 |
| `JUDGE_PASS_THRESHOLD` | 3.5 | 通过阈值（1.0-5.0），严格场景建议 4.0 |
| `JUDGE_STRICT_MODE` | true | 规则层和 LLM 层都必须通过 |

## API 速查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 + LLM 配置状态 |
| GET | `/api/dimensions` | 支持的测试维度元数据 |
| GET/POST | `/api/agents` | 智能体 CRUD |
| GET/PUT/DELETE | `/api/agents/{id}` | 单个智能体 |
| GET | `/api/agents_overview` | 智能体卡片视图（含用例统计/最近任务） |
| POST | `/api/agents/{id}/analyze` | 分析智能体（`?force=true` 强制刷新） |
| POST | `/api/cases/generate_async` | 维度驱动生成（异步） |
| POST | `/api/cases/generate_pd_async` | PD 风格生成（异步） |
| GET | `/api/cases/generation_jobs/{id}` | 查询生成任务进度 |
| GET | `/api/agents/{id}/cases` | 用例列表 |
| GET | `/api/agents/{id}/batches` | 用例批次列表 |
| POST | `/api/cases/batch_delete` | 批量删除用例 |
| POST | `/api/runs` | 启动批量测试 |
| GET | `/api/runs` | 任务列表 |
| GET | `/api/runs/{id}/stream` | SSE 进度流 |
| GET | `/api/runs/{id}/results` | 用例结果 |
| GET | `/api/runs/{id}/report?format=json\|md` | 聚合测试报告 |
| POST | `/api/agents/{id}/optimize_prompt` | 提示词反向优化 |
| GET/PUT | `/api/config` | 运行时 LLM 配置（无需重启） |
| GET/POST/PUT/DELETE | `/api/templates` | 模板 CRUD |
| POST | `/api/templates/init` | 一键初始化默认模板 |
| GET/POST/PUT/DELETE | `/api/schedules` | 定时任务 CRUD |

完整 OpenAPI 文档：启动后访问 `/docs`。

## 环境变量

见 [.env.example](.env.example)，主要分类：

- **生成模型**：`LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` / `LLM_TEMPERATURE`
- **评估模型**：`JUDGE_BASE_URL` / `JUDGE_API_KEY` / `JUDGE_MODEL`（留空复用生成模型）
- **评估参数**：`JUDGE_ENSEMBLE_N` / `JUDGE_PASS_THRESHOLD` / `JUDGE_STRICT_MODE` 等
- **服务配置**：`PORT` / `HOST` / `MAX_CONCURRENCY` / `DATA_DIR`
- **安全**：`ATF_PASSWORD`（留空关闭鉴权）/ `LLM_SSL_VERIFY`

## 注意事项

1. **启动目录**：从 `platform/` 目录运行，或在 `.env` 中设置 `DATA_DIR` 为绝对路径
2. **Token 消耗**：维度驱动每条用例 1 次生成 + N 次评审（默认 N=3），批量跑测前留意用量
3. **数据备份**：所有业务数据在 `data/platform.db`（SQLite），备份只需复制此文件
4. **后台生成**：异步生成任务记录在内存中，进程重启会丢失进度浮窗，但已生成用例已落库不受影响
5. **百炼/Coze 变量**：自动透传 `biz_params` / `custom_variables`，确保 system prompt 中占位符与变量 key 对应

## 技术栈

| 层 | 选型 |
|----|------|
| 后端 | FastAPI + uvicorn + SSE |
| 存储 | SQLite（WAL 模式） |
| 数据模型 | Pydantic v2 |
| 前端 | 单页 HTML + Tailwind + 原生 JS（无构建） |
| LLM | httpx + OpenAI 兼容协议 |
| 并发 | asyncio + Semaphore |
| 字体 | JetBrains Mono + Inter |

## 灵感来源

- **AgentTester** — 「分析智能体 → 按维度设计用例」的提示词体系与多维度分类
- **TwinTalk** — 事件驱动执行结构（runner + bus + SSE）

本项目用 Python + FastAPI 重写，聚焦「围绕提示词的自动化测试」核心目标，不依赖 Node.js 技术栈，部署更轻量。

## License

MIT
