"""首次启动时的示例数据：DAG、智能体、技能、工具、模型别名、知识库文档。"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from . import builtin_tools
from .kb import index_document
from .models import Agent, AgentVersion, Alias, AppKey, KbDoc, Provider, Setting, Skill, SkillVersion, Tool

SAMPLE_DAG = {
    "name": "客户全景分析流程",
    "version": "v1",
    "state_schema": {"features": "dict", "report_html": "str"},
    "entry": "n_start",
    "nodes": [
        {"id": "n_start", "type": "start", "name": "开始", "next": ["n_dispatch"]},
        {"id": "n_dispatch", "type": "condition", "name": "报文类型分流", "branches": [
            {"when": "credit", "to": "n_credit"},
            {"when": "finance", "to": "n_finance"},
            {"when": "judicial", "to": "n_judicial"}]},
        {"id": "n_credit", "type": "tool", "name": "征信报文解析", "tool": "征信报文解析",
         "timeout": 8, "next": ["n_merge"]},
        {"id": "n_finance", "type": "tool", "name": "财报指标计算", "tool": "财报指标计算",
         "timeout": 10, "next": ["n_merge"]},
        {"id": "n_judicial", "type": "tool", "name": "司法风险标签", "tool": "司法风险标签",
         "timeout": 6, "next": ["n_merge"]},
        {"id": "n_merge", "type": "llm", "name": "特征聚合与风险判读", "model": "default-chat",
         "timeout": 60, "next": ["n_hitl"]},
        {"id": "n_hitl", "type": "hitl", "name": "高风险客户人工复核", "next": ["n_report"]},
        {"id": "n_report", "type": "tool", "name": "HTML 报告渲染", "tool": "HTML 报告渲染",
         "timeout": 20, "next": ["n_end"]},
        {"id": "n_end", "type": "end", "name": "结束"},
    ],
}

KB_DOCS = [
    ("客户风险分类口径_v3.md", "口径", "风险等级划分、阈值与例外",
     "综合风险得分由授信使用率、逾期次数、偿债能力与司法风险四项加权得出，得分越高风险越高。\n"
     "风险等级划分：A 级不高于 30 分，B 级 31 至 55 分，C 级 56 至 75 分，D 级高于 75 分。\n"
     "C 级及以上必须人工复核，复核结论需在报告中留痕。\n"
     "授信使用率超过 80% 视为额度紧张，需在报告中单独提示；30 天以上逾期达到 2 次进入关注名单。"),
    ("征信报文字段说明.md", "报文", "字段含义、单位与缺失值处理",
     "授信使用率等于已用额度除以授信总额，授信总额为 0 时按缺失处理，不参与评分。\n"
     "逾期次数按账户与月份去重统计，30 天以上逾期单独计数，金额单位统一为元。\n"
     "近 3 个月查询次数反映资金饥渴程度，超过 10 次需关注多头借贷风险。"),
    ("财报指标计算规则.md", "规则", "盈利 / 偿债 / 营运指标口径",
     "资产负债率等于负债总额除以资产总额，超过 75% 标记为高杠杆。\n"
     "净利润为负且连续两期时标记为持续亏损，与司法失信标签叠加判定为高风险。\n"
     "净利率等于净利润除以营业收入，用于观察盈利质量。"),
]


def seed_all(db: Session) -> None:
    if db.query(Agent).count() == 0:
        agent = Agent(
            id="ag_001", name="客户全景分析", description="全量报文 → 特征聚合 → HTML 全景分析报告",
            version=1, status="published", dag_json=json.dumps(SAMPLE_DAG, ensure_ascii=False),
        )
        db.add(agent)
        db.add(AgentVersion(id="av_001", agent_id="ag_001", version=1,
                            trigger="初始版本", gate="PASS", note="示例 DAG：9 个节点"))

    if db.query(Tool).count() == 0:
        builtin_meta = {
            "征信报文解析": ("内置", "Python", "read", 8),
            "财报指标计算": ("内置", "Python", "read", 10),
            "司法风险标签": ("内置", "Python", "read", 6),
            "HTML 报告渲染": ("内置", "Jinja2", "fs:write", 20),
            "JSON 提取器": ("内置", "Python", "read", 3),
            "计算器": ("内置", "Python", "read", 3),
        }
        for name in builtin_tools.names():
            source, impl, perm, timeout = builtin_meta.get(name, ("内置", "Python", "read", 10))
            db.add(Tool(id=f"tool_{abs(hash(name)) % 100000}", name=name, source=source, impl=impl,
                        permission=perm, timeout_s=timeout, status="启用",
                        schema_json=json.dumps({"type": "object", "properties": {}}, ensure_ascii=False),
                        refs=1))
        db.add(Tool(id="tool_fs", name="文件系统", source="MCP", impl="stdio", permission="fs:read",
                    timeout_s=15, status="启用", schema_json="{}", refs=1))
        db.add(Tool(id="tool_crm", name="客户主数据查询", source="自定义", impl="HTTP",
                    permission="biz:read", timeout_s=8, idempotency=True, status="启用",
                    schema_json=json.dumps({"type": "object", "properties": {"customer_no": {"type": "string"}}},
                                           ensure_ascii=False), refs=1))

    if db.query(Skill).count() == 0:
        skills = [
            Skill(id="sk_01", name="客户全景分析技能", category="业务", version=1, status="published",
                  prompt="panorama-report@v1", model="default-chat",
                  tools_json=json.dumps(["征信报文解析", "财报指标计算", "司法风险标签", "HTML 报告渲染"],
                                        ensure_ascii=False),
                  timeout_s=90, retry=2, refs=1, description="三类报文解析 + 特征聚合 + 报告渲染"),
            Skill(id="sk_02", name="征信特征提取", category="业务", version=1, status="published",
                  prompt="credit-features@v1", model="default-chat",
                  tools_json=json.dumps(["征信报文解析", "JSON 提取器"], ensure_ascii=False),
                  timeout_s=30, retry=1, refs=1, description="授信使用率 / 逾期 / 查询频次口径"),
            Skill(id="sk_03", name="财报指标测算", category="业务", version=1, status="published",
                  prompt="finance-metrics@v1", model="default-chat-lite",
                  tools_json=json.dumps(["财报指标计算", "计算器"], ensure_ascii=False),
                  timeout_s=25, retry=1, refs=1, description="盈利 / 偿债 / 营运指标计算"),
            Skill(id="sk_04", name="单客户快速体检", category="实验", version=1, status="draft",
                  prompt="quick-check@v1", model="default-chat-lite",
                  tools_json=json.dumps(["JSON 提取器", "计算器"], ensure_ascii=False),
                  timeout_s=15, retry=1, refs=0, description="只取关键字段，快速出摘要"),
        ]
        db.add_all(skills)
        for s in skills:
            db.add(SkillVersion(id=f"sv_{s.id}", skill_id=s.id, version=1,
                                trigger="初始版本", gate="PASS", changelog="示例数据"))

    if db.query(Provider).count() == 0:
        db.add_all([
            Provider(id="prov_rule", name="rule", kind="规则", base_url="", models="rule-based",
                     caps="聊天", status="healthy"),
            Provider(id="prov_ollama", name="ollama", kind="本地",
                     base_url="http://127.0.0.1:11434/v1", models="qwen2.5:7b / bge-m3",
                     caps="聊天 / 流式 / 函数调用 / embedding", status="healthy"),
            Provider(id="prov_deepseek", name="DeepSeek", kind="云端",
                     base_url="https://api.deepseek.com/v1", models="deepseek-chat",
                     caps="聊天 / 流式 / 函数调用", status="healthy"),
        ])

    if db.query(Alias).count() == 0:
        db.add_all([
            Alias(id="al_01", alias="default-chat", provider_name="rule", model="rule-based",
                  caps="聊天", status="默认"),
            Alias(id="al_02", alias="default-chat-lite", provider_name="rule", model="rule-lite",
                  caps="聊天", status="启用"),
            Alias(id="al_03", alias="default-embedding", provider_name="rule", model="hash-embedding",
                  caps="embedding", status="默认"),
            Alias(id="al_04", alias="cloud-deepseek", provider_name="DeepSeek", model="deepseek-chat",
                  caps="聊天 / 流式 / 函数调用", status="降级备用"),
        ])
        db.add(Setting(key="fallback", value=json.dumps(["default-chat", "default-chat-lite"])))

    if db.query(KbDoc).count() == 0:
        for i, (name, kind, note, content) in enumerate(KB_DOCS):
            doc = KbDoc(id=f"doc_{i + 1:02d}", name=name, kind=kind, note=note,
                        status="已索引", content=content)
            db.add(doc)
            db.flush()
            index_document(db, doc)

    # 本地调试凭证：明文固定为 DEMO_KEY，便于业务侧直接联调（仅本地演示使用）
    from .auth import DEMO_KEY, digest as _digest

    demo = db.query(AppKey).filter(AppKey.id == "ak_demo").first()
    if not demo:
        db.add(AppKey(id="ak_demo", app="本地调试", type="PAT", prefix=DEMO_KEY[:14] + "…",
                      digest=_digest(DEMO_KEY), status="启用", expires_at="2027-12-31"))
    elif not demo.digest:
        demo.digest = _digest(DEMO_KEY)
        demo.prefix = DEMO_KEY[:14] + "…"

    db.commit()
