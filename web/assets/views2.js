"use strict";

/* 页面与动作：知识库、模型接入、运行监控、接入与凭证 */
window.PAGES = window.PAGES || {};
window.ACTIONS = window.ACTIONS || {};
const P2 = window.PAGES, A2 = window.ACTIONS;

/* ================= 知识库 ================= */
P2["kb/docs"] = async () => {
  const [docs, info] = await Promise.all([api("/kb/documents"), api("/kb/info")]);
  const cols = [
    { t: "文档", render: r => "<b>" + esc(r.name) + '</b><div class="s">' + esc(r.id) + " · " + esc(r.note) + "</div>" },
    { t: "类型", render: r => pill(r.kind, "mute") },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "切片", render: r => r.chunks || "—" },
    { t: "更新", render: r => fmtTime(r.updated) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("切片", "kb.chunks", { id: r.id }, "sm") +
        btn("重建索引", "kb.reindex", { id: r.id }, "sm") +
        btn("删除", "kb.delete", { id: r.id }, "sm danger") + "</div>" }
  ];
  return head("文档与切片", "单库最小闭环：上传 → 切片 → 向量化 → 检索；无多租户 ACL",
      btn("上传文档", "kb.new", {}, "primary"))
    + grid(kpi("知识库", esc(info.name), info.mode)
        + kpi("文档", info.docs + " 个", "解析状态见列表")
        + kpi("切片", num(info.chunks) + " 条", "向量维度 64（bigram 哈希）")
        + kpi("向量模型", esc(info.embedding), "可替换为真实 embedding"))
    + grid(card("文档清单", tbl(cols, docs, { empty: "暂无文档" }), { cls: "col-12" }))
    + grid(card("切片口径", code(esc('chunk = {\n  chunk_id, doc_id, chunk_index,\n  text, vector[64]\n  // 裁剪后不携带 acl_snapshot / classification\n}')), { cls: "col-6" }),
      card("与课题「知识库集成」的对应", '<div class="srcline">课题要求知识库集成能力，裁剪后由「单库 + 向量检索」承担。</div>'
        + '<div class="srcline">DAG 的 tool 节点可绑定「知识库检索」，为报告中的口径段落提供依据。</div>'
        + '<div class="srcline">后续恢复空间隔离时，只需在 chunk 上补 space_id 与 ACL 字段，检索入口不变。</div>', { cls: "col-6" }));
};

P2["kb/test"] = async () => {
  const res = state.kbResult;
  const form = '<form data-form="kb.search">' +
    fld("查询语句", inp("q", state.kbQuery || "授信使用率 逾期 口径"))
    + '<div class="frow">' + fld("top_k", sel("top_k", ["2", "4", "6"], "4")) + fld("检索方式", sel("mode", ["向量 + 关键词", "仅向量"], "向量 + 关键词")) + "</div>"
    + '<button type="submit" class="btn primary">运行检索</button></form>';

  let out = '<div class="empt">输入查询语句，查看召回切片与分数</div>';
  if (res) {
    out = (res.rows.length
      ? res.rows.map(r => '<div class="hrow" style="align-items:flex-start">' +
          '<div style="flex:1"><div class="n"><span class="mono">' + esc(r.id) + "</span> · " + esc(r.doc) + " #" + r.idx + "</div>" +
          '<div class="s" style="margin-top:3px;line-height:1.75">' + highlight(r.text, res.terms) + "</div></div>" +
          '<div style="flex:0 0 130px;text-align:right"><div class="s">score ' + r.score + "</div><div class=\"s\">vec " + r.vec + " · hit " + r.hit + '</div><div style="margin-top:4px">' + bar(r.score, "ok") + "</div></div></div>").join("")
      : '<div class="empt">未召回相关切片，建议放宽关键词</div>');
  }
  return head("检索测试", "验证切片召回与打分；用于给报告口径提供引用依据")
    + grid(card("检索参数", form, { cls: "col-4" }), card("召回结果", out, { cls: "col-8" }))
    + grid(card("打分口径", code(esc("score = 0.6 × 向量相似度 + 0.4 × 关键词命中率")), { cls: "col-12" }));
};

function highlight(text, terms) {
  let s = esc(text);
  (terms || []).forEach(t => {
    if (!t) return;
    const re = new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g");
    s = s.replace(re, '<span style="background:#fff3c4">' + esc(t) + "</span>");
  });
  return s;
}

/* ================= 模型接入 ================= */
P2["models/list"] = async () => {
  const [providers, aliases] = await Promise.all([api("/providers"), api("/aliases")]);
  const pcols = [
    { t: "Provider", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + "</div>" },
    { t: "类型", render: r => pill(r.kind, r.kind === "本地" ? "ok" : r.kind === "规则" ? "info" : "warn") },
    { t: "base_url", render: r => '<span class="mono">' + esc(r.url || "—") + "</span>" },
    { t: "模型", render: r => esc(r.models || "—") },
    { t: "能力", render: r => esc(r.caps) },
    { t: "健康", render: r => statusPill(r.status === "healthy" ? "健康" : r.status) },
    { t: "操作", render: r => '<div class="ops">' + btn("检查", "provider.check", { id: r.id }, "sm") +
        btn(r.status === "stopped" ? "启用" : "停用", "provider.toggle", { id: r.id, status: r.status === "stopped" ? "healthy" : "stopped" }, "sm") + "</div>" }
  ];
  const acols = [
    { t: "别名", render: r => '<b class="mono">' + esc(r.alias) + "</b>" },
    { t: "Provider", render: r => esc(r.provider) },
    { t: "模型", render: r => esc(r.model) },
    { t: "能力", render: r => esc(r.caps) },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => btn("设为默认", "alias.default", { alias: r.alias }, "sm") }
  ];
  return head("Provider 与模型别名", "统一 OpenAI 兼容协议；业务侧只引用别名，切换只改配置",
      btn("注册 Provider", "provider.new", {}, "primary") + btn("新增别名", "alias.new", {}, ""))
    + grid(card("Provider 清单", tbl(pcols, providers), { cls: "col-12" }))
    + grid(card("模型别名", tbl(acols, aliases), { cls: "col-7" }),
      card("当前设置", '<div class="srcline">默认 Mock 模式：不真正出网，用规则化文本兜底，保证离线可跑通。</div>'
        + '<div class="srcline">配置 AH_LLM_MOCK=false 后，provider 的 base_url 会真实调用 /chat/completions。</div>'
        + '<div class="srcline">Provider 停用时请求进入降级链。</div>', { cls: "col-5" }));
};

P2["models/routing"] = async () => {
  const routing = await api("/routing");
  const chain = state.chainDraft || routing.chain;
  const rows = chain.map((name, i) => '<div class="hrow">' +
    '<div><div class="n mono">' + (i + 1) + ". " + esc(name) + '</div><div class="s">' + (i === 0 ? "默认路由" : i === 1 ? "二级回落" : "备用") + "</div></div>" +
    '<span class="r">' +
    (i > 0 ? btn("上移", "routing.move", { i: i, dir: -1 }, "sm") : "") +
    (i < chain.length - 1 ? btn("下移", "routing.move", { i: i, dir: 1 }, "sm") : "") + "</span></div>").join("");
  return head("降级链", "主模型不可用时按顺序回落；链耗尽返回 AH-MODEL-502",
      btn("测试链", "routing.test", {}, "") + btn("保存链路", "routing.save", {}, "primary"))
    + grid(card("当前链路", rows, { cls: "col-6", right: pill(chain.length + " 级", "info") }),
      card("说明", '<div class="srcline">顺序仅保存在服务端 settings 表，立即生效。</div>'
        + '<div class="srcline">DAG 与技能引用的是别名，链路调整不影响配置。</div>'
        + '<div class="srcline">本轮只做顺序回落，不做基于成本或延迟的动态路由。</div>', { cls: "col-6" }));
};

/* ================= 运行监控 ================= */
P2["observe/traces"] = async () => {
  const traces = await api("/traces");
  if (!traces.length) return head("Trace", "暂无运行记录") + grid(card("Trace 列表", '<div class="empt">先发起一次运行</div>'));
  const id = state.traceId && traces.some(t => t.id === state.traceId) ? state.traceId : traces[0].id;
  state.traceId = id;
  const detail = await api("/traces/" + id);
  const list = traces.map(t => '<div class="hrow" style="cursor:pointer" data-act="trace.select" data-id="' + esc(t.id) + '">' +
    '<div><div class="n mono">' + esc(t.id) + (t.id === id ? ' <span class="tag info">当前</span>' : "") + "</div>" +
    '<div class="s">' + esc(t.agent) + " v" + t.v + " · " + esc(t.customer) + " · " + esc(t.at) + "</div></div>" +
    '<span class="r">' + statusPill(t.status) + "</span></div>").join("");
  const spans = (detail.spans || []).map(s => '<div class="hrow" style="padding-left:' + (s.depth * 16) + 'px">' +
    '<div style="flex:1"><div class="n mono" style="font-size:11.5px">' + esc(s.n) + "</div>" +
    '<div class="s">' + esc(s.k) + " · " + esc(s.a) + "</div></div>" +
    '<div style="flex:0 0 150px;display:flex;gap:8px;align-items:center">' + bar(Math.min(1, s.d / Math.max(1, detail.dur)), s.d > 1000 ? "warn" : "ok") +
    '<span class="s" style="flex:0 0 62px;text-align:right">' + num(s.d) + " ms</span></div></div>").join("");
  return head("Trace", "一次运行一条 Trace；span 覆盖网关 / 运行时 / 节点 / 工具 / 模型")
    + grid(card("Trace 列表", list, { cls: "col-4" }),
        card("Span 树 · " + id, spans || '<div class="empt">暂无 span</div>', { cls: "col-8", right: statusPill(detail.status) }))
    + '<div class="footnote">总耗时 ' + (detail.dur / 1000).toFixed(2) + " s · tokens " + num(detail.tokens) +
      " · Langfuse 为可选旁路，关闭不影响本地 Trace。</div>";
};

P2["observe/stats"] = async () => {
  const s = await api("/stats");
  const cols = [
    { t: "工具", render: r => esc(r.name) },
    { t: "调用", render: r => num(r.calls) },
    { t: "成功", render: r => num(r.ok) },
    { t: "失败", render: r => (r.fail ? '<span class="tag err">' + r.fail + "</span>" : '<span class="tag ok">0</span>') },
    { t: "成功率", render: r => pct(r.calls ? r.ok / r.calls : 0) },
    { t: "平均耗时", render: r => r.avg + " ms" }
  ];
  const failures = (s.failures || []).length
    ? s.failures.map(f => '<div class="hrow"><div><div class="n">' + esc(f.tool) + '</div><div class="s">' + esc(f.error) + " · " + f.time + "</div></div>" +
        '<span class="r">' + pill(f.dur_ms + " ms", "err") + "</span></div>").join("")
    : '<div class="empt" style="padding:12px">暂无失败记录</div>';
  return head("耗时与统计", "按工具聚合，定位慢环节与失败来源；不做可视化大屏")
    + grid(kpi("运行总数", num(s.runs), "完成 " + s.completed + " · 失败 " + s.failed)
        + kpi("成功率", pct(s.success_rate), "含人工审批后恢复")
        + kpi("平均耗时", num(s.avg_dur) + " ms", "端到端")
        + kpi("待审批", num(s.waiting), "WAITING_APPROVAL"))
    + grid(card("工具调用统计", tbl(cols, s.tools || [], { empty: "暂无调用" }), { cls: "col-7" }),
      card("失败样本", failures, { cls: "col-5" }))
    + '<div class="footnote">统计口径：成功率 = 完成 Run / 总 Run；耗时为端到端，含模型与工具调用。生成时间 ' + esc(s.generated_at) + "</div>";
};

/* ================= 接入与凭证 ================= */
P2["access/invoke"] = async () => {
  const agents = await api("/invoke/agents");
  if (!agents.length) {
    return head("对外调用", "暂无可调用的智能体") + grid(card("提示", '<div class="empt">请先创建智能体</div>'));
  }
  const preferred = agents.find(a => a.callable && a.status === "published") || agents.find(a => a.callable) || agents[0];
  const id = state.invokeAgent && agents.some(a => a.id === state.invokeAgent) ? state.invokeAgent : preferred.id;
  state.invokeAgent = id;
  const info = await api("/invoke/" + id + "/info");
  const key = apiKey() || "ah_pat_demo_local_dev";
  const origin = location.origin;

  const sample = JSON.stringify({
    customer_no: "C-2026-0031",
    mode: "sync",
    payload: {
      credit: { total_limit: 8000000, used_limit: 6200000, overdue_30d: 1, query_3m: 11 },
      finance: { revenue: 126000000, net_profit: -3200000, total_assets: 210000000, total_liability: 168000000 },
      judicial: { cases: 2, executed: 0, dishonest: 0 },
    },
  }, null, 2);

  const curl = 'curl -X POST ' + origin + '/api/v1/invoke/' + id + ' \\\n'
    + '  -H "Authorization: Bearer ' + key + '" \\\n'
    + '  -H "Content-Type: application/json" \\\n'
    + "  -d '" + JSON.stringify({ customer_no: "C-2026-0031", mode: "sync", payload: { credit: { total_limit: 8000000, used_limit: 6200000, overdue_30d: 1 } } }) + "'";

  const py = 'import json\nfrom openai import OpenAI\n\n'
    + 'client = OpenAI(base_url="' + origin + '/v1", api_key="' + key + '")\n'
    + 'payload = {"credit": {"total_limit": 8000000, "used_limit": 6200000, "overdue_30d": 1}}\n'
    + 'resp = client.chat.completions.create(\n'
    + '    model="' + id + '",\n'
    + '    messages=[{"role": "user", "content": json.dumps(payload)}],\n'
    + ')\n'
    + 'print(resp.choices[0].message.content)';

  const node = 'const resp = await fetch("' + origin + '/api/v1/invoke/' + id + '", {\n'
    + '  method: "POST",\n'
    + '  headers: { "Content-Type": "application/json", "Authorization": "Bearer ' + key + '" },\n'
    + '  body: JSON.stringify({ customer_no: "C-2026-0031", payload }),\n'
    + '});\n'
    + 'const data = await resp.json();\n'
    + 'console.log(data.status, data.summary, data.report_url);';

  const form = '<form data-form="invoke.run">'
    + '<input type="hidden" name="agent_id" value="' + esc(id) + '">'
    + '<div class="frow">'
    + fld("智能体", sel2("agent_switch", agents.map(a => [a.id, a.name + (a.callable ? "" : "（DAG 未就绪）")]), id, 'data-change="invoke.switch"'))
    + fld("调用模式", sel("mode", [["sync", "sync · 直接返回结论"], ["async", "async · 返回 run_id"]], "sync"))
    + '</div>'
    + fld("客户编号", inp("customer_no", "C-2026-0031"))
    + fld("报文（JSON）", '<textarea class="ta mono" name="payload" style="min-height:180px">' + esc(JSON.stringify({
        credit: { total_limit: 8000000, used_limit: 6200000, overdue_30d: 1, query_3m: 11 },
        finance: { revenue: 126000000, net_profit: -3200000, total_assets: 210000000, total_liability: 168000000 },
        judicial: { cases: 2, executed: 0, dishonest: 0 },
      }, null, 2)) + "</textarea>")
    + '<button type="submit" class="btn primary">试调用</button>'
    + '<span class="tag info" style="margin-left:8px">POST /api/v1/invoke/' + esc(id) + "</span></form>";

  const result = state.invokeResult
    ? kv([
        ["run_id", '<span class="mono">' + esc(state.invokeResult.run_id) + "</span>", true],
        ["状态", statusPill(state.invokeResult.status), true],
        ["调用方", esc(state.invokeResult.caller)],
        ["tokens / 耗时", num(state.invokeResult.usage.tokens) + " / " + state.invokeResult.usage.dur_ms + " ms"],
        ["trace_id", '<span class="mono">' + esc(state.invokeResult.usage.trace_id) + "</span>", true],
        ["报告", state.invokeResult.report_url
          ? '<a class="tag info" target="_blank" href="' + esc(state.invokeResult.report_url) + '">打开 HTML 报告</a>' : "—", true],
        ["结论", esc(state.invokeResult.summary || "—")],
      ]) + code(esc(JSON.stringify({ run_id: state.invokeResult.run_id, status: state.invokeResult.status,
        usage: state.invokeResult.usage, links: state.invokeResult.links }, null, 2)))
    : '<div class="empt">填写报文后试调用，结果会显示在这里</div>';

  const recent = (info.recent_calls || []).length
    ? info.recent_calls.map(r => '<div class="hrow"><div><div class="n mono">' + esc(r.run_id) + '</div>' +
        '<div class="s">客户 ' + esc(r.customer) + " · " + esc(r.start) + "</div></div>" +
        '<span class="r">' + statusPill(r.status) + "</span></div>").join("")
    : '<div class="empt" style="padding:12px">暂无调用记录</div>';

  return head("对外调用", "业务系统通过标准 HTTP 调用智能体；原生契约与 OpenAI 兼容契约二选一",
      btn("设置 Key", "key.setting", {}, "primary") + btn("接口契约", "nav.to", { p: "access/contract" }, ""))
    + note("调用地址：<b>" + esc(origin) + "/api/v1/invoke/" + esc(id) + "</b> · 兼容地址：<b>" + esc(origin) + "/v1/chat/completions</b>（model 填 " + esc(id) + "）· 当前凭证 " + (apiKey() ? "已设置" : "未设置（使用本地演示凭证）"))
    + grid(card("原生契约 · curl", code(esc(curl)), { cls: "col-6" }),
        card("OpenAI 兼容 · Python SDK", code(esc(py)), { cls: "col-6" }))
    + grid(card("Node / 浏览器", code(esc(node)), { cls: "col-6" }),
        card("入参与返回", kv([
          ["customer_no", "客户编号，用于报告命名"],
          ["payload.credit / finance / judicial", "三类报文，缺省则跳过对应节点"],
          ["mode", "sync 直接返回结论 / async 返回 run_id"],
          ["返回 summary / features", "模型结论与结构化特征"],
          ["返回 report_url", "HTML 全景报告地址"],
          ["返回 links.events", "SSE 事件流地址"],
        ]) + '<div class="footnote">完整入参见 ' + '<a class="mono" target="_blank" href="/docs">/docs</a></div>', { cls: "col-6" }))
    + grid(card("在线试调用", form, { cls: "col-7" }), card("调用结果", result, { cls: "col-5" }))
    + grid(card("最近调用 · " + esc(info.agent.name), recent, { cls: "col-7" }),
      card("可调用智能体", agents.map(a => '<div class="hrow"><div><div class="n">' + esc(a.name) + '</div>' +
        '<div class="s mono">' + esc(a.endpoint) + "</div></div>" +
        '<span class="r">' + (a.callable ? pill("可调用", "ok") : pill("DAG 未就绪", "err")) + "</span></div>").join(""), { cls: "col-5" }));
};

P2["access/keys"] = async () => {
  const keys = await api("/keys");
  const cols = [
    { t: "应用", render: r => esc(r.app) },
    { t: "类型", render: r => pill(r.type, "info") },
    { t: "前缀", render: r => '<span class="mono">' + esc(r.prefix) + "</span>" },
    { t: "创建", render: r => fmtTime(r.created) },
    { t: "过期", render: r => esc(r.expires) },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => btn(r.status === "停用" ? "启用" : "停用", "key.toggle", { id: r.id, status: r.status === "停用" ? "启用" : "停用" }, "sm") }
  ];
  return head("AppKey", "业务系统调用平台的凭证；明文只在签发时返回一次", btn("签发凭证", "key.new", {}, "primary"))
    + grid(card("凭证清单", tbl(cols, keys), { cls: "col-12" }))
    + grid(card("使用方式", code(esc("Authorization: Bearer ah_live_xxxx\n\n# 服务端只存 sha256 摘要，日志与响应体不含明文")), { cls: "col-6" }),
      card("权限说明", '<div class="srcline">AppKey 用于业务系统调用 <b>/api/v1/invoke</b> 与 <b>/v1/chat/completions</b>。</div>'
        + '<div class="srcline">本地演示凭证：<span class="mono">ah_pat_demo_local_dev</span>（仅本地调试使用）。</div>'
        + '<div class="srcline">开启强制鉴权：设置环境变量 <span class="mono">AH_AUTH_REQUIRED=true</span> 后重启。</div>'
        + '<div class="srcline">SSE 无法自定义请求头时，可用 <span class="mono">?access_token=</span> 传递凭证。</div>', { cls: "col-6" }));
};

P2["access/contract"] = async () => {
  const rows = [
    ["POST", "/api/v1/invoke/{agent}", "对外统一调用：sync 返回结论 / async 返回 run_id", "AppKey"],
    ["GET", "/api/v1/invoke/agents", "可被外部调用的智能体清单", "AppKey"],
    ["POST", "/v1/chat/completions", "OpenAI 兼容调用（model 填智能体 id）", "AppKey"],
    ["GET", "/v1/models", "OpenAI 兼容模型列表（智能体即模型）", "AppKey"],
    ["POST", "/api/v1/runs", "触发一次运行（async_mode 可异步）", "AppKey"],
    ["GET", "/api/v1/runs/{run_id}", "查询运行状态、事件与产物路径", "AppKey"],
    ["GET", "/api/v1/runs/{run_id}/events", "SSE 事件流", "AppKey"],
    ["POST", "/api/v1/runs/{run_id}/approve", "人工审批（APPROVED / REJECTED）", "PAT"],
    ["POST", "/api/v1/runs/{run_id}/replay", "从 Checkpoint 重放", "PAT"],
    ["POST", "/api/v1/agents/{id}/dag", "导入并保存 DAG", "PAT"],
    ["POST", "/api/v1/agents/{id}/dag/validate", "只校验 DAG（不落库）", "PAT"],
    ["POST", "/api/v1/skills/{id}/publish", "发布技能（校验工具绑定）", "PAT"],
    ["POST", "/api/v1/tools/{id}/invoke", "直接调用工具", "PAT"],
    ["POST", "/api/v1/kb/retrieve", "知识库检索", "AppKey"],
    ["GET", "/api/v1/traces/{trace_id}", "Trace 与 span 树", "AppKey"],
    ["GET", "/api/v1/stats", "运行与工具统计", "AppKey"],
    ["GET", "/health", "健康检查", "—"],
  ].map(r => ({ method: r[0], path: r[1], desc: r[2], auth: r[3] }));
  const cols = [
    { t: "方法", render: r => pill(r.method, r.method === "GET" ? "info" : "ok") },
    { t: "路径", render: r => '<span class="mono">' + esc(r.path) + "</span>" },
    { t: "说明", render: r => esc(r.desc) },
    { t: "鉴权", render: r => esc(r.auth) }
  ];
  const curl = 'curl -X POST http://127.0.0.1:8000/api/v1/runs \\\n  -H "Content-Type: application/json" \\\n  -d \'{"agent_id":"ag_001","customer_no":"C-2026-0031","payload":{"credit":{"total_limit":8000000,"used_limit":6200000,"overdue_30d":1,"query_3m":11}}}';
  return head("接口契约", "原生 /api/v1 一套契约；所有错误带 code 与 retryable")
    + grid(card("端点清单", tbl(cols, rows), { cls: "col-7" }),
      card("调用示例", code(esc(curl)) + '<div class="footnote">SSE：GET /api/v1/runs/{run_id}/events，事件含 run.started / node.start / tool.call / tool.result / message.delta / run.completed。</div>', { cls: "col-5" }))
    + grid(card("错误码", code(esc("AH-RUNTIME-422  DAG 校验失败（含具体节点）\nAH-TOOL-422    工具参数校验失败\nAH-TOOL-501    工具无内置实现\nAH-TOOL-504    工具执行超时\nAH-RUNTIME-206 HITL 等待人工审批（非 HTTP 错误）\nAH-MODEL-502   Provider 不可用且降级链耗尽")), { cls: "col-7" }),
      card("返回体", code(esc('{\n  "code": "AH-TOOL-504",\n  "message": "工具执行超时",\n  "retryable": true\n}')), { cls: "col-5" }));
};

/* ===== 动作：知识库 ===== */
A2["kb.new"] = () => modalForm("上传文档", "kb.create",
  fld("文件名", inp("name", "客户风险分类口径_v4.md"))
  + '<div class="frow">' + fld("类型", sel("kind", ["口径", "报文", "规则"], "口径")) + fld("说明", inp("note", "风险等级划分与阈值")) + "</div>"
  + fld("正文（按段落切分，约 500 字一片）", '<textarea class="ta" name="content" style="min-height:160px">风险等级划分：A 级不高于 30 分，B 级 31 至 55 分，C 级 56 至 75 分，D 级高于 75 分。\n授信使用率超过 80% 视为额度紧张，需在报告中单独提示。</textarea>'),
  "上传并建索引");
A2["kb.create"] = async form => {
  const o = formObj(form);
  try {
    const doc = await api("/kb/documents", { method: "POST", body: o });
    closeModal(); toast("已上传", doc.name + " · " + doc.chunks + " 个切片"); render();
  } catch (e) { toast("上传失败", e.message, "err"); }
};
A2["kb.chunks"] = async d => {
  const rows = await api("/kb/documents/" + d.id + "/chunks");
  modal("切片预览", rows.length
    ? rows.map(c => '<div class="hrow" style="align-items:flex-start"><div style="flex:1"><div class="n mono">' + esc(c.id) + " · #" + c.idx + "</div>" +
        '<div class="s" style="margin-top:3px;line-height:1.75">' + esc(c.text) + "</div></div></div>").join("")
    : '<div class="empt">暂无切片</div>', btn("关闭", "modal.close", {}, "primary"));
};
A2["kb.reindex"] = async d => {
  try { const res = await api("/kb/documents/" + d.id + "/reindex", { method: "POST" }); toast("重建完成", res.chunks + " 个切片"); render(); }
  catch (e) { toast("重建失败", e.message, "err"); }
};
A2["kb.delete"] = async d => {
  try { await api("/kb/documents/" + d.id, { method: "DELETE" }); toast("已删除", "关联切片一并移除", "warn"); render(); }
  catch (e) { toast("删除失败", e.message, "err"); }
};
A2["kb.search"] = async form => {
  const o = formObj(form);
  try {
    const res = await api("/kb/retrieve", { method: "POST", body: { q: o.q, top_k: Number(o.top_k) || 4 } });
    state.kbResult = { rows: res.rows, terms: String(o.q).split(/[\s,，、]+/).filter(Boolean) };
    state.kbQuery = o.q;
    render(); toast("检索完成", res.rows.length + " 条候选");
  } catch (e) { toast("检索失败", e.message, "err"); }
};

/* ===== 动作：模型接入 ===== */
A2["provider.new"] = () => modalForm("注册 Provider", "provider.create",
  fld("名称", inp("name", ""))
  + '<div class="frow">' + fld("类型", sel("kind", ["本地", "云端", "规则"], "本地")) + fld("能力", sel("caps", ["聊天 / 流式", "聊天 / 流式 / 函数调用", "聊天 / 流式 / 函数调用 / embedding"], "聊天 / 流式")) + "</div>"
  + fld("base_url（OpenAI 兼容）", inp("base_url", "http://127.0.0.1:11434/v1"))
  + fld("模型", inp("models", "qwen2.5:7b")), "注册");
A2["provider.create"] = async form => {
  const o = formObj(form);
  try { await api("/providers", { method: "POST", body: o }); closeModal(); toast("已注册", o.name); render(); }
  catch (e) { toast("注册失败", e.message, "err"); }
};
A2["provider.toggle"] = async d => {
  try { await api("/providers/" + d.id, { method: "PATCH", body: { status: d.status } }); toast("状态已更新", d.status); render(); }
  catch (e) { toast("操作失败", e.message, "err"); }
};
A2["provider.check"] = async d => {
  try { const res = await api("/providers/" + d.id + "/check", { method: "POST" }); toast("检查通过", res.checked); render(); }
  catch (e) { toast("检查失败", e.message, "err"); }
};
A2["alias.new"] = () => modalForm("新增模型别名", "alias.create",
  fld("别名", inp("alias", ""))
  + fld("Provider", inp("provider_name", "rule"))
  + fld("模型", inp("model", "rule-based")), "创建");
A2["alias.create"] = async form => {
  const o = formObj(form);
  try { await api("/aliases", { method: "POST", body: o }); closeModal(); toast("已创建", o.alias); render(); }
  catch (e) { toast("创建失败", e.message, "err"); }
};
A2["alias.default"] = async d => {
  try { const res = await api("/aliases/" + d.alias + "/default", { method: "POST" }); toast("已设为默认", res.chain.join(" → ")); render(); }
  catch (e) { toast("操作失败", e.message, "err"); }
};

A2["routing.move"] = async (d) => {
  const routing = state.chainDraft || (await api("/routing")).chain;
  const chain = routing.slice();
  const i = Number(d.i), j = i + Number(d.dir);
  if (j < 0 || j >= chain.length) return;
  [chain[i], chain[j]] = [chain[j], chain[i]];
  state.chainDraft = chain; render();
};
A2["routing.save"] = async () => {
  const chain = state.chainDraft;
  if (!chain) { toast("链路未修改", "无需保存", "warn"); return; }
  try { const res = await api("/routing", { method: "PUT", body: { chain } }); state.chainDraft = null; toast("已保存", res.chain.join(" → ")); render(); }
  catch (e) { toast("保存失败", e.message, "err"); }
};
A2["routing.test"] = async () => {
  try {
    const res = await api("/routing/test", { method: "POST" });
    modal("降级链测试", res.steps.map(s => '<div class="hrow"><div><div class="n mono">' + esc(s.alias) + '</div><div class="s">' + esc(s.reason) + "</div></div>" +
      '<span class="r">' + pill(s.result, s.result === "OK" ? "ok" : "err") + "</span></div>").join(""), btn("关闭", "modal.close", {}, "primary"));
  } catch (e) { toast("测试失败", e.message, "err"); }
};

/* ===== 动作：监控与接入 ===== */
A2["trace.select"] = d => { state.traceId = d.id; render(); };
A2["key.new"] = () => modalForm("签发凭证", "key.create",
  fld("应用", inp("app", "客户全景分析"))
  + '<div class="frow">' + fld("类型", sel("type", ["AppKey", "PAT"], "AppKey")) + fld("有效期（天）", inp("days", "365", 'type="number"')) + "</div>", "签发");
A2["key.create"] = async form => {
  const o = formObj(form);
  try {
    const res = await api("/keys", { method: "POST", body: { app: o.app, type: o.type, days: Number(o.days) || 365 } });
    closeModal(); render();
    modal("凭证已签发 · 明文仅展示一次", code('<span style="color:#86efac">' + esc(res.plain) + "</span>") +
      note("服务端只存摘要，关闭后不再展示。"), btn("我已保存", "modal.close", {}, "primary"));
  } catch (e) { toast("签发失败", e.message, "err"); }
};
A2["key.toggle"] = async d => {
  try { await api("/keys/" + d.id, { method: "PATCH", body: { status: d.status } }); toast("状态已更新", d.status); render(); }
  catch (e) { toast("操作失败", e.message, "err"); }
};

/* ===== 动作：对外调用 ===== */
A2["nav.to"] = d => { if (d && d.p) go(d.p); };

A2["key.setting"] = () => {
  const cur = apiKey();
  modalForm("设置调用凭证", "key.save",
    fld("AppKey / PAT", inp("key", cur || "ah_pat_demo_local_dev"),
      "保存在浏览器 localStorage，仅用于本地控制台调试")
    + note("本地演示默认凭证：<b>ah_pat_demo_local_dev</b>；服务端 AH_AUTH_REQUIRED=false 时可不填。"),
    "保存");
};

A2["key.save"] = form => {
  const o = formObj(form);
  try { localStorage.setItem("ah_key", (o.key || "").trim()); } catch (e) {}
  closeModal();
  toast("已保存", o.key ? "后续请求将携带该凭证" : "已清空凭证");
  render();
};

A2["invoke.switch"] = (d, el) => {
  state.invokeAgent = el.value;
  state.invokeResult = null;
  render();
};

A2["invoke.run"] = async form => {
  const o = formObj(form);
  let payload;
  try { payload = JSON.parse(o.payload || "{}"); }
  catch (e) { toast("报文不是合法 JSON", e.message, "err"); return; }
  try {
    const res = await api("/invoke/" + o.agent_id, {
      method: "POST",
      body: { customer_no: o.customer_no, payload: payload, mode: o.mode },
    });
    state.invokeResult = res;
    state.invokeAgent = o.agent_id;
    render();
    toast("调用完成", res.status, res.status === "COMPLETED" ? "ok" : "warn");
  } catch (e) {
    toast("调用失败", e.message, "err");
  }
};
