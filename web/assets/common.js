"use strict";

/* ===== 基础工具 ===== */
const $ = s => document.querySelector(s);
const esc = o => String(o == null ? "" : o).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n, d = 0) => Number(n || 0).toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => (v == null ? "—" : (Number(v) * 100).toFixed(d) + "%");
const money = v => (v == null ? "—" : Number(v).toLocaleString("zh-CN", { maximumFractionDigits: 2 }));

/* ===== API ===== */
const API_BASE = "/api/v1";

function apiKey() { try { return localStorage.getItem("ah_key") || ""; } catch (e) { return ""; } }

async function api(path, options = {}) {
  const opts = { headers: { "Content-Type": "application/json" }, ...options };
  const key = apiKey();
  if (key) opts.headers = { ...opts.headers, Authorization: "Bearer " + key };
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  const resp = await fetch(API_BASE + path, opts);
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { message: text }; }
  if (!resp.ok) {
    const msg = (data && (data.message || data.detail)) || ("请求失败 " + resp.status);
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.code = data && data.code;
    err.payload = data;
    throw err;
  }
  return data;
}

/* ===== 组件 ===== */
function pill(text, tone = "mute") { return '<span class="tag ' + tone + '">' + esc(text) + "</span>"; }
function btn(label, act, data = {}, cls = "") {
  const attrs = Object.entries(data).map(([k, v]) => 'data-' + k + '="' + esc(v) + '"').join(" ");
  return '<button class="btn ' + cls + '" data-act="' + act + '" ' + attrs + ">" + label + "</button>";
}
function card(title, body, opts = {}) {
  return '<section class="card ' + (opts.cls || "col-12") + '"><div class="card-hd"><h3>' + esc(title) + "</h3>" +
    (opts.right ? '<div class="r">' + opts.right + "</div>" : "") + '</div><div class="card-bd' + (opts.tight ? " tight" : "") + '">' +
    body + "</div>" + (opts.note ? '<div class="card-note">' + opts.note + "</div>" : "") + "</section>";
}
function grid() { return '<div class="grid">' + Array.from(arguments).join("") + "</div>"; }
function tbl(cols, rows, opts = {}) {
  if (!rows || !rows.length) return '<div class="empt">' + (opts.empty || "暂无数据") + "</div>";
  return '<div class="tbl-wrap"><table class="tbl"><thead><tr>' +
    cols.map(c => "<th>" + esc(c.t) + "</th>").join("") + "</tr></thead><tbody>" +
    rows.map(r => "<tr>" + cols.map(c => "<td>" + (c.render ? c.render(r) : esc(r[c.k] == null ? "—" : r[c.k])) + "</td>").join("") + "</tr>").join("") +
    "</tbody></table></div>";
}
function kv(pairs) {
  return '<dl class="kv">' + pairs.map(p => "<dt>" + esc(p[0]) + "</dt><dd>" + (p[2] ? p[1] : esc(p[1])) + "</dd>").join("") + "</dl>";
}
function fld(label, control, hint = "") {
  return '<div class="fld"><label>' + esc(label) + "</label>" + control + (hint ? '<div class="hint">' + hint + "</div>" : "") + "</div>";
}
function inp(name, value = "", extra = "") { return '<input class="inp" name="' + name + '" value="' + esc(value) + '" ' + extra + ">"; }
function sel(name, opts, value) {
  return '<select class="sel" name="' + name + '">' + opts.map(o => {
    const v = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o;
    return '<option value="' + esc(v) + '"' + (String(value) === String(v) ? " selected" : "") + ">" + esc(t) + "</option>";
  }).join("") + "</select>";
}
function sel2(name, opts, value, extra) { return sel(name, opts, value).replace("<select ", "<select " + extra + " "); }
function statusPill(s) {
  const raw = String(s || "");
  const map = { published: "已发布", draft: "草稿", archived: "已归档", 启用: "启用", 停用: "停用", 成功: "成功", 失败: "失败" };
  const label = map[raw] || raw || "—";
  const tone = /COMPLETED|published|启用|成功|健康|PASS|默认/.test(raw) ? "ok"
    : /WAITING|draft|草稿|PENDING|灰度|观察|解析中/.test(raw) ? "warn"
    : /FAILED|REJECTED|停用|失败|BLOCK|异常/.test(raw) ? "err" : "mute";
  return pill(label, tone);
}
function head(title, sub, actions = "") {
  return '<div class="pagehead"><div><h2>' + esc(title) + "</h2><p>" + esc(sub) + '</p></div><div class="ha">' + actions + "</div></div>";
}
function note(text, tone = "") { return '<div class="alertline ' + tone + '">' + text + "</div>"; }
function bar(v, tone = "") { return '<div class="bar ' + tone + '"><i style="width:' + Math.min(100, Math.max(0, (v || 0) * 100)) + '%"></i></div>'; }
function kpi(l, v, d) { return '<div class="kpi col-3"><div class="l">' + esc(l) + '</div><div class="v">' + v + '</div><div class="d">' + esc(d || "") + "</div></div>"; }
function code(text) { return '<div class="code">' + text + "</div>"; }

/* ===== 弹窗与提示 ===== */
function modal(title, body, footer = "") {
  $("#modal-root").innerHTML = '<div class="mask" data-act="modal.mask"><div class="dialog"><div class="dialog-hd"><h3>' +
    esc(title) + '</h3><span class="x" data-act="modal.close">×</span></div><div class="dialog-bd">' + body +
    '</div><div class="dialog-ft">' + footer + "</div></div></div>";
}
function closeModal() { $("#modal-root").innerHTML = ""; }
function modalForm(title, action, body, submitLabel = "确定") {
  const fid = "f_" + Math.random().toString(16).slice(2, 8);
  modal(title, '<form id="' + fid + '" data-form="' + action + '">' + body + "</form>",
    btn("取消", "modal.close", {}, "") + '<button type="submit" form="' + fid + '" class="btn primary">' + esc(submitLabel) + "</button>");
}
function toast(title, msg = "", tone = "ok") {
  const el = document.createElement("div");
  el.className = "toast " + tone;
  el.innerHTML = "<b>" + esc(title) + "</b>" + esc(msg);
  $("#toast-root").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = ".2s"; setTimeout(() => el.remove(), 220); }, 3200);
}
function formObj(form) { return Object.fromEntries(new FormData(form).entries()); }
function fmtTime(s) { return s || "—"; }
