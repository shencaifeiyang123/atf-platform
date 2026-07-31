(function() { 'use strict';

// ============= Cases =============
// 新版：测试用例页分为两层
//   第一层（cases-agent-view）：智能体卡片列表，每张卡显示用例数 / 维度分布 / 最近一次任务
//   第二层（cases-list-view）：进入后展示该智能体的具体用例
let casesAgentOverview = [];
const casesAgentPager = { page: 1, pageSize: 12 };
let casesAgentQuery = "";

async function loadCasesTab() {
  // 默认显示第一层；保留之前选中的智能体（如果有）
  showCasesAgentList();
  await loadCasesAgentOverview();
}

async function loadCasesAgentOverview() {
  const list = $("#cases-agent-list");
  const pagerEl = $("#cases-agent-pager");
  pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
  list.innerHTML = `
    <div class="skeleton h-24"></div>
    <div class="skeleton h-24"></div>
    <div class="skeleton h-24"></div>
  `;
  try {
    casesAgentOverview = await api("/api/agents_overview");
  } catch (e) {
    list.innerHTML = `<div class="bg-white border rounded p-6 text-red-600">加载失败：${escapeHtml(e.message || "")}</div>`;
    return;
  }
  renderCasesAgentOverviewPage();
}

function renderCasesAgentOverviewPage() {
  const list = $("#cases-agent-list");
  const pagerEl = $("#cases-agent-pager");
  if (!casesAgentOverview.length) {
    list.innerHTML = renderEmptyState("暂无智能体", "请先到「智能体」页新建一个，然后回这里生成测试用例", "+ 新建智能体", "#");
    pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
    return;
  }
  const filtered = _filterAgents(casesAgentOverview, casesAgentQuery);
  if (!filtered.length) {
    list.innerHTML = renderEmptyState("没有匹配结果", `没有找到包含 "${escapeHtml(casesAgentQuery)}" 的智能体`);
    pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
    return;
  }
  const info = paginate(filtered, casesAgentPager);
  list.innerHTML = info.items.map((a) => {
    const total = a.total_cases || 0;
    const lr = a.last_run;
    const dimChips = Object.entries(a.by_dimension || {})
      .sort((x, y) => y[1] - x[1])
      .map(([k, v]) => `<span class="dim-badge dim-${k}">${escapeHtml(dimLabel[k] || k)} ${v}</span>`)
      .join(" ");
    const empty = total === 0;
    const lastRunHtml = lr
      ? `<div class="text-xs text-slate-500 mt-2">
           最近任务：<span class="status-badge ${lr.status}">${escapeHtml(lr.status)}</span> ·
           ${lr.passed}/${lr.total} 通过 · 平均分 ${lr.average_score || 0}
           ${lr.created_at ? ` · ${escapeHtml(_formatTime(lr.created_at))}` : ""}
         </div>`
      : `<div class="text-xs text-slate-400 mt-2">最近任务：暂无</div>`;
    return `
      <div class="card p-4 list-item cursor-pointer"
           onclick="enterCasesForAgent('${a.id}')" role="button" tabindex="0">
        <div class="flex justify-between items-start gap-4 flex-wrap">
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <div class="font-semibold text-base">${escapeHtml(a.name)}</div>
              <span class="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600">${escapeHtml(a.adapter || "")}</span>
              <span class="dim-badge">${escapeHtml(a.industry || "")}</span>
              ${a.has_analysis ? `<span class="tag tag-pass">已分析</span>` : ""}
            </div>
            ${a.description ? `<div class="text-sm text-slate-600 mt-1 truncate">${escapeHtml(a.description)}</div>` : ""}
            <div class="mt-3 flex items-center gap-4 flex-wrap">
              <div>
                <span class="text-2xl font-bold ${empty ? "text-slate-300" : ""}">${total}</span>
                <span class="text-xs text-slate-500 ml-1">条用例</span>
              </div>
              <div class="flex flex-wrap gap-1 flex-1 min-w-0">
                ${dimChips || `<span class="text-xs text-slate-400">尚未生成用例</span>`}
              </div>
            </div>
            ${lastRunHtml}
          </div>
          <div class="flex flex-col gap-2 shrink-0" onclick="event.stopPropagation()">
            <button class="btn btn-primary btn-sm" onclick="enterCasesForAgent('${a.id}')">
              ${empty ? "去生成用例" : "查看测试用例"} →
            </button>
          </div>
        </div>
      </div>
    `;
  }).join("");
  renderPager(pagerEl, info, (patch) => {
    Object.assign(casesAgentPager, patch); renderCasesAgentOverviewPage();
  });
}

// 进入二级（具体智能体的用例视图）
window.enterCasesForAgent = function (agent_id) {
  if (!agent_id) return;
  // 把 agents 列表 + 隐藏 select 也填上，让旧逻辑（生成 / 启动测试）继续工作
  if (!agents.length) agents = casesAgentOverview.map((o) => ({ id: o.id, name: o.name }));
  const sel = $("#case-agent-select");
  sel.innerHTML = (agents.length ? agents : casesAgentOverview)
    .map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
  sel.value = agent_id;

  // 切视图
  $("#cases-agent-view").classList.add("hidden");
  $("#cases-list-view").classList.remove("hidden");

  // 头部展示当前智能体信息
  const ov = casesAgentOverview.find((o) => o.id === agent_id);
  if (ov) {
    $("#cases-current-name").textContent = ov.name;
    const meta = [];
    if (ov.adapter) meta.push(escapeHtml(ov.adapter));
    if (ov.industry) meta.push(escapeHtml(ov.industry));
    if (ov.description) meta.push(escapeHtml(ov.description));
    $("#cases-current-meta").innerHTML = meta.join(" · ");
    const dimChips = Object.entries(ov.by_dimension || {})
      .sort((x, y) => y[1] - x[1])
      .map(([k, v]) => `<span class="dim-badge dim-${k}">${escapeHtml(dimLabel[k] || k)} ${v}</span>`)
      .join(" ");
    $("#cases-current-stats").innerHTML = `
      <div class="text-right">
        <div class="text-2xl font-bold">${ov.total_cases || 0}</div>
        <div class="text-xs text-slate-500">条用例</div>
      </div>
      <div class="flex flex-wrap gap-1 max-w-md">${dimChips}</div>
    `;
  } else {
    $("#cases-current-name").textContent = sel.options[sel.selectedIndex]?.text || "";
    $("#cases-current-meta").textContent = "";
    $("#cases-current-stats").innerHTML = "";
  }

  // 加载用例 + 分析
  closeAnalysis();
  $("#case-list").innerHTML = renderLoading();
  loadCasesForSelected();
  loadAnalysisCached();
};

window.backToCasesAgentList = function () {
  showCasesAgentList();
  loadCasesAgentOverview();   // 回到列表时刷一下统计（用例数可能变了）
};

function showCasesAgentList() {
  $("#cases-agent-view").classList.remove("hidden");
  $("#cases-list-view").classList.add("hidden");
  closeAnalysis();
}

// 列表页刷新按钮
$("#btn-refresh-cases-agents").addEventListener("click", loadCasesAgentOverview);

// 列表页搜索框：输入即时过滤（页码重置到 1）
$("#cases-agent-search").addEventListener("input", (e) => {
  casesAgentQuery = e.target.value || "";
  casesAgentPager.page = 1;
  renderCasesAgentOverviewPage();
});

// ============= 一键生成测试用例（仅对 total_cases === 0 的智能体生效） =============
// 默认采用动态对话模式，与「生成测试用例」弹窗的默认值保持一致：
//   generate_count = 20
//   dimensions     = ["alignment","boundary","industry","badcase","multi_turn"]（DEFAULT_DYN_DIMS）
//   opening_style  = "mixed"
//   use_analysis   = true
// 多个智能体串行执行（避免把生成 LLM 一次打满），失败的不阻塞后续。
const _bulkGen = { running: false, cancelled: false, queue: [], done: 0, ok: 0, fail: 0, total: 0, currentName: "" };

function _bulkBannerRender() {
  const banner = $("#bulk-gen-banner");
  if (!banner) return;
  if (!_bulkGen.running && _bulkGen.total === 0) {
    banner.classList.add("hidden"); return;
  }
  banner.classList.remove("hidden");
  const title = _bulkGen.cancelled
    ? "一键生成已中止"
    : _bulkGen.running ? "一键生成进行中…" : "一键生成完成";
  $("#bulk-gen-banner-title").textContent = title;
  const cur = _bulkGen.running && _bulkGen.currentName ? `· 当前：${_bulkGen.currentName}` : "";
  $("#bulk-gen-banner-detail").textContent =
    `进度 ${_bulkGen.done}/${_bulkGen.total}，成功 ${_bulkGen.ok}，失败 ${_bulkGen.fail} ${cur}`;
  $("#btn-bulk-gen-cancel").classList.toggle("hidden", !_bulkGen.running);
}

// 等待某个 job 跑到终态（done / error）。轮询 1.5s。
// 直接拉 /api/cases/generation_jobs/{id}，与右下角浮窗的 pollGenJob 各自独立，
// 浮窗仍会同步刷新（pollGenJob 已在提交时启动）。
async function _waitGenJob(job_id, timeoutMs = 15 * 60 * 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (_bulkGen.cancelled) return { status: "cancelled" };
    try {
      const job = await api("/api/cases/generation_jobs/" + job_id);
      if (job.status === "done" || job.status === "error") return job;
    } catch (e) {
      // 临时网络错误：再试一次，连续失败到超时再放弃
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  return { status: "error", error: "等待生成超时" };
}

async function _runBulkGen(targets) {
  _bulkGen.running = true;
  _bulkGen.cancelled = false;
  _bulkGen.queue = targets.slice();
  _bulkGen.total = targets.length;
  _bulkGen.done = 0; _bulkGen.ok = 0; _bulkGen.fail = 0;
  _bulkGen.currentName = "";
  _bulkBannerRender();

  const dims = [...DEFAULT_DYN_DIMS];
  for (const a of _bulkGen.queue) {
    if (_bulkGen.cancelled) break;
    _bulkGen.currentName = a.name || a.id;
    _bulkBannerRender();
    try {
      const res = await api("/api/cases/generate_dynamic_async", {
        method: "POST",
        body: {
          agent_id: a.id,
          generate_count: 20,
          opening_style: "mixed",
          user_hint: "",
          use_analysis: true,
          dimensions: dims,
        },
      });
      // 复用右下角浮窗的轮询 + 渲染
      _genJobs.set(res.job_id, {
        job_id: res.job_id,
        agent_id: a.id,
        agent_name: a.name || "",
        planned: res.planned ?? 20,
        generated: 0,
        status: "running",
        dims,
      });
      renderGenToasts();
      pollGenJob(res.job_id);

      const job = await _waitGenJob(res.job_id);
      if (job.status === "done") _bulkGen.ok++;
      else if (job.status === "cancelled") { /* 用户中止：不计入失败 */ }
      else _bulkGen.fail++;
    } catch (e) {
      _bulkGen.fail++;
      console.warn("[bulk-gen]", a.id, e);
    } finally {
      _bulkGen.done++;
      _bulkBannerRender();
    }
  }

  _bulkGen.running = false;
  _bulkGen.currentName = "";
  _bulkBannerRender();
  // 刷新概览（用例数变了）
  loadCasesAgentOverview();
  // 5 秒后收起横幅
  setTimeout(() => {
    if (!_bulkGen.running) { _bulkGen.total = 0; _bulkBannerRender(); }
  }, 5000);
}

$("#btn-bulk-gen").addEventListener("click", async () => {
  if (_bulkGen.running) {
    alert("已有一键生成任务在运行中，请等待结束或先中止。");
    return;
  }
  // 用最新概览数据筛选未生成过用例的智能体
  if (!casesAgentOverview.length) await loadCasesAgentOverview();
  const targets = (casesAgentOverview || []).filter((a) => (a.total_cases || 0) === 0);
  if (!targets.length) {
    alert("当前所有智能体都已经有用例，没有需要生成的对象。\n\n（如需重新生成，请到具体智能体内手动操作。）");
    return;
  }
  const ok = confirm(
    `将为以下 ${targets.length} 个尚未生成用例的智能体依次生成动态对话用例：\n\n` +
    targets.slice(0, 12).map((a, i) => `${i + 1}. ${a.name}`).join("\n") +
    (targets.length > 12 ? `\n…（共 ${targets.length} 个）` : "") +
    `\n\n默认参数：动态对话 / 每个 20 条 / 5 个核心维度（预期效果·边界兜底·行业规范·Bad Case·多轮对话）/ 使用智能体分析结果。\n\n确认开始？`
  );
  if (!ok) return;
  _runBulkGen(targets);
});

$("#btn-bulk-gen-cancel").addEventListener("click", () => {
  if (_bulkGen.running && confirm("确认中止后续未开始的智能体？当前正在生成的智能体不受影响。")) {
    _bulkGen.cancelled = true;
    _bulkBannerRender();
  }
});

// ---------- 一键执行测试（串行调度，刷新可恢复） ----------
// 候选：last_run==null 且 total_cases>0 的智能体；按 casesAgentOverview 顺序执行。
// 单个智能体执行：POST /api/runs {agent_id}（默认跑最新批次的全部用例 / 默认并发）。
// 提交后每 2s 轮询 GET /api/runs/{id}，直到 status ∈ {completed, failed, canceled}，
// 单条任务超时 60 分钟。失败 / 错误不阻塞后续。
//
// 持久化：状态写 localStorage(BULK_RUN_LS_KEY)，4 小时内刷新页面会自动恢复并继续。
// queue 存「待启动 agents」，inflight 存「已 POST 但未到终态的 run」。
const BULK_RUN_LS_KEY = "atf_bulk_run_v1";
const BULK_RUN_LS_TTL_MS = 4 * 60 * 60 * 1000;
// 单次轮询 fetch 的超时（避免被卡住的请求挂死整个串行循环）
const _BULK_RUN_POLL_FETCH_TIMEOUT_MS = 10000;
// 连续轮询失败到此阈值才把当前 run 判错（≈ 30 * 2s = 60s 抖动容忍）
const _BULK_RUN_POLL_MAX_CONSECUTIVE_ERRORS = 30;

const _bulkRun = {
  running: false,
  cancelled: false,
  queue: [],          // [{id, name}]
  inflight: null,     // {agent_id, name, run_id} | null
  done: 0, ok: 0, fail: 0, total: 0,
  currentName: "",
  startedAt: 0,
};

function _persistBulkRun() {
  try {
    if (!_bulkRun.running && _bulkRun.total === 0) {
      localStorage.removeItem(BULK_RUN_LS_KEY);
      return;
    }
    const snap = {
      v: 1,
      running: _bulkRun.running,
      cancelled: _bulkRun.cancelled,
      queue: _bulkRun.queue,
      inflight: _bulkRun.inflight,
      done: _bulkRun.done, ok: _bulkRun.ok, fail: _bulkRun.fail, total: _bulkRun.total,
      currentName: _bulkRun.currentName,
      startedAt: _bulkRun.startedAt || Date.now(),
    };
    localStorage.setItem(BULK_RUN_LS_KEY, JSON.stringify(snap));
  } catch (e) { /* localStorage 可能被禁用，静默 */ }
}

function _bulkRunBannerRender() {
  const banner = $("#bulk-run-banner");
  if (!banner) return;
  if (!_bulkRun.running && _bulkRun.total === 0) {
    banner.classList.add("hidden"); return;
  }
  banner.classList.remove("hidden");
  const title = _bulkRun.cancelled
    ? "一键执行已中止"
    : _bulkRun.running ? "一键执行进行中…" : "一键执行完成";
  $("#bulk-run-banner-title").textContent = title;
  const cur = _bulkRun.running && _bulkRun.currentName ? `· 当前：${_bulkRun.currentName}` : "";
  $("#bulk-run-banner-detail").textContent =
    `进度 ${_bulkRun.done}/${_bulkRun.total}，成功 ${_bulkRun.ok}，失败 ${_bulkRun.fail} ${cur}`;
  $("#btn-bulk-run-cancel").classList.toggle("hidden", !_bulkRun.running);
}

// 单次轮询：带 AbortController 超时，避免请求卡住后整个串行循环冻死。
// 失败时抛错，由 _waitRun 用连续计数兜底。
async function _fetchRunOnce(run_id) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), _BULK_RUN_POLL_FETCH_TIMEOUT_MS);
  try {
    const r = await fetch("/api/runs/" + encodeURIComponent(run_id), { signal: ctrl.signal });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

// 轮询某个 run 到终态。RunStatus 终态为 completed / failed / canceled。
// - 单次 fetch 有 10s 硬超时
// - 连续 30 次失败（≈60s）才把这个 run 判错并继续往下走
async function _waitRun(run_id, timeoutMs = 60 * 60 * 1000) {
  const TERMINAL = new Set(["completed", "failed", "canceled"]);
  const deadline = Date.now() + timeoutMs;
  let consecutiveErrors = 0;
  while (Date.now() < deadline) {
    if (_bulkRun.cancelled) return { status: "cancelled_local" };
    try {
      const run = await _fetchRunOnce(run_id);
      consecutiveErrors = 0;
      if (run && TERMINAL.has(run.status)) return run;
    } catch (e) {
      consecutiveErrors++;
      if (consecutiveErrors >= _BULK_RUN_POLL_MAX_CONSECUTIVE_ERRORS) {
        console.warn("[bulk-run] 轮询连续失败放弃", run_id, e);
        return { status: "failed", error: "轮询连续失败：" + (e && e.message || e) };
      }
    }
    await new Promise((r) => setTimeout(r, 2000));
  }
  return { status: "failed", error: "等待执行超时" };
}

// 把 inflight 的 run_id 跑到终态，更新计数并清理 inflight。
async function _drainInflight() {
  if (!_bulkRun.inflight || !_bulkRun.inflight.run_id) return;
  _bulkRun.currentName = _bulkRun.inflight.name || _bulkRun.inflight.agent_id;
  _bulkRunBannerRender(); _persistBulkRun();
  let final;
  try {
    final = await _waitRun(_bulkRun.inflight.run_id);
  } catch (e) {
    final = { status: "failed", error: String(e) };
  }
  if (final.status === "completed") _bulkRun.ok++;
  else if (final.status === "cancelled_local") { /* 用户中止：不计入失败 */ }
  else _bulkRun.fail++;
  _bulkRun.done++;
  _bulkRun.inflight = null;
  _bulkRunBannerRender(); _persistBulkRun();
}

// 主循环：从 queue 里拉一个、提交、等到终态、写状态。每一步都 _persistBulkRun()。
async function _runBulkRunLoop() {
  while (!_bulkRun.cancelled && _bulkRun.queue.length > 0) {
    const a = _bulkRun.queue.shift();
    _bulkRun.currentName = a.name || a.id;
    _bulkRunBannerRender(); _persistBulkRun();
    try {
      const run = await api("/api/runs", {
        method: "POST",
        body: { agent_id: a.id },
      });
      if (!run || !run.id) throw new Error("create_run 未返回 id");
      _bulkRun.inflight = { agent_id: a.id, name: a.name || "", run_id: run.id };
      _persistBulkRun();
      await _drainInflight();
    } catch (e) {
      // 提交阶段就失败：直接计 fail
      console.warn("[bulk-run]", a.id, e);
      _bulkRun.fail++;
      _bulkRun.done++;
      _bulkRun.inflight = null;
      _bulkRunBannerRender(); _persistBulkRun();
    }
  }
}

async function _runBulkRun(targets) {
  _bulkRun.running = true;
  _bulkRun.cancelled = false;
  _bulkRun.queue = targets.map((a) => ({ id: a.id, name: a.name || "" }));
  _bulkRun.inflight = null;
  _bulkRun.total = targets.length;
  _bulkRun.done = 0; _bulkRun.ok = 0; _bulkRun.fail = 0;
  _bulkRun.currentName = "";
  _bulkRun.startedAt = Date.now();
  _bulkRunBannerRender(); _persistBulkRun();

  try {
    await _runBulkRunLoop();
  } finally {
    _bulkRun.running = false;
    _bulkRun.currentName = "";
    _bulkRunBannerRender(); _persistBulkRun();
    // 刷新概览（last_run 变了）
    loadCasesAgentOverview();
    setTimeout(() => {
      if (!_bulkRun.running) {
        _bulkRun.total = 0; _bulkRun.done = 0; _bulkRun.ok = 0; _bulkRun.fail = 0;
        _bulkRun.queue = []; _bulkRun.inflight = null;
        _bulkRunBannerRender(); _persistBulkRun();
      }
    }, 5000);
  }
}

// -------- 一键执行：多选弹窗状态 --------
// _bulkRunPick 维护弹窗内的瞬时状态：
//   list   - 候选智能体（total_cases > 0），按 casesAgentOverview 顺序
//   picked - Set<agent_id> 当前勾选
//   query  - 搜索关键字（名称模糊）
// 关闭弹窗即丢弃。
const _bulkRunPick = {
  list: [],
  picked: new Set(),
  query: "",
};

function _bulkRunSummarizeLastRun(lr) {
  if (!lr) return `<span class="text-slate-400">从未执行</span>`;
  const status = lr.status || "";
  const cls = `status-${status}`;
  const passed = (lr.passed != null && lr.total != null) ? `${lr.passed}/${lr.total} 通过` : "";
  const time = lr.created_at ? _formatTime(lr.created_at) : "";
  return `<span class="${cls}">${escapeHtml(status)}</span>${passed ? ` · ${passed}` : ""}${time ? ` · ${escapeHtml(time)}` : ""}`;
}

// 「上次失败」判定：status === failed，或者通过率 < 1（有未通过用例）
function _bulkRunIsFailedLastRun(a) {
  const lr = a.last_run;
  if (!lr) return false;
  if (lr.status === "failed" || lr.status === "canceled") return true;
  const total = Number(lr.total || 0);
  const passed = Number(lr.passed || 0);
  return total > 0 && passed < total;
}

function _bulkRunRenderList() {
  const wrap = $("#bulk-run-list");
  if (!wrap) return;
  const q = (_bulkRunPick.query || "").trim().toLowerCase();
  const filtered = q
    ? _bulkRunPick.list.filter((a) => (a.name || "").toLowerCase().includes(q))
    : _bulkRunPick.list;
  if (!filtered.length) {
    wrap.innerHTML = `<div class="text-sm text-slate-500 text-center py-8">${q ? `没有匹配 "${escapeHtml(_bulkRunPick.query)}" 的智能体` : "没有可执行的智能体（需要先生成用例）"}</div>`;
    _bulkRunRefreshFooter();
    return;
  }
  wrap.innerHTML = filtered.map((a) => {
    const checked = _bulkRunPick.picked.has(a.id) ? "checked" : "";
    const total = a.total_cases || 0;
    return `
      <label class="flex items-center gap-3 px-3 py-2 hover:bg-slate-50 cursor-pointer">
        <input type="checkbox" class="bulk-run-cb w-4 h-4" data-id="${escapeHtml(a.id)}" ${checked} />
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-medium truncate">${escapeHtml(a.name || a.id)}</span>
            <span class="text-xs text-slate-500">${total} 条用例</span>
            ${a.adapter ? `<span class="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">${escapeHtml(a.adapter)}</span>` : ""}
          </div>
          <div class="text-xs text-slate-500 mt-0.5">最近：${_bulkRunSummarizeLastRun(a.last_run)}</div>
        </div>
      </label>
    `;
  }).join("");
  _bulkRunRefreshFooter();
}

function _bulkRunRefreshFooter() {
  const n = _bulkRunPick.picked.size;
  $("#bulk-run-selected-count").textContent = String(n);
  $("#btn-confirm-bulk-run").disabled = (n === 0);
}

function _bulkRunOpenModal() {
  const all = casesAgentOverview || [];
  // 候选：必须有用例（total_cases > 0），否则没法启动 run。
  _bulkRunPick.list = all.filter((a) => (a.total_cases || 0) > 0);
  _bulkRunPick.picked = new Set();
  _bulkRunPick.query = "";

  // 默认勾选 = 「上次没跑过的」（兼容旧版一键行为，常见场景）
  _bulkRunPick.list.forEach((a) => { if (!a.last_run) _bulkRunPick.picked.add(a.id); });

  const skipped = all.length - _bulkRunPick.list.length;
  const hint = $("#bulk-run-skipped-hint");
  if (skipped > 0) {
    hint.textContent = `（另有 ${skipped} 个智能体没有用例，未列出。请先到对应智能体页生成。）`;
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
  }

  const sb = $("#bulk-run-search");
  if (sb) sb.value = "";

  _bulkRunRenderList();
  openModal("#bulk-run-modal");
}

// -------- 弹窗事件绑定（idempotent：仅绑定一次） --------
$("#btn-close-bulk-run")?.addEventListener("click", () => closeModal("#bulk-run-modal"));
$("#btn-cancel-bulk-run")?.addEventListener("click", () => closeModal("#bulk-run-modal"));

$("#bulk-run-search")?.addEventListener("input", (e) => {
  _bulkRunPick.query = e.target.value || "";
  _bulkRunRenderList();
});

// 行内 checkbox：事件委托到列表容器
$("#bulk-run-list")?.addEventListener("change", (e) => {
  const cb = e.target.closest(".bulk-run-cb");
  if (!cb) return;
  const id = cb.dataset.id;
  if (cb.checked) _bulkRunPick.picked.add(id);
  else _bulkRunPick.picked.delete(id);
  _bulkRunRefreshFooter();
});

// 全选 = 当前过滤视图内全部勾上（避免搜索时全选不可见行造成困惑）
$("#bulk-run-pick-all")?.addEventListener("click", () => {
  const q = (_bulkRunPick.query || "").trim().toLowerCase();
  const visible = q
    ? _bulkRunPick.list.filter((a) => (a.name || "").toLowerCase().includes(q))
    : _bulkRunPick.list;
  visible.forEach((a) => _bulkRunPick.picked.add(a.id));
  _bulkRunRenderList();
});

$("#bulk-run-pick-none")?.addEventListener("click", () => {
  _bulkRunPick.picked.clear();
  _bulkRunRenderList();
});

$("#bulk-run-pick-unrun")?.addEventListener("click", () => {
  _bulkRunPick.picked.clear();
  _bulkRunPick.list.forEach((a) => { if (!a.last_run) _bulkRunPick.picked.add(a.id); });
  _bulkRunRenderList();
});

$("#bulk-run-pick-failed")?.addEventListener("click", () => {
  _bulkRunPick.picked.clear();
  _bulkRunPick.list.forEach((a) => { if (_bulkRunIsFailedLastRun(a)) _bulkRunPick.picked.add(a.id); });
  _bulkRunRenderList();
});

$("#btn-confirm-bulk-run")?.addEventListener("click", () => {
  if (_bulkRun.running) {
    alert("已有一键执行任务在运行中，请等待结束或先中止。");
    return;
  }
  const idSet = _bulkRunPick.picked;
  if (!idSet.size) return;
  // 按 list 顺序（即 casesAgentOverview 顺序）拼 targets，保留稳定的执行序
  const targets = _bulkRunPick.list
    .filter((a) => idSet.has(a.id))
    .map((a) => ({ id: a.id, name: a.name || "" }));
  closeModal("#bulk-run-modal");
  _runBulkRun(targets);
});

$("#btn-bulk-run").addEventListener("click", async () => {
  if (_bulkRun.running) {
    alert("已有一键执行任务在运行中，请等待结束或先中止。");
    return;
  }
  if (!casesAgentOverview.length) await loadCasesAgentOverview();
  const all = casesAgentOverview || [];
  const eligible = all.filter((a) => (a.total_cases || 0) > 0);
  if (!eligible.length) {
    const skippedNoCase = all.length;
    alert(skippedNoCase
      ? `当前没有可执行对象。\n\n（${skippedNoCase} 个智能体还没有用例，请先到对应智能体页生成。）`
      : "暂无智能体。请先到「智能体」页新建。");
    return;
  }
  _bulkRunOpenModal();
});

$("#btn-bulk-run-cancel").addEventListener("click", () => {
  if (_bulkRun.running && confirm("确认中止后续未开始的智能体？已在执行的不受影响。")) {
    _bulkRun.cancelled = true;
    _bulkRunBannerRender(); _persistBulkRun();
  }
});

// 页面加载时尝试恢复未完成的批量执行
(async function restoreBulkRun() {
  let snap = null;
  try {
    const raw = localStorage.getItem(BULK_RUN_LS_KEY);
    if (!raw) return;
    snap = JSON.parse(raw);
  } catch (e) { return; }
  if (!snap || snap.v !== 1) { localStorage.removeItem(BULK_RUN_LS_KEY); return; }
  // 过期清理
  const startedAt = Number(snap.startedAt || 0);
  if (!startedAt || Date.now() - startedAt > BULK_RUN_LS_TTL_MS) {
    localStorage.removeItem(BULK_RUN_LS_KEY); return;
  }
  // 已自然结束：保留横幅 5 秒后自清
  if (!snap.running && (!snap.queue || !snap.queue.length) && !snap.inflight) {
    Object.assign(_bulkRun, snap);
    _bulkRunBannerRender();
    setTimeout(() => {
      _bulkRun.total = 0; _bulkRun.done = 0; _bulkRun.ok = 0; _bulkRun.fail = 0;
      _bulkRun.queue = []; _bulkRun.inflight = null;
      _bulkRunBannerRender(); _persistBulkRun();
    }, 5000);
    return;
  }
  // 还有活儿：恢复状态、把横幅亮起来、继续推进
  Object.assign(_bulkRun, snap);
  _bulkRun.running = true;
  _bulkRun.cancelled = false;       // 刷新视为放弃上次的中止意图
  _bulkRunBannerRender();
  try {
    // 先把上一回合 inflight 的 run 跑到终态（如果还在 inflight）
    if (_bulkRun.inflight && _bulkRun.inflight.run_id) {
      await _drainInflight();
    }
    // 继续推进剩余 queue
    await _runBulkRunLoop();
  } finally {
    _bulkRun.running = false;
    _bulkRun.currentName = "";
    _bulkRunBannerRender(); _persistBulkRun();
    loadCasesAgentOverview();
    setTimeout(() => {
      if (!_bulkRun.running) {
        _bulkRun.total = 0; _bulkRun.done = 0; _bulkRun.ok = 0; _bulkRun.fail = 0;
        _bulkRun.queue = []; _bulkRun.inflight = null;
        _bulkRunBannerRender(); _persistBulkRun();
      }
    }, 5000);
  }
})();

// ---------- 时间格式化（统一北京时间 UTC+8） ----------
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

// 切换 / 进入用例页时按需读取已保存的分析（不会触发 LLM 调用）
async function loadAnalysisCached() {
  const id = $("#case-agent-select").value;
  if (!id) return;
  try {
    const data = await api("/api/agents/" + id + "/analysis");
    const agent = (agents || []).find((a) => a.id === id);
    if (data?.analysis && Object.keys(data.analysis).length) {
      setAnalysis(data.analysis, {
        agentName: agent?.name || "",
        open: false,
        analysisAt: data.analysis_at,
        cached: true,
      });
    } else {
      closeAnalysis();
    }
  } catch (_) { closeAnalysis(); }
}
window.closeAnalysis = function () {
  const wrap = $("#case-analysis");
  wrap.classList.add("hidden");
  wrap.open = false;
  $("#case-analysis-json").textContent = "";
  $("#case-analysis-meta").innerHTML = "";
};
window.copyAnalysis = async function () {
  const text = $("#case-analysis-json").textContent || "";
  try { await navigator.clipboard.writeText(text); }
  catch { alert("复制失败，请手动选择文本"); return; }
  const btn = $("#btn-copy-analysis"); const old = btn.textContent;
  btn.textContent = "已复制";
  setTimeout(() => (btn.textContent = old), 1200);
};

// 编辑 / 保存智能体分析结果
let _analysisEditMode = false;
window.toggleEditAnalysis = function () {
  const preEl = $("#case-analysis-json");
  const editorEl = $("#case-analysis-editor");
  const btnEdit = $("#btn-edit-analysis");
  const btnSave = $("#btn-save-analysis");
  
  if (!_analysisEditMode) {
    // 进入编辑模式
    editorEl.value = preEl.textContent || "";
    preEl.classList.add("hidden");
    editorEl.classList.remove("hidden");
    btnEdit.classList.add("hidden");
    btnSave.classList.remove("hidden");
    _analysisEditMode = true;
  } else {
    // 取消编辑（不保存）
    preEl.classList.remove("hidden");
    editorEl.classList.add("hidden");
    btnEdit.classList.remove("hidden");
    btnSave.classList.add("hidden");
    _analysisEditMode = false;
  }
};

window.saveAnalysis = async function () {
  const id = $("#case-agent-select").value;
  if (!id) return alert("未选择智能体");
  const editorEl = $("#case-analysis-editor");
  const text = editorEl.value.trim();
  if (!text) return alert("分析结果不能为空");
  
  let analysis;
  try {
    analysis = JSON.parse(text);
  } catch (e) {
    return alert("JSON 格式错误：" + e.message);
  }
  
  const btn = $("#btn-save-analysis");
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "保存中...";
  
  try {
    const res = await api("/api/agents/" + id + "/analysis", {
      method: "PUT",
      body: { analysis },
    });
    // 更新显示区
    $("#case-analysis-json").textContent = JSON.stringify(res.analysis || {}, null, 2);
    const meta = [];
    const agent = (agents || []).find((a) => a.id === id);
    if (agent?.name) meta.push(escapeHtml(agent.name));
    if (res.analysis?.core_value) meta.push(escapeHtml(String(res.analysis.core_value).slice(0, 40)));
    if (res.analysis_at) meta.push(`分析于 ${_formatTime(res.analysis_at)}`);
    $("#case-analysis-meta").innerHTML = meta.join(" · ");
    
    // 退出编辑模式
    $("#case-analysis-json").classList.remove("hidden");
    $("#case-analysis-editor").classList.add("hidden");
    $("#btn-edit-analysis").classList.remove("hidden");
    $("#btn-save-analysis").classList.add("hidden");
    _analysisEditMode = false;
    
    btn.textContent = "✓ 已保存";
    setTimeout(() => (btn.textContent = old), 1200);
  } catch (e) {
    alert("保存失败：" + (e.message || ""));
    btn.textContent = old;
  } finally {
    btn.disabled = false;
  }
};

async function loadCasesForSelected() {
  const id = $("#case-agent-select").value;
  if (!id) return;
  const cases = await api("/api/agents/" + id + "/cases");
  renderCases(cases);
}
// 当前页用例的全集（用于批量操作）
let _currentCases = [];

function renderCases(cases) {
  _currentCases = cases || [];
  const el = $("#case-list");
  const toolbar = $("#case-batch-toolbar");
  if (!cases.length) {
    el.innerHTML = renderEmptyState("暂无用例", "点击「✨ 生成用例」按钮快速生成测试用例", "✨ 生成用例", "#");
    toolbar.classList.add("hidden");
    return;
  }
  // 展开工具栏并重置全选状态
  toolbar.classList.remove("hidden");
  $("#case-select-all").checked = false;
  $("#case-select-all").indeterminate = false;

  // 按 batch_id 分组（保持后端排序：created_at DESC，所以先出现的就是新批次）
  // 老数据没有 batch_id 时统一归到「未分组」
  const groups = []; // [{batch_id, batch_label, created_at, items}]
  const idx = new Map();
  for (const c of cases) {
    const key = c.batch_id || "__legacy__";
    let g = idx.get(key);
    if (!g) {
      g = {
        batch_id: c.batch_id || "",
        batch_label: c.batch_label || "未分组（历史用例）",
        created_at: c.created_at || 0,
        items: [],
      };
      idx.set(key, g);
      groups.push(g);
    }
    g.items.push(c);
  }

  const renderCard = (c) => `
    <div class="card-flat p-3 list-item" data-dim="${escapeHtml(c.dimension || '')}">
      <div class="flex justify-between items-start gap-2">
        <input type="checkbox" class="case-row-check w-4 h-4 mt-1 shrink-0" data-id="${c.id}" aria-label="选中此用例" />
        <div class="flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="dim-badge dim-${c.dimension}">${dimLabel[c.dimension] || c.dimension}</span>
            ${c.sub_type ? `<span class="text-xs text-slate-500">${escapeHtml(c.sub_type)}</span>` : ""}
            <span class="text-xs text-slate-400">权重 ${c.weight}</span>
            ${c.opening_mode === "ai"
              ? `<span class="tag tag-info" title="执行时由智能体先开口">AI 开场</span>`
              : c.opening_mode === "user"
                ? `<span class="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600">用户开场</span>`
                : ""}
            ${c.created_at
              ? `<span class="text-xs text-slate-400 ml-auto" title="${escapeHtml(_formatTime(c.created_at))}（北京时间）">生成于 ${escapeHtml(_formatTimeShort(c.created_at))}</span>`
              : ""}
          </div>
          <div class="font-medium mt-1">${escapeHtml(c.title || c.turns[0]?.content || "")}</div>
          <div class="text-sm text-slate-600 mt-1">
            ${c.turns.map((t, i) => `<div><span class="text-slate-400">${i + 1}.</span> ${escapeHtml(t.content)}</div>`).join("")}
          </div>
          <div class="text-xs text-slate-500 mt-1">期望：${escapeHtml(c.expectation || "（未指定）")}</div>
          ${c.pass_criteria?.length ? `<div class="text-xs text-slate-500">通过条件：${c.pass_criteria.map(escapeHtml).join("；")}</div>` : ""}
        </div>
        <div class="flex flex-col gap-1 shrink-0">
          <button class="btn btn-ghost btn-sm" onclick="runSingleCase('${c.id}')">▶ 执行</button>
          <button class="btn btn-ghost btn-sm" onclick="editCase('${c.id}')">编辑</button>
          <button class="btn btn-danger btn-sm" onclick="delCase('${c.id}')">删除</button>
        </div>
      </div>
    </div>
  `;

  // 每个批次一个折叠面板。第一个（最新）默认展开，其余收起。
  el.innerHTML = groups.map((g, i) => {
    const safeBid = escapeHtml(g.batch_id || "");
    // 统计该批次内各维度的用例数（按出现顺序）
    const dimCounts = new Map();
    for (const item of g.items) {
      const dim = item.dimension || "";
      dimCounts.set(dim, (dimCounts.get(dim) || 0) + 1);
    }
    // 仅当批次内含多个维度时，才显示维度筛选 tab
    const tabsHtml = dimCounts.size > 1
      ? `<div class="flex flex-wrap gap-1 bg-slate-100 p-1 rounded-lg w-max max-w-full mb-2" data-batch-tabs="${i}">
           <button type="button" data-dim="" data-active="true" class="batch-dim-tab px-3 py-1 rounded text-sm">全部 (${g.items.length})</button>
           ${[...dimCounts].map(([dim, count]) => `
             <button type="button" data-dim="${escapeHtml(dim)}" class="batch-dim-tab px-3 py-1 rounded text-sm">
               ${escapeHtml(dimLabel[dim] || dim || "未分类")} (${count})
             </button>
           `).join("")}
         </div>`
      : "";
    return `
    <details class="border rounded-lg bg-slate-50/60 overflow-hidden" ${i === 0 ? "open" : ""}>
      <summary class="cursor-pointer select-none px-3 py-2 flex items-center gap-2 flex-wrap hover:bg-slate-100">
        <span class="text-base font-semibold text-slate-700">${escapeHtml(g.batch_label || "未命名批次")}</span>
        <span class="text-xs px-2 py-0.5 rounded bg-white border text-slate-600">${g.items.length} 条</span>
        ${g.batch_id ? `<span class="text-xs text-slate-400" title="批次 ID：${safeBid}">#${safeBid.slice(-6)}</span>` : ""}
        <button type="button"
          class="ml-auto text-xs border rounded px-2 py-0.5 bg-white hover:bg-slate-100"
          onclick="event.preventDefault(); event.stopPropagation(); selectBatchCases('${safeBid}', true);"
          title="勾选本批次所有用例">全选本批次</button>
        <button type="button"
          class="text-xs border rounded px-2 py-0.5 bg-white hover:bg-slate-100"
          onclick="event.preventDefault(); event.stopPropagation(); selectBatchCases('${safeBid}', false);"
          title="取消本批次的勾选">清除</button>
      </summary>
      <div class="px-3 pb-3 pt-1">
        ${tabsHtml}
        <div class="space-y-2" data-batch-list="${i}">
          ${g.items.map(renderCard).join("")}
        </div>
      </div>
    </details>`;
  }).join("");

  // 绑定行选中事件
  el.querySelectorAll(".case-row-check").forEach((cb) => {
    cb.addEventListener("change", updateBatchSelectionUI);
  });

  // 绑定批次内维度筛选 tab：仅在同一批次内切换显隐
  el.querySelectorAll(".batch-dim-tab").forEach((tab) => {
    tab.addEventListener("click", (e) => {
      e.preventDefault();
      const tabBar = tab.parentElement;
      const batchIdx = tabBar.dataset.batchTabs;
      const dim = tab.dataset.dim || "";
      // 切换 active 状态：用 data-active 替代硬编码 Tailwind 颜色类
      tabBar.querySelectorAll(".batch-dim-tab").forEach((t) => {
        t.removeAttribute("data-active");
      });
      tab.setAttribute("data-active", "true");
      // 仅过滤当前批次的卡片
      const list = el.querySelector(`[data-batch-list="${batchIdx}"]`);
      if (!list) return;
      list.querySelectorAll("[data-dim]").forEach((card) => {
        if (!dim || card.dataset.dim === dim) {
          card.classList.remove("hidden");
        } else {
          card.classList.add("hidden");
        }
      });
    });
  });

  updateBatchSelectionUI();
}

// 一键勾选 / 取消勾选某个批次的所有用例
window.selectBatchCases = function(batchId, checked) {
  const ids = new Set(_currentCases
    .filter((c) => (c.batch_id || "") === (batchId || ""))
    .map((c) => c.id));
  $$(".case-row-check").forEach((cb) => {
    if (ids.has(cb.dataset.id)) cb.checked = !!checked;
  });
  updateBatchSelectionUI();
};

// 计算并刷新批量管理工具栏的状态（选中数 / 全选 / 按钮可用性）
function updateBatchSelectionUI() {
  const all = Array.from($$(".case-row-check"));
  const checked = all.filter((cb) => cb.checked);
  const cnt = checked.length;
  const total = all.length;
  const cntEl = $("#case-selected-count");
  const btn = $("#btn-batch-delete");
  const selectAll = $("#case-select-all");

  cntEl.textContent = cnt > 0 ? `已选 ${cnt} / ${total} 条` : `共 ${total} 条`;
  btn.disabled = cnt === 0;

  if (cnt === 0) {
    selectAll.checked = false;
    selectAll.indeterminate = false;
  } else if (cnt === total) {
    selectAll.checked = true;
    selectAll.indeterminate = false;
  } else {
    selectAll.checked = false;
    selectAll.indeterminate = true;
  }
}

// 全选 / 全不选
$("#case-select-all").addEventListener("change", (e) => {
  const checked = e.target.checked;
  $$(".case-row-check").forEach((cb) => (cb.checked = checked));
  updateBatchSelectionUI();
});

// 批量删除
$("#btn-batch-delete").addEventListener("click", async () => {
  const ids = Array.from($$(".case-row-check"))
    .filter((cb) => cb.checked)
    .map((cb) => cb.dataset.id);
  if (!ids.length) return;

  // 显示确认模态框
  $("#delete-count").textContent = ids.length;
  openModal("#confirm-delete-modal");

  // 等待用户确认
  const confirmed = await new Promise((resolve) => {
    const confirmBtn = $("#btn-confirm-delete");
    const cancelBtn = $("#btn-cancel-delete");

    const handleConfirm = () => {
      cleanup();
      resolve(true);
    };
    const handleCancel = () => {
      cleanup();
      resolve(false);
    };
    const cleanup = () => {
      confirmBtn.removeEventListener("click", handleConfirm);
      cancelBtn.removeEventListener("click", handleCancel);
      closeModal("#confirm-delete-modal");
    };

    confirmBtn.addEventListener("click", handleConfirm);
    cancelBtn.addEventListener("click", handleCancel);
  });

  if (!confirmed) return;

  const btn = $("#btn-batch-delete");
  const old = btn.textContent;
  btn.disabled = true;
  btn.textContent = "删除中...";
  try {
    const res = await api("/api/cases/batch_delete", {
      method: "POST",
      body: { ids },
    });
    const skipped = (res.skipped && res.skipped.length) ? `，${res.skipped.length} 条跳过` : "";
    alert(`已删除 ${res.deleted || 0} 条用例${skipped}`);
    await loadCasesForSelected();
  } catch (e) {
    alert("批量删除失败：" + (e.message || ""));
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
});

window.delCase = async (id) => {
  if (!confirm("删除此用例？")) return;
  await api("/api/cases/" + id, { method: "DELETE" });
  loadCasesForSelected();
};

// 单条执行：用现有 POST /api/runs（支持 case_ids 列表），跑完跳到任务详情页观察实时进度
window.runSingleCase = async (id) => {
  const agent_id = $("#case-agent-select").value;
  if (!agent_id) return alert("先选择智能体");
  const c = (_currentCases || []).find((x) => x.id === id);
  const title = c?.title || c?.turns?.[0]?.content || id;
  if (!confirm(`确定执行此用例？\n\n「${title}」`)) return;

  try {
    const run = await api("/api/runs", {
      method: "POST",
      body: {
        agent_id,
        name: `单条调试 - ${title}`.slice(0, 80),
        concurrency: 1,
        case_ids: [id],
      },
    });
    switchTab("runs");
    viewRun(run.id);
  } catch (e) {
    alert("启动失败：" + (e.message || ""));
  }
};

// ---------- 用例编辑弹窗 ----------
let editingCase = null; // null=新增；否则编辑既有

function openCaseEditor(c) {
  editingCase = c;
  $("#case-modal-title").textContent = c ? "编辑用例" : "新增用例";

  // 维度下拉
  $("#cf-dimension").innerHTML = dimensionsMeta.map((d) =>
    `<option value="${d.key}">${escapeHtml(d.label)} (${d.key})</option>`
  ).join("");
  $("#cf-dimension").value = c?.dimension || dimensionsMeta[0]?.key || "alignment";

  $("#cf-sub-type").value = c?.sub_type || "";
  $("#cf-weight").value = c?.weight ?? 3;
  $("#cf-title").value = c?.title || "";
  $("#cf-expectation").value = c?.expectation || "";
  $("#cf-pass").value = (c?.pass_criteria || []).join("\n");
  // 开场设置；老数据没有这个字段，默认走 default
  $("#cf-opening-mode").value = (c?.opening_mode || "default");

  // 对话模式（动态对话 MVP）
  $("#cf-dialogue-mode").value = (c?.dialogue_mode || "scripted");
  $("#cf-user-persona").value = c?.user_persona || "";
  $("#cf-user-goal").value = c?.user_goal || "";
  $("#cf-max-turns").value = c?.max_turns || 6;
  $("#cf-term-kw").value = (c?.termination_keywords || []).join("\n");
  syncDialogueModeUI();

  // turns
  const turns = (c?.turns && c.turns.length) ? c.turns : [{ role: "user", content: "" }];
  renderCaseTurns(turns);

  $("#case-modal").classList.remove("hidden"); $("#case-modal").classList.add("flex");
  openModal("#case-modal");
}

// 动态对话切换：显隐模拟用户字段，并把 turns 的必填星号去掉
function syncDialogueModeUI() {
  const isDynamic = $("#cf-dialogue-mode").value === "dynamic";
  $("#cf-dynamic-wrap").classList.toggle("hidden", !isDynamic);
  $("#cf-turns-required").classList.toggle("hidden", isDynamic);
  $("#cf-turns-hint-dynamic").classList.toggle("hidden", !isDynamic);
}
function closeCaseEditor() {
  $("#case-modal").classList.add("hidden"); $("#case-modal").classList.remove("flex");
  closeModal("#case-modal");
}
function renderCaseTurns(turns) {
  const wrap = $("#cf-turns");
  wrap.innerHTML = turns.map((t, i) => `
    <div class="flex items-start gap-2" data-turn>
      <span class="text-xs text-slate-400 mt-2 w-6 text-right">${i + 1}.</span>
      <textarea data-turn-input rows="2"
        class="border rounded flex-1 px-2 py-1 text-sm"
        placeholder="第 ${i + 1} 轮用户消息">${escapeHtml(t?.content || "")}</textarea>
      <button type="button" data-turn-del class="btn btn-danger btn-sm mt-1">删除</button>
    </div>
  `).join("");
  wrap.querySelectorAll("[data-turn-del]").forEach((b) => b.addEventListener("click", () => {
    const all = collectTurns();
    const idx = Array.from(wrap.querySelectorAll("[data-turn-del]")).indexOf(b);
    all.splice(idx, 1);
    if (all.length === 0) all.push({ role: "user", content: "" });
    renderCaseTurns(all);
  }));
}
function collectTurns() {
  return Array.from($$("#cf-turns [data-turn-input]")).map((t) => ({
    role: "user",
    content: t.value.trim(),
  }));
}

$("#cf-add-turn").addEventListener("click", () => {
  const all = collectTurns();
  all.push({ role: "user", content: "" });
  renderCaseTurns(all);
});
$("#cf-dialogue-mode").addEventListener("change", syncDialogueModeUI);
$("#btn-cancel-case").addEventListener("click", closeCaseEditor);
$("#btn-new-case").addEventListener("click", () => {
  if (!$("#case-agent-select").value) return alert("先选择智能体");
  openCaseEditor(null);
});
window.editCase = async (id) => {
  try {
    const c = await api("/api/cases/" + id);
    openCaseEditor(c);
  } catch (e) {
    alert("加载用例失败：" + e.message);
  }
};

$("#btn-save-case").addEventListener("click", async () => {
  const dialogueMode = $("#cf-dialogue-mode").value || "scripted";
  const turns = collectTurns().filter((t) => t.content);
  // dynamic 模式下 turns 可空（仅作为可选开场触发）；scripted 模式必须至少 1 条
  if (dialogueMode !== "dynamic" && !turns.length) {
    return alert("脚本模式下至少要有一轮用户消息");
  }

  const payload = {
    agent_id: $("#case-agent-select").value,
    dimension: $("#cf-dimension").value,
    sub_type: $("#cf-sub-type").value.trim(),
    title: $("#cf-title").value.trim(),
    turns,
    expectation: $("#cf-expectation").value.trim(),
    pass_criteria: $("#cf-pass").value.split("\n").map((s) => s.trim()).filter(Boolean),
    weight: Math.max(1, Math.min(5, parseInt($("#cf-weight").value || "3", 10) || 3)),
    opening_mode: $("#cf-opening-mode").value || "default",
    dialogue_mode: dialogueMode,
    user_persona: $("#cf-user-persona").value.trim(),
    user_goal: $("#cf-user-goal").value.trim(),
    max_turns: Math.max(1, Math.min(20, parseInt($("#cf-max-turns").value || "6", 10) || 6)),
    termination_keywords: $("#cf-term-kw").value.split("\n").map(s => s.trim()).filter(Boolean),
  };

  try {
    if (editingCase) {
      await api("/api/cases/" + editingCase.id, { method: "PUT", body: payload });
    } else {
      await api("/api/cases", { method: "POST", body: payload });
    }
    closeCaseEditor();
    loadCasesForSelected();
  } catch (e) {
    alert("保存失败：" + e.message);
  }
});

// ---------- 全局：Esc 键关闭顶层 modal ----------
// 每个 modal 默认带 hidden 类，按 z 顺序找到最上层一个还在显示的，关闭它
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const modals = [
    { sel: "#case-modal",   close: closeCaseEditor },
    { sel: "#gen-modal",    close: closeGenModal },
    { sel: "#run-modal",    close: closeRunModal },
    { sel: "#tpl-modal",    close: () => { $("#tpl-modal").classList.add("hidden"); $("#tpl-modal").classList.remove("flex"); } },
    { sel: "#agent-modal",  close: () => { $("#agent-modal").classList.add("hidden"); $("#agent-modal").classList.remove("flex"); } },
  ];
  for (const m of modals) {
    const el = $(m.sel);
    if (el && !el.classList.contains("hidden")) {
      m.close();
      e.stopPropagation();
      return;
    }
  }
});

// ---------- 关闭策略 ----------
// 弹窗只能通过关闭按钮（× / 取消）或 Esc 键关闭，点击遮罩不关闭，避免误操作丢失编辑内容。

// ---------- 智能体分析按钮（强制重新分析）----------
$("#btn-analyze-agent").addEventListener("click", async () => {
  const id = $("#case-agent-select").value;
  if (!id) return alert("先选择智能体");
  const agent = (agents || []).find((a) => a.id === id);
  const btn = $("#btn-analyze-agent"); const old = btn.textContent;
  btn.disabled = true; btn.textContent = "分析中...";
  try {
    // force=true 强制重做并覆盖数据库；返回 {analysis, analysis_at, cached:false}
    const res = await api("/api/agents/" + id + "/analyze?force=true", { method: "POST" });
    setAnalysis(res.analysis, {
      agentName: agent?.name || "",
      open: true,
      analysisAt: res.analysis_at,
      cached: false,
    });
  } catch (e) {
    alert("分析失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
  }
});

// ---------- 生成用例：弹窗里勾选维度 + 每维度数量 ----------
const DEFAULT_SELECTED_DIMS = new Set(["alignment", "boundary", "industry", "badcase", "security"]);
let lastDimSelection = null;

// 生成模式状态：dyn / pd / dim
let currentGenMode = "dyn";

function openGenModal() {
  if (!$("#case-agent-select").value) return alert("先选择智能体");
  currentGenMode = "dyn";  // 默认动态对话
  switchGenMode("dyn");
  renderGenDimList();          // 「维度驱动」Tab 的维度列表
  renderDynDimChips();         // 「动态对话」Tab 内的维度多选
  $("#gen-modal").classList.remove("hidden"); $("#gen-modal").classList.add("flex");
  openModal("#gen-modal");
  updateGenTotal();
  syncUserOpeningVisibility();
}


// 生成模式 Tab 切换
function switchGenMode(mode) {
  currentGenMode = mode;
  // 切换 Tab 样式：用 data-active 替代硬编码 Tailwind 颜色类
  $$(".gen-mode-tab").forEach(b => {
    if (b.dataset.genMode === mode) {
      b.setAttribute("data-active", "true");
    } else {
      b.removeAttribute("data-active");
    }
  });
  // 切换内容区（三选一）
  $("#gen-section-dim").classList.toggle("hidden", mode !== "dim");
  $("#gen-section-pd").classList.toggle("hidden", mode !== "pd");
  $("#gen-section-dyn").classList.toggle("hidden", mode !== "dyn");
  // 更新副标题
  const subtitle = mode === "dim"
    ? "勾选维度并设定数量，0 表示不生成"
    : mode === "pd"
      ? "填写测试要点与级别，生成定向回归用例"
      : "LLM 根据系统提示词自动构造虚拟用户的人设和目标";
  $("#gen-modal-subtitle").textContent = subtitle;
}

// 绑定 Tab 点击
$$(".gen-mode-tab").forEach(b => b.addEventListener("click", () => switchGenMode(b.dataset.genMode)));

// 动态对话维度多选（chip 形式，仅多选无数量）
const DEFAULT_DYN_DIMS = new Set(["alignment", "boundary", "industry", "badcase", "multi_turn"]);
let _lastDynDims = null;  // Set<string>
function renderDynDimChips() {
  const wrap = $("#gen-dyn-dim-list");
  if (!wrap) return;
  const selected = _lastDynDims || new Set([...DEFAULT_DYN_DIMS].filter(k => dimensionsMeta.some(d => d.key === k)));
  wrap.innerHTML = dimensionsMeta.map(d => {
    const on = selected.has(d.key);
    return `<button type="button" data-key="${d.key}"${on ? ' data-active="true"' : ''}
      class="gen-dyn-dim text-xs px-2 py-1 rounded-full border"
      title="${escapeHtml(d.desc || '')}">${escapeHtml(d.label)}</button>`;
  }).join("");
  wrap.querySelectorAll(".gen-dyn-dim").forEach(b => {
    b.addEventListener("click", () => {
      const isOn = b.dataset.active === "true";
      if (isOn) {
        b.removeAttribute("data-active");
      } else {
        b.setAttribute("data-active", "true");
      }
    });
  });
}
function collectDynDims() {
  return Array.from($$('#gen-dyn-dim-list .gen-dyn-dim[data-active="true"]')).map(b => b.dataset.key);
}
$("#gen-dyn-dim-all")?.addEventListener("click", () => {
  _lastDynDims = new Set(dimensionsMeta.map(d => d.key));
  renderDynDimChips();
});
$("#gen-dyn-dim-none")?.addEventListener("click", () => {
  _lastDynDims = new Set();
  renderDynDimChips();
});


// 「用户开场内容」输入框只在 opening_mode=user 时显示
function syncUserOpeningVisibility() {
  const cur = document.querySelector('input[name="gen-opening"]:checked')?.value || "default";
  const wrap = $("#gen-user-opening-wrap");
  if (!wrap) return;
  wrap.classList.toggle("hidden", cur !== "user");
  if (cur !== "user") $("#gen-user-opening").value = "";
}
document.addEventListener("change", (e) => {
  if (e.target?.name === "gen-opening") syncUserOpeningVisibility();
});
function closeGenModal() {
  $("#gen-modal").classList.add("hidden"); $("#gen-modal").classList.remove("flex");
  closeModal("#gen-modal");
}
function renderGenDimList() {
  const list = $("#gen-dim-list");
  const defaultN = parseInt($("#gen-default-n").value || "3", 10) || 0;
  list.innerHTML = dimensionsMeta.map((d) => {
    const prev = lastDimSelection ? lastDimSelection[d.key] : undefined;
    const checked = prev !== undefined ? prev > 0 : DEFAULT_SELECTED_DIMS.has(d.key);
    const n = prev !== undefined ? prev : defaultN;
    return `
      <label class="border rounded p-3 flex items-start gap-3 hover:bg-slate-50 cursor-pointer">
        <input type="checkbox" class="gen-dim-check mt-1" data-key="${d.key}" ${checked ? "checked" : ""} />
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="dim-badge dim-${d.key}">${escapeHtml(d.label)}</span>
            <span class="text-xs text-slate-400">${escapeHtml(d.key)}</span>
          </div>
          <div class="text-xs text-slate-500 mt-1">${escapeHtml(d.desc || "")}</div>
        </div>
        <div class="flex items-center gap-1">
          <input type="number" min="0" max="20" value="${n}" class="gen-dim-n border rounded w-16 px-2 py-1 text-sm" data-key="${d.key}" />
          <span class="text-xs text-slate-400">条</span>
        </div>
      </label>
    `;
  }).join("");
  list.querySelectorAll(".gen-dim-check").forEach((c) => c.addEventListener("change", updateGenTotal));
  list.querySelectorAll(".gen-dim-n").forEach((c) => c.addEventListener("input", updateGenTotal));
}
function collectGenSelection() {
  const checks = $$("#gen-dim-list .gen-dim-check");
  const map = {}, dims = []; let total = 0;
  checks.forEach((c) => {
    const key = c.dataset.key;
    const nInput = $(`#gen-dim-list .gen-dim-n[data-key="${key}"]`);
    let n = parseInt(nInput.value || "0", 10);
    if (isNaN(n)) n = 0;
    n = Math.max(0, Math.min(20, n));
    if (c.checked && n > 0) { dims.push(key); map[key] = n; total += n; }
  });
  return { dims, map, total };
}
function updateGenTotal() { $("#gen-total").textContent = String(collectGenSelection().total); }

$("#btn-open-gen").addEventListener("click", openGenModal);
$("#btn-cancel-gen").addEventListener("click", closeGenModal);
$("#gen-select-all").addEventListener("click", () => { $$("#gen-dim-list .gen-dim-check").forEach((c) => (c.checked = true)); updateGenTotal(); });
$("#gen-select-none").addEventListener("click", () => { $$("#gen-dim-list .gen-dim-check").forEach((c) => (c.checked = false)); updateGenTotal(); });
$("#gen-apply-default").addEventListener("click", () => {
  const n = parseInt($("#gen-default-n").value || "0", 10) || 0;
  $$("#gen-dim-list .gen-dim-check").forEach((c) => {
    if (c.checked) $(`#gen-dim-list .gen-dim-n[data-key="${c.dataset.key}"]`).value = n;
  });
  updateGenTotal();
});

// ---------- 后台生成进度浮窗 ----------
// 同时运行的多个 job 都会渲染为右下角的卡片
const _genJobs = new Map();   // job_id -> { agent_id, agent_name, planned, dims, status, generated, error }

function renderGenToasts() {
  const wrap = $("#gen-toast");
  if (_genJobs.size === 0) { wrap.classList.add("hidden"); wrap.innerHTML = ""; return; }
  wrap.classList.remove("hidden");
  const jobs = Array.from(_genJobs.values());
  wrap.innerHTML = jobs.map((j) => {
    const isRunning = j.status === "running";
    const isDone = j.status === "done";
    const isErr = j.status === "error";
    const color = isErr ? "border-red-300" : isDone ? "border-emerald-300" : "border-amber-300";
    const titleText = isRunning ? "生成中…" : isDone ? "生成完成" : "生成失败";
    const titleColor = isRunning ? "text-amber-700" : isDone ? "text-emerald-700" : "text-red-700";
    const dimsText = (j.dims || []).map(d => dimLabel[d] || d).join("、");
    const total = j.planned || 0;
    const generated = j.generated || 0;
    const pct = total ? Math.min(100, Math.round((generated / total) * 100)) : (isRunning ? 0 : 100);
    return `
      <div class="bg-white border ${color} shadow rounded-lg p-3 w-80 text-sm">
        <div class="flex items-center justify-between gap-2">
          <div class="flex items-center gap-2 min-w-0">
            ${isRunning ? `<span class="w-2.5 h-2.5 rounded-full bg-amber-500 animate-pulse"></span>` : ""}
            ${isDone ? `<span class="text-emerald-500">✓</span>` : ""}
            ${isErr ? `<span class="text-red-500">✗</span>` : ""}
            <div class="font-medium truncate ${titleColor}">${escapeHtml(titleText)}</div>
            <div class="text-xs text-slate-400 truncate">· ${escapeHtml(j.agent_name || "")}</div>
          </div>
          <button class="text-xs text-slate-400 hover:text-slate-600" data-job-close="${j.job_id}">×</button>
        </div>
        <div class="text-xs text-slate-500 mt-1 truncate" title="${escapeHtml(dimsText)}">维度：${escapeHtml(dimsText || "-")}</div>
        <div class="mt-2 h-2.5 bg-slate-100 rounded overflow-hidden">
          <div class="h-full ${isErr ? "bg-red-500" : isDone ? "bg-emerald-600" : "bg-amber-500"} transition-all" style="width:${pct}%"></div>
        </div>
        <div class="text-xs text-slate-500 mt-1 flex items-center justify-between">
          <span>${isRunning ? `计划 ${total} 条，已生成 ${generated}` : isDone ? `已生成 ${generated} / 计划 ${total}` : escapeHtml(j.error || "未知错误")}</span>
          ${isDone ? `<button class="text-rust-link hover:underline" data-job-view="${j.job_id}">查看用例</button>` : ""}
        </div>
      </div>
    `;
  }).join("");

  wrap.querySelectorAll("[data-job-close]").forEach((b) => b.addEventListener("click", () => {
    _genJobs.delete(b.dataset.jobClose); renderGenToasts();
  }));
  wrap.querySelectorAll("[data-job-view]").forEach((b) => b.addEventListener("click", () => {
    const j = _genJobs.get(b.dataset.jobView);
    if (j?.agent_id) {
      switchTab("cases");
      $("#case-agent-select").value = j.agent_id;
      loadCasesForSelected();
    }
  }));
}

async function pollGenJob(job_id) {
  try {
    const job = await api("/api/cases/generation_jobs/" + job_id);
    const local = _genJobs.get(job_id) || {};
    Object.assign(local, {
      job_id,
      agent_id: job.agent_id,
      agent_name: job.agent_name,
      planned: job.planned,
      generated: job.generated,
      status: job.status,
      error: job.error,
    });
    _genJobs.set(job_id, local);
    renderGenToasts();

    if (job.status === "running") {
      setTimeout(() => pollGenJob(job_id), 2000);
    } else if (job.status === "done") {
      // 自动刷新当前选中智能体的用例列表，并从数据库读取最新分析（带时间戳）
      const sel = $("#case-agent-select")?.value;
      if (sel === job.agent_id) {
        loadCasesForSelected();
        loadAnalysisCached();
      }
      // 8 秒后自动收起浮窗
      setTimeout(() => { _genJobs.delete(job_id); renderGenToasts(); }, 8000);
    }
  } catch (e) {
    const local = _genJobs.get(job_id);
    if (local) {
      local.status = "error";
      local.error = e.message || "查询失败";
      renderGenToasts();
    }
  }
}

$("#btn-confirm-gen").addEventListener("click", async () => {
  const agent_id = $("#case-agent-select").value;
  if (!agent_id) return alert("先选择智能体");
  const agent = (agents || []).find((a) => a.id === agent_id);
  const btn = $("#btn-confirm-gen");

  // 根据当前模式分发提交路径
  if (currentGenMode === "dyn") {
    // ---- 动态对话 ----
    let generate_count = parseInt($("#gen-dyn-count").value || "8", 10) || 8;
    generate_count = Math.max(1, Math.min(30, generate_count));
    const opening_style = $("#gen-dyn-opening").value || "mixed";
    const user_hint = ($("#gen-dyn-hint")?.value || "").trim();
    const use_analysis = !!$("#gen-dyn-use-analysis")?.checked;
    const dimensions = collectDynDims();   // 多选维度

    btn.disabled = true; btn.textContent = "提交中...";
    try {
      const res = await api("/api/cases/generate_dynamic_async", {
        method: "POST",
        body: { agent_id, generate_count, opening_style, user_hint, use_analysis, dimensions },
      });
      closeGenModal();
      // 浮窗里显示选中的维度（无则提示「💬 动态对话」）
      const dimsForToast = dimensions.length ? dimensions : ["💬 动态对话"];
      _genJobs.set(res.job_id, {
        job_id: res.job_id,
        agent_id,
        agent_name: agent?.name || "",
        planned: res.planned ?? generate_count,
        generated: 0,
        status: "running",
        dims: dimsForToast,
      });

      renderGenToasts();
      pollGenJob(res.job_id);
    } catch (e) {
      alert("启动生成失败：" + e.message);
    } finally {
      btn.disabled = false; btn.textContent = "开始生成（后台）";
    }
    return;
  }

  if (currentGenMode === "pd") {
    // ---- PD 风格 ----
    const test_points = ($("#gen-pd-points")?.value || "").trim();
    const test_case_level = $("#gen-pd-level").value || "p1_p2";
    const opening_style = $("#gen-pd-opening").value || "mixed";
    const user_opening_text = ($("#gen-pd-user-opening")?.value || "").trim();
    let generate_count = parseInt($("#gen-pd-count").value || "10", 10) || 10;
    generate_count = Math.max(1, Math.min(50, generate_count));
    const use_analysis = !!$("#gen-pd-use-analysis")?.checked;

    btn.disabled = true; btn.textContent = "提交中...";
    try {
      const res = await api("/api/cases/generate_pd_async", {
        method: "POST",
        body: { agent_id, test_points, test_case_level, opening_style, user_opening_text, generate_count, use_analysis },
      });
      closeGenModal();
      _genJobs.set(res.job_id, {
        job_id: res.job_id,
        agent_id,
        agent_name: agent?.name || "",
        planned: res.planned ?? generate_count,
        generated: 0,
        status: "running",
        dims: ["PD:" + test_case_level],   // 在浮窗里显示模式标识
      });
      renderGenToasts();
      pollGenJob(res.job_id);
    } catch (e) {
      alert("启动生成失败：" + e.message);
    } finally {
      btn.disabled = false; btn.textContent = "开始生成（后台）";
    }
    return;
  }

  // ---- 维度驱动（原有逻辑） ----
  const { dims, map, total } = collectGenSelection();
  if (!dims.length || total === 0) { alert("请至少勾选一个维度并设置数量 > 0"); return; }
  lastDimSelection = { ...map };

  btn.disabled = true; btn.textContent = "提交中...";
  try {
    const openingMode = (document.querySelector('input[name="gen-opening"]:checked')?.value) || "default";
    const userOpeningText = openingMode === "user"
      ? ($("#gen-user-opening")?.value || "").trim()
      : "";
    const res = await api("/api/cases/generate_async", {
      method: "POST",
      body: {
        agent_id, dimensions: dims,
        cases_per_dim: parseInt($("#gen-default-n").value || "3", 10) || 3,
        cases_per_dim_map: map,
        opening_mode: openingMode,
        user_opening_text: userOpeningText,
      },
    });
    // 立即关闭弹窗，加入浮窗
    closeGenModal();
    _genJobs.set(res.job_id, {
      job_id: res.job_id,
      agent_id,
      agent_name: agent?.name || "",
      planned: res.planned ?? total,
      generated: 0,
      status: "running",
      dims,
    });
    renderGenToasts();
    pollGenJob(res.job_id);
  } catch (e) {
    alert("启动生成失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "开始生成（后台）";
  }
});

// ---------- 启动批量测试弹窗 ----------
async function openRunModal() {
  const agent_id = $("#case-agent-select").value;
  if (!agent_id) return alert("先选择智能体");
  $("#run-f-name").value = "";
  $("#run-f-concurrency").value = "8";

  // 拉取该智能体的批次列表，渲染为下拉框；默认选「最新批次（默认）」
  const sel = $("#run-f-batch");
  const hint = $("#run-f-batch-hint");
  sel.innerHTML = '<option value="">最新生成的用例组（默认）</option>';
  sel.disabled = true;
  hint.textContent = "加载用例组中…";
  try {
    const batches = await api("/api/agents/" + agent_id + "/batches");
    if (Array.isArray(batches) && batches.length) {
      const opts = batches.map((b, idx) => {
        const label = b.batch_label || (b.batch_id ? `#${b.batch_id.slice(-6)}` : "未分组");
        const tag = idx === 0 ? "（最新）" : "";
        return `<option value="${escapeHtml(b.batch_id || "")}">${escapeHtml(label)} · ${b.count} 条${tag}</option>`;
      }).join("");
      sel.innerHTML = '<option value="">最新生成的用例组（默认）</option>' + opts;
      hint.textContent = `共 ${batches.length} 组用例。不选择则默认跑最新一组。`;
    } else {
      hint.textContent = "该智能体暂无用例分组。";
    }
  } catch (e) {
    hint.textContent = "加载用例组失败：" + (e.message || "");
  } finally {
    sel.disabled = false;
  }

  $("#run-modal").classList.remove("hidden"); $("#run-modal").classList.add("flex");
  openModal("#run-modal");
}
function closeRunModal() {
  $("#run-modal").classList.add("hidden"); $("#run-modal").classList.remove("flex");
  closeModal("#run-modal");
}
$("#btn-start-run").addEventListener("click", openRunModal);
$("#btn-cancel-run").addEventListener("click", closeRunModal);
$("#btn-close-run").addEventListener("click", closeRunModal);

$("#btn-confirm-run").addEventListener("click", async () => {
  const agent_id = $("#case-agent-select").value;
  if (!agent_id) return alert("先选择智能体");
  const name = $("#run-f-name").value.trim();
  let concurrency = parseInt($("#run-f-concurrency").value || "8", 10);
  if (isNaN(concurrency) || concurrency < 1) concurrency = 1;
  if (concurrency > 20) concurrency = 20;

  const btn = $("#btn-confirm-run");
  btn.disabled = true; btn.textContent = "启动中...";
  try {
    const batch_id = $("#run-f-batch").value || undefined;
    const run = await api("/api/runs", { method: "POST", body: { agent_id, name, concurrency, batch_id } });
    closeRunModal();
    switchTab("runs"); viewRun(run.id);
  } catch (e) {
    alert("启动失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "开始";
  }
});

})();