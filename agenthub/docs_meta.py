"""接口文档增强：OpenAPI 摘要 / 示例 / 鉴权声明 + 控制台契约数据。

FastAPI 自动生成的 summary 是英文函数名，直接看 /docs 很难用。
这里用一张中心化的元数据表补齐中文摘要、说明、请求/响应示例，
并给 /docs 加上 Authorize、错误格式与快速开始说明。
"""
from __future__ import annotations

import json
from typing import Any

from .config import settings

APP_DESCRIPTION = """\
智能体运行与能力管理 API：调用智能体、编排 DAG，管理技能 / 工具 / 知识库 / 模型与运行观测。

本地默认不强制鉴权；需要鉴权时点右上角 **Authorize** 填入 AppKey。
"""

TAGS: list[dict] = [
    {"name": "智能体", "description": "DAG 编排、导入、发布与版本"},
    {"name": "技能", "description": "SKILL.md + 脚本包 + 工具/模型绑定"},
    {"name": "工具", "description": "内置 / HTTP / Jinja2 工具与调用记录"},
    {"name": "知识库", "description": "文档、切片、索引与检索"},
    {"name": "模型接入", "description": "Provider、模型别名、降级链与调用统计"},
    {"name": "运行与监控", "description": "运行、Trace、Checkpoint 与统计"},
    {"name": "对外调用", "description": "业务系统调用入口，含 OpenAI 兼容协议"},
    {"name": "配额与凭证", "description": "AppKey 生命周期与三层限流"},
    {"name": "系统", "description": "健康检查、元信息、接口契约"},
    {"name": "可选：外部追踪", "description": "Langfuse 旁路，默认关闭，不影响主链路"},
]

_PAYLOAD = {
    "credit": {"total_limit": 8000000, "used_limit": 6200000, "overdue_30d": 1, "query_3m": 11},
    "finance": {"revenue": 126000000, "net_profit": -3200000,
                "total_assets": 210000000, "total_liability": 168000000},
    "judicial": {"cases": 2, "executed": 0, "dishonest": 0},
}

_SUMMARY: dict[tuple[str, str], str] = {
    # 智能体
    ("GET", "/api/v1/agents"): "查询智能体列表",
    ("POST", "/api/v1/agents"): "创建智能体（可同时提交 DAG）",
    ("GET", "/api/v1/agents/{agent_id}"): "查询智能体详情（含 DAG 与布局）",
    ("PATCH", "/api/v1/agents/{agent_id}"): "更新智能体名称 / 描述 / 状态",
    ("DELETE", "/api/v1/agents/{agent_id}"): "删除智能体（保留历史运行）",
    ("GET", "/api/v1/agents/{agent_id}/dag"): "读取 DAG 与图结构",
    ("POST", "/api/v1/agents/{agent_id}/dag"): "导入 DAG（结构校验 + 依赖门禁）",
    ("POST", "/api/v1/agents/{agent_id}/dag/validate"): "校验 DAG（不落库）",
    ("POST", "/api/v1/agents/{agent_id}/publish"): "发布智能体（版本 +1）",
    ("GET", "/api/v1/agents/{agent_id}/runs"): "查询该智能体的运行记录",
    ("GET", "/api/v1/agents/{agent_id}/versions"): "查询智能体版本历史",
    # 技能
    ("GET", "/api/v1/skills"): "查询技能列表",
    ("POST", "/api/v1/skills"): "新建技能包（SKILL.md + 脚本）",
    ("GET", "/api/v1/skills/{skill_id}"): "查询技能详情（含 SKILL.md 与源码）",
    ("PATCH", "/api/v1/skills/{skill_id}"): "编辑技能包",
    ("DELETE", "/api/v1/skills/{skill_id}"): "删除草稿技能",
    ("POST", "/api/v1/skills/{skill_id}/publish"): "发布技能（内容 / 工具 / 别名 / 脚本校验）",
    ("POST", "/api/v1/skills/{skill_id}/test"): "在沙箱中试运行技能脚本",
    ("GET", "/api/v1/skills/{skill_id}/versions"): "查询技能版本历史",
    # 工具
    ("GET", "/api/v1/tools"): "查询工具列表",
    ("POST", "/api/v1/tools"): "注册工具（内置不在此处注册）",
    ("GET", "/api/v1/tools/calls"): "查询工具调用记录",
    ("PATCH", "/api/v1/tools/{tool_id}"): "更新工具配置 / 启停",
    ("DELETE", "/api/v1/tools/{tool_id}"): "删除非内置工具",
    ("POST", "/api/v1/tools/{tool_id}/invoke"): "直接调用一个工具（调试）",
    # 知识库
    ("GET", "/api/v1/kb/documents"): "查询知识库文档",
    ("POST", "/api/v1/kb/documents"): "上传知识库文档并建索引",
    ("GET", "/api/v1/kb/documents/{doc_id}"): "查询文档正文",
    ("PATCH", "/api/v1/kb/documents/{doc_id}"): "编辑文档（正文变更自动重建切片）",
    ("DELETE", "/api/v1/kb/documents/{doc_id}"): "删除文档与切片",
    ("GET", "/api/v1/kb/documents/{doc_id}/chunks"): "查询文档切片",
    ("POST", "/api/v1/kb/documents/{doc_id}/reindex"): "重建文档索引",
    ("GET", "/api/v1/kb/info"): "知识库概览（文档数 / 切片数 / 字数）",
    ("POST", "/api/v1/kb/retrieve"): "知识库检索（hybrid / vector）",
    # 模型接入
    ("GET", "/api/v1/providers"): "查询 Provider 列表",
    ("POST", "/api/v1/providers"): "注册 Provider",
    ("PATCH", "/api/v1/providers/{provider_id}"): "更新 Provider",
    ("DELETE", "/api/v1/providers/{provider_id}"): "删除 Provider（被别名引用时拒绝）",
    ("POST", "/api/v1/providers/{provider_id}/check"): "检查 Provider 连通性",
    ("GET", "/api/v1/aliases"): "查询模型别名",
    ("POST", "/api/v1/aliases"): "新增模型别名",
    ("PATCH", "/api/v1/aliases/{alias_name}"): "更新模型别名",
    ("DELETE", "/api/v1/aliases/{alias_name}"): "删除模型别名（在降级链中时拒绝）",
    ("POST", "/api/v1/aliases/{alias_name}/default"): "设为默认别名并置顶降级链",
    ("GET", "/api/v1/models/stats"): "查询别名调用统计",
    ("GET", "/api/v1/routing"): "查询降级链",
    ("PUT", "/api/v1/routing"): "更新降级链顺序",
    ("POST", "/api/v1/routing/test"): "测试降级链可用性",
    # 运行与监控
    ("GET", "/api/v1/runs"): "查询运行记录（可按状态筛选）",
    ("POST", "/api/v1/runs"): "发起一次运行",
    ("GET", "/api/v1/runs/{run_id}"): "查询运行详情（事件 / Checkpoint / 产物）",
    ("POST", "/api/v1/runs/{run_id}/approve"): "人工审批（通过 / 拒绝）",
    ("GET", "/api/v1/runs/{run_id}/events"): "订阅 SSE 事件流（不适用于 Swagger 直接试）",
    ("GET", "/api/v1/runs/{run_id}/checkpoints"): "查询运行 Checkpoint",
    ("POST", "/api/v1/runs/{run_id}/replay"): "从 Checkpoint 重放",
    ("GET", "/api/v1/traces"): "查询调用链路列表",
    ("GET", "/api/v1/traces/{trace_id}"): "查询链路与步骤耗时树",
    ("GET", "/api/v1/stats"): "运行与工具统计",
    # 对外调用
    ("GET", "/api/v1/invoke/agents"): "查询可被外部调用的智能体",
    ("POST", "/api/v1/invoke/{agent_id}"): "调用智能体（sync / async）",
    ("GET", "/api/v1/invoke/{agent_id}/info"): "查询智能体接入信息",
    ("GET", "/v1/models"): "OpenAI 兼容模型列表（智能体即模型）",
    ("POST", "/v1/chat/completions"): "OpenAI 兼容对话（model 填智能体 id）",
    # 凭证与配额
    ("GET", "/api/v1/keys"): "查询调用凭证",
    ("POST", "/api/v1/keys"): "签发调用凭证（明文只返回一次）",
    ("PATCH", "/api/v1/keys/{key_id}"): "启用 / 停用凭证",
    ("POST", "/api/v1/keys/{key_id}/rotate"): "轮换凭证（旧明文立即失效）",
    ("POST", "/api/v1/keys/{key_id}/revoke"): "吊销凭证",
    ("GET", "/api/v1/quota"): "查询配额策略与今日用量",
    ("GET", "/api/v1/quota/usage"): "查询指定日期的用量",
    ("PUT", "/api/v1/quota/{key_id}"): "更新配额策略",
    ("POST", "/api/v1/quota/{key_id}/reset"): "重置今日用量",
    # 系统 / 可选
    ("GET", "/api/v1/system/contract"): "查询接口契约（控制台使用）",
    ("GET", "/api/v1/system/health"): "查询系统健康状态",
    ("GET", "/api/v1/system/meta"): "查询系统元信息",
    ("GET", "/api/v1/system/storage"): "查询存储占用",
    ("GET", "/api/v1/observability/langfuse"): "查询 Langfuse 旁路配置",
    ("POST", "/api/v1/observability/langfuse"): "更新 Langfuse 旁路配置",
    ("POST", "/api/v1/observability/langfuse/test"): "测试 Langfuse 连通性",
    ("GET", "/health"): "健康检查",
}

_DESCRIPTIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/v1/invoke/{agent_id}"): (
        "业务系统调用 AgentHub 的统一入口。`agent_id` 可传智能体 id 或名称。\n\n"
        "- `mode=sync`：等待运行结束并直接返回结论与报告地址。\n"
        "- `mode=async`：立即返回 `run_id`，通过 `links.self` 查询或 `links.events` 订阅 SSE。\n"
        "- `payload` 至少包含 `credit / finance / judicial` 之一；`customer_no` 用于报告命名。\n"
        "- 高风险客户可能返回 `WAITING_APPROVAL`，审批后才会出报告。\n"
        "- 带 `idempotency_key` 或请求头 `Idempotency-Key` 时，重试不会重复执行。"
    ),
    ("POST", "/api/v1/runs"): (
        "直接按 agent_id 发起运行，行为与 `/api/v1/invoke/{agent_id}` 类似，"
        "但返回完整的运行对象（含 trace_id / thread_id / report_path）。"
    ),
    ("DELETE", "/api/v1/agents/{agent_id}"): (
        "删除智能体与它的版本配置。历史运行记录会保留，便于审计；"
        "如果仍有 `CREATED / RUNNING / WAITING_APPROVAL` 的运行，返回 `AH-RUNTIME-409`，"
        "需要先处理完在途或待审批运行。"
    ),
    ("POST", "/api/v1/agents/{agent_id}/dag"): (
        "导入完整 DAG。保存前会做两层校验：\n\n"
        "1. 结构：id 唯一、类型合法、无环、可达、condition 有 branches。\n"
        "2. 依赖：工具已注册且启用；模型别名存在且 Provider 未停用；技能已发布。\n\n"
        "`tool` / `skill` 节点支持 `params` 覆盖报文顶层字段。失败返回 `AH-RUNTIME-422`。"
    ),
    ("POST", "/api/v1/agents/{agent_id}/dag/validate"): (
        "只做结构和依赖校验，不写入数据库。返回 `errors`、`warnings`、"
        "`dependency_errors` 与布局预览 `structure`，适合前端实时校验。"
    ),
    ("POST", "/api/v1/skills"): (
        "技能包 = `SKILL.md`（frontmatter + 说明书）+ `scripts[]` + 工具/模型绑定。\n\n"
        "脚本目前支持 Python，运行时可用 `payload / features / customer_no / params`，"
        "用 `emit({...})` 输出 JSON。新建时为草稿，需调用发布接口上线。"
    ),
    ("POST", "/api/v1/skills/{skill_id}/test"): (
        "在简易沙箱中运行技能脚本：独立进程、AST 白名单、超时强杀、输出截断。\n\n"
        "输入可传 `payload / features / customer_no / params`；脚本通过 `emit({...})` 输出 JSON。\n"
        "这是演示级隔离，不是安全边界。"
    ),
    ("POST", "/api/v1/tools/{tool_id}/invoke"): (
        "直接调用工具，用于注册后自测。HTTP 工具按 endpoint + method + headers 请求；"
        "Jinja2 工具渲染模板；内置工具走进程内实现。参数会按 Schema 校验。"
    ),
    ("POST", "/api/v1/kb/retrieve"): (
        "检索知识库切片。`mode=hybrid` 为向量 0.6 + 关键词 0.4；`mode=vector` 只用向量相似度。"
        "返回文档名、切片文本、总分与分数拆解，便于解释召回原因。"
    ),
    ("POST", "/api/v1/keys"): (
        "签发调用凭证。明文只在本次响应中返回一次，服务端只保存 sha256 摘要；"
        "`days` 为有效天数，过期后鉴权层直接拒绝。"
    ),
    ("POST", "/api/v1/keys/{key_id}/rotate"): (
        "轮换凭证：同一行换新摘要，旧明文立即失效，配额与统计保持不变。"
        "响应中的 `plain` 只返回一次。"
    ),
    ("POST", "/api/v1/runs/{run_id}/approve"): (
        "对 `WAITING_APPROVAL` 的运行做审批。`APPROVED` 会从断点继续，跳过已完成节点；"
        "`REJECTED` 将运行置为 `REJECTED`。"
    ),
    ("POST", "/v1/chat/completions"): (
        "兼容 OpenAI SDK。把 `base_url` 指向 `http://127.0.0.1:8000/v1`，"
        "`model` 填智能体 id；消息内容为报文的 JSON 字符串即可。`stream=true` 时走 SSE。"
    ),
    ("GET", "/api/v1/system/contract"): (
        "返回控制台「接口文档」使用的结构化契约：参数、请求/响应示例、curl 与是否可测试。"
        "数据直接来自 OpenAPI，不会与真实路由漂移。"
    ),
}

_REQUEST_EXAMPLES: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/invoke/{agent_id}"): {
        "customer_no": "C-2026-0031", "mode": "sync", "idempotency_key": "",
        "payload": _PAYLOAD,
    },
    ("POST", "/api/v1/runs"): {
        "agent_id": "ag_001", "customer_no": "C-2026-0031", "async_mode": False,
        "idempotency_key": "", "payload": _PAYLOAD,
    },
    ("POST", "/api/v1/runs/{run_id}/approve"): {"decision": "APPROVED", "comment": "同意，继续出报告"},
    ("POST", "/api/v1/runs/{run_id}/replay"): {"checkpoint_id": "cp_xxx"},
    ("POST", "/api/v1/agents"): {
        "name": "演示智能体", "description": "可选：同时提交 DAG",
        "dag": {
            "name": "最小流程", "version": "v1", "entry": "s",
            "nodes": [
                {"id": "s", "type": "start", "name": "开始", "next": ["t"]},
                {"id": "t", "type": "tool", "name": "征信解析", "tool": "征信报文解析", "next": ["e"]},
                {"id": "e", "type": "end", "name": "结束"},
            ],
        },
    },
    ("POST", "/api/v1/agents/{agent_id}/dag"): {
        "publish": False,
        "dag": {
            "name": "演示流程", "version": "v1", "entry": "s",
            "nodes": [
                {"id": "s", "type": "start", "name": "开始", "next": ["kb"]},
                {"id": "kb", "type": "tool", "name": "口径检索", "tool": "知识库检索",
                 "params": {"q": "授信使用率 口径", "top_k": 3}, "next": ["e"]},
                {"id": "e", "type": "end", "name": "结束"},
            ],
        },
    },
    ("POST", "/api/v1/skills"): {
        "name": "自定义技能", "category": "业务", "model": "default-chat",
        "tools": ["征信报文解析"], "prompt": "custom@v1",
        "content": "# 自定义技能\n\n## 何时使用\n输入包含征信报文时。\n",
        "scripts": [{"name": "main.py", "language": "python",
                     "code": 'emit({"features": {"ok": True}})'}],
        "entrypoint": "main.py", "timeout_s": 30, "retry": 1,
    },
    ("PATCH", "/api/v1/skills/{skill_id}"): {
        "content": "# 更新后的 SKILL.md\n", "entrypoint": "main.py",
        "scripts": [{"name": "main.py", "language": "python",
                     "code": 'emit({"features": {"ok": True}})'}],
    },
    ("POST", "/api/v1/skills/{skill_id}/test"): {
        "input": {"payload": {"credit": {"total_limit": 8000000, "used_limit": 6200000}},
                  "features": {}, "customer_no": "C-TEST"},
        "script": "main.py", "timeout_s": 5,
    },
    ("POST", "/api/v1/tools"): {
        "name": "客户主数据查询", "source": "自定义", "impl": "HTTP",
        "permission": "biz:read", "timeout_s": 8, "method": "POST",
        "endpoint": "http://127.0.0.1:9000/customer",
        "headers": "{\"X-Token\":\"demo\"}",
        "params_schema": "{\"type\":\"object\",\"required\":[\"customer_no\"],"
                         "\"properties\":{\"customer_no\":{\"type\":\"string\"}}}",
    },
    ("POST", "/api/v1/tools/{tool_id}/invoke"): {"params": {"customer_no": "C-2026-0031"}},
    ("POST", "/api/v1/kb/documents"): {
        "name": "客户风险分类口径_v4.md", "kind": "口径", "note": "风险等级与阈值",
        "content": "风险等级划分：A 级不高于 30 分，B 级 31 至 55 分，C 级及以上需人工复核。",
    },
    ("PATCH", "/api/v1/kb/documents/{doc_id}"): {
        "content": "更新后的正文，保存后自动重建切片。", "note": "已更新",
    },
    ("POST", "/api/v1/kb/retrieve"): {"q": "授信使用率 逾期 口径", "top_k": 3, "mode": "hybrid"},
    ("POST", "/api/v1/keys"): {"app": "crm-system", "type": "AppKey", "days": 365},
    ("POST", "/api/v1/keys/{key_id}/rotate"): {"days": 365},
    ("PATCH", "/api/v1/keys/{key_id}"): {"status": "停用"},
    ("PUT", "/api/v1/quota/{key_id}"): {"rps": 10, "concurrency": 4, "daily_calls": 1000, "enabled": True},
    ("POST", "/api/v1/providers"): {
        "name": "ollama", "kind": "本地", "base_url": "http://127.0.0.1:11434/v1",
        "models": "qwen2.5:7b", "caps": "聊天 / 流式", "api_key": "",
    },
    ("PATCH", "/api/v1/providers/{provider_id}"): {"models": "qwen2.5:7b / qwen2.5:14b"},
    ("POST", "/api/v1/aliases"): {
        "alias": "local-qwen", "provider_name": "ollama", "model": "qwen2.5:7b", "caps": "聊天",
    },
    ("PATCH", "/api/v1/aliases/{alias_name}"): {"model": "qwen2.5:14b"},
    ("PUT", "/api/v1/routing"): {"chain": ["default-chat", "default-chat-lite"]},
    ("POST", "/api/v1/observability/langfuse"): {"enabled": False, "sampling": 0.2,
                                                 "host": "http://127.0.0.1:3100"},
    ("POST", "/v1/chat/completions"): {
        "model": "ag_001", "stream": False,
        "messages": [{"role": "user",
                      "content": json.dumps({"credit": _PAYLOAD["credit"]}, ensure_ascii=False)}],
    },
}

_RESPONSE_EXAMPLES: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/invoke/{agent_id}"): {
        "run_id": "run_ab12cd34ef", "status": "COMPLETED",
        "agent": {"id": "ag_001", "name": "客户全景分析", "version": 2},
        "customer_no": "C-2026-0031", "summary": "综合判断：授信使用率 77.5%……",
        "features": {"credit": {"usage_rate": 0.775}, "kb": {"rows": []}},
        "evidence": {"claims": [{"id": "c1", "text": "授信使用率 77.5%",
                                 "evidence": ["e1"]}],
                     "items": [{"id": "e1", "kind": "tool", "title": "征信报文解析",
                                "detail": "授信使用率 77.5%"}]},
        "report_url": "http://127.0.0.1:8000/reports/C-2026-0031_run_ab12cd34ef.html",
        "usage": {"tokens": 420, "amount": 0.00504, "dur_ms": 3200, "trace_id": "tr_xxx"},
        "links": {"self": "http://127.0.0.1:8000/api/v1/runs/run_ab12cd34ef",
                  "events": "http://127.0.0.1:8000/api/v1/runs/run_ab12cd34ef/events"},
        "caller": "crm-system/ah_live_xxx…", "idempotency_key": "",
        "idempotent_reuse": False, "elapsed_ms": 3214,
    },
    ("POST", "/api/v1/runs"): {
        "id": "run_ab12cd34ef", "agent_id": "ag_001", "status": "COMPLETED",
        "tokens": 420, "amount": 0.00504, "dur_ms": 3200,
        "report_path": "reports/C-2026-0031_run_ab12cd34ef.html",
        "error": "", "trace_id": "tr_xxx", "thread_id": "th_xxx",
    },
    ("POST", "/api/v1/agents/{agent_id}/dag/validate"): {
        "ok": True, "errors": [], "warnings": [], "dependency_errors": [],
        "structure": {"nodes": [{"id": "s", "type": "start", "name": "开始"}], "edges": []},
    },
    ("GET", "/api/v1/skills/{skill_id}"): {
        "id": "sk_02", "name": "征信特征提取", "status": "published", "version": 1,
        "model": "default-chat", "tools": ["征信报文解析", "JSON 提取器"],
        "content": "---\nname: credit-features\n---\n# 征信特征提取",
        "scripts": [{"name": "usage_rate.py", "language": "python", "code": "emit({...})"}],
        "entrypoint": "usage_rate.py",
    },
    ("POST", "/api/v1/skills/{skill_id}/test"): {
        "skill": "sk_02", "script": "usage_rate.py", "ok": True, "exit_code": 0,
        "stdout": '{"features":{"credit":{"usage_rate":0.9375,"risk_band":"高风险"}}}',
        "stderr": "", "dur_ms": 80, "timed_out": False, "errors": [],
        "result": {"features": {"credit": {"usage_rate": 0.9375, "risk_band": "高风险"}}},
    },
    ("POST", "/api/v1/kb/retrieve"): {
        "q": "授信使用率 逾期 口径", "top_k": 3, "mode": "hybrid",
        "terms": ["授信使用率", "逾期", "口径"],
        "rows": [{"id": "chunk_01", "doc": "doc_01", "doc_name": "客户风险分类口径_v3.md",
                  "idx": 0, "text": "授信使用率超过 80% 视为额度紧张……",
                  "score": 0.732, "vec": 0.61, "hit": 0.91}],
    },
    ("POST", "/api/v1/keys"): {
        "id": "ak_xxx", "app": "crm-system", "type": "AppKey", "prefix": "ah_live_ab12…",
        "status": "启用", "expires": "2027-11-25", "expired": False,
        "plain": "ah_live_ab12cd34ef56…",
    },
    ("POST", "/v1/chat/completions"): {
        "id": "chatcmpl-run_ab12cd34ef", "object": "chat.completion", "created": 1764000000,
        "model": "ag_001",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "综合判断：……"},
                     "metadata": {"run_id": "run_ab12cd34ef", "status": "COMPLETED"}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 50, "total_tokens": 420},
    },
    ("GET", "/health"): {"status": "ok", "version": "0.1.0", "profile": "lite"},
}

_PARAM_DOCS = {
    "agent_id": "智能体 id 或名称（调用接口两者都支持）",
    "run_id": "运行 id",
    "trace_id": "Trace id（也可传 run_id）",
    "doc_id": "知识库文档 id",
    "skill_id": "技能 id",
    "tool_id": "工具 id",
    "provider_id": "Provider id",
    "alias_name": "模型别名，例如 default-chat",
    "key_id": "凭证 id（不是明文 AppKey）",
    "limit": "返回条数上限（默认 50，最大 500）",
    "status": "状态筛选，多个用逗号分隔，如 COMPLETED,FAILED",
    "agent_id_query": "按智能体筛选",
    "q": "关键词或查询语句",
    "kind": "文档类型：口径 / 报文 / 规则",
    "day": "日期 YYYY-MM-DD，默认今天",
    "top_k": "返回切片数，默认 4",
    "source": "工具来源：内置 / MCP / 自定义",
}

_PARAM_EXAMPLES = {
    "agent_id": "ag_001", "run_id": "run_xxx", "trace_id": "tr_xxx",
    "doc_id": "doc_01", "skill_id": "sk_02", "tool_id": "tool_xxx",
    "provider_id": "prov_rule", "alias_name": "default-chat", "key_id": "ak_demo",
    "limit": 20, "top_k": 3, "q": "授信使用率", "status": "COMPLETED",
}


def tag_for(path: str) -> str:
    if path.startswith("/api/v1/agents"):
        return "智能体"
    if "/skills" in path:
        return "技能"
    if "/tools" in path:
        return "工具"
    if "/kb" in path:
        return "知识库"
    if any(x in path for x in ("/providers", "/aliases", "/routing", "/models")):
        return "模型接入"
    if any(x in path for x in ("/runs", "/traces", "/stats")):
        return "运行与监控"
    if path.startswith("/api/v1/invoke") or path.startswith("/v1/"):
        return "对外调用"
    if "/quota" in path or "/keys" in path:
        return "配额与凭证"
    if "/observability" in path:
        return "可选：外部追踪"
    return "系统"


def auth_scope(path: str) -> str:
    if path == "/health":
        return "—"
    if path.startswith("/v1/") or path.startswith("/api/v1/invoke"):
        return "AppKey"
    return "PAT"


def _base_url() -> str:
    host = settings.host if settings.host not in ("0.0.0.0", "::") else "127.0.0.1"
    return f"http://{host}:{settings.port}"


def _default_summary(method: str, path: str) -> str:
    action = {"GET": "查询", "POST": "创建 / 执行", "PATCH": "更新",
              "PUT": "更新", "DELETE": "删除"}.get(method, method)
    return f"{action} {path}"


def _collect_parameters(path_item: dict, operation: dict) -> list[dict]:
    params = list(path_item.get("parameters") or []) + list(operation.get("parameters") or [])
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for param in params:
        key = (param.get("name"), param.get("in"))
        if key in seen:
            continue
        seen.add(key)
        schema = param.get("schema") or {}
        out.append({
            "name": param.get("name", ""),
            "in": param.get("in", ""),
            "required": bool(param.get("required")),
            "type": schema.get("type") or ("array" if "items" in schema else "string"),
            "desc": param.get("description") or _PARAM_DOCS.get(param.get("name"), ""),
            "default": schema.get("default"),
            "example": param.get("example", _PARAM_EXAMPLES.get(param.get("name"))),
        })
    return out


def _request_example(operation: dict) -> Any:
    content = (operation.get("requestBody") or {}).get("content") or {}
    for media in content.values():
        examples = media.get("examples") or {}
        for item in examples.values():
            if isinstance(item, dict) and "value" in item:
                return item["value"]
    return None


def _response_example(operation: dict) -> Any:
    for code in ("200", "201", "202"):
        content = ((operation.get("responses") or {}).get(code) or {}).get("content") or {}
        for media in content.values():
            examples = media.get("examples") or {}
            for item in examples.values():
                if isinstance(item, dict) and "value" in item:
                    return item["value"]
    return None


def _curl(method: str, path: str, params: list[dict], example: Any) -> str:
    url = _base_url() + path
    for param in params:
        if param["in"] == "path":
            value = param.get("example") or param.get("default") or "{" + param["name"] + "}"
            url = url.replace("{" + param["name"] + "}", str(value))
    query = []
    for param in params:
        if param["in"] == "query" and param["required"]:
            value = param.get("example") or param.get("default") or "{" + param["name"] + "}"
            query.append(f"{param['name']}={value}")
    if query:
        url += "?" + "&".join(query)
    lines = [f"curl -X {method} '{url}'"]
    if auth_scope(path) != "—":
        lines.append("  -H 'Authorization: Bearer <AppKey>'")
    if example is not None:
        lines.append("  -H 'Content-Type: application/json'")
        body = json.dumps(example, ensure_ascii=False)
        lines.append("  -d '" + body.replace("'", "'\\''") + "'")
    return " \\\n".join(lines)


def enrich_schema(schema: dict) -> None:
    """把中文摘要、示例、鉴权与标签写进 OpenAPI。"""
    schema["tags"] = TAGS
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "AppKey": {"type": "http", "scheme": "bearer",
                   "description": "调用凭证，形如 ah_live_xxx / ah_pat_xxx"},
        "AppKeyQuery": {"type": "apiKey", "in": "query", "name": "access_token",
                        "description": "SSE / EventSource 等无法自定义请求头时使用"},
    }
    schema["security"] = [{"AppKey": []}, {"AppKeyQuery": []}]

    for path, path_item in (schema.get("paths") or {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            key = (method.upper(), path)
            operation["tags"] = [tag_for(path)]
            operation["summary"] = _SUMMARY.get(key) or _default_summary(method.upper(), path)
            desc = _DESCRIPTIONS.get(key, "")
            if desc:
                operation["description"] = desc
            for param in _collect_parameters(path_item, operation):
                if not param["desc"]:
                    continue
                for raw in (path_item.get("parameters") or []) + (operation.get("parameters") or []):
                    if raw.get("name") == param["name"] and raw.get("in") == param["in"]:
                        raw["description"] = param["desc"]
                        if "example" not in raw and param["example"] is not None:
                            raw["example"] = param["example"]

            example = _REQUEST_EXAMPLES.get(key)
            if example is not None:
                content = ((operation.get("requestBody") or {}).get("content") or {})
                for media in content.values():
                    media.setdefault("examples", {})["默认示例"] = {
                        "summary": "可直接修改后 Try it out", "value": example}
            response = _RESPONSE_EXAMPLES.get(key)
            if response is not None:
                for code in ("200", "201"):
                    item = (operation.get("responses") or {}).get(code)
                    if item and "content" in item:
                        for media in item["content"].values():
                            media.setdefault("examples", {})["示例"] = {
                                "summary": "成功响应", "value": response}
            if path == "/health":
                operation["security"] = []


def install(app) -> None:
    """用增强后的 OpenAPI 替换 FastAPI 默认实现（保持缓存语义）。"""
    from fastapi.openapi.utils import get_openapi

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version,
                             description=app.description, routes=app.routes, tags=TAGS)
        enrich_schema(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi


def build_contract() -> dict:
    """控制台接口文档使用的结构化契约。"""
    from .main import app

    schema = app.openapi()
    rows: list[dict] = []
    for path, path_item in (schema.get("paths") or {}).items():
        if not (path.startswith("/api/v1") or path.startswith("/v1/") or path == "/health"):
            continue
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            key = (method.upper(), path)
            params = _collect_parameters(path_item, operation)
            example = _request_example(operation)
            response = _response_example(operation)
            body_schema = None
            content = ((operation.get("requestBody") or {}).get("content") or {})
            if content:
                body_schema = next(iter(content.values())).get("schema")
            rows.append({
                "method": method.upper(), "path": path,
                "summary": operation.get("summary", ""),
                "desc": operation.get("description", ""),
                "tag": tag_for(path), "tags": operation.get("tags") or [],
                "auth": auth_scope(path),
                "params": params,
                "request_example": example,
                "request_schema": body_schema,
                "response_example": response,
                "curl": _curl(method.upper(), path, params, example),
                "testable": "/events" not in path,
                "destructive": method.upper() in {"DELETE", "PUT", "PATCH"}
                               or any(x in path for x in ("/revoke", "/rotate", "/reset")),
                "doc_url": "/docs",
                "operation_id": operation.get("operationId", ""),
            })
    tag_order = {t["name"]: i for i, t in enumerate(TAGS)}
    rows.sort(key=lambda r: (tag_order.get(r["tag"], 99), r["path"], r["method"]))
    return {"title": schema.get("info", {}).get("title", ""),
            "version": schema.get("info", {}).get("version", ""),
            "base_url": _base_url(), "count": len(rows),
            "tags": TAGS, "endpoints": rows}
