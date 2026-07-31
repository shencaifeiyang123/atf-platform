(function() {
'use strict';

// ============= 工具 =============
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);
const api = async (path, opts = {}) => {
  const r = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  // 401 = 会话过期或未登录：弹蒙层，让用户重新登录
  if (r.status === 401) {
    try { showLoginOverlay(); } catch (e) { /* 蒙层未初始化时忽略 */ }
    throw new Error("未登录或会话过期，请重新登录");
  }
  if (!r.ok) throw new Error((await r.text()).slice(0, 300));
  const ct = r.headers.get("content-type") || "";
  return ct.includes("json") ? r.json() : r.text();
};
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

// ============= 分页工具 =============
// 全前端分页：把整列数据切片，再渲染分页控件。
// 使用方：维护一个 state {page, pageSize}，调用 paginate(items, state)
// 取得当前页切片，再调用 renderPager() 把控件挂到容器上。
const PAGE_SIZE_OPTIONS = [12, 24, 48];
function paginate(items, state) {
  const total = items.length;
  const pageSize = Math.max(1, state.pageSize | 0);
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  // clamp page
  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;
  const start = (state.page - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    page: state.page, pageSize, total, totalPages,
    start: total ? start + 1 : 0,
    end: Math.min(start + pageSize, total),
  };
}

// 计算页码按钮序列：1 ... cur-1 cur cur+1 ... last
function _pagerPages(cur, last) {
  if (last <= 7) {
    const arr = []; for (let i = 1; i <= last; i++) arr.push(i); return arr;
  }
  const out = new Set([1, last, cur, cur - 1, cur + 1]);
  const sorted = [...out].filter((n) => n >= 1 && n <= last).sort((a, b) => a - b);
  const result = [];
  for (let i = 0; i < sorted.length; i++) {
    result.push(sorted[i]);
    if (i + 1 < sorted.length && sorted[i + 1] - sorted[i] > 1) result.push("…");
  }
  return result;
}

// 渲染分页控件到指定容器。`onChange(newState)` 会在用户切页 / 切换 pageSize 时回调。
function renderPager(containerSel, info, onChange) {
  const el = typeof containerSel === "string" ? $(containerSel) : containerSel;
  if (!el) return;
  if (info.total === 0) { el.classList.add("hidden"); el.innerHTML = ""; return; }
  el.classList.remove("hidden");
  const { page, pageSize, total, totalPages, start, end } = info;
  const pages = _pagerPages(page, totalPages);
  const sizeOpts = PAGE_SIZE_OPTIONS
    .map((n) => `<option value="${n}" ${n === pageSize ? "selected" : ""}>${n} / 页</option>`).join("");
  const pageBtns = pages.map((p) => {
    if (p === "…") return `<span class="pager-ellipsis">…</span>`;
    const active = p === page ? `data-active="true"` : "";
    return `<button class="pager-btn" data-page="${p}" ${active}>${p}</button>`;
  }).join("");
  el.innerHTML = `
    <div class="pager-info">
      <span>显示</span>
      <code>${start}</code><span>–</span><code>${end}</code>
      <span>共</span><code>${total}</code><span>条</span>
    </div>
    <div class="pager-controls">
      <button class="pager-btn" data-act="prev" ${page <= 1 ? "disabled" : ""} title="上一页">◂</button>
      ${pageBtns}
      <button class="pager-btn" data-act="next" ${page >= totalPages ? "disabled" : ""} title="下一页">▸</button>
      <select class="pager-size" data-act="size" title="每页条数">${sizeOpts}</select>
    </div>
  `;
  el.querySelectorAll(".pager-btn[data-page]").forEach((b) => {
    b.addEventListener("click", () => onChange({ page: parseInt(b.dataset.page, 10) }));
  });
  el.querySelector('[data-act="prev"]')?.addEventListener("click", () => onChange({ page: page - 1 }));
  el.querySelector('[data-act="next"]')?.addEventListener("click", () => onChange({ page: page + 1 }));
  el.querySelector('[data-act="size"]')?.addEventListener("change", (e) => {
    onChange({ pageSize: parseInt(e.target.value, 10), page: 1 });
  });
}

// ============= 模态框管理（Esc 关闭 + 焦点管理）=============
let _activeModal = null;
let _previousFocus = null;

function openModal(modalId) {
  const modal = $(modalId);
  if (!modal) return;

  // 保存当前焦点，关闭时恢复
  _previousFocus = document.activeElement;
  _activeModal = modal;

  // 显示模态框
  modal.classList.remove("hidden");
  modal.classList.add("flex");

  // 焦点移到第一个可聚焦元素
  setTimeout(() => {
    const firstFocusable = modal.querySelector('input:not([type=hidden]), textarea, select, button:not([disabled])');
    if (firstFocusable) firstFocusable.focus();
  }, 50);

  // 绑定 Esc 关闭（仅绑定一次）
  if (!modal._escBound) {
    modal._escBound = true;
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && _activeModal === modal) {
        closeModal(modalId);
      }
    });
  }
}

function closeModal(modalId) {
  const modal = $(modalId);
  if (!modal) return;

  modal.classList.add("hidden");
  modal.classList.remove("flex");

  if (_activeModal === modal) {
    _activeModal = null;
    // 恢复之前的焦点
    if (_previousFocus && _previousFocus.focus) {
      _previousFocus.focus();
    }
    _previousFocus = null;
  }
}

// ============= 加载状态组件 =============
function renderLoading(text = "加载中...") {
  return `
    <div class="space-y-3 py-6">
      <div class="loading-shimmer h-20 max-w-2xl mx-auto rounded"></div>
      <div class="loading-shimmer h-20 max-w-2xl mx-auto rounded"></div>
      <div class="loading-shimmer h-20 max-w-2xl mx-auto rounded"></div>
      <div class="loading-spinner justify-center mt-4">${escapeHtml(text)}</div>
    </div>
  `;
}

// 空状态组件
function renderEmptyState(title = "暂无数据", hint = "", actionText = "", actionUrl = "") {
  return `
    <div class="empty-state">
      <div class="ascii-art" aria-hidden="true">
        ╭───────────────╮<br>
        │               │<br>
        │    ─ ─ ─ ─    │<br>
        │               │<br>
        ╰───────────────╯
      </div>
      <h3>${escapeHtml(title)}</h3>
      ${hint ? `<p class="text-sm mt-2">${escapeHtml(hint)}</p>` : ""}
      ${actionText && actionUrl ? `<p class="action-hint"><a href="${escapeHtml(actionUrl)}">${escapeHtml(actionText)}</a></p>` : ""}
    </div>
  `;
}

// 统计面板组件
function renderStatPanel(stats) {
  const items = stats.map(s => `
    <div class="stat-item">
      <div class="stat-value">${escapeHtml(String(s.value))}</div>
      <div class="stat-label">${escapeHtml(s.label)}</div>
    </div>
  `).join("");
  return `<div class="stat-panel">${items}</div>`;
}

// 状态徽章组件
function renderStatusBadge(status, label) {
  const cls = status === "running" ? "status-badge running" : "status-badge";
  return `<span class="${cls}">${escapeHtml(label || status)}</span>`;
}

// ============= 维度元数据 =============
let dimensionsMeta = [
  { key: "alignment", label: "预期效果", desc: "" },
  { key: "boundary",  label: "边界兜底", desc: "" },
  { key: "industry",  label: "行业规范", desc: "" },
  { key: "badcase",   label: "Bad Case", desc: "" },
  { key: "security",  label: "安全性",  desc: "" },
];
let dimLabel = Object.fromEntries(dimensionsMeta.map(d => [d.key, d.label]));
async function loadDimensions() {
  try {
    const list = await api("/api/dimensions");
    if (Array.isArray(list) && list.length) {
      dimensionsMeta = list;
      dimLabel = Object.fromEntries(list.map(d => [d.key, d.label]));
    }
  } catch (_) {}
}

// 行业元数据（从 /api/templates/meta 获取）
let industryOptions = [
  { key: "general",          label: "通用" },
  { key: "education",        label: "教育" },
  { key: "finance",          label: "金融" },
  { key: "medical",          label: "医疗" },
  { key: "customer_service", label: "客服" },
  { key: "ecommerce",        label: "电商" },
];
async function loadIndustries() {
  try {
    const meta = await api("/api/templates/meta");
    if (Array.isArray(meta?.industries) && meta.industries.length) {
      industryOptions = meta.industries;
    }
  } catch (_) {}
}
// 渲染行业下拉；尽量保留旧值（兼容老数据中存的中文值）
function renderIndustrySelect(currentValue) {
  const sel = $("#f-industry");
  if (!sel) return;
  const seen = new Set();
  const opts = [];
  for (const it of industryOptions) {
    if (seen.has(it.key)) continue;
    seen.add(it.key);
    opts.push(`<option value="${escapeHtml(it.key)}">${escapeHtml(it.label)}</option>`);
  }
  // 兼容：当前值不在枚举里（例如老数据是中文「通用」），追加一个保留它的选项
  const v = currentValue || "general";
  const matchedKey = industryOptions.find(o => o.key === v || o.label === v)?.key;
  if (!matchedKey) {
    opts.push(`<option value="${escapeHtml(v)}">${escapeHtml(v)}（自定义）</option>`);
    sel.innerHTML = opts.join("");
    sel.value = v;
  } else {
    sel.innerHTML = opts.join("");
    sel.value = matchedKey;
  }
}

// ============= Tab 切换 =============
function switchTab(name) {
  ["agents", "cases", "runs", "schedules", "templates"].forEach((n) => {
    $("#tab-" + n).classList.toggle("hidden", n !== name);
  });
  $$(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "agents" && typeof loadAgents === "function") loadAgents();
  if (name === "cases" && typeof loadCasesTab === "function") loadCasesTab();
  if (name === "runs" && typeof loadRuns === "function") loadRuns();
  if (name === "schedules" && typeof loadSchedules === "function") loadSchedules();
  if (name === "templates" && typeof loadTemplatesTab === "function") loadTemplatesTab();
}

// ============= 时间格式化（统一北京时间 UTC+8）=============
function _formatTime(ts) {
  if (!ts) return "";
  try {
    const d = new Date(Number(ts));
    return d.toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hour12: false,
    });
  } catch { return ""; }
}
// 简短时间格式（月-日 时:分），用于列表内联展示
function _formatTimeShort(ts) {
  if (!ts) return "";
  try {
    const d = new Date(Number(ts));
    return d.toLocaleString("zh-CN", {
      timeZone: "Asia/Shanghai",
      month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
      hour12: false,
    });
  } catch { return ""; }
}
// 时长（毫秒 → 「Xm Ys」/「Xs」/「Xh Ym」），用于「耗时 ...」展示
function _formatDuration(ms) {
  const n = Number(ms);
  if (!n || n < 0) return "-";
  const sec = Math.round(n / 1000);
  if (sec < 60) return sec + "s";
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return s ? `${m}m ${s}s` : `${m}m`;
  const h = Math.floor(m / 60), mm = m % 60;
  return mm ? `${h}h ${mm}m` : `${h}h`;
}

// ============= 分析结果展示 =============
function setAnalysis(data, { agentName = "", open = false, analysisAt = null, cached = false } = {}) {
  const wrap = $("#case-analysis");
  if (!data || (typeof data === "object" && Object.keys(data).length === 0)) {
    closeAnalysis(); return;
  }
  wrap.classList.remove("hidden");
  wrap.open = !!open;
  const text = (typeof data === "string") ? data : JSON.stringify(data, null, 2);
  $("#case-analysis-json").textContent = text;
  const meta = [];
  if (agentName) meta.push(escapeHtml(agentName));
  if (data && typeof data === "object" && data.core_value) meta.push(escapeHtml(String(data.core_value).slice(0, 40)));
  if (analysisAt) meta.push(`分析于 ${_formatTime(analysisAt)}` + (cached ? "（已缓存）" : ""));
  $("#case-analysis-meta").innerHTML = meta.join(" · ");
}

// ============= 主题切换（亮色 / 暗色）=============
// 持久化键 atf_theme：值 "light" / "dark"。未设过时读 prefers-color-scheme。
// 通过 <html data-theme="..."> 切换；CSS 变量重写在 [data-theme="dark"] 块。
const THEME_LS_KEY = "atf_theme";

function _getInitialTheme() {
  try {
    const saved = localStorage.getItem(THEME_LS_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch (e) { /* localStorage 禁用 */ }
  if (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

function applyTheme(theme) {
  const t = theme === "dark" ? "dark" : "light";
  document.documentElement.setAttribute("data-theme", t);
  const btn = $("#btn-toggle-theme");
  if (btn) {
    // 按钮文本显示「将切换到的目标」，更直觉
    btn.textContent = t === "dark" ? "[ light ]" : "[ dark ]";
    btn.title = t === "dark" ? "切换到亮色主题" : "切换到暗色主题";
  }
  try { localStorage.setItem(THEME_LS_KEY, t); } catch (e) { /* ignore */ }
}

// ============= 暴露全局 =============
window.$ = $;
window.$$ = $$;
window.api = api;
window.escapeHtml = escapeHtml;
window.paginate = paginate;
window.renderPager = renderPager;
window.openModal = openModal;
window.closeModal = closeModal;
window.renderLoading = renderLoading;
window.renderEmptyState = renderEmptyState;
window.renderStatPanel = renderStatPanel;
window.renderStatusBadge = renderStatusBadge;
window.dimensionsMeta = dimensionsMeta;
window.dimLabel = dimLabel;
window.loadDimensions = loadDimensions;
window.loadIndustries = loadIndustries;
window.industryOptions = industryOptions;
window.renderIndustrySelect = renderIndustrySelect;
window.switchTab = switchTab;
window._formatTime = _formatTime;
window._formatTimeShort = _formatTimeShort;
window._formatDuration = _formatDuration;
window.setAnalysis = setAnalysis;
window.applyTheme = applyTheme;
window.closeAnalysis = function() {
  const wrap = $("#case-analysis");
  wrap.classList.add("hidden");
  wrap.open = false;
  $("#case-analysis-json").textContent = "";
  $("#case-analysis-meta").innerHTML = "";
};

// 绑定 tab 点击事件
$$(".tab").forEach((b) =>
  b.addEventListener("click", () => switchTab(b.dataset.tab))
);

// 初始化主题
applyTheme(_getInitialTheme());

// 跟随系统
if (window.matchMedia) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const handler = (e) => {
    try { if (localStorage.getItem(THEME_LS_KEY)) return; } catch (_) { return; }
    applyTheme(e.matches ? "dark" : "light");
  };
  if (mq.addEventListener) mq.addEventListener("change", handler);
  else if (mq.addListener) mq.addListener(handler);
}

})();
