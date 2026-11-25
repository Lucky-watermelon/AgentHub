"use strict";

/* 页面与动作：智能体、技能、工具 */
window.PAGES = window.PAGES || {};
window.ACTIONS = window.ACTIONS || {};
const P1 = window.PAGES, A1 = window.ACTIONS;

/* ===== DAG 图反显 ===== */
function dagView(structure, selId) {
  if (!structure || !structure.nodes) return '<div class="empt">暂无图结构</div>';
  const { nodes, edges, node_w: W, node_h: H, width, height } = structure;
  const pos = {};
  nodes.forEach(n => (pos[n.id] = n));
  const edgeSvg = edges.map(e => {
    const A = pos[e.from], B = pos[e.to];
    if (!A || !B) return "";
    const x1 = A.x + W, y1 = A.y + H / 2, x2 = B.x, y2 = B.y + H / 2, mx = (x1 + x2) / 2;
    const d = "M " + x1 + "," + y1 + " C " + mx + "," + y1 + " " + mx + "," + y2 + " " + x2 + "," + y2;
    let s = '<path d="' + d + '" fill="none" stroke="#b9c6e6" stroke-width="1.6" marker-end="url(#ar)"/>';
    if (e.label) s += '<text x="' + mx + '" y="' + (y1 + y2) / 2 + '" font-size="9" fill="#8b98ae" text-anchor="middle">' + esc(e.label) + "</text>";
    return s;
  }).join("");
  const nodeHtml = nodes.map(n => {
    const meta = n.meta || "—";
    return '<div class="gnode ' + esc(n.type) + (selId === n.id ? " on" : "") + '" style="left:' + n.x + "px;top:" + n.y + 'px" data-act="dag.select" data-id="' + esc(n.id) + '">' +
      '<div class="gt">' + esc(n.type) + '</div><div class="gn">' + esc(n.name) + '</div><div class="gm">' + esc(n.id) + " · " + esc(meta) + "</div></div>";
  }).join("");
  return '<div class="dagcanvas"><div class="daginner" style="width:' + width + "px;height:" + height + 'px">' +
    '<svg class="edges" width="' + width + '" height="' + height + '"><defs><marker id="ar" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="#b9c6e6"/></marker></defs>' + edgeSvg + "</svg>" +
    nodeHtml + "</div></div>";
}

/* ================= 智能体 ================= */
P1["agents/list"] = async () => {
  const [rows, tools] = await Promise.all([api("/agents"), api("/tools")]);
  const cols = [
    { t: "智能体", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + " · " + esc(r.description) + "</div>" },
    { t: "版本", render: r => "v" + r.version },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "DAG", render: r => (r.dag_ok ? pill("校验通过", "ok") : pill(r.dag_errors.length + " 项错误", "err")) },
    { t: "节点数", render: r => r.node_count },
    { t: "运行次数", render: r => r.run_count },
    { t: "更新时间", render: r => fmtTime(r.updated_at) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("DAG", "agent.openDag", { id: r.id }, "sm") +
        btn("运行", "agent.openRun", { id: r.id }, "sm") +
        btn("发布", "agent.publish", { id: r.id }, "sm primary") +
        btn("删除", "agent.delete", { id: r.id, name: r.name }, "sm danger") + "</div>" }
  ];
  return head("智能体", "智能体 = DAG 流程 + 节点引用的工具 / 技能 / 模型；版本只增不改",
      btn("新建智能体", "agent.new", {}, "primary"))
    + grid(kpi("智能体", rows.length + " 个", "已发布 " + rows.filter(r => r.status === "published").length)
        + kpi("DAG 通过", rows.filter(r => r.dag_ok).length + " 个", "结构 + 依赖门禁均需通过")
        + kpi("累计运行", rows.reduce((a, b) => a + b.run_count, 0) + " 次", "可在运行记录中查看")
        + kpi("内置工具", tools.filter(t => t.builtin).length + " 个", "含知识库检索 / 报告渲染"))
    + grid(card("智能体清单", tbl(cols, rows), { cls: "col-12" }));
};

P1["agents/dag"] = async () => {
  const agents = await api("/agents");
  if (!agents.length) return head("DAG 导入", "暂无智能体") + grid(card("提示", '<div class="empt">请先在智能体列表创建</div>'));
  const id = state.agentId && agents.some(a => a.id === state.agentId) ? state.agentId : agents[0].id;
  state.agentId = id;
  const data = await api("/agents/" + id);
  const dagText = state.dagText != null && state.dagAgent === id ? state.dagText : JSON.stringify(data.dag, null, 2);
  const structure = state.dagStructure || data.structure;
  const errors = state.dagErrors || data.errors;
  const warnings = state.dagWarnings || data.warnings;
  const sel = (structure.nodes || []).find(n => n.id === state.dagSel);

  const editor = '<div class="toolbar">' +
      sel2("智能体", agents.map(a => [a.id, a.name + " v" + a.version]), id, 'data-change="dag.switch"') +
      "</div>" +
    '<textarea class="jsonedit" id="dagText" spellcheck="false">' + esc(dagText) + "</textarea>" +
    '<div class="toolbar" style="margin:10px 0 0">' +
      btn("格式化", "dag.format", {}, "sm") +
      btn("只校验", "dag.validate", {}, "sm") +
      btn("校验并保存", "dag.save", {}, "sm primary") +
      '<span class="sp"></span>' +
      (errors.length ? pill(errors.length + " 项错误", "err") : pill("校验通过", "ok")) +
    "</div>";

  const errBody = errors.length
    ? errors.map(e => '<div class="hrow"><span class="tag err">ERROR</span><span class="n" style="font-weight:500">' + esc(e) + "</span></div>").join("")
    : note("全部校验项通过：id 唯一 / 类型合法 / 边端点存在 / 无环路 / 全部可达 / 无孤立节点。");
  const warnBody = warnings.length
    ? warnings.map(w => '<div class="hrow"><span class="tag warn">WARN</span><span style="font-weight:500">' + esc(w) + "</span></div>").join("")
    : '<div class="empt" style="padding:12px">无警告</div>';

  const props = sel ? card("节点属性 · " + sel.id, kv([
    ["节点 id", '<span class="mono">' + esc(sel.id) + "</span>", true],
    ["类型", esc(sel.type)], ["名称", esc(sel.name)],
    ["绑定资产", esc(sel.tool || sel.skill || sel.model || "—")],
    ["层级", sel.level],
  ]), { cls: "col-5" }) : "";

  return head("DAG 导入与反显", "导入结构化 DAG → 后端校验 → 分层布局反显；不做拖拉拽编排",
      btn("运行该智能体", "agent.openRun", { id: id }, "primary"))
    + grid(card("DAG JSON", editor, { cls: "col-5", note: "校验通过后点「校验并保存」写入草稿；图结构由后端计算" }),
        card("图结构反显 · " + (data.agent.name || ""), dagView(structure, state.dagSel), { cls: "col-7" }))
    + grid(card("校验结果", errBody, { cls: "col-7" }), card("警告", warnBody, { cls: "col-5" }))
    + grid(props, card("编译说明", '<div class="srcline">DAG 由运行时编译为 LangGraph StateGraph：start 出边从 START 出发，end 映射为 END，条件分支按并行出边处理。</div>'
        + '<div class="srcline">节点类型：start / end / llm / tool / skill / condition / hitl；skill 节点按技能配置执行其只读工具并调用技能模型。</div>'
        + '<div class="srcline">tool 节点可写 <span class="mono">"params": {"q": "授信使用率"}</span>，覆盖报文顶层字段；知识库检索靠它传查询语句。</div>'
        + '<div class="srcline">校验 = 结构校验 + 依赖门禁：工具需已注册且启用，模型别名与其 Provider 可用，技能需已发布。</div>'
        + '<div class="srcline">校验不通过时不允许保存或运行，返回 AH-RUNTIME-422 与具体节点。</div>', { cls: "col-7" }));
};

P1["agents/runs"] = async () => {
  const runs = await api("/runs");
  if (!runs.length) return head("运行记录", "暂无运行") + grid(card("运行记录", '<div class="empt">去「运行调试」发起一次运行</div>'));
  const id = state.runId && runs.some(r => r.id === state.runId) ? state.runId : runs[0].id;
  state.runId = id;
  const detail = await api("/runs/" + id);
  const list = runs.map(r => '<div class="hrow" style="cursor:pointer" data-act="run.select" data-id="' + esc(r.id) + '">' +
      '<div><div class="n mono">' + esc(r.id) + (r.id === id ? ' <span class="tag info">当前</span>' : "") + "</div>" +
      '<div class="s">' + esc(r.agent) + " v" + r.v + " · " + esc(r.customer) + " · " + esc(r.start) + "</div></div>" +
      '<span class="r">' + statusPill(r.status) + "</span></div>").join("");

  const events = (detail.events || []).map((e, i) => ({
    h: e.event, t: e.time,
    b: '<span class="mono">' + esc(JSON.stringify(e.data)) + "</span>",
    tone: /failed|waiting/.test(e.event) ? "err" : (i === detail.events.length - 1 ? "ok" : ""),
  }));
  const cps = tbl([
    { t: "checkpoint", render: c => '<span class="mono">' + esc(c.id) + "</span>" },
    { t: "step", render: c => c.step }, { t: "节点", render: c => esc(c.node) },
    { t: "时间", render: c => esc(c.at) },
    { t: "操作", render: c => btn("重放", "run.replay", { id: c.id }, "sm") },
  ], detail.checkpoints || []);

  const actions = detail.status === "WAITING_APPROVAL"
    ? note("HITL 中断：等待人工审批（AH-RUNTIME-206），审批后从 Checkpoint 恢复。", "warn") +
      btn("批准并继续", "run.approve", { id: id }, "primary") + btn("拒绝", "run.reject", { id: id }, "danger")
    : "";

  const ev = (detail.output && detail.output.evidence) || null;
  const evMap = {};
  if (ev) (ev.items || []).forEach(x => { evMap[x.id] = x; });
  const evTone = { tool: "info", kb: "ok", skill: "warn", model: "mute", error: "err" };
  const evidenceHtml = ev && (ev.claims || []).length
    ? '<div class="toolbar">' + pill((ev.claims || []).length + " 条结论", "info")
      + pill((ev.items || []).length + " 条证据", "ok")
      + '<span class="s">证据来源：工具输出 / 知识库切片 / 技能脚本 / 模型调用</span></div>'
      + (ev.claims || []).map((c, idx) => {
          const refs = (c.evidence || []).map(eid => evMap[eid]).filter(Boolean);
          return '<div class="hrow" style="align-items:flex-start"><div style="flex:1">' +
            '<div class="n">' + (idx + 1) + ". " + esc(c.text) + "</div>" +
            (refs.length ? refs.map(e => '<div class="s" style="margin-top:5px;line-height:1.7">' +
                pill(e.kind_label || e.kind, evTone[e.kind] || "mute") + " <b>" + esc(e.title) + "</b>" +
                (e.node ? ' <span class="s">· ' + esc(e.node) + "</span>" : "") +
                '<div style="margin-top:2px">' + esc((e.detail || "").slice(0, 220)) + "</div></div>").join("")
              : '<div class="s" style="margin-top:4px">未匹配到证据</div>') +
            "</div></div>";
        }).join("")
    : '<div class="empt">本次运行没有证据链（旧运行或未生成结论）</div>';

  return head("运行记录", "Run 状态机、证据链、事件流、Checkpoint 与人工审批")
    + grid(card("运行列表", list, { cls: "col-4" }),
        card("详情 · " + id, actions + kv([
          ["状态", statusPill(detail.status), true],
          ["智能体", esc(detail.agent) + " v" + detail.v],
          ["客户", esc(detail.customer)],
          ["trace_id", '<span class="mono">' + esc(detail.trace_id) + "</span>", true],
          ["thread_id", '<span class="mono">' + esc(detail.thread_id) + "</span>", true],
          ["耗时 / tokens", (detail.dur_ms / 1000).toFixed(2) + " s / " + num(detail.tokens), true],
          ["幂等键", detail.idempotency_key ? '<span class="mono">' + esc(detail.idempotency_key) + "</span>" : "—"],
          ["报告", detail.report_path ? '<a class="tag info" target="_blank" href="/' + esc(detail.report_path) + '">打开 HTML 报告</a>' : "—", true],
          ["错误", esc(detail.error || "—")]
        ]), { cls: "col-8" }))
    + grid(card("结论与证据链", evidenceHtml, { cls: "col-12" }))
    + grid(card("事件时间轴", events.length ? '<div class="timeline">' + events.map(e =>
        '<div class="tl ' + (e.tone || "") + '"><div class="tl-h"><b class="mono">' + esc(e.h) + '</b><span class="t">' + esc(e.t) + '</span></div><div class="tl-b">' + e.b + "</div></div>").join("") + "</div>"
        : '<div class="empt">暂无事件</div>', { cls: "col-7" }),
      card("Checkpoint · 时间旅行", cps, { cls: "col-5" }));
};

P1["agents/playground"] = async () => {
  const agents = await api("/agents");
  const id = state.agentId || agents[0].id;
  const normal = JSON.stringify({
    credit: { msg_no: "CR-20261120-0031", total_limit: 8000000, used_limit: 6200000, overdue_30d: 1, query_3m: 11 },
    finance: { msg_no: "FR-20261120-0031", revenue: 126000000, net_profit: -3200000, total_assets: 210000000, total_liability: 168000000 },
    judicial: { msg_no: "JD-20261120-0031", cases: 2, executed: 0, dishonest: 0 }
  }, null, 2);
  const risky = JSON.stringify({
    credit: { msg_no: "CR-20261120-0032", total_limit: 5000000, used_limit: 4800000, overdue_30d: 3, query_3m: 14 },
    finance: { msg_no: "FR-20261120-0032", revenue: 62000000, net_profit: -8600000, total_assets: 120000000, total_liability: 108000000 },
    judicial: { msg_no: "JD-20261120-0032", cases: 3, executed: 2, dishonest: 1 }
  }, null, 2);
  const form = '<form data-form="run.create">' +
    '<div class="frow">' + fld("智能体", sel("agent_id", agents.map(a => [a.id, a.name + " v" + a.version]), id)) +
    fld("客户编号", inp("customer_no", "C-2026-0031")) + "</div>" +
    fld("报文（JSON）", '<textarea class="ta mono" name="payload" style="min-height:230px">' + esc(state.payloadText || normal) + "</textarea>",
      "三类报文可缺省：缺哪类就不执行对应解析节点") +
    '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">' +
    '<button type="submit" class="btn primary">发起运行</button>' +
    btn("填入高风险报文", "pg.risky", { json: risky }, "sm") +
    btn("填入普通报文", "pg.normal", { json: normal }, "sm") +
    "</div></form>";

  return head("运行调试", "提交报文触发一次真实运行，事件通过 SSE 实时返回")
    + grid(card("运行输入", form, { cls: "col-5" }),
        card("运行事件", '<div id="pg-events" class="timeline"><div class="tl"><div class="tl-h"><b>等待运行</b></div>' +
          '<div class="tl-b">事件：run.started / node.start / tool.call / tool.result / message.delta / run.completed</div></div></div>', { cls: "col-7" }))
    + '<div id="pg-result">' + grid(
        card("运行结果", '<div class="empt">运行完成后显示状态、耗时与报告入口</div>', { cls: "col-12" })) + "</div>";
};

/* ================= 技能 ================= */
P1["skills/list"] = async () => {
  const [rows, tools] = await Promise.all([api("/skills"), api("/tools")]);
  const published = rows.filter(r => r.status === "published");
  const refTotal = rows.reduce((a, b) => a + (b.refs || 0), 0);
  const cols = [
    { t: "技能", render: r => "<b>" + esc(r.name) + '</b><div class="s">' + esc(r.id) + " · " + esc(r.description) + "</div>" },
    { t: "分类", render: r => pill(r.category, r.category === "业务" ? "info" : "mute") },
    { t: "版本", render: r => "v" + r.version },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "SKILL.md / 脚本", render: r => (r.content ? pill(r.content.length + " 字", "info") : '<span class="s">未填写</span>')
        + ((r.scripts || []).length ? " " + pill((r.scripts || []).length + " 脚本", "ok") : "") },
    { t: "模型", render: r => esc(r.model) },
    { t: "工具", render: r => r.tools.join("、") || "—" },
    { t: "引用", render: r => (r.refs ? pill(r.refs + " 个智能体", "info") : '<span class="s">—</span>') },
    { t: "操作", render: r => '<div class="ops">' +
        btn("详情", "skill.detail", { id: r.id }, "sm") +
        btn("编辑", "skill.edit", { id: r.id }, "sm") +
        btn("历史", "skill.versions", { id: r.id }, "sm") +
        btn("发布", "skill.publish", { id: r.id }, "sm primary") +
        (r.status === "published" ? "" : btn("删除", "skill.delete", { id: r.id, name: r.name }, "sm danger")) + "</div>" }
  ];
  return head("技能 Skill", "技能包 = SKILL.md + 脚本 + 工具 + 模型别名；DAG 的 skill 节点可直接执行",
      btn("新建技能", "skill.new", {}, "primary"))
    + grid(kpi("技能", rows.length + " 个", "已发布 " + published.length)
        + kpi("被智能体引用", refTotal + " 次", "skill 节点 + 绑定工具")
        + kpi("含脚本技能", rows.filter(r => (r.scripts || []).length).length + " 个", "沙箱执行 Python")
        + kpi("可发布率", pct(rows.length ? published.length / rows.length : 0), "草稿需补齐 SKILL.md / 工具 / 别名"))
    + grid(card("技能清单", tbl(cols, rows), { cls: "col-12" }))
    + grid(card("字段口径", '<div class="srcline">技能包对标 Agent Skills：SKILL.md（frontmatter + 说明书）+ scripts/ + allowed-tools。</div>'
        + '<div class="srcline">DAG 的 skill 节点执行顺序：只读工具 → 沙箱脚本 → 技能模型判读；跳过 fs:write 与 LLM 类工具。</div>'
        + '<div class="srcline">发布前校验：SKILL.md 非空、工具已注册、模型别名存在、Python 脚本通过 AST 白名单。</div>'
        + '<div class="srcline">脚本在独立进程中执行，可用 payload / features / customer_no / params，用 emit({...}) 输出 JSON。</div>', { cls: "col-6" }),
      card("当前工具覆盖", '<div class="srcline">' + tools.map(t => esc(t.name) + (t.status === "启用" ? "" : "（" + esc(t.status) + "）")).join(" · ") + "</div>", { cls: "col-6" }));
};

/* ================= 工具 ================= */
P1["tools/list"] = async () => {
  const rows = await api("/tools");
  const enabled = rows.filter(r => r.status === "启用");
  const builtin = rows.filter(r => r.builtin);
  const cols = [
    { t: "工具", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + (r.endpoint ? " · " + esc(r.method + " " + r.endpoint) : "") + "</div>" },
    { t: "来源", render: r => pill(r.source, r.source === "内置" ? "info" : "warn") },
    { t: "实现", render: r => esc(r.impl) },
    { t: "权限", render: r => '<span class="mono">' + esc(r.permission) + "</span>" },
    { t: "超时", render: r => r.timeout_s + " s" },
    { t: "引用", render: r => (r.refs ? pill(r.refs + " 处", "info") : '<span class="s">—</span>') },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("调用测试", "tool.invoke", { id: r.id }, "sm") +
        btn("Schema", "tool.schema", { id: r.id }, "sm") +
        btn("编辑", "tool.edit", { id: r.id }, "sm") +
        btn(r.status === "启用" ? "停用" : "启用", "tool.toggle", { id: r.id, status: r.status === "启用" ? "停用" : "启用" }, "sm") +
        (r.builtin ? "" : btn("删除", "tool.delete", { id: r.id, name: r.name }, "sm danger")) + "</div>" }
  ];
  return head("工具清单", "统一 Tool 接口：内置函数 / HTTP / Jinja2，注册后可直接在 DAG 节点与控制台调用",
      btn("注册工具", "tool.new", {}, "primary"))
    + grid(kpi("工具", rows.length + " 个", "启用 " + enabled.length)
        + kpi("内置工具", builtin.length + " 个", "随服务发布，不可删除")
        + kpi("外部工具", (rows.length - builtin.length) + " 个", "HTTP / Jinja2 / MCP")
        + kpi("被引用", rows.reduce((a, b) => a + (b.refs || 0), 0) + " 处", "DAG 节点 + 技能绑定"))
    + grid(card("工具清单", tbl(cols, rows), { cls: "col-12" }))
    + grid(card("调用方式", code('POST /api/v1/tools/{tool_id}/invoke\n{\n  "params": {"customer_no": "C-2026-0031"}\n}'), { cls: "col-6" }),
      card("执行口径", '<div class="srcline">调用前按参数 Schema 校验 required / type / enum，失败返回 AH-TOOL-422。</div>'
        + '<div class="srcline">HTTP 工具按 endpoint + method + headers 发起请求，超时按 timeout_s 中断（AH-TOOL-504）。</div>'
        + '<div class="srcline">Jinja2 工具用 endpoint 作为模板渲染，适合报文拼装与轻量转换。</div>'
        + '<div class="srcline">删除前会检查引用；内置工具只能停用，不能删除。</div>', { cls: "col-6" }));
};

P1["tools/calls"] = async () => {
  const [tools, rows] = await Promise.all([api("/tools"), api("/tools/calls" + toolCallQuery())]);
  const cols = [
    { t: "时间", render: r => fmtTime(r.time) },
    { t: "工具", render: r => esc(r.tool) },
    { t: "trace_id", render: r => '<span class="mono">' + esc(r.trace || "—") + "</span>" },
    { t: "run_id", render: r => '<span class="mono">' + esc(r.run_id || "—") + "</span>" },
    { t: "参数", render: r => '<span class="mono">' + esc(r.params) + "</span>" },
    { t: "耗时", render: r => r.dur_ms + " ms" },
    { t: "结果", render: r => statusPill(r.status) },
    { t: "错误", render: r => esc(r.error || "—") }
  ];
  const ok = rows.filter(r => r.status === "成功").length;
  const avg = rows.length ? Math.round(rows.reduce((a, b) => a + (b.dur_ms || 0), 0) / rows.length) : 0;
  const filter = '<div class="toolbar">' +
    fld("工具", sel2("tool", [["", "全部工具"]].concat(tools.map(t => [t.name, t.name])), state.toolCallTool || "", 'data-change="toolcalls.filter"')) +
    fld("结果", sel2("status", ["全部", "成功", "失败"], state.toolCallStatus || "全部", 'data-change="toolcalls.filter"')) +
    btn("清空筛选", "toolcalls.clear", {}, "sm") + "</div>";
  return head("调用记录", "按工具 / 结果筛选，并可沿 trace_id 回溯每次调用")
    + grid(kpi("本次查询", rows.length + " 条", "最多展示 200 条")
        + kpi("成功", ok + " 条", "失败 " + (rows.length - ok) + " 条")
        + kpi("平均耗时", avg + " ms", "按当前筛选结果")
        + kpi("覆盖工具", new Set(rows.map(r => r.tool)).size + " 个", "不同工具数"))
    + grid(card("筛选", filter, { cls: "col-12", tight: false })
        + card("调用记录", tbl(cols, rows, { empty: "暂无工具调用" }), { cls: "col-12" }));
};

function toolCallQuery() {
  const parts = [];
  if (state.toolCallTool) parts.push("tool=" + encodeURIComponent(state.toolCallTool));
  if (state.toolCallStatus && state.toolCallStatus !== "全部") parts.push("status=" + encodeURIComponent(state.toolCallStatus));
  parts.push("limit=200");
  return "?" + parts.join("&");
}

/* ===== 动作：智能体 ===== */
A1["agent.new"] = () => modalForm("新建智能体", "agent.create",
  fld("名称", inp("name", "")) + fld("描述", inp("description", ""))
  + note("新建时默认写入示例 DAG（客户全景分析流程），创建后可在 DAG 页修改。"), "创建");

A1["agent.create"] = async (form) => {
  const body = formObj(form);
  try {
    const agent = await api("/agents", { method: "POST", body });
    closeModal(); toast("已创建", agent.name + " · v1"); go("agents/dag", { agentId: agent.id });
  } catch (e) { toast("创建失败", e.message, "err"); }
};

A1["agent.openDag"] = d => go("agents/dag", { agentId: d.id });
A1["agent.openRun"] = d => go("agents/playground", { agentId: d.id });

A1["agent.publish"] = async d => {
  try {
    const res = await api("/agents/" + d.id + "/publish", { method: "POST" });
    toast("已发布", "v" + res.version); render();
  } catch (e) { toast("发布失败", e.message, "err"); }
};
A1["agent.delete"] = d => confirmBox("删除智能体",
  "确认删除「" + d.name + "」？历史运行记录会保留，但智能体与版本配置会移除。存在在途或待审批运行时会拒绝删除。",
  "agent.doDelete", { id: d.id }, "删除");
A1["agent.doDelete"] = async d => {
  try {
    await api("/agents/" + d.id, { method: "DELETE" });
    closeModal();
    if (state.agentId === d.id) state.agentId = null;
    toast("已删除", d.id, "warn");
    render();
  } catch (e) { toast("删除失败", e.message, "err"); }
};

A1["dag.switch"] = (d, el) => {
  state.agentId = el.value;
  state.dagText = null; state.dagStructure = null; state.dagErrors = null; state.dagWarnings = null; state.dagSel = null;
  render();
};
A1["dag.select"] = d => { state.dagSel = d.id; render(); };

A1["dag.format"] = () => {
  const ta = $("#dagText");
  if (!ta) return;
  try { state.dagText = JSON.stringify(JSON.parse(ta.value), null, 2); }
  catch (e) { toast("JSON 解析失败", e.message, "err"); return; }
  render();
};

A1["dag.validate"] = async () => {
  const ta = $("#dagText");
  if (!ta) return;
  state.dagText = ta.value;
  let parsed;
  try { parsed = JSON.parse(ta.value); }
  catch (e) { toast("JSON 解析失败", e.message, "err"); state.dagErrors = ["JSON 解析失败：" + e.message]; render(); return; }
  try {
    const res = await api("/agents/" + state.agentId + "/dag/validate", { method: "POST", body: { dag: parsed } });
    state.dagErrors = res.errors; state.dagWarnings = res.warnings;
    if (res.ok) state.dagStructure = res.structure;
    render();
    toast(res.ok ? "校验通过" : "校验未通过", res.ok ? res.structure.nodes.length + " 个节点" : res.errors.length + " 项错误", res.ok ? "ok" : "err");
  } catch (e) { toast("校验失败", e.message, "err"); }
};

A1["dag.save"] = async () => {
  const ta = $("#dagText");
  if (!ta) return;
  state.dagText = ta.value;
  let parsed;
  try { parsed = JSON.parse(ta.value); }
  catch (e) { toast("JSON 解析失败", e.message, "err"); return; }
  try {
    const res = await api("/agents/" + state.agentId + "/dag", { method: "POST", body: { dag: parsed, publish: false } });
    state.dagStructure = res.structure; state.dagErrors = []; state.dagWarnings = res.warnings;
    render(); toast("已保存草稿", "版本 v" + res.version);
  } catch (e) { toast("保存失败", e.message, "err"); state.dagErrors = (e.payload && e.payload.errors) || [e.message]; render(); }
};

/* ===== 动作：运行 ===== */
A1["pg.normal"] = d => { state.payloadText = d.json; render(); };
A1["pg.risky"] = d => { state.payloadText = d.json; render(); };

A1["run.create"] = async (form) => {
  const o = formObj(form);
  let payload;
  try { payload = JSON.parse(o.payload || "{}"); }
  catch (e) { toast("报文不是合法 JSON", e.message, "err"); return; }
  state.payloadText = o.payload;
  const box = $("#pg-events");
  if (box) box.innerHTML = '<div class="tl"><div class="tl-h"><b>正在提交…</b></div></div>';
  try {
    const run = await api("/runs", { method: "POST", body: { agent_id: o.agent_id, customer_no: o.customer_no, payload, async_mode: true } });
    state.runId = run.id;
    subscribe(run.id);
  } catch (e) {
    toast("运行失败", e.message, "err");
    if (box) box.innerHTML = '<div class="empt">' + esc(e.message) + "</div>";
  }
};

const SSE_EVENTS = ["run.started", "node.start", "node.result", "tool.call", "tool.result",
  "message.delta", "state.checkpoint", "run.completed", "run.waiting_approval",
  "run.failed", "run.notice", "approvals.decision", "run.rejected", "stream.closed"];

function subscribe(runId) {
  const box = $("#pg-events");
  const result = $("#pg-result");
  if (!box) return;
  box.innerHTML = "";
  if (window.__es) { try { window.__es.close(); } catch (e) {} }
  const token = apiKey();
  const sseUrl = API_BASE + "/runs/" + runId + "/events" + (token ? "?access_token=" + encodeURIComponent(token) : "");
  const es = new EventSource(sseUrl);
  window.__es = es;
  const append = (name, data) => {
    const line = document.createElement("div");
    line.className = "tl " + (/completed/.test(name) ? "ok" : /failed|waiting/.test(name) ? "err" : "");
    line.innerHTML = '<div class="tl-h"><b class="mono">' + esc(name) + '</b><span class="t">' + new Date().toLocaleTimeString("zh-CN", { hour12: false }) + "</span></div>" +
      '<div class="tl-b mono">' + esc(JSON.stringify(data)) + "</div>";
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  };
  SSE_EVENTS.forEach(name => {
    es.addEventListener(name, ev => {
      let payload = {};
      try { payload = JSON.parse(ev.data); } catch (e) {}
      append(name, payload.data != null ? payload.data : payload);
      if (name === "run.completed" || name === "run.failed" || name === "run.waiting_approval" || name === "stream.closed") {
        es.close(); window.__es = null;
        finishRun(runId, name, result);
      }
    });
  });
  es.onerror = () => { es.close(); window.__es = null; };
}

async function finishRun(runId, lastEvent, resultBox) {
  try {
    const run = await api("/runs/" + runId);
    if (!resultBox) return;
    const tone = run.status === "COMPLETED" ? "ok" : run.status === "WAITING_APPROVAL" ? "warn" : "err";
    resultBox.innerHTML = grid(card("运行结果",
      kv([
        ["状态", statusPill(run.status), true],
        ["耗时", (run.dur_ms / 1000).toFixed(2) + " s"],
        ["tokens", num(run.tokens)],
        ["trace_id", '<span class="mono">' + esc(run.trace_id) + "</span>", true],
        ["报告", run.report_path ? '<a class="tag info" target="_blank" href="/' + esc(run.report_path) + '">打开 HTML 全景报告</a>' : "—", true],
        ["摘要", esc((run.output && run.output.summary) || run.error || "—")]
      ])
      + (run.status === "WAITING_APPROVAL" ? note("命中高风险客户，需人工审批。前往「运行记录」处理。", "warn") +
          btn("去审批", "nav.to", { p: "agents/runs" }, "primary") : ""), { cls: "col-12" }));
    toast("运行结束", run.status, tone);
  } catch (e) { toast("查询运行结果失败", e.message, "err"); }
}

A1["run.select"] = d => { state.runId = d.id; render(); };
A1["run.approve"] = async d => {
  try {
    await api("/runs/" + d.id + "/approve", { method: "POST", body: { decision: "APPROVED", comment: "控制台审批" } });
    toast("已批准", "从 Checkpoint 恢复执行"); render();
  } catch (e) { toast("审批失败", e.message, "err"); }
};
A1["run.reject"] = async d => {
  try {
    await api("/runs/" + d.id + "/approve", { method: "POST", body: { decision: "REJECTED", comment: "控制台拒绝" } });
    toast("已拒绝", "运行状态 REJECTED", "warn"); render();
  } catch (e) { toast("操作失败", e.message, "err"); }
};
A1["run.replay"] = async d => {
  try {
    const run = await api("/runs/" + state.runId + "/replay", { method: "POST", body: { checkpoint_id: d.id } });
    toast("已从 Checkpoint 重放", "新运行 " + run.id); go("agents/runs", { runId: run.id });
  } catch (e) { toast("重放失败", e.message, "err"); }
};

/* ===== 动作：技能 ===== */
async function skillToolHint() {
  const tools = await api("/tools");
  return tools.map(t => t.name).join(" / ");
}
async function aliasOptionList() {
  const aliases = await api("/aliases");
  return aliases.length ? aliases.map(a => a.alias) : ["default-chat"];
}

const DEFAULT_SKILL_MD = `---
name: custom-skill
description: 一句话说明这个技能在什么场景下使用
model: default-chat
allowed-tools: 征信报文解析
---

# 自定义技能

## 何时使用
描述触发条件，以及不适用的场景。

## 工作步骤
1. 解析输入报文，缺失字段按缺失处理。
2. 运行 scripts/main.py 复核关键指标。
3. 输出不超过 120 字的结论。

## 输出约定
- 脚本输出：JSON {"features": {...}}
- 模型输出：summary 一段话

## 注意事项
- 不臆造报文里没有的字段。
`;

function skillScriptFields(s = {}) {
  const scripts = s.scripts || [];
  const main = scripts.find(x => x.name === s.entrypoint) || scripts[0] || {};
  const rest = scripts.filter(x => x !== main);
  return fld("SKILL.md（frontmatter + 说明书）",
      '<textarea class="ta mono" name="content" style="min-height:230px">' + esc(s.content || DEFAULT_SKILL_MD) + "</textarea>",
      "YAML frontmatter + 何时使用 / 步骤 / 输出约定 / 注意事项")
    + '<div class="frow">' + fld("入口脚本名", inp("entrypoint", s.entrypoint || main.name || ""))
    + fld("脚本语言", sel("script_language", ["python"], main.language || "python")) + "</div>"
    + fld("主脚本名", inp("script_name", main.name || ""))
    + fld("主脚本代码（Python）",
      '<textarea class="ta mono" name="script_code" style="min-height:190px">' + esc(main.code || "") + "</textarea>",
      "可用 payload / features / customer_no / params，用 emit({...}) 输出 JSON")
    + '<input type="hidden" name="other_scripts" value="' + esc(JSON.stringify(rest)) + '">';
}

function collectSkillScripts(o) {
  let rest = [];
  try { rest = JSON.parse(o.other_scripts || "[]"); } catch (e) { rest = []; }
  const name = (o.script_name || "").trim();
  const code = o.script_code || "";
  const scripts = (name && code.trim())
    ? [{ name: name, language: o.script_language || "python", code: code }] : [];
  return scripts.concat(Array.isArray(rest) ? rest : []);
}

A1["skill.new"] = async () => {
  const [aliases, toolHint] = await Promise.all([aliasOptionList(), skillToolHint()]);
  modalForm("新建技能", "skill.create",
    fld("名称", inp("name", ""))
    + '<div class="frow">' + fld("分类", sel("category", ["业务", "内置", "实验"], "业务")) + fld("模型别名", sel("model", aliases, aliases[0])) + "</div>"
    + fld("说明", inp("description", ""))
    + fld("Prompt 模板标识", inp("prompt", "custom@v1"))
    + fld("绑定工具（逗号分隔）", inp("tools", "征信报文解析"), toolHint)
    + '<div class="frow">' + fld("超时（秒）", inp("timeout_s", "30", 'type="number"')) + fld("重试", inp("retry", "1", 'type="number"')) + "</div>"
    + skillScriptFields(),
    "创建");
};
A1["skill.create"] = async form => {
  const o = formObj(form);
  try {
    await api("/skills", {
      method: "POST",
      body: {
        name: o.name, category: o.category, prompt: o.prompt, model: o.model,
        description: o.description || "",
        tools: (o.tools || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
        content: o.content || "", scripts: collectSkillScripts(o),
        entrypoint: (o.entrypoint || "").trim(),
        timeout_s: Number(o.timeout_s) || 30, retry: Number(o.retry) || 0,
      },
    });
    closeModal(); toast("已创建", "草稿 v1"); render();
  } catch (e) { toast("创建失败", e.message, "err"); }
};
A1["skill.edit"] = async d => {
  const [s, aliases] = await Promise.all([api("/skills/" + d.id), aliasOptionList()]);
  modalForm("编辑技能 · " + s.name, "skill.save",
    '<input type="hidden" name="id" value="' + esc(s.id) + '">'
    + fld("名称", inp("name", s.name))
    + '<div class="frow">' + fld("分类", sel("category", ["业务", "内置", "实验"], s.category)) + fld("模型别名", sel("model", aliases, s.model)) + "</div>"
    + fld("说明", inp("description", s.description))
    + fld("Prompt 模板标识", inp("prompt", s.prompt))
    + fld("绑定工具（逗号分隔）", inp("tools", s.tools.join(", ")))
    + '<div class="frow">' + fld("超时（秒）", inp("timeout_s", s.timeout_s, 'type="number"')) + fld("重试", inp("retry", s.retry, 'type="number"')) + "</div>"
    + skillScriptFields(s),
    "保存");
};
A1["skill.save"] = async form => {
  const o = formObj(form);
  try {
    await api("/skills/" + o.id, {
      method: "PATCH",
      body: {
        name: o.name, category: o.category, description: o.description,
        prompt: o.prompt, model: o.model,
        tools: (o.tools || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
        content: o.content || "", scripts: collectSkillScripts(o),
        entrypoint: (o.entrypoint || "").trim(),
        timeout_s: Number(o.timeout_s) || 30, retry: Number(o.retry) || 0,
      },
    });
    closeModal(); toast("已保存", "下次发布生效"); render();
  } catch (e) { toast("保存失败", e.message, "err"); }
};
A1["skill.detail"] = async d => {
  const s = await api("/skills/" + d.id);
  const scripts = s.scripts || [];
  const meta = kv([
    ["编号", '<span class="mono">' + esc(s.id) + "</span>", true],
    ["状态", statusPill(s.status) + " · v" + s.version, true],
    ["模型 / 工具", esc(s.model) + " · " + esc((s.tools || []).join("、") || "—")],
    ["超时 / 重试", s.timeout_s + " s / " + s.retry],
    ["脚本", scripts.length + " 个 · 入口 " + esc(s.entrypoint || "—")],
  ]);
  const scriptHtml = scripts.length
    ? scripts.map(x => '<div class="hrow"><div><div class="n mono">' + esc(x.name) + '</div>' +
        '<div class="s">' + esc(x.language) + " · " + num((x.code || "").length) + " 字符</div></div></div>" +
        code(esc(x.code || ""))).join("")
    : '<div class="empt">该技能没有脚本，仅使用工具 + 模型</div>';
  modal("技能详情 · " + s.name,
    meta
    + '<h3 style="margin:14px 0 8px;font-size:13px">SKILL.md</h3>' + code(esc(s.content || "（未填写）"))
    + '<h3 style="margin:14px 0 8px;font-size:13px">脚本包</h3>' + scriptHtml,
    btn("关闭", "modal.close", {}, "") +
    (scripts.length ? btn("试运行脚本", "skill.test", { id: s.id }, "primary") : ""));
};
A1["skill.test"] = async d => {
  const s = await api("/skills/" + d.id);
  const scripts = s.scripts || [];
  if (!scripts.length) { toast("没有可执行脚本", "先在编辑中添加脚本", "warn"); return; }
  const sample = {
    payload: { credit: { msg_no: "CR-TEST", total_limit: 8000000, used_limit: 6200000,
                         overdue_30d: 2, query_3m: 11 } },
    features: {}, customer_no: "C-TEST",
  };
  modal("试运行脚本 · " + s.name,
    note("脚本在独立进程沙箱中执行；可用 payload / features / customer_no / params，用 emit({...}) 输出 JSON。")
    + fld("脚本", sel2("script", scripts.map(x => [x.name, x.name]),
        s.entrypoint || scripts[0].name, 'id="skill-script"'))
    + fld("输入（JSON）", '<textarea class="ta mono" id="skill-input" style="min-height:170px">'
        + esc(JSON.stringify(sample, null, 2)) + "</textarea>")
    + '<div id="skill-test-result"></div>',
    btn("关闭", "modal.close", {}, "") + btn("运行", "skill.doTest", { id: s.id }, "primary"));
};
A1["skill.doTest"] = async d => {
  let input;
  try { input = JSON.parse($("#skill-input").value || "{}"); }
  catch (e) { toast("输入不是合法 JSON", e.message, "err"); return; }
  const box = $("#skill-test-result");
  box.innerHTML = note("执行中…");
  try {
    const res = await api("/skills/" + d.id + "/test", {
      method: "POST", body: { input: input, script: $("#skill-script").value },
    });
    box.innerHTML = kv([
      ["执行", res.ok ? pill("成功", "ok") : pill("失败", "err"), true],
      ["退出码", res.exit_code == null ? "—" : res.exit_code],
      ["耗时", res.dur_ms + " ms"],
    ])
      + ((res.errors || []).length ? note(esc(res.errors.join("；")), "err") : "")
      + '<div class="s" style="margin:8px 0 4px">stdout</div>' + code(esc(res.stdout || "（空）"))
      + (res.stderr ? '<div class="s" style="margin:8px 0 4px">stderr</div>' + code(esc(res.stderr)) : "")
      + (res.result ? '<div class="s" style="margin:8px 0 4px">解析结果</div>'
          + code(esc(JSON.stringify(res.result, null, 2))) : "");
  } catch (e) { box.innerHTML = note(esc(e.message), "err"); }
};
A1["skill.publish"] = async d => {
  try {
    const res = await api("/skills/" + d.id + "/publish", { method: "POST" });
    toast("已发布", "v" + res.version); render();
  } catch (e) { toast("发布失败", e.message, "err"); }
};
A1["skill.versions"] = async d => {
  const rows = await api("/skills/" + d.id + "/versions");
  modal("版本历史 · " + d.id, tbl([
    { t: "版本", render: r => "v" + r.version },
    { t: "触发", render: r => esc(r.trigger) },
    { t: "门禁", render: r => statusPill(r.gate) },
    { t: "变更", render: r => esc(r.changelog) },
    { t: "时间", render: r => fmtTime(r.time) },
  ], rows, { empty: "暂无版本记录" }), btn("关闭", "modal.close", {}, "primary"));
};
A1["skill.delete"] = d => confirmBox("删除技能", "确认删除「" + d.name + "」？版本历史会一并移除。",
  "skill.doDelete", { id: d.id }, "删除");
A1["skill.doDelete"] = async d => {
  try {
    await api("/skills/" + d.id, { method: "DELETE" });
    closeModal(); toast("已删除", d.id, "warn"); render();
  } catch (e) { toast("删除失败", e.message, "err"); }
};

/* ===== 动作：工具 ===== */
function toolFormFields(t = {}) {
  const lock = t.builtin ? 'readonly title="内置工具名称不可修改"' : "";
  return fld("名称", inp("name", t.name || "", lock))
    + '<div class="frow">' + fld("来源", sel("source", ["内置", "MCP", "自定义"], t.source || "自定义"))
    + fld("实现", sel("impl", ["HTTP", "Jinja2", "Python"], t.impl || "HTTP")) + "</div>"
    + '<div class="frow">' + fld("权限标签", inp("permission", t.permission || "biz:read"))
    + fld("超时（秒）", inp("timeout_s", t.timeout_s || 10, 'type="number"')) + "</div>"
    + '<div class="frow">' + fld("HTTP 方法", sel("method", ["POST", "GET", "PUT", "PATCH", "DELETE"], t.method || "POST"))
    + fld("幂等", sel("idempotency", [["0", "否"], ["1", "是"]], t.idempotency ? "1" : "0")) + "</div>"
    + fld("endpoint / 模板", inp("endpoint", t.endpoint || ""), "HTTP 填完整地址；Jinja2 填模板，例如 {\"no\": \"{{ params.customer_no }}\"}")
    + fld("请求头（JSON）", '<textarea class="ta mono" name="headers" style="min-height:60px">' + esc(t.headers || "{}") + "</textarea>")
    + fld("参数 Schema（JSON）", '<textarea class="ta mono" name="params_schema" style="min-height:110px">' + esc(t.schema || '{"type":"object","properties":{}}') + "</textarea>");
}
A1["tool.new"] = () => modalForm("注册工具", "tool.create", toolFormFields(), "注册");
A1["tool.create"] = async form => {
  const o = formObj(form);
  try {
    await api("/tools", {
      method: "POST",
      body: { name: o.name, source: o.source, impl: o.impl, permission: o.permission,
              timeout_s: Number(o.timeout_s) || 10, idempotency: o.idempotency === "1",
              params_schema: o.params_schema, endpoint: o.endpoint, method: o.method, headers: o.headers },
    });
    closeModal(); toast("已注册", o.name); render();
  } catch (e) { toast("注册失败", e.message, "err"); }
};
A1["tool.edit"] = async d => {
  const t = (await api("/tools")).find(x => x.id === d.id);
  if (!t) { toast("工具不存在", d.id, "err"); return; }
  modalForm("编辑工具 · " + t.name, "tool.save",
    '<input type="hidden" name="id" value="' + esc(t.id) + '">' + toolFormFields(t), "保存");
};
A1["tool.save"] = async form => {
  const o = formObj(form);
  try {
    await api("/tools/" + o.id, {
      method: "PATCH",
      body: { name: o.name, source: o.source, impl: o.impl, permission: o.permission,
              timeout_s: Number(o.timeout_s) || 10, idempotency: o.idempotency === "1",
              params_schema: o.params_schema, endpoint: o.endpoint, method: o.method, headers: o.headers },
    });
    closeModal(); toast("已保存", o.name); render();
  } catch (e) { toast("保存失败", e.message, "err"); }
};
A1["tool.schema"] = async d => {
  const t = (await api("/tools")).find(x => x.id === d.id);
  if (!t) return;
  modal("参数 Schema · " + t.name, code(esc(t.schema || "{}"))
    + '<div class="footnote">调用时将按 required / properties.type / enum 做轻量校验。</div>',
    btn("关闭", "modal.close", {}, "primary"));
};
A1["tool.delete"] = d => confirmBox("删除工具", "确认删除「" + d.name + "」？被引用时将拒绝删除。",
  "tool.doDelete", { id: d.id }, "删除");
A1["tool.doDelete"] = async d => {
  try {
    await api("/tools/" + d.id, { method: "DELETE" });
    closeModal(); toast("已删除", d.id, "warn"); render();
  } catch (e) { toast("删除失败", e.message, "err"); }
};
A1["tool.toggle"] = async d => {
  try {
    await api("/tools/" + d.id, { method: "PATCH", body: { status: d.status } });
    toast("状态已更新", d.status); render();
  } catch (e) { toast("操作失败", e.message, "err"); }
};
A1["tool.invoke"] = async d => {
  const t = (await api("/tools")).find(x => x.id === d.id);
  modal("调用工具 · " + t.name,
    note(t.endpoint ? esc(t.method + " " + t.endpoint) : "内置工具，进程内执行")
    + '<div class="fld"><label>参数（JSON）</label><textarea class="ta mono" id="tool-params" style="min-height:120px">{}</textarea></div>'
    + '<div id="tool-result"></div>',
    btn("关闭", "modal.close", {}, "") + btn("调用", "tool.doInvoke", { id: d.id }, "primary"));
};
A1["tool.doInvoke"] = async d => {
  const ta = $("#tool-params");
  let params = {};
  try { params = JSON.parse(ta.value || "{}"); } catch (e) { toast("参数不是合法 JSON", e.message, "err"); return; }
  try {
    const res = await api("/tools/" + d.id + "/invoke", { method: "POST", body: { params } });
    $("#tool-result").innerHTML = kv([["状态", statusPill(res.status), true], ["耗时", res.dur_ms + " ms"]]) +
      code(esc(JSON.stringify(res.result, null, 2)));
  } catch (e) {
    $("#tool-result").innerHTML = note(esc(e.message), "err");
  }
};
A1["toolcalls.filter"] = (d, el) => {
  if (el.name === "tool") state.toolCallTool = el.value;
  if (el.name === "status") state.toolCallStatus = el.value;
  render();
};
A1["toolcalls.clear"] = () => {
  state.toolCallTool = "";
  state.toolCallStatus = "全部";
  render();
};
