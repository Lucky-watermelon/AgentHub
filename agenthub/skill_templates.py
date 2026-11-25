"""示例技能包：SKILL.md + 脚本。

组织方式对齐 Agent Skills：
- SKILL.md 用 YAML frontmatter 声明 name / description / allowed-tools / model
- 正文写“何时使用、步骤、输出约定、注意事项”
- scripts/ 下放可直接运行的脚本，由平台沙箱执行
"""
from __future__ import annotations

import json

USAGE_RATE_CODE = """# scripts/usage_rate.py
# 输入：payload（征信报文）、features（已聚合特征）
# 输出：JSON，写入 features.credit
credit = payload.get("credit") or {}
total = float(credit.get("total_limit") or 0)
used = float(credit.get("used_limit") or 0)
overdue = int(credit.get("overdue_30d") or 0)
query = int(credit.get("query_3m") or 0)

usage = (used / total) if total else None
band = "正常"
reasons = []
if usage is not None and usage >= 0.8:
    band = "关注"
    reasons.append("授信使用率 %.1f%%" % (usage * 100))
if overdue >= 2:
    band = "高风险"
    reasons.append("30 天以上逾期 %d 次" % overdue)
if query >= 10:
    reasons.append("近 3 个月查询 %d 次" % query)

emit({"features": {"credit": {
    "usage_rate": round(usage, 4) if usage is not None else None,
    "risk_band": band,
    "script_reasons": reasons,
}}})
"""

DEBT_RATIO_CODE = """# scripts/debt_ratio.py
# 输入：payload（财报报文）
# 输出：JSON，写入 features.finance
fin = payload.get("finance") or {}
revenue = float(fin.get("revenue") or 0)
profit = float(fin.get("net_profit") or 0)
assets = float(fin.get("total_assets") or 0)
liability = float(fin.get("total_liability") or 0)

ratio = (liability / assets) if assets else None
margin = (profit / revenue) if revenue else None
flags = []
if ratio is not None and ratio > 0.75:
    flags.append("资产负债率超过 75%")
if profit < 0:
    flags.append("当期亏损")

emit({"features": {"finance": {
    "debt_ratio": round(ratio, 4) if ratio is not None else None,
    "net_margin": round(margin, 4) if margin is not None else None,
    "script_flags": flags,
}}})
"""

QUICK_CHECK_CODE = """# scripts/quick_check.py
# 单客户快速体检：把三类信号压成一个 0-100 的风险分
credit = payload.get("credit") or {}
finance = payload.get("finance") or {}
judicial = payload.get("judicial") or {}

total = float(credit.get("total_limit") or 0)
used = float(credit.get("used_limit") or 0)
usage = (used / total) if total else 0
overdue = int(credit.get("overdue_30d") or 0)
profit = float(finance.get("net_profit") or 0)
executed = int(judicial.get("executed") or 0)
dishonest = int(judicial.get("dishonest") or 0)

score = 0.0
score += min(usage, 1.0) * 35
score += min(overdue, 3) * 8
score += 15 if profit < 0 else 0
score += 20 if executed else 0
score += 20 if dishonest else 0
score = round(min(score, 100), 1)
level = "高" if score >= 75 else "中" if score >= 45 else "低"

emit({"features": {"quick_check": {"score": score, "level": level}}})
"""

SKILL_CONTENTS: dict[str, str] = {
    "sk_01": """---
name: panorama-report
description: 三类报文解析 → 特征聚合 → 风险判读 → HTML 全景报告
version: 1
model: default-chat
allowed-tools: 征信报文解析, 财报指标计算, 司法风险标签, HTML 报告渲染
---

# 客户全景分析技能

## 何时使用
需要一次性完成征信、财报、司法三类报文解析，并输出可归档报告时。

## 工作步骤
1. 解析本次提交中实际存在的报文类型，缺失类型直接跳过。
2. 聚合 `features.credit` / `features.finance` / `features.judicial`。
3. 按知识库口径给出风险等级；授信使用率超过 80% 必须单独提示。
4. 调用模型输出不超过 120 字的综合判断。
5. 由「HTML 报告渲染」生成报告。

## 输出约定
- `features`：三类报文的结构化特征
- `summary`：模型结论
- `report_html`：报告节点产物

## 注意事项
- 司法命中被执行 / 失信时，必须在 hitl 节点人工复核。
- 不臆造报文里没有的字段；缺失值按“—”展示。
""",
    "sk_02": """---
name: credit-features
description: 从征信报文提取授信使用率、逾期与查询频次，并按口径给出风险提示
version: 1
model: default-chat
allowed-tools: 征信报文解析, JSON 提取器
entrypoint: usage_rate.py
---

# 征信特征提取

## 何时使用
输入包含 `credit` 报文，需要计算授信使用率、逾期次数与查询频次时。

## 工作步骤
1. 调用「征信报文解析」得到基础特征。
2. 运行 `scripts/usage_rate.py`：按 80% 使用率、2 次逾期、10 次查询三个阈值打标签。
3. 将 `risk_band` 与 `script_reasons` 合并进 `features.credit`。

## 脚本契约
- 输入：`payload.credit`（原始报文）
- 输出：JSON `{"features": {"credit": {...}}}`
- 可用变量：`payload` / `features` / `customer_no` / `params` / `emit(data)`

## 注意事项
- 授信总额为 0 时使用率按缺失处理，不参与评分。
- 逾期与查询次数按整数处理，缺失按 0。
""",
    "sk_03": """---
name: finance-metrics
description: 盈利 / 偿债 / 营运指标测算，输出高杠杆与亏损标签
version: 1
model: default-chat-lite
allowed-tools: 财报指标计算, 计算器
entrypoint: debt_ratio.py
---

# 财报指标测算

## 何时使用
输入包含 `finance` 报文，需要计算资产负债率、净利率并判断高杠杆时。

## 工作步骤
1. 调用「财报指标计算」得到基础指标。
2. 运行 `scripts/debt_ratio.py` 复核资产负债率与净利率。
3. 资产负债率 > 75% 标记高杠杆；净利润为负标记当期亏损。

## 脚本契约
- 输入：`payload.finance`
- 输出：JSON `{"features": {"finance": {...}}}`
- 金额单位统一为元，除零时返回 `null`

## 注意事项
- 只做指标计算与标签，不给出授信建议。
- 连续两期亏损需要人工结合司法标签判断。
""",
    "sk_04": """---
name: quick-check
description: 只取关键字段，把三类信号压成一个 0-100 的风险分
version: 1
model: default-chat-lite
allowed-tools: JSON 提取器, 计算器
entrypoint: quick_check.py
---

# 单客户快速体检

## 何时使用
只需要一个粗筛分数，不要求完整报告与逐项指标时。

## 工作步骤
1. 从三类报文中取关键字段，缺失按 0 处理。
2. 运行 `scripts/quick_check.py` 计算 0-100 风险分。
3. 分数 ≥ 75 判为高风险，≥ 45 判为中风险，其余低风险。

## 脚本契约
- 输入：`payload`（三类报文）
- 输出：JSON `{"features": {"quick_check": {"score": 62.5, "level": "中"}}}`
- 打分权重：使用率 35 / 逾期 24 / 亏损 15 / 被执行 20 / 失信 20（上限 100）

## 注意事项
- 这是粗筛，不替代全景报告与人工复核。
- 字段缺失时宁可低估，不要臆造。
""",
}

SKILL_SCRIPTS: dict[str, list[dict]] = {
    "sk_02": [{"name": "usage_rate.py", "language": "python", "code": USAGE_RATE_CODE}],
    "sk_03": [{"name": "debt_ratio.py", "language": "python", "code": DEBT_RATIO_CODE}],
    "sk_04": [{"name": "quick_check.py", "language": "python", "code": QUICK_CHECK_CODE}],
}

SKILL_ENTRYPOINTS: dict[str, str] = {
    "sk_02": "usage_rate.py",
    "sk_03": "debt_ratio.py",
    "sk_04": "quick_check.py",
}


def content_for(skill_id: str) -> str:
    return SKILL_CONTENTS.get(skill_id, "")


def scripts_for(skill_id: str) -> list[dict]:
    return [dict(item) for item in SKILL_SCRIPTS.get(skill_id, [])]


def entrypoint_for(skill_id: str) -> str:
    return SKILL_ENTRYPOINTS.get(skill_id, "")


def scripts_json(skill_id: str) -> str:
    return json.dumps(scripts_for(skill_id), ensure_ascii=False)
