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
  const rows = await api("/agents");
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
        btn("发布", "agent.publish", { id: r.id }, "sm primary") + "</div>" }
  ];
  return head("智能体", "智能体 = DAG 流程 + 引用的技能；版本只增不改",
      btn("新建智能体", "agent.new", {}, "primary"))
    + grid(kpi("智能体", rows.length + " 个", "已发布 " + rows.filter(r => r.status === "published").length)
        + kpi("DAG 通过", rows.filter(r => r.dag_ok).length + " 个", "存在错误的智能体无法运行")
        + kpi("累计运行", rows.reduce((a, b) => a + b.run_count, 0) + " 次", "可在运行记录中查看")
        + kpi("内置工具", "6 个", "征信 / 财报 / 司法 / 报告 / JSON / 计算器"))
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
    ["绑定工具 / 模型", esc(sel.tool || sel.model || "—")],
    ["层级", sel.level],
  ]), { cls: "col-5" }) : "";

  return head("DAG 导入与反显", "导入结构化 DAG → 后端校验 → 分层布局反显；不做拖拉拽编排",
      btn("运行该智能体", "agent.openRun", { id: id }, "primary"))
    + grid(card("DAG JSON", editor, { cls: "col-5", note: "校验通过后点「校验并保存」写入草稿；图结构由后端计算" }),
        card("图结构反显 · " + (data.agent.name || ""), dagView(structure, state.dagSel), { cls: "col-7" }))
    + grid(card("校验结果", errBody, { cls: "col-7" }), card("警告", warnBody, { cls: "col-5" }))
    + grid(props, card("编译说明", '<div class="srcline">DAG 由运行时编译为 LangGraph StateGraph：start 出边从 START 出发，end 映射为 END，条件分支按并行出边处理。</div>'
        + '<div class="srcline">校验不通过时不允许保存或运行，返回 AH-RUNTIME-422 与具体节点。</div>'
        + '<div class="srcline">发布时版本号 +1，历史版本保留可回滚。</div>', { cls: "col-7" }));
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

  return head("运行记录", "Run 状态机、事件流、Checkpoint 与人工审批")
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
  const rows = await api("/skills");
  const cols = [
    { t: "技能", render: r => "<b>" + esc(r.name) + '</b><div class="s">' + esc(r.id) + " · " + esc(r.description) + "</div>" },
    { t: "分类", render: r => pill(r.category, r.category === "业务" ? "info" : "mute") },
    { t: "版本", render: r => "v" + r.version },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "Prompt", render: r => '<span class="mono">' + esc(r.prompt) + "</span>" },
    { t: "模型", render: r => esc(r.model) },
    { t: "工具", render: r => r.tools.join("、") || "—" },
    { t: "引用", render: r => r.refs },
    { t: "操作", render: r => '<div class="ops">' +
        btn("编辑", "skill.edit", { id: r.id }, "sm") +
        btn("发布", "skill.publish", { id: r.id }, "sm primary") + "</div>" }
  ];
  return head("技能 Skill", "技能 = Prompt + 工具组合 + 模型别名；DAG 的 tool 节点按技能执行",
      btn("新建技能", "skill.new", {}, "primary"))
    + grid(card("技能清单", tbl(cols, rows), { cls: "col-12" }))
    + grid(card("字段口径", '<div class="srcline">技能描述「一次可复用的分析动作」，不含流程控制；流程由智能体的 DAG 承担。</div>'
        + '<div class="srcline">发布前校验：绑定的工具必须已注册且启用，模型别名必须存在。</div>'
        + '<div class="srcline">版本只增不改，回滚只切换版本指向。</div>', { cls: "col-6" }),
      card("当前内置工具", '<div class="srcline">' + (await api("/tools")).filter(t => t.source === "内置").map(t => esc(t.name)).join(" · ") + "</div>", { cls: "col-6" }));
};

/* ================= 工具 ================= */
P1["tools/list"] = async () => {
  const rows = await api("/tools");
  const cols = [
    { t: "工具", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + "</div>" },
    { t: "来源", render: r => pill(r.source, r.source === "内置" ? "info" : "warn") },
    { t: "实现", render: r => esc(r.impl) },
    { t: "权限", render: r => '<span class="mono">' + esc(r.permission) + "</span>" },
    { t: "超时", render: r => r.timeout_s + " s" },
    { t: "幂等", render: r => (r.idempotency ? pill("required", "info") : "—") },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("调用测试", "tool.invoke", { id: r.id }, "sm") +
        btn(r.status === "启用" ? "停用" : "启用", "tool.toggle", { id: r.id, status: r.status === "启用" ? "停用" : "启用" }, "sm") + "</div>" }
  ];
  return head("工具清单", "统一 Tool 接口：Python 函数 / HTTP / Jinja2；内置工具可直接在 DAG 中引用",
      btn("注册工具", "tool.new", {}, "primary"))
    + grid(card("工具清单", tbl(cols, rows), { cls: "col-12" }))
    + grid(card("调用方式", code('POST /api/v1/tools/{tool_id}/invoke\n{\n  "params": {"customer_no": "C-2026-0031"}\n}'), { cls: "col-6" }),
      card("边界", '<div class="srcline">只做进程内调用，不引入生产级沙箱（LangGraph 轻量能力 + 本地文件系统）。</div>'
        + '<div class="srcline">无内置实现的工具在调用时返回 AH-TOOL-501。</div>'
        + '<div class="srcline">执行超时按工具级 timeout_s 中断，返回 AH-TOOL-504。</div>', { cls: "col-6" }));
};

P1["tools/calls"] = async () => {
  const rows = await api("/tools/calls");
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
  return head("调用记录", "按 trace_id 回溯每次工具调用的参数、耗时与结果")
    + grid(card("调用记录", tbl(cols, rows, { empty: "暂无工具调用" }), { cls: "col-12" }));
};

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
A1["skill.new"] = async () => {
  const tools = await api("/tools");
  modalForm("新建技能", "skill.create",
    fld("名称", inp("name", ""))
    + '<div class="frow">' + fld("分类", sel("category", ["业务", "内置", "实验"], "业务")) + fld("模型别名", sel("model", ["default-chat", "default-chat-lite"], "default-chat")) + "</div>"
    + fld("Prompt 模板", inp("prompt", "custom@v1"))
    + fld("绑定工具（逗号分隔）", inp("tools", "征信报文解析"), tools.map(t => t.name).join(" / "))
    + '<div class="frow">' + fld("超时（秒）", inp("timeout_s", "30", 'type="number"')) + fld("重试", inp("retry", "1", 'type="number"')) + "</div>",
    "创建");
};
A1["skill.create"] = async form => {
  const o = formObj(form);
  try {
    await api("/skills", {
      method: "POST",
      body: {
        name: o.name, category: o.category, prompt: o.prompt, model: o.model,
        tools: (o.tools || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
        timeout_s: Number(o.timeout_s) || 30, retry: Number(o.retry) || 0,
      },
    });
    closeModal(); toast("已创建", "草稿 v1"); render();
  } catch (e) { toast("创建失败", e.message, "err"); }
};
A1["skill.edit"] = async d => {
  const s = await api("/skills/" + d.id);
  modalForm("编辑技能 · " + s.name, "skill.save",
    '<input type="hidden" name="id" value="' + esc(s.id) + '">'
    + fld("Prompt", inp("prompt", s.prompt))
    + fld("模型别名", sel("model", ["default-chat", "default-chat-lite", "cloud-deepseek"], s.model))
    + fld("绑定工具（逗号分隔）", inp("tools", s.tools.join(", ")))
    + '<div class="frow">' + fld("超时（秒）", inp("timeout_s", s.timeout_s, 'type="number"')) + fld("重试", inp("retry", s.retry, 'type="number"')) + "</div>",
    "保存");
};
A1["skill.save"] = async form => {
  const o = formObj(form);
  try {
    await api("/skills/" + o.id, {
      method: "PATCH",
      body: {
        prompt: o.prompt, model: o.model,
        tools: (o.tools || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
        timeout_s: Number(o.timeout_s) || 30, retry: Number(o.retry) || 0,
      },
    });
    closeModal(); toast("已保存", "下次发布生效"); render();
  } catch (e) { toast("保存失败", e.message, "err"); }
};
A1["skill.publish"] = async d => {
  try {
    const res = await api("/skills/" + d.id + "/publish", { method: "POST" });
    toast("已发布", "v" + res.version); render();
  } catch (e) { toast("发布失败", e.message, "err"); }
};

/* ===== 动作：工具 ===== */
A1["tool.new"] = () => modalForm("注册工具", "tool.create",
  fld("名称", inp("name", ""))
  + '<div class="frow">' + fld("来源", sel("source", ["内置", "MCP", "自定义"], "自定义")) + fld("实现", sel("impl", ["Python", "HTTP", "Jinja2"], "HTTP")) + "</div>"
  + '<div class="frow">' + fld("权限标签", inp("permission", "biz:read")) + fld("超时（秒）", inp("timeout_s", "10", 'type="number"')) + "</div>"
  + fld("参数 Schema（JSON）", '<textarea class="ta mono" name="params_schema">{"type":"object","properties":{}}</textarea>'),
  "注册");
A1["tool.create"] = async form => {
  const o = formObj(form);
  try {
    await api("/tools", {
      method: "POST",
      body: { name: o.name, source: o.source, impl: o.impl, permission: o.permission,
              timeout_s: Number(o.timeout_s) || 10, params_schema: o.params_schema },
    });
    closeModal(); toast("已注册", o.name); render();
  } catch (e) { toast("注册失败", e.message, "err"); }
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
    '<div class="fld"><label>参数（JSON）</label><textarea class="ta mono" id="tool-params" style="min-height:120px">{}</textarea></div>'
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
