"use strict";

/* 页面与动作：知识库、模型接入、运行监控、接入与凭证 */
window.PAGES = window.PAGES || {};
window.ACTIONS = window.ACTIONS || {};
const P2 = window.PAGES, A2 = window.ACTIONS;

/* ================= 知识库 ================= */
P2["kb/docs"] = async () => {
  const query = state.kbKind && state.kbKind !== "全部"
    ? "?kind=" + encodeURIComponent(state.kbKind) : "";
  const [docs, info] = await Promise.all([api("/kb/documents" + query), api("/kb/info")]);
  const kinds = ["全部"].concat(Object.keys(info.kinds || {}));
  const cols = [
    { t: "文档", render: r => "<b>" + esc(r.name) + '</b><div class="s">' + esc(r.id) + " · " + esc(r.note) + "</div>" },
    { t: "类型", render: r => pill(r.kind, "mute") },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "切片", render: r => r.chunks || "—" },
    { t: "字数", render: r => num(r.chars) },
    { t: "更新", render: r => fmtTime(r.updated) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("正文", "kb.view", { id: r.id }, "sm") +
        btn("编辑", "kb.edit", { id: r.id }, "sm") +
        btn("切片", "kb.chunks", { id: r.id }, "sm") +
        btn("重建", "kb.reindex", { id: r.id }, "sm") +
        btn("删除", "kb.delete", { id: r.id, name: r.name }, "sm danger") + "</div>" }
  ];
  const toolbar = '<div class="toolbar">' +
    sel2("kind", kinds, state.kbKind || "全部", 'data-change="kb.filter"') +
    '<span class="s">共 ' + docs.length + " 篇 · " + num(docs.reduce((a, b) => a + (b.chars || 0), 0)) + " 字</span>" +
    '<span class="sp"></span>' + btn("刷新", "kb.refresh", {}, "sm") + "</div>";
  return head("文档与切片", "单库最小闭环：上传 / 编辑 → 切片 → 向量化 → 检索；无多租户 ACL",
      btn("上传文档", "kb.new", {}, "primary"))
    + grid(kpi("知识库", esc(info.name), info.mode)
        + kpi("文档", info.docs + " 个", "按类型筛选见下方")
        + kpi("切片", num(info.chunks) + " 条", "向量维度 64（bigram 哈希）")
        + kpi("总字数", num(info.chars), "切片长度约 500 字"))
    + grid(card("文档清单", toolbar + tbl(cols, docs, { empty: "暂无文档" }), { cls: "col-12" }))
    + grid(card("切片口径", code(esc('chunk = {\n  chunk_id, doc_id, chunk_index,\n  text, vector[64]\n  // 裁剪后不携带 acl_snapshot / classification\n}')), { cls: "col-6" }),
      card("编辑与重建", '<div class="srcline">编辑正文后自动重建该文档的切片与向量，无需手动操作。</div>'
        + '<div class="srcline">重建索引只清空并重算当前文档的切片，不影响其他文档。</div>'
        + '<div class="srcline">内置工具「知识库检索」可直接被 DAG tool 节点引用：<span class="mono">"params": {"q": "授信使用率 口径"}</span>。</div>', { cls: "col-6" }));
};

P2["kb/test"] = async () => {
  const res = state.kbResult;
  const form = '<form data-form="kb.search">' +
    fld("查询语句", inp("q", state.kbQuery || "授信使用率 逾期 口径"))
    + '<div class="frow">' + fld("top_k", sel("top_k", ["2", "4", "6"], "4"))
    + fld("检索方式", sel("mode", [["hybrid", "向量 + 关键词"], ["vector", "仅向量"]], state.kbMode || "hybrid")) + "</div>"
    + '<button type="submit" class="btn primary">运行检索</button></form>';

  let out = '<div class="empt">输入查询语句，查看召回切片与分数</div>';
  if (res) {
    out = (res.rows.length
      ? '<div class="toolbar">' + pill(res.mode === "vector" ? "仅向量" : "向量 + 关键词", "info")
        + '<span class="s">命中词：' + esc((res.terms || []).join(" / ") || "—") + "</span></div>"
        + res.rows.map(r => '<div class="hrow" style="align-items:flex-start">' +
          '<div style="flex:1"><div class="n"><span class="mono">' + esc(r.id) + "</span> · " + esc(r.doc_name || r.doc) + " #" + r.idx + "</div>" +
          '<div class="s" style="margin-top:3px;line-height:1.75">' + highlight(r.text, res.terms) + "</div></div>" +
          '<div style="flex:0 0 130px;text-align:right"><div class="s">score ' + r.score + "</div><div class=\"s\">vec " + r.vec + " · hit " + r.hit + '</div><div style="margin-top:4px">' + bar(r.score, "ok") + "</div></div></div>").join("")
      : '<div class="empt">未召回相关切片，建议放宽关键词或切换为混合检索</div>');
  }
  return head("检索测试", "验证切片召回与打分；用于给报告口径提供引用依据")
    + grid(card("检索参数", form, { cls: "col-4" }), card("召回结果", out, { cls: "col-8" }))
    + grid(card("打分口径", code(esc("hybrid: score = 0.6 × 向量相似度 + 0.4 × 关键词命中率\nvector: score = 向量相似度")), { cls: "col-12" }));
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
  const [providers, aliases, meta] = await Promise.all([api("/providers"), api("/aliases"), api("/system/meta")]);
  const pcols = [
    { t: "Provider", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + "</div>" },
    { t: "类型", render: r => pill(r.kind, r.kind === "本地" ? "ok" : r.kind === "规则" ? "info" : "warn") },
    { t: "base_url", render: r => '<span class="mono">' + esc(r.url || "—") + "</span>" },
    { t: "模型", render: r => esc(r.models || "—") },
    { t: "能力", render: r => esc(r.caps) },
    { t: "密钥", render: r => (r.api_key_set ? pill("已配置", "ok") : '<span class="s">—</span>') },
    { t: "健康", render: r => statusPill(r.status === "healthy" ? "健康" : r.status) },
    { t: "操作", render: r => '<div class="ops">' + btn("检查", "provider.check", { id: r.id }, "sm") +
        btn("编辑", "provider.edit", { id: r.id }, "sm") +
        btn(r.status === "stopped" ? "启用" : "停用", "provider.toggle", { id: r.id, status: r.status === "stopped" ? "healthy" : "stopped" }, "sm") +
        btn("删除", "provider.delete", { id: r.id, name: r.name }, "sm danger") + "</div>" }
  ];
  const acols = [
    { t: "别名", render: r => '<b class="mono">' + esc(r.alias) + "</b>" },
    { t: "Provider", render: r => esc(r.provider) },
    { t: "模型", render: r => esc(r.model) },
    { t: "能力", render: r => esc(r.caps) },
    { t: "链路", render: r => (r.in_chain ? pill("在链", "ok") : '<span class="s">—</span>') },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("设为默认", "alias.default", { alias: r.alias }, "sm") +
        btn("编辑", "alias.edit", { alias: r.alias }, "sm") +
        btn("删除", "alias.delete", { alias: r.alias }, "sm danger") + "</div>" }
  ];
  return head("Provider 与模型别名", "统一 OpenAI 兼容协议；业务侧只引用别名，切换只改配置",
      btn("注册 Provider", "provider.new", {}, "primary") + btn("新增别名", "alias.new", {}, ""))
    + grid(kpi("Provider", providers.length + " 个", "健康 " + providers.filter(p => p.status === "healthy").length)
        + kpi("模型别名", aliases.length + " 个", "在降级链 " + aliases.filter(a => a.in_chain).length)
        + kpi("默认别名", aliases.filter(a => a.status === "默认").map(a => a.alias).join(" / ") || "—", "聊天与向量分别维护")
        + kpi("当前模式", meta.llm_mock ? "Mock（不出网）" : "真实调用", "AH_LLM_MOCK 控制"))
    + grid(card("Provider 清单", tbl(pcols, providers), { cls: "col-12" }))
    + grid(card("模型别名", tbl(acols, aliases), { cls: "col-8" }),
      card("当前设置", '<div class="srcline">默认 Mock 模式：不真正出网，用规则化文本兜底，保证离线可跑通。</div>'
        + '<div class="srcline">配置 AH_LLM_MOCK=false 后，「检查」会真实请求 Provider 的 /models。</div>'
        + '<div class="srcline">仍在降级链中的别名不可删除，需先从链路移除。</div>', { cls: "col-4" }));
};

P2["models/routing"] = async () => {
  const [routing, stats] = await Promise.all([api("/routing"), api("/models/stats")]);
  const chain = state.chainDraft || routing.chain;
  const rows = chain.map((name, i) => '<div class="hrow">' +
    '<div><div class="n mono">' + (i + 1) + ". " + esc(name) + '</div><div class="s">' + (i === 0 ? "默认路由" : i === 1 ? "二级回落" : "备用") + "</div></div>" +
    '<span class="r">' +
    (i > 0 ? btn("上移", "routing.move", { i: i, dir: -1 }, "sm") : "") +
    (i < chain.length - 1 ? btn("下移", "routing.move", { i: i, dir: 1 }, "sm") : "") + "</span></div>").join("");
  const statRows = (stats.aliases || []).length
    ? stats.aliases.map(s => '<div class="hrow"><div><div class="n mono">' + esc(s.alias) + '</div>' +
        '<div class="s">调用 ' + num(s.calls) + " · 成功 " + num(s.ok) + " · 失败 " + num(s.fail) +
        (s.degraded ? " · 降级 " + num(s.degraded) : "") + (s.last_error ? " · " + esc(s.last_error) : "") + "</div></div>" +
        '<span class="r">' + pill(num(s.avg_ms) + " ms", "mute") + pill(pct(s.success_rate), s.success_rate >= 0.9 ? "ok" : "warn") + "</span></div>").join("")
    : '<div class="empt" style="padding:12px">暂无调用，发起一次运行后出现</div>';
  const totalCalls = (stats.aliases || []).reduce((a, b) => a + b.calls, 0);
  const totalFail = (stats.aliases || []).reduce((a, b) => a + b.fail, 0);
  return head("降级链", "主模型不可用时按顺序回落；链耗尽返回 AH-MODEL-502",
      btn("测试链", "routing.test", {}, "") + btn("保存链路", "routing.save", {}, "primary"))
    + grid(kpi("链路级数", chain.length + " 级", "按顺序尝试")
        + kpi("调用合计", num(totalCalls) + " 次", "进程内统计")
        + kpi("失败合计", num(totalFail) + " 次", "含自动回落")
        + kpi("Provider", (stats.providers || []).filter(p => p.status === "healthy").length + " 健康",
          "共 " + (stats.providers || []).length + " 个"))
    + grid(card("当前链路", rows, { cls: "col-6", right: pill(chain.length + " 级", "info") }),
      card("别名调用统计", statRows, { cls: "col-6" }))
    + grid(card("说明", '<div class="srcline">顺序仅保存在服务端 settings 表，立即生效。</div>'
        + '<div class="srcline">统计为进程内累加，重启归零；多副本部署不共享。</div>'
        + '<div class="srcline">本轮只做顺序回落，不做基于成本或延迟的动态路由。</div>', { cls: "col-12" }));
};

/* ================= 运行监控 ================= */
P2["observe/overview"] = async () => {
  const [s, health, storage] = await Promise.all([
    api("/stats?days=7"), api("/system/health"), api("/system/storage"),
  ]);
  const links = [
    ["调用链路", "挑一次具体运行，看每一步的耗时、工具调用和错误", "observe/traces", "看链路"],
    ["运行统计", "看整体跑了多少次、成功率、P50/P95 和失败工具排行", "observe/stats", "看统计"],
    ["异常与审批", "集中处理失败的、被中断的、等待人工审批的运行", "observe/incidents", "去处理"],
    ["成本与用量", "按智能体和模型看 tokens、估算费用，定位“谁最费钱”", "observe/cost", "看成本"],
  ];
  const services = (health.services || []).map(x => '<div class="hrow"><div><div class="n">' + esc(x.name) +
    '</div><div class="s">' + esc(x.role) + '</div></div><span class="r">' + statusPill(x.status) + "</span></div>").join("");
  const storageHtml = (storage.items || []).map(x => '<div class="hrow"><div><div class="n">' + esc(x.desc) +
    '</div><div class="s mono">' + esc(x.path) + '</div></div><span class="r">' + pill(x.size, "mute") + "</span></div>").join("");
  const pool = health.run_pool || {};

  return head("运行观测总览", "这一组页面回答四个问题：跑得怎么样、慢在哪、错在哪、花了多少")
    + grid(kpi("近 7 天运行", num(s.runs) + " 次", "完成 " + s.completed + " · 失败 " + s.failed)
        + kpi("成功率", pct(s.success_rate), "按已终态运行计算")
        + kpi("P95 耗时", num(s.p95) + " ms", "95% 的运行快于这个值")
        + kpi("待人工审批", num(s.waiting) + " 次", "高风险客户会停在这里"))
    + grid(card("四个页面分别做什么", links.map(([name, desc, path, label]) =>
        '<div class="hrow"><div><div class="n">' + esc(name) + '</div><div class="s">' + esc(desc) +
        '</div></div><span class="r">' + btn(label, "nav.to", { p: path }, "sm") + "</span></div>").join(""),
        { cls: "col-7" }),
      card("推荐排查顺序", '<div class="srcline">1. 先看「运行统计」，判断是整体变慢还是个别失败。</div>'
        + '<div class="srcline">2. 再进「异常与审批」，把失败和待审批的运行处理掉。</div>'
        + '<div class="srcline">3. 最后进「调用链路」，看具体卡在哪个节点。</div>'
        + '<div class="srcline">「成本与用量」用于横向比较，不参与故障排查。</div>', { cls: "col-5" }))
    + grid(card("系统健康", services + '<div class="footnote">运行线程池：' + num(pool.running) + " 在跑 / "
          + num(pool.pending) + " 排队，上限 " + num(pool.queue_limit) + "；检查时间 " + esc(health.checked_at) + "。</div>",
        { cls: "col-6" }),
      card("数据落盘", storageHtml + '<div class="footnote">共 ' + num(storage.runs) + " 次运行、"
          + num(storage.tool_calls) + " 条工具调用记录。</div>", { cls: "col-6" }));
};

P2["observe/traces"] = async () => {
  const query = ["limit=50"];
  if (state.traceStatus && state.traceStatus !== "全部") query.push("status=" + encodeURIComponent(state.traceStatus));
  if (state.searchText) query.push("q=" + encodeURIComponent(state.searchText));
  const traces = await api("/traces?" + query.join("&"));
  const toolbar = '<div class="toolbar">' +
    sel2("status", ["全部", "COMPLETED", "WAITING_APPROVAL", "FAILED", "REJECTED", "INTERRUPTED"],
      state.traceStatus || "全部", 'data-change="trace.status"') +
    '<input class="inp" id="traceQ" placeholder="trace_id / 客户 / 智能体" style="max-width:240px" value="' + esc(state.searchText || "") + '">' +
    btn("搜索", "trace.search", {}, "sm") + btn("清空", "trace.clear", {}, "sm") + "</div>";
  if (!traces.length) {
    return head("调用链路（Trace）", "一次运行内部的步骤时间线：哪个节点慢、哪个工具失败")
      + note("先在上方筛选一条运行，再在右侧看每一步的耗时；红色 ERROR 表示该工具执行失败。")
      + grid(card("筛选", toolbar, { cls: "col-12" }))
      + grid(card("运行链路列表", '<div class="empt">当前筛选下暂无运行记录</div>', { cls: "col-12" }));
  }
  const id = state.traceId && traces.some(t => t.id === state.traceId) ? state.traceId : traces[0].id;
  state.traceId = id;
  const detail = await api("/traces/" + id);
  const list = traces.map(t => '<div class="hrow" style="cursor:pointer" data-act="trace.select" data-id="' + esc(t.id) + '">' +
    '<div><div class="n mono">' + esc(t.id) + (t.id === id ? ' <span class="tag info">当前</span>' : "") + "</div>" +
    '<div class="s">' + esc(t.agent) + " v" + t.v + " · " + esc(t.customer) + " · " + esc(t.at) + "</div>" +
    '<div class="s">' + (t.dur || 0) + " ms · " + num(t.tokens) + " tokens</div></div>" +
    '<span class="r">' + statusPill(t.status) + "</span></div>").join("");
  const spans = (detail.spans || []).map(s => '<div class="hrow" style="padding-left:' + (s.depth * 16) + 'px">' +
    '<div style="flex:1"><div class="n mono" style="font-size:11.5px">' + esc(s.n) +
    (s.level === "ERROR" ? ' <span class="tag err">ERROR</span>' : "") + "</div>" +
    '<div class="s">' + esc(s.k) + " · " + esc(s.a) + "</div></div>" +
    '<div style="flex:0 0 150px;display:flex;gap:8px;align-items:center">' + bar(Math.min(1, s.d / Math.max(1, detail.dur)), s.level === "ERROR" ? "err" : s.d > 1000 ? "warn" : "ok") +
    '<span class="s" style="flex:0 0 62px;text-align:right">' + num(s.d) + " ms</span></div></div>").join("");
  const completed = traces.filter(t => t.status === "COMPLETED").length;
  const tokens = traces.reduce((a, b) => a + (b.tokens || 0), 0);
  return head("调用链路（Trace）", "一次运行内部的步骤时间线：哪个节点慢、哪个工具失败")
    + note("左侧选一条运行，右侧看它的步骤树；红色 ERROR 表示该工具执行失败，条形长度是耗时占比。")
    + grid(kpi("当前筛选", traces.length + " 条", "最多展示 50 条")
        + kpi("完成", completed + " 条", "成功率 " + pct(traces.length ? completed / traces.length : 0))
        + kpi("tokens", num(tokens), "当前筛选合计")
        + kpi("平均耗时", num(traces.length ? traces.reduce((a, b) => a + (b.dur || 0), 0) / traces.length : 0) + " ms", "端到端"))
    + grid(card("筛选", toolbar, { cls: "col-12" }))
    + grid(card("运行链路列表", list, { cls: "col-4" }),
        card("步骤耗时树 · " + id, spans || '<div class="empt">这条运行还没有步骤记录</div>',
          { cls: "col-8", right: statusPill(detail.status) }))
    + '<div class="footnote">总耗时 ' + (detail.dur / 1000).toFixed(2) + " s · tokens " + num(detail.tokens) +
      " · 客户 " + esc(detail.customer || "—") + " · 数据来自本地 RunEvent / ToolCall，不依赖外部系统。</div>";
};

P2["observe/stats"] = async () => {
  const s = await api("/stats?days=" + (state.statsDays || 0));
  const range = '<div class="toolbar">' +
    sel2("days", [["0", "全部时间"], ["1", "今日"], ["7", "近 7 天"], ["30", "近 30 天"]],
      String(state.statsDays || 0), 'data-change="stats.range"') +
    '<span class="s">生成时间 ' + esc(s.generated_at) + "</span></div>";
  const cols = [
    { t: "工具", render: r => esc(r.name) },
    { t: "调用", render: r => num(r.calls) },
    { t: "成功", render: r => num(r.ok) },
    { t: "失败", render: r => (r.fail ? '<span class="tag err">' + r.fail + "</span>" : '<span class="tag ok">0</span>') },
    { t: "成功率", render: r => pct(r.success_rate != null ? r.success_rate : (r.calls ? r.ok / r.calls : 0)) },
    { t: "平均耗时", render: r => r.avg + " ms" }
  ];
  const agentCols = [
    { t: "智能体", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + "</div>" },
    { t: "运行", render: r => num(r.runs) },
    { t: "完成", render: r => num(r.completed) },
    { t: "失败/中断", render: r => (r.failed ? '<span class="tag err">' + r.failed + "</span>" : '<span class="tag ok">0</span>') },
    { t: "成功率", render: r => pct(r.success_rate) },
    { t: "平均耗时", render: r => num(r.avg_dur) + " ms" }
  ];
  const failures = (s.failures || []).length
    ? s.failures.map(f => '<div class="hrow"><div><div class="n">' + esc(f.tool) + '</div><div class="s">' + esc(f.error) +
        " · " + esc(f.time) + '</div><div class="s mono">' + esc(f.run_id || "—") + "</div></div>" +
        '<span class="r">' + pill(f.dur_ms + " ms", "err") + btn("查看", "stats.openRun", { id: f.run_id }, "sm") + "</span></div>").join("")
    : '<div class="empt" style="padding:12px">暂无失败记录</div>';
  return head("运行统计", "看整体：跑了多少次、成功率、耗时分布、哪个工具最容易失败")
    + note("这里不看单独某一次运行，用来判断“整体稳不稳、慢在哪”；先选时间范围，再看下面的表。")
    + grid(card("统计范围", range, { cls: "col-12" }))
    + grid(kpi("运行总数", num(s.runs), "完成 " + s.completed + " · 失败 " + s.failed + " · 待审批 " + s.waiting)
        + kpi("成功率", pct(s.success_rate), "完成 / 已终态 " + num(s.terminal))
        + kpi("P50 耗时", num(s.p50) + " ms", "已完成运行")
        + kpi("P95 耗时", num(s.p95) + " ms", "已完成运行"))
    + grid(card("按智能体", tbl(agentCols, s.agents || [], { empty: "暂无运行" }), { cls: "col-12" }))
    + grid(card("工具调用统计", tbl(cols, s.tools || [], { empty: "暂无调用" }), { cls: "col-7" }),
      card("失败样本", failures, { cls: "col-5" }))
    + '<div class="footnote">成功率 = 完成 Run / 已终态 Run（COMPLETED + FAILED + REJECTED + INTERRUPTED），待审批与在途不计入分母。</div>';
};

P2["observe/incidents"] = async () => {
  const rows = await api("/runs?limit=200&status=FAILED,WAITING_APPROVAL,INTERRUPTED,REJECTED");
  const waiting = rows.filter(r => r.status === "WAITING_APPROVAL").length;
  const failed = rows.filter(r => r.status === "FAILED").length;
  const interrupted = rows.filter(r => r.status === "INTERRUPTED").length;
  const rejected = rows.filter(r => r.status === "REJECTED").length;
  const cols = [
    { t: "时间", render: r => fmtTime(r.start) },
    { t: "智能体", render: r => "<b>" + esc(r.agent) + '</b><div class="s mono">' + esc(r.id) + "</div>" },
    { t: "客户", render: r => esc(r.customer) },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "耗时", render: r => num(r.dur_ms) + " ms" },
    { t: "原因", render: r => esc(r.error || (r.status === "WAITING_APPROVAL" ? "等待人工审批" : "—")) },
    { t: "操作", render: r => '<div class="ops">' + btn("去处理", "stats.openRun", { id: r.id }, "sm") +
        (r.status === "WAITING_APPROVAL" ? btn("去审批", "stats.openRun", { id: r.id }, "sm primary") : "") + "</div>" }
  ];
  return head("异常与审批", "把失败的、被中断的、等待人工审批的运行集中在一页处理")
    + note("这里只列需要人介入的运行；正常完成的运行去「调用链路」看。")
    + grid(kpi("待人工审批", waiting + " 次", "高风险客户会停在这里")
        + kpi("失败", failed + " 次", "工具 / 模型 / 配置问题")
        + kpi("服务中断", interrupted + " 次", "服务重启导致")
        + kpi("已拒绝", rejected + " 次", "审批人拒绝"))
    + grid(card("待处理运行", tbl(cols, rows, { empty: "当前没有异常运行" }), { cls: "col-12" }))
    + grid(card("处理建议", '<div class="srcline"><b>待审批：</b>进入运行记录，点「批准并继续」或「拒绝」；审批后会从断点继续。</div>'
        + '<div class="srcline"><b>失败：</b>先看原因，常见是工具不存在 / 已停用、模型别名不可用、DAG 依赖校验不通过。</div>'
        + '<div class="srcline"><b>服务中断：</b>服务重启会把在途运行标记为 INTERRUPTED，可重放或重新发起。</div>', { cls: "col-12" }));
};

P2["observe/cost"] = async () => {
  const [runs, ms] = await Promise.all([api("/runs?limit=200"), api("/models/stats")]);
  const tokens = runs.reduce((a, b) => a + (b.tokens || 0), 0);
  const amount = runs.reduce((a, b) => a + (b.amount || 0), 0);
  const avg = runs.length ? Math.round(tokens / runs.length) : 0;
  const aliases = ms.aliases || [];
  const aliasCalls = aliases.reduce((a, b) => a + (b.calls || 0), 0);

  const byAgent = {};
  runs.forEach(r => {
    const key = r.agent_id || r.agent;
    const item = byAgent[key] || (byAgent[key] = { id: key, name: r.agent, runs: 0, tokens: 0, amount: 0 });
    item.runs += 1;
    item.tokens += r.tokens || 0;
    item.amount += r.amount || 0;
  });
  const agentRows = Object.values(byAgent).sort((a, b) => b.tokens - a.tokens);
  const top = runs.slice().sort((a, b) => (b.tokens || 0) - (a.tokens || 0)).slice(0, 10);

  const agentCols = [
    { t: "智能体", render: r => "<b>" + esc(r.name) + '</b><div class="s mono">' + esc(r.id) + "</div>" },
    { t: "运行", render: r => num(r.runs) },
    { t: "tokens", render: r => num(r.tokens) },
    { t: "平均 tokens", render: r => num(r.runs ? Math.round(r.tokens / r.runs) : 0) },
    { t: "估算费用", render: r => "$" + num(r.amount, 4) },
  ];
  const topCols = [
    { t: "时间", render: r => fmtTime(r.start) },
    { t: "智能体", render: r => esc(r.agent) },
    { t: "客户", render: r => esc(r.customer) },
    { t: "tokens", render: r => num(r.tokens) },
    { t: "费用", render: r => "$" + num(r.amount, 4) },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => btn("查看", "stats.openRun", { id: r.id }, "sm") },
  ];
  const aliasHtml = aliases.length
    ? aliases.map(a => '<div class="hrow"><div><div class="n mono">' + esc(a.alias) + '</div>' +
        '<div class="s">调用 ' + num(a.calls) + " · 失败 " + num(a.fail) + (a.last_error ? " · " + esc(a.last_error) : "") +
        "</div></div><span class=\"r\">" + pill(num(a.avg_ms) + " ms", "mute") + pill(pct(a.success_rate), a.success_rate >= 0.9 ? "ok" : "warn") + "</span></div>").join("")
    : '<div class="empt" style="padding:12px">暂无模型调用；进程内统计，重启后归零</div>';

  return head("成本与用量", "按智能体和模型看 tokens / 估算费用，定位“谁最费钱”")
    + note("费用按 tokens × 0.000012 估算，仅用于演示和横向比较，不代表真实账单。")
    + grid(kpi("累计 tokens", num(tokens), "最近 " + runs.length + " 次运行")
        + kpi("估算费用", "$" + num(amount, 4), "统一单价")
        + kpi("平均 tokens/次", num(avg), "含未完成运行")
        + kpi("模型调用", num(aliasCalls) + " 次", "进程内统计，重启归零"))
    + grid(card("按智能体", tbl(agentCols, agentRows, { empty: "暂无运行" }), { cls: "col-7" }),
      card("模型别名调用", aliasHtml, { cls: "col-5" }))
    + grid(card("高 tokens 运行 Top 10", tbl(topCols, top, { empty: "暂无运行" }), { cls: "col-12" }));
};

/* ================= 对外接入 ================= */
P2["access/overview"] = async () => {
  const [keys, quotas, agents] = await Promise.all([
    api("/keys"), api("/quota"), api("/invoke/agents"),
  ]);
  const callable = (agents || []).filter(a => a.callable).length;
  const today = (quotas || []).reduce((a, b) => a + b.used_calls, 0);
  const steps = [
    ["1", "签发调用凭证", "在「调用凭证」签发 AppKey；明文只显示一次，保存到业务系统配置里。", "nav.to", { p: "access/keys" }, "去签发"],
    ["2", "查接口与参数", "在「接口文档」确认路径、入参和鉴权；需要在线调试就打开 /docs。", "nav.to", { p: "access/contract" }, "看文档"],
    ["3", "用 curl / SDK 调通", "复制下面的示例，把 AppKey 换成实际凭证即可。", "contract.docs", {}, "打开 /docs"],
  ].map(([n, title, desc, act, data, label]) =>
    '<div class="hrow"><div><div class="n">' + n + ". " + esc(title) + '</div><div class="s">' +
    esc(desc) + '</div></div><span class="r">' + btn(label, act, data, "sm") + "</span></div>").join("");
  const links = [
    ["调用凭证（AppKey）", "业务系统调用平台的“钥匙”；签发、轮换、吊销都在这里", "nav.to", { p: "access/keys" }, "管理凭证"],
    ["接口文档", "平台有哪些接口、怎么鉴权、错误码是什么意思", "nav.to", { p: "access/contract" }, "看接口"],
    ["OpenAPI 在线调试", "/docs 里可以直接试请求、看字段说明", "contract.docs", {}, "打开 /docs"],
  ].map(([name, desc, act, data, label]) =>
    '<div class="hrow"><div><div class="n">' + esc(name) + '</div><div class="s">' + esc(desc) +
    '</div></div><span class="r">' + btn(label, act, data, "sm") + "</span></div>").join("");
  const curl = 'curl -X POST ' + location.origin + '/api/v1/invoke/ag_001 \\\n'
    + '  -H "Authorization: Bearer <AppKey>" \\\n'
    + '  -H "Content-Type: application/json" \\\n'
    + '  -d \'{"customer_no":"C-001","mode":"sync","payload":{"credit":{"total_limit":8000000,"used_limit":6200000}}}\'';

  return head("对外接入总览", "业务系统怎么调用平台：签凭证 → 查接口 → 用 curl / SDK 调通",
      btn("打开 /docs", "contract.docs", {}, "primary"))
    + grid(kpi("调用凭证", keys.length + " 个", "启用 " + keys.filter(k => k.status === "启用").length
          + (keys.some(k => k.expired) ? " · 过期 " + keys.filter(k => k.expired).length : ""))
        + kpi("可调用智能体", callable + " 个", "已发布且 DAG 校验通过")
        + kpi("今日调用", num(today) + " 次", "按凭证累计")
        + kpi("鉴权要求", "本地可关闭", "生产建议开启 AH_AUTH_REQUIRED"))
    + grid(card("三步完成接入", steps, { cls: "col-7" }),
      card("最小调用示例", note("把 <span class=\"mono\">&lt;AppKey&gt;</span> 换成实际凭证即可；完整字段见 /docs。")
        + code(esc(curl)), { cls: "col-5" }))
    + grid(card("接入入口", links, { cls: "col-7" }),
      card("常见疑问", '<div class="srcline"><b>没有凭证能调用吗？</b>本地默认可以；开启强制鉴权后必须带 AppKey。</div>'
        + '<div class="srcline"><b>sync 和 async 怎么选？</b>演示/短任务用 sync 直接拿结果；长任务用 async 拿 run_id 再查。</div>'
        + '<div class="srcline"><b>会不会重复执行？</b>带同一个幂等键重试不会重复跑，直接复用原运行。</div>'
        + '<div class="srcline"><b>返回里有什么？</b>run_id、状态、结论、报告链接、tokens/耗时与 trace_id。</div>', { cls: "col-5" }));
};

P2["access/keys"] = async () => {
  const [keys, quotas] = await Promise.all([api("/keys"), api("/quota")]);
  const qmap = {};
  (quotas || []).forEach(q => { qmap[q.key_id] = q; });
  const cols = [
    { t: "应用", render: r => esc(r.app) + (r.expired ? ' <span class="tag err">已过期</span>' : "") },
    { t: "类型", render: r => pill(r.type, "info") },
    { t: "前缀", render: r => '<span class="mono">' + esc(r.prefix) + "</span>" },
    { t: "到期", render: r => esc(r.expires || "—") },
    { t: "最近使用", render: r => esc(r.last_used || "—") },
    { t: "今日 / 日配额", render: r => {
        const q = qmap[r.id];
        if (!q) return "—";
        const limit = q.daily_calls ? num(q.daily_calls) : "不限";
        const tone = q.daily_calls && q.used_calls / q.daily_calls >= 0.8 ? "err" : "";
        return '<span class="' + (tone ? "tag " + tone : "") + '">' + num(q.used_calls) + " / " + limit + "</span>";
      } },
    { t: "拦截", render: r => { const q = qmap[r.id]; return q && q.blocked ? '<span class="tag err">' + q.blocked + "</span>" : "0"; } },
    { t: "状态", render: r => statusPill(r.status) },
    { t: "操作", render: r => '<div class="ops">' +
        btn("配额", "quota.edit", { id: r.id }, "sm") +
        btn("轮换", "key.rotate", { id: r.id, app: r.app }, "sm") +
        (r.status === "停用"
          ? btn("启用", "key.toggle", { id: r.id, status: "启用" }, "sm")
          : btn("吊销", "key.revoke", { id: r.id, app: r.app }, "sm danger")) + "</div>" }
  ];
  const quotaCards = (quotas || []).map(q => '<div class="hrow">' +
      '<div><div class="n">' + esc(q.app) + ' <span class="mono">' + esc(q.prefix) + "</span></div>" +
      '<div class="s">今日 ' + num(q.used_calls) + " 次 · 拦截 " + q.blocked + " 次 · 实时窗口 " + q.live.window + "/" + q.live.rps_limit + " · 在途 " + q.live.inflight + "</div></div>" +
      '<span class="r">' + bar(q.daily_calls ? q.used_calls / q.daily_calls : 0, q.daily_calls && q.used_calls / q.daily_calls > 0.8 ? "err" : "ok") +
      btn("重置", "quota.reset", { id: q.key_id }, "sm") + "</span></div>").join("");
  const usedTotal = (quotas || []).reduce((a, b) => a + b.used_calls, 0);
  const blockedTotal = (quotas || []).reduce((a, b) => a + b.blocked, 0);

  return head("调用凭证（AppKey）", "业务系统调用平台的“钥匙”；本地可关闭强制鉴权，生产建议开启",
      btn("签发凭证", "key.new", {}, "primary"))
    + note("凭证明文只在签发 / 轮换时显示一次；服务端只存摘要，忘了只能轮换。")
    + grid(kpi("凭证", keys.length + " 个", "启用 " + keys.filter(k => k.status === "启用").length)
        + kpi("今日调用", num(usedTotal) + " 次", "按凭证累计")
        + kpi("拦截", num(blockedTotal) + " 次", "RPS / 并发 / 日配额")
        + kpi("即将到期", keys.filter(k => k.expires && k.expires <= new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10)).length + " 个", "30 天内"))
    + grid(card("凭证清单", tbl(cols, keys), { cls: "col-12" }))
    + grid(card("配额与限流", quotaCards || '<div class="empt">暂无凭证</div>', { cls: "col-7" }),
      card("限额口径", '<div class="srcline">RPS：1 秒滑动窗口内的请求数上限。</div>'
        + '<div class="srcline">并发：同时在途的请求数上限，超限立即拒绝。</div>'
        + '<div class="srcline">日配额：当天累计调用次数上限，跨日自动重置。</div>'
        + '<div class="srcline">超限返回 <span class="mono">AH-QUOTA-429</span> 并带 Retry-After；单机内存实现，多副本需换 Redis。</div>', { cls: "col-5" }))
    + grid(card("使用方式", code(esc("Authorization: Bearer ah_live_xxxx\n\n# 服务端只存 sha256 摘要，日志与响应体不含明文")), { cls: "col-6" }),
      card("凭证生命周期", '<div class="srcline">过期凭证会被鉴权层直接拒绝（AH-AUTH-401），无需手工清理。</div>'
        + '<div class="srcline">轮换：同一行换新摘要，旧明文立即失效，配额与统计保持不变。</div>'
        + '<div class="srcline">吊销：状态置停用，可随时恢复；本地演示凭证 ah_pat_demo_local_dev 仅用于调试。</div>'
        + '<div class="srcline">SSE 无法自定义请求头时，可用 <span class="mono">?access_token=</span> 传递凭证。</div>', { cls: "col-6" }));
};

P2["access/contract"] = async () => {
  const contract = await api("/system/contract");
  const tags = ["全部"].concat((contract.tags || []).map(t => t.name));
  const methods = ["全部", "GET", "POST", "PATCH", "PUT", "DELETE"];
  const q = (state.contractQuery || "").toLowerCase();
  const rows = (contract.endpoints || []).filter(r =>
    (state.contractTag === "全部" || !state.contractTag || r.tag === state.contractTag) &&
    (state.contractMethod === "全部" || !state.contractMethod || r.method === state.contractMethod) &&
    (!q || (r.path + " " + r.summary + " " + (r.desc || "")).toLowerCase().includes(q)));
  const toolbar = '<div class="toolbar">' +
    '<input class="inp" id="contract-q" placeholder="搜索路径 / 说明" style="max-width:220px" value="' + esc(state.contractQuery || "") + '">' +
    btn("搜索", "contract.search", {}, "sm") +
    sel2("tag", tags, state.contractTag || "全部", 'data-change="contract.filter"') +
    sel2("method", methods, state.contractMethod || "全部", 'data-change="contract.filter"') +
    btn("重置", "contract.clear", {}, "sm") +
    '<span class="sp"></span>' + pill(rows.length + " / " + contract.count, "info") + "</div>";
  const cols = [
    { t: "方法", render: r => pill(r.method, r.method === "GET" ? "info" : "ok") },
    { t: "路径", render: r => '<span class="mono">' + esc(r.path) + "</span>" },
    { t: "说明", render: r => esc(r.summary || r.desc || "—") },
    { t: "分组", render: r => pill(r.tag, "mute") },
    { t: "鉴权", render: r => esc(r.auth) },
    { t: "操作", render: r => btn(r.testable ? "详情 / 测试" : "查看详情",
        "contract.detail", { method: r.method, path: r.path }, "sm") }
  ];
  const testable = (contract.endpoints || []).filter(r => r.testable).length;
  const mutating = (contract.endpoints || []).filter(r => r.destructive).length;
  return head("接口文档", "每个接口都能展开看参数、示例和 curl，并直接在页面上试请求",
      btn("打开 /docs", "contract.docs", {}, "primary"))
    + note("接口契约由服务端 OpenAPI 实时生成；点「详情 / 测试」可以看完整参数并直接发请求，DELETE / PUT / PATCH 会修改真实数据。")
    + grid(kpi("端点", contract.count + " 个", "共 " + (contract.tags || []).length + " 个分组")
        + kpi("可直接测试", testable + " 个", "SSE 事件流除外")
        + kpi("会修改数据", mutating + " 个", "DELETE / PUT / PATCH")
        + kpi("版本", esc(contract.version), esc(contract.title)))
    + grid(card("端点清单", toolbar + tbl(cols, rows, { empty: "没有匹配的接口" }), { cls: "col-12" }))
    + grid(card("怎么用", '<div class="srcline">1. 先按分组或关键词找到接口。</div>'
        + '<div class="srcline">2. 点「详情 / 测试」查看参数、请求/响应示例与 curl。</div>'
        + '<div class="srcline">3. 在弹窗里填参数后点「发送请求」，响应会直接显示在下方。</div>'
        + '<div class="srcline">4. 需要完整 Swagger 体验时，点右上角「打开 /docs」。</div>', { cls: "col-7" }),
      card("错误码", code(esc("AH-RUNTIME-422  DAG 校验失败（含具体节点）\nAH-SKILL-422    技能发布校验失败（工具 / 模型别名）\nAH-TOOL-422     工具参数校验失败或配置缺失\nAH-TOOL-501     工具无可用实现\nAH-TOOL-504     工具执行超时\nAH-AUTH-401     凭证无效 / 停用 / 过期\nAH-QUOTA-429    超出 RPS / 并发 / 日配额\nAH-MODEL-502    Provider 不可用且降级链耗尽")), { cls: "col-5" }));
};

/* ===== 动作：知识库 ===== */
A2["kb.new"] = () => modalForm("上传文档", "kb.create",
  fld("从本地文件导入（.md / .txt / .csv）", '<input class="inp" type="file" accept=".md,.txt,.csv" data-change="kb.file">')
  + fld("文件名", inp("name", "客户风险分类口径_v4.md"))
  + '<div class="frow">' + fld("类型", sel("kind", ["口径", "报文", "规则"], "口径")) + fld("说明", inp("note", "风险等级划分与阈值")) + "</div>"
  + fld("正文（按段落切分，约 500 字一片）", '<textarea class="ta" id="kbContent" name="content" style="min-height:160px">风险等级划分：A 级不高于 30 分，B 级 31 至 55 分，C 级 56 至 75 分，D 级高于 75 分。\n授信使用率超过 80% 视为额度紧张，需在报告中单独提示。</textarea>'),
  "上传并建索引");
A2["kb.file"] = (d, el) => {
  const file = el.files && el.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    const ta = $("#kbContent");
    if (ta) ta.value = String(reader.result || "");
    const form = el.closest("form");
    const nameInput = form && form.querySelector('[name="name"]');
    if (nameInput && !nameInput.value.trim()) nameInput.value = file.name;
    toast("已读入文件", file.name + " · " + Math.round(file.size / 1024) + " KB");
  };
  reader.readAsText(file, "utf-8");
};
A2["kb.create"] = async form => {
  const o = formObj(form);
  if (!(o.content || "").trim()) { toast("正文为空", "请填写或导入文档内容", "err"); return; }
  try {
    const doc = await api("/kb/documents", { method: "POST", body: o });
    closeModal(); toast("已上传", doc.name + " · " + doc.chunks + " 个切片"); render();
  } catch (e) { toast("上传失败", e.message, "err"); }
};
A2["kb.view"] = async d => {
  const doc = await api("/kb/documents/" + d.id);
  modal("文档正文 · " + doc.name, kv([
    ["编号", '<span class="mono">' + esc(doc.id) + "</span>", true],
    ["类型", esc(doc.kind)], ["字数", num(doc.chars)],
    ["切片", doc.chunks + " 条"], ["更新", esc(doc.updated)],
  ]) + code(esc(doc.content || "（空文档）")), btn("关闭", "modal.close", {}, "primary"));
};
A2["kb.edit"] = async d => {
  const doc = await api("/kb/documents/" + d.id);
  modalForm("编辑文档 · " + doc.name, "kb.save",
    '<input type="hidden" name="id" value="' + esc(doc.id) + '">'
    + fld("文件名", inp("name", doc.name))
    + '<div class="frow">' + fld("类型", sel("kind", ["口径", "报文", "规则"], doc.kind)) + fld("说明", inp("note", doc.note)) + "</div>"
    + fld("正文（修改后自动重建该文档切片）", '<textarea class="ta" name="content" style="min-height:220px">' + esc(doc.content) + "</textarea>"),
    "保存");
};
A2["kb.save"] = async form => {
  const o = formObj(form);
  try {
    const doc = await api("/kb/documents/" + o.id, {
      method: "PATCH",
      body: { name: o.name, kind: o.kind, note: o.note, content: o.content },
    });
    closeModal(); toast("已保存", doc.name + " · " + doc.chunks + " 个切片"); render();
  } catch (e) { toast("保存失败", e.message, "err"); }
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
A2["kb.delete"] = d => confirmBox("删除文档", "确认删除「" + d.name + "」？关联切片会一并移除。",
  "kb.doDelete", { id: d.id }, "删除");
A2["kb.doDelete"] = async d => {
  try { await api("/kb/documents/" + d.id, { method: "DELETE" }); closeModal(); toast("已删除", "关联切片一并移除", "warn"); render(); }
  catch (e) { toast("删除失败", e.message, "err"); }
};
A2["kb.filter"] = (d, el) => { state.kbKind = el.value; render(); };
A2["kb.refresh"] = () => render();
A2["kb.search"] = async form => {
  const o = formObj(form);
  if (!(o.q || "").trim()) { toast("查询语句为空", "请输入至少一个关键词", "err"); return; }
  try {
    const res = await api("/kb/retrieve", {
      method: "POST",
      body: { q: o.q, top_k: Number(o.top_k) || 4, mode: o.mode || "hybrid" },
    });
    state.kbResult = { rows: res.rows, terms: res.terms, mode: res.mode };
    state.kbQuery = o.q;
    state.kbMode = res.mode;
    render(); toast("检索完成", res.rows.length + " 条候选");
  } catch (e) { toast("检索失败", e.message, "err"); }
};

/* ===== 动作：模型接入 ===== */
const CAPS_OPTIONS = ["聊天 / 流式", "聊天 / 流式 / 函数调用", "聊天 / 流式 / 函数调用 / JSON 模式", "聊天 / 流式 / 函数调用 / embedding", "聊天", "embedding"];

function providerFields(p = {}) {
  return fld("名称", inp("name", p.name || ""))
    + '<div class="frow">' + fld("类型", sel("kind", ["本地", "云端", "规则"], p.kind || "本地"))
    + fld("能力", sel("caps", CAPS_OPTIONS, p.caps || "聊天 / 流式")) + "</div>"
    + fld("base_url（OpenAI 兼容）", inp("base_url", p.url || "http://127.0.0.1:11434/v1"))
    + fld("模型（逗号或 / 分隔）", inp("models", p.models || "qwen2.5:7b"))
    + fld("API Key（留空表示不修改）", '<input class="inp" name="api_key" type="password" placeholder="' + (p.api_key_set ? "已配置，留空保持" : "未配置") + '">');
}

A2["provider.new"] = () => modalForm("注册 Provider", "provider.create", providerFields(), "注册");
A2["provider.create"] = async form => {
  const o = formObj(form);
  try {
    await api("/providers", { method: "POST", body: o });
    closeModal(); toast("已注册", o.name); render();
  } catch (e) { toast("注册失败", e.message, "err"); }
};
A2["provider.edit"] = async d => {
  const p = (await api("/providers")).find(x => x.id === d.id);
  if (!p) { toast("Provider 不存在", d.id, "err"); return; }
  modalForm("编辑 Provider · " + p.name, "provider.save",
    '<input type="hidden" name="id" value="' + esc(p.id) + '">' + providerFields(p), "保存");
};
A2["provider.save"] = async form => {
  const o = formObj(form);
  const body = { name: o.name, kind: o.kind, base_url: o.base_url, models: o.models, caps: o.caps };
  if (o.api_key) body.api_key = o.api_key;
  try {
    await api("/providers/" + o.id, { method: "PATCH", body });
    closeModal(); toast("已保存", o.name); render();
  } catch (e) { toast("保存失败", e.message, "err"); }
};
A2["provider.delete"] = d => confirmBox("删除 Provider", "确认删除「" + d.name + "」？仍被别名引用时会拒绝删除。",
  "provider.doDelete", { id: d.id }, "删除");
A2["provider.doDelete"] = async d => {
  try { await api("/providers/" + d.id, { method: "DELETE" }); closeModal(); toast("已删除", d.id, "warn"); render(); }
  catch (e) { toast("删除失败", e.message, "err"); }
};
A2["provider.toggle"] = async d => {
  try { await api("/providers/" + d.id, { method: "PATCH", body: { status: d.status } }); toast("状态已更新", d.status); render(); }
  catch (e) { toast("操作失败", e.message, "err"); }
};
A2["provider.check"] = async d => {
  try {
    const res = await api("/providers/" + d.id + "/check", { method: "POST" });
    toast(res.ok ? "检查通过" : "检查未通过", res.detail + " · " + res.latency_ms + " ms",
      res.ok ? "ok" : "err");
    render();
  } catch (e) { toast("检查失败", e.message, "err"); }
};
A2["alias.new"] = async () => {
  const providers = await api("/providers");
  modalForm("新增模型别名", "alias.create",
    fld("别名", inp("alias", ""))
    + fld("Provider", sel("provider_name", providers.map(p => [p.name, p.name + (p.status === "stopped" ? "（停用）" : "")]), providers[0] && providers[0].name))
    + '<div class="frow">' + fld("模型", inp("model", "rule-based")) + fld("能力", sel("caps", CAPS_OPTIONS, "聊天 / 流式")) + "</div>",
    "创建");
};
A2["alias.create"] = async form => {
  const o = formObj(form);
  try { await api("/aliases", { method: "POST", body: o }); closeModal(); toast("已创建", o.alias); render(); }
  catch (e) { toast("创建失败", e.message, "err"); }
};
A2["alias.edit"] = async d => {
  const [aliases, providers] = await Promise.all([api("/aliases"), api("/providers")]);
  const a = aliases.find(x => x.alias === d.alias);
  if (!a) { toast("别名不存在", d.alias, "err"); return; }
  modalForm("编辑别名 · " + a.alias, "alias.save",
    '<input type="hidden" name="alias" value="' + esc(a.alias) + '">'
    + fld("Provider", sel("provider_name", providers.map(p => [p.name, p.name]), a.provider))
    + '<div class="frow">' + fld("模型", inp("model", a.model)) + fld("能力", sel("caps", CAPS_OPTIONS, a.caps)) + "</div>",
    "保存");
};
A2["alias.save"] = async form => {
  const o = formObj(form);
  try { await api("/aliases/" + o.alias, { method: "PATCH", body: { provider_name: o.provider_name, model: o.model, caps: o.caps } }); closeModal(); toast("已保存", o.alias); render(); }
  catch (e) { toast("保存失败", e.message, "err"); }
};
A2["alias.delete"] = d => confirmBox("删除别名", "确认删除「" + d.alias + "」？仍在降级链中时会拒绝删除。",
  "alias.doDelete", { alias: d.alias }, "删除");
A2["alias.doDelete"] = async d => {
  try {
    const res = await api("/aliases/" + d.alias, { method: "DELETE" });
    closeModal();
    toast("已删除", res.promoted ? "默认别名已切换为 " + res.promoted : d.alias, "warn");
    render();
  } catch (e) { toast("删除失败", e.message, "err"); }
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
A2["trace.status"] = (d, el) => { state.traceStatus = el.value; state.traceId = null; render(); };
A2["trace.search"] = () => {
  const input = $("#traceQ");
  state.searchText = input ? input.value.trim() : "";
  state.traceId = null;
  render();
};
A2["trace.clear"] = () => { state.searchText = ""; state.traceStatus = "全部"; state.traceId = null; render(); };
A2["stats.range"] = (d, el) => { state.statsDays = Number(el.value) || 0; render(); };
A2["stats.openRun"] = d => { if (d.id) go("agents/runs", { runId: d.id }); };
A2["contract.docs"] = () => { window.open("/docs", "_blank"); };

A2["contract.search"] = () => {
  const el = $("#contract-q");
  state.contractQuery = el ? el.value.trim() : "";
  render();
};
A2["contract.filter"] = (d, el) => {
  if (el.name === "tag") state.contractTag = el.value;
  if (el.name === "method") state.contractMethod = el.value;
  render();
};
A2["contract.clear"] = () => {
  state.contractQuery = ""; state.contractTag = "全部"; state.contractMethod = "全部";
  render();
};
A2["contract.detail"] = async d => {
  const contract = await api("/system/contract");
  const row = (contract.endpoints || []).find(x => x.method === d.method && x.path === d.path);
  if (!row) { toast("接口不存在", d.method + " " + d.path, "err"); return; }
  state.contractDetail = row;
  const params = row.params || [];
  const pathParams = params.filter(p => p.in === "path");
  const queryParams = params.filter(p => p.in === "query");
  const bodyText = row.request_example != null
    ? JSON.stringify(row.request_example, null, 2)
    : (["POST", "PUT", "PATCH"].includes(row.method) && row.request_schema ? "{}" : "");
  const paramRows = params.map(p => [
    p.name + " (" + p.in + (p.required ? " · 必填" : "") + ")",
    '<span class="tag">' + esc(p.type) + "</span> " + esc(p.desc || "—")
    + (p.example != null ? ' <span class="s">示例 ' + esc(p.example) + "</span>" : "")
  ]);
  const pathInputs = pathParams.map(p => fld(esc(p.name) + (p.required ? " *" : ""),
    '<input class="inp" id="ct-p-' + esc(p.name) + '" value="'
      + esc(row.destructive ? "" : (p.example != null ? p.example : ""))
      + '" placeholder="' + esc(p.example != null ? p.example : "必填") + '">',
    esc(p.desc || ""))).join("");
  const queryInputs = queryParams.map(p => fld(esc(p.name) + (p.required ? " *" : ""),
    '<input class="inp" id="ct-q-' + esc(p.name) + '" value="'
      + esc(p.required ? (p.example != null ? p.example : "") : "")
      + '" placeholder="' + esc(p.example != null ? p.example : (p.default != null ? p.default : "可选")) + '">',
    esc(p.desc || ""))).join("");
  const keyField = row.auth === "—" ? "" : fld("Authorization",
    '<input class="inp" id="ct-key" value="' + esc(apiKey() || "ah_pat_demo_local_dev") + '">',
    "留空则不携带；本地默认凭证 ah_pat_demo_local_dev");
  const testPanel = row.testable
    ? note(row.destructive
        ? "这是会修改真实数据的接口，确认参数后再执行。"
        : "填好参数后点「发送请求」，响应会显示在下方。", row.destructive ? "warn" : "")
      + pathInputs + queryInputs
      + (bodyText ? fld("请求体（JSON）", '<textarea class="ta mono" id="ct-body" style="min-height:170px">' + esc(bodyText) + "</textarea>") : "")
      + keyField
      + '<div id="ct-result"></div>'
    : note("该接口是 SSE 流式接口，请用终端或 /docs 测试。", "warn");

  modal("接口详情 · " + row.method + " " + row.path,
    kv([
      ["方法 / 路径", pill(row.method, row.method === "GET" ? "info" : "ok") + ' <span class="mono">' + esc(row.path) + "</span>", true],
      ["分组", esc(row.tag)],
      ["鉴权", esc(row.auth)],
      ["可测试", row.testable ? pill("是", "ok") : pill("否（SSE）", "warn")],
    ])
    + (row.desc ? '<div class="srcline" style="margin-top:10px">' + esc(row.desc).replace(/\n/g, "<br>") + "</div>" : "")
    + '<h3 style="margin:14px 0 8px;font-size:13px">请求参数</h3>'
    + (paramRows.length ? kv(paramRows) : '<div class="s">无路径 / 查询参数</div>')
    + '<h3 style="margin:14px 0 8px;font-size:13px">请求示例</h3>'
    + (bodyText ? code(esc(bodyText)) : '<div class="s">无请求体</div>')
    + '<h3 style="margin:14px 0 8px;font-size:13px">响应示例</h3>'
    + (row.response_example != null
        ? code(esc(JSON.stringify(row.response_example, null, 2)))
        : '<div class="s">该接口未提供固定示例，调用后以实际响应为准</div>')
    + '<h3 style="margin:14px 0 8px;font-size:13px">curl</h3>' + code(esc(row.curl))
    + '<h3 style="margin:14px 0 8px;font-size:13px">直接测试</h3>' + testPanel,
    btn("关闭", "modal.close", {}, "") +
    btn("复制 curl", "contract.copy", {}, "") +
    btn("打开 /docs", "contract.docs", {}, "") +
    (row.testable ? btn("发送请求", "contract.run", { method: row.method, path: row.path }, "primary") : ""));
};
A2["contract.copy"] = async () => {
  const text = state.contractDetail && state.contractDetail.curl;
  if (!text) return;
  try { await navigator.clipboard.writeText(text); toast("已复制 curl", "可直接在终端执行"); }
  catch (e) { toast("复制失败", "请手动选择代码块复制", "err"); }
};
A2["contract.run"] = async d => {
  const row = state.contractDetail;
  if (!row) { toast("请重新打开接口详情", "", "err"); return; }
  const box = $("#ct-result");
  let path = row.path;
  for (const p of (row.params || []).filter(x => x.in === "path")) {
    const el = $("#ct-p-" + p.name);
    const value = el ? el.value.trim() : "";
    if (!value) { toast("缺少路径参数", p.name, "err"); return; }
    path = path.replace("{" + p.name + "}", encodeURIComponent(value));
  }
  const query = [];
  for (const p of (row.params || []).filter(x => x.in === "query")) {
    const el = $("#ct-q-" + p.name);
    const value = el ? el.value.trim() : "";
    if (value) query.push(encodeURIComponent(p.name) + "=" + encodeURIComponent(value));
    else if (p.required) { toast("缺少必填查询参数", p.name, "err"); return; }
  }
  let body;
  const bodyEl = $("#ct-body");
  if (bodyEl) {
    const raw = bodyEl.value.trim();
    if (raw) {
      try { body = JSON.stringify(JSON.parse(raw)); }
      catch (e) { toast("请求体不是合法 JSON", e.message, "err"); return; }
    }
  }
  const keyEl = $("#ct-key");
  const key = keyEl ? keyEl.value.trim() : "";
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (key) headers.Authorization = "Bearer " + key;
  const url = location.origin + path + (query.length ? "?" + query.join("&") : "");
  box.innerHTML = note("执行中…（最长等待 120 秒）");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 120000);
  const started = performance.now();
  try {
    const resp = await fetch(url, { method: row.method, headers: headers, body: body, signal: controller.signal });
    const text = await resp.text();
    const dur = Math.round(performance.now() - started);
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    const payload = data != null ? JSON.stringify(data, null, 2) : (text || "（无响应体）");
    box.innerHTML = kv([
      ["结果", resp.ok ? pill("HTTP " + resp.status, "ok") : pill("HTTP " + resp.status, "err"), true],
      ["耗时", dur + " ms"],
      ["地址", '<span class="mono">' + esc(url) + "</span>", true],
    ]) + (data && data.report_url
      ? '<div style="margin:6px 0"><a class="tag info" target="_blank" href="' + esc(data.report_url) + '">打开报告</a></div>'
      : "")
      + code(esc(payload));
  } catch (e) {
    box.innerHTML = note(e.name === "AbortError" ? "请求超时（>120s）" : ("请求失败：" + e.message), "err");
  } finally {
    clearTimeout(timer);
  }
};
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
A2["key.rotate"] = d => confirmBox("轮换凭证", "确认轮换「" + d.app + "」的凭证？旧明文会立即失效，配额与统计保持不变。",
  "key.doRotate", { id: d.id }, "轮换");
A2["key.doRotate"] = async d => {
  try {
    const res = await api("/keys/" + d.id + "/rotate", { method: "POST", body: {} });
    closeModal(); render();
    modal("凭证已轮换 · 明文仅展示一次", code('<span style="color:#86efac">' + esc(res.plain) + "</span>") +
      note("旧明文已失效，请立即更新业务侧配置。"), btn("我已保存", "modal.close", {}, "primary"));
  } catch (e) { toast("轮换失败", e.message, "err"); }
};
A2["key.revoke"] = d => confirmBox("吊销凭证", "确认吊销「" + d.app + "」？吊销后调用会返回 AH-AUTH-401，可随时恢复。",
  "key.doRevoke", { id: d.id }, "吊销");
A2["key.doRevoke"] = async d => {
  try { await api("/keys/" + d.id + "/revoke", { method: "POST" }); closeModal(); toast("已吊销", "调用将被拒绝", "warn"); render(); }
  catch (e) { toast("吊销失败", e.message, "err"); }
};

/* ===== 动作：配额 ===== */
A2["quota.edit"] = async d => {
  const list = await api("/quota");
  const q = list.find(x => x.key_id === d.id);
  if (!q) { toast("无配额记录", "请刷新后重试", "warn"); return; }
  modalForm("配额与限流 · " + q.app, "quota.save",
    '<input type="hidden" name="id" value="' + esc(q.key_id) + '">'
    + '<div class="frow">'
    + fld("RPS（0 不限）", inp("rps", q.rps, 'type="number"'))
    + fld("并发上限（0 不限）", inp("concurrency", q.concurrency, 'type="number"'))
    + "</div>"
    + '<div class="frow">'
    + fld("日调用配额（0 不限）", inp("daily_calls", q.daily_calls, 'type="number"'))
    + fld("启用限额", sel("enabled", [["1", "启用"], ["0", "关闭"]], q.enabled ? "1" : "0"))
    + "</div>"
    + note("今日已用 " + num(q.used_calls) + " 次，拦截 " + q.blocked + " 次；调整后立即生效。"),
    "保存");
};

A2["quota.save"] = async form => {
  const o = formObj(form);
  try {
    await api("/quota/" + o.id, {
      method: "PUT",
      body: {
        rps: Number(o.rps) || 0,
        concurrency: Number(o.concurrency) || 0,
        daily_calls: Number(o.daily_calls) || 0,
        enabled: o.enabled === "1",
      },
    });
    closeModal(); toast("配额已保存", "立即生效"); render();
  } catch (e) { toast("保存失败", e.message, "err"); }
};

A2["quota.reset"] = async d => {
  try { await api("/quota/" + d.id + "/reset", { method: "POST" }); toast("用量已重置", "今日调用数归零"); render(); }
  catch (e) { toast("重置失败", e.message, "err"); }
};

/* ===== 动作：对外接入 ===== */
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


