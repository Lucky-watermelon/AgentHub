# AgentHub

企业级智能体开发与运行支撑平台（裁剪版实现）。基于 FastAPI + SQLAlchemy + LangGraph 的本地单机服务，
提供模型接入、智能体 DAG 编排、工具调用、知识库检索、运行监控与标准化 API。

## 快速启动

```bash
# 1. 安装依赖（conda 环境 AgentHub，Python 3.11）
pip install -r requirements.txt

# 2. 启动服务（默认 127.0.0.1:8000）
python -m agenthub

# 3. 打开控制台
#    http://127.0.0.1:8000        Web 控制台
#    http://127.0.0.1:8000/docs   OpenAPI 文档
```

首次启动自动建库并写入示例数据：1 个智能体（9 节点客户全景分析流程）、4 个技能、
6 个内置工具、3 篇知识库口径文档、3 个 Provider 与 4 个模型别名。

## 目录结构

```
agenthub/
  config.py         运行配置（环境变量 AH_* 覆盖）
  db.py             数据库连接与会话
  models.py         ORM：智能体/技能/工具/运行/知识库/模型/凭证
  schemas.py        请求模型与序列化
  dag.py            DAG 校验、分层布局、LangGraph 编译
  builtin_tools.py  内置工具：征信/财报/司法解析、报告渲染、JSON、计算器
  llm.py            模型别名解析、Provider 调用、降级链
  kb.py             知识库切分、向量化（bigram 哈希）、检索
  report.py         HTML 全景分析报告渲染
  runner.py         运行执行器：事件流、Checkpoint、人工审批
  seed.py           首次启动示例数据
  api/              REST 接口（catalog / knowledge / models_api / runs / access）
  templates/        报告模板
web/                控制台（原生 JS，无构建）
tests/              单元测试与接口测试
```

## 核心链路

```
POST /api/v1/runs  →  校验 DAG  →  编译 LangGraph StateGraph  →  逐节点执行
                   →  tool 节点调用工具、llm 节点调用模型、hitl 节点判定风险
                   →  聚合特征  →  渲染 HTML 报告  →  写 RunEvent / Checkpoint
GET  /api/v1/runs/{id}/events  →  SSE 实时事件
```

- DAG JSON 格式：`nodes`（id / type / name / next / branches / tool / model）+ 可选 `edges`
- 校验项：id 唯一、类型合法、边端点存在、环路、可达性、孤立节点、不可达终节点
- 执行优先走 LangGraph，编译或执行异常时降级为按拓扑序顺序执行
- 高风险客户（司法命中被执行/失信）在 hitl 节点中断，状态置 `WAITING_APPROVAL`，审批后从 Checkpoint 恢复

## 对外调用（业务系统嵌入）

平台是中台，业务侧不需要了解内部 DAG，两种方式二选一。

### 1. 原生调用

```bash
curl -X POST http://127.0.0.1:8000/api/v1/invoke/ag_001 \
  -H "Authorization: Bearer ah_pat_demo_local_dev" \
  -H "Content-Type: application/json" \
  -d '{"customer_no":"C-2026-0031","mode":"sync",
       "payload":{"credit":{"total_limit":8000000,"used_limit":6200000,"overdue_30d":1}}}'
```

- `mode=sync`：直接返回 `summary` / `features` / `report_url`
- `mode=async`：立即返回 `run_id`，用 `links.events`（SSE）或 `links.self`（轮询）取结果
- `credit` / `finance` / `judicial` 也可平铺在请求体顶层

### 2. OpenAI 兼容

已有 OpenAI SDK 的业务侧只改 `base_url`，`model` 填智能体 id：

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="ah_pat_demo_local_dev")
resp = client.chat.completions.create(model="ag_001", messages=[{"role": "user", "content": payload_json}])
print(resp.choices[0].message.content)
```

兼容端点：`POST /v1/chat/completions`（支持 `stream=true`）、`GET /v1/models`。

### 辅助端点

| 端点 | 用途 |
| --- | --- |
| `GET /api/v1/invoke/agents` | 可被外部调用的智能体清单 |
| `GET /api/v1/invoke/{agent}/info` | 接入信息（端点、字段、最近调用） |

### 鉴权

- 请求头 `Authorization: Bearer <AppKey>`，也支持 `X-API-Key`、`?access_token=`（SSE 用）
- 本地演示凭证：`ah_pat_demo_local_dev`（服务端只存 sha256 摘要）
- `AH_AUTH_REQUIRED=true` 时对 `/api/v1` 与 `/v1` 强制校验，未开启时允许匿名调用并记录调用方

### 幂等调用

业务侧重试时带上幂等键，平台保证同一键只执行一次：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/invoke/ag_001 \
  -H "Idempotency-Key: order-20261120-0001" \
  -H "Authorization: Bearer ah_pat_demo_local_dev" \
  -H "Content-Type: application/json" \
  -d '{"customer_no":"C-2026-0031","payload":{...}}'
```

- 支持请求体 `idempotency_key` 或请求头 `Idempotency-Key`
- 命中（状态为 CREATED / RUNNING / WAITING_APPROVAL / COMPLETED）直接返回原 `run_id`，响应里 `idempotent_reuse=true`
- 失败或被拒绝的运行不占用幂等键，可重试

控制台「接入与凭证 → 对外调用」页可直接复制 curl / Python / Node 片段并在线试调用。

## 运行与并发

| 项 | 默认 | 说明 |
| --- | --- | --- |
| `AH_RUN_WORKERS` | 4 | 运行执行器 worker 数（有界线程池） |
| `AH_RUN_QUEUE_SIZE` | 32 | 排队上限，超出返回 `AH-RUNTIME-429` + `Retry-After` |

- SQLite 开启 WAL + `busy_timeout=15s`，多线程写入不再互相长时间阻塞
- 停机时等待在途运行结束（优雅停机），不会硬切断任务
- 进程池状态见 `GET /api/v1/system/health` 的 `run_pool`

**HITL 断点续跑**：运行在审批节点中断时保存状态快照与已完成节点；审批通过后只执行剩余节点，
已完成的工具调用不会重复执行（可通过 `tool_calls` 增量与 `node.skipped` 事件验证）。

## 数据库迁移

启动时自动执行 `agenthub/migrations/` 下未应用的迁移，版本记录在 `schema_migrations` 表。

```
agenthub/migrations/
  runner.py              执行器（版本表 + 顺序应用）
  m0001_baseline.py      基线
  m0002_run_resume.py    runs: idempotency_key / state_json / node_progress
```

新增迁移：添加 `m000N_xxx.py`，暴露 `VERSION`、`DESCRIPTION`、`up(conn)`，并注册到 `runner._migration_modules()`。
迁移必须幂等（先判断列/索引是否存在），因为新库由 `create_all` 直接建出目标结构。

## 配额与限流

按调用凭证（AppKey / PAT）维度三层限额，超限返回 `AH-QUOTA-429` 并带 `Retry-After`：

| 维度 | 说明 | 默认 |
| --- | --- | --- |
| RPS | 1 秒滑动窗口内的请求数 | 10 |
| 并发 | 同时在途请求数 | 4 |
| 日配额 | 当天累计调用次数，跨日自动重置 | 1000 |

```bash
GET  /api/v1/quota              # 各凭证的策略与今日用量
PUT  /api/v1/quota/{key_id}     # 调整 rps / concurrency / daily_calls / enabled
POST /api/v1/quota/{key_id}/reset
```

控制台「接入与凭证 → AppKey」可直接编辑与重置。限流状态在进程内存，多副本部署需换 Redis。

## 可观测性（Langfuse 旁路）

可选，关闭时零开销，上报失败不影响运行结果。

```bash
export AH_LANGFUSE_PUBLIC_KEY=pk-xxx
export AH_LANGFUSE_SECRET_KEY=sk-xxx
export AH_LANGFUSE_HOST=http://127.0.0.1:3100   # 自托管地址
```

- 通过 Langfuse Ingestion API 上报，无额外 SDK 依赖
- 一次运行 = 一条 trace，节点/工具为 span，模型调用为 generation
- 开关与采样率存在数据库（控制台「运行监控 → Langfuse」可调），重启后保持
- `POST /api/v1/observability/langfuse/test` 可验证连通性

## 配置

环境变量或 `.env`（前缀 `AH_`）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `AH_PORT` | 8000 | 监听端口 |
| `AH_DATABASE_URL` | 空（SQLite） | 可切 PostgreSQL |
| `AH_LLM_MOCK` | true | true 时不出网，用规则化文本兜底 |
| `AH_AUTH_REQUIRED` | false | 接入业务系统时置 true |
| `AH_DATA_DIR` | data | 数据目录 |
| `AH_REPORTS_DIR` | reports | 报告产物目录 |
| `AH_RUN_WORKERS` | 4 | 运行执行器 worker 数 |
| `AH_RUN_QUEUE_SIZE` | 32 | 运行排队上限 |
| `AH_LANGFUSE_ENABLED` | false | Langfuse 旁路开关（控制台也可切换） |
| `AH_LANGFUSE_HOST` | http://127.0.0.1:3100 | 自托管地址 |
| `AH_LANGFUSE_PUBLIC_KEY` / `AH_LANGFUSE_SECRET_KEY` | 空 | 上报凭证 |
| `AH_LANGFUSE_SAMPLING` | 0.2 | 采样率 |

## 测试

```bash
pytest -q
```

## 非功能与实测

| 维度 | 实测结论 |
| --- | --- |
| 同步吞吐 | 23.4 QPS（并发 10，10 节点 DAG，Mock 模型）；优化前 9.6 |
| 延迟 | 并发 10 时 P50 93 ms / P95 2.0 s（优化前 689 ms / 2.7 s） |
| 并发 20 | 成功率 100%，19.4 QPS；连接池修复前为 74% |
| 异步提交 | 约 21 QPS（并发 50，只计提交） |
| 故障演练 | 模型不可用→降级完成；工具异常→FAILED；DB 锁→503 可重试；进程 kill→INTERRUPTED |
| 多副本 | 2 worker 实测通过，并暴露「限流/队列为进程内状态」的改造点 |
| 可观测 | request_id 贯穿响应头/错误体/事件/JSON 日志 |

复现：`pytest -q`、`scripts/bench.py`、`scripts/fault_test.py`、`scripts/multiworker_check.py`。
完整数据与取舍论证见 `docs/11-非功能设计与实测.md`（设计文档目录不入版本库）。

## 交付边界

已实现课题六项能力的最小可用形态（模型接入 / 智能体编排 / 工具调用 / 知识库集成 / 运行监控 / 标准化 API），
按评审结论裁剪掉多租户隔离、SKU、可视化大屏、容器化部署与 API 预算管控。
设计文档与原型图不纳入本仓库。
