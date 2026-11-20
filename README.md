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

控制台「接入与凭证 → 对外调用」页可直接复制 curl / Python / Node 片段并在线试调用。

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
| `AH_LANGFUSE_ENABLED` | false | Langfuse 旁路开关（控制台也可切换） |
| `AH_LANGFUSE_HOST` | http://127.0.0.1:3100 | 自托管地址 |
| `AH_LANGFUSE_PUBLIC_KEY` / `AH_LANGFUSE_SECRET_KEY` | 空 | 上报凭证 |
| `AH_LANGFUSE_SAMPLING` | 0.2 | 采样率 |

## 测试

```bash
pytest -q
```

## 交付边界

已实现课题六项能力的最小可用形态（模型接入 / 智能体编排 / 工具调用 / 知识库集成 / 运行监控 / 标准化 API），
按评审结论裁剪掉多租户隔离、SKU、可视化大屏、容器化部署与 API 预算管控。
设计文档与原型图不纳入本仓库。
