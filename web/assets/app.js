"use strict";

const PAGES = window.PAGES || {};
const ACTIONS = window.ACTIONS || {};

/* 弹窗通用动作：取消 / × / 点击遮罩关闭 */
ACTIONS["modal.close"] = () => closeModal();

/* ===== 导航信息架构 ===== */
const NAV = [
  { id: "agents", name: "智能体", ico: "AG", group: "能力资产", tabs: [
    ["list", "列表"], ["dag", "DAG 导入"], ["runs", "运行记录"], ["playground", "运行调试"]] },
  { id: "skills", name: "技能", ico: "SK", group: "能力资产", tabs: [["list", "技能列表"]] },
  { id: "tools", name: "工具", ico: "TL", group: "能力资产", tabs: [["list", "工具清单"], ["calls", "调用记录"]] },
  { id: "kb", name: "知识库", ico: "KB", group: "能力资产", tabs: [["docs", "文档与切片"], ["test", "检索测试"]] },
  { id: "models", name: "模型接入", ico: "MD", group: "能力资产", tabs: [["list", "Provider 与别名"], ["routing", "降级链"]] },
  { id: "observe", name: "运行监控", ico: "OB", group: "运行与接入", tabs: [
    ["traces", "Trace"], ["stats", "统计"], ["langfuse", "Langfuse"]] },
  { id: "access", name: "接入与凭证", ico: "AP", group: "运行与接入", tabs: [
    ["keys", "AppKey"], ["invoke", "对外调用"], ["contract", "接口契约"]] },
];

/* ===== 运行时状态 ===== */
const state = {
  agentId: null, runId: null, traceId: null,
  dagText: null, dagStructure: null, dagErrors: null, dagWarnings: null, dagSel: null, dagAgent: null,
  payloadText: null, kbResult: null, kbQuery: "", chainDraft: null, searchText: "",
};
window.state = state;

/* ===== 路由 ===== */
function route() {
  const raw = (location.hash || "").replace(/^#\/?/, "").trim();
  const parts = raw.split("/");
  const nav = NAV.find(n => n.id === parts[0]) || NAV[0];
  const sub = parts[1] || nav.tabs[0][0];
  return { nav, sub, key: nav.id + "/" + sub };
}
function go(path, params = {}) {
  Object.assign(state, params || {});
  const target = "#/" + path;
  if (location.hash === target) { render(); return; }
  location.hash = target;
}
window.go = go;

/* ===== 外壳 ===== */
function sidebarHTML(activeId) {
  let h = '<div class="brand"><div class="logo">AH</div><div><div class="brand-t">AgentHub</div>' +
    '<div class="brand-s">智能体运行支撑平台</div></div></div>';
  h += '<div class="ctx"><div class="ctx-l">当前项目</div><div class="ctx-v">客户全景分析</div>' +
    '<div class="ctx-s">本地单机 · 真实后端</div></div>';
  let last = "";
  NAV.forEach(n => {
    if (n.group !== last) { h += '<div class="navgroup">' + esc(n.group) + "</div>"; last = n.group; }
    h += '<a class="navitem' + (n.id === activeId ? " on" : "") + '" href="#/' + n.id + '">' +
      '<span class="navico">' + n.ico + "</span>" + esc(n.name) + "</a>";
  });
  h += '<div class="sidefoot"><span class="mockdot"></span>已连接后端 API<br>数据持久化在 data/agenthub.db</div>';
  return h;
}

function topbarHTML(nav, sub) {
  const subName = (nav.tabs.find(t => t[0] === sub) || [])[1] || "";
  return '<div class="crumb"><b>AgentHub</b><span>/</span><b>' + esc(nav.name) + "</b>" +
    (subName ? "<span>/</span>" + esc(subName) : "") + "</div>" +
    '<div class="topright">' +
    '<button class="btn sm" data-act="key.setting">设置 Key</button>' +
    '<a class="btn sm" href="/docs" target="_blank">API 文档</a>' +
    '<a class="btn sm" href="/reports/" target="_blank">报告目录</a>' +
    '<span class="tag ok">后端在线</span></div>';
}

function tabsHTML(nav, sub) {
  return '<div class="tabs">' + nav.tabs.map(t =>
    '<a class="tab' + (t[0] === sub ? " on" : "") + '" href="#/' + nav.id + "/" + t[0] + '">' + esc(t[1]) + "</a>").join("") + "</div>";
}

/* ===== 渲染 ===== */
async function render() {
  const r = route();
  document.querySelector("#sidebar").innerHTML = sidebarHTML(r.nav.id);
  document.querySelector("#topbar").innerHTML = topbarHTML(r.nav, r.sub);
  const tabs = tabsHTML(r.nav, r.sub);
  const view = document.querySelector("#view");
  const fn = PAGES[r.key];
  if (!fn) {
    view.innerHTML = tabs + grid(card("页面建设中", '<div class="empt">' + esc(r.key) + " 尚未实现</div>"));
    return;
  }
  const token = r.key + ":" + Date.now();
  render._token = token;
  view.innerHTML = tabs + '<div class="empt">加载中…</div>';
  try {
    const html = await fn();
    if (render._token !== token) return;
    view.innerHTML = tabs + html;
  } catch (err) {
    if (render._token !== token) return;
    const extra = err.code ? kv([["错误码", err.code]]) : "";
    view.innerHTML = tabs + grid(card("加载失败",
      note(esc(err.message || String(err)), "err") + extra +
      '<div class="footnote">请确认后端已启动：python -m agenthub</div>', { cls: "col-12" }));
  }
}
window.render = render;

/* ===== 事件委托 ===== */
function dispatch(name, el) {
  const fn = ACTIONS[name];
  if (!fn) { console.warn("未实现动作：" + name); return; }
  try {
    const res = fn(el.dataset || {}, el);
    if (res && typeof res.catch === "function") res.catch(e => toast("操作失败", e.message || String(e), "err"));
  } catch (e) {
    toast("操作失败", e.message || String(e), "err");
  }
}

document.addEventListener("click", e => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  if (el.dataset.act === "modal.mask") { if (e.target === el) closeModal(); return; }
  e.preventDefault();
  dispatch(el.dataset.act, el);
});

document.addEventListener("change", e => {
  const el = e.target.closest("[data-change]");
  if (el) dispatch(el.dataset.change, el);
});

document.addEventListener("submit", e => {
  const form = e.target.closest("[data-form]");
  if (!form) return;
  e.preventDefault();
  const fn = ACTIONS[form.dataset.form];
  if (!fn) { console.warn("未实现表单：" + form.dataset.form); return; }
  try {
    const res = fn(form);
    if (res && typeof res.catch === "function") res.catch(err => toast("提交失败", err.message || String(err), "err"));
  } catch (err) {
    toast("提交失败", err.message || String(err), "err");
  }
});

window.addEventListener("hashchange", render);
window.addEventListener("beforeunload", () => { if (window.__es) try { window.__es.close(); } catch (e) {} });

/* ===== 启动 ===== */
if (!location.hash) location.hash = "#/agents/list";
render();
