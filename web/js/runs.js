(function() { 'use strict';

// ============= Runs =============
$("#btn-refresh-runs").addEventListener("click", loadRuns);
let runsCache = [];
const runsPager = { page: 1, pageSize: 12 };
let runsQuery = "";
$("#run-search").addEventListener("input", (e) => {
  runsQuery = e.target.value || "";
  runsPager.page = 1;
  renderRunsPage();
});
function _filterRuns(list, q) {
  const s = (q || "").trim().toLowerCase();
  if (!s) return list;
  return list.filter((r) => {
    return (r.name || "").toLowerCase().includes(s)
      || (r.agent_name || "").toLowerCase().includes(s)
      || (r.summary || "").toLowerCase().includes(s)
      || (r.status || "").toLowerCase().includes(s);
  });
}
async function loadRuns() {
  // 修复：从「查看任务详情」切回列表时，确保 run-list 不再 hidden
  $("#run-detail").classList.add("hidden");
  $("#run-list").classList.remove("hidden");
  const el = $("#run-list");
  const pagerEl = $("#run-pager");
  pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
  el.innerHTML = renderLoading();
  try {
    runsCache = await api("/api/runs");
  } catch (e) {
    el.innerHTML = `<div class="text-red-600 text-center py-6 text-sm">加载失败：${escapeHtml(e.message || "")}</div>`;
    return;
  }
  renderRunsPage();
}

function renderRunsPage() {
  const el = $("#run-list");
  const pagerEl = $("#run-pager");
  if (!runsCache.length) {
    el.innerHTML = renderEmptyState("暂无任务", "创建智能体并运行测试用例后，任务记录会显示在这里");
    pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
    return;
  }
  const filtered = _filterRuns(runsCache, runsQuery);
  if (!filtered.length) {
    el.innerHTML = renderEmptyState("没有匹配结果", `没有找到包含 "${escapeHtml(runsQuery)}" 的任务`);
    pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
    return;
  }
  const info = paginate(filtered, runsPager);
  el.innerHTML = info.items.map((r) => {
    const reportable = r.status === "completed" && r.total > 0;
    const hasTokens = r.tokens_in || r.tokens_out || r.cost_usd;
    return `
    <div class="card p-3 list-item flex justify-between items-center gap-3 flex-wrap">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 mb-1">
          <span class="font-medium">${escapeHtml(r.name || "(未命名)")}</span>
          <span class="status-badge ${r.status}">${r.status}</span>
        </div>
        <div class="text-xs text-slate-500 mt-1">
          ${r.agent_name ? `<span class="text-slate-600">${escapeHtml(r.agent_name)}</span> · ` : ""}
          共 ${r.total}，通过 ${r.passed}，失败 ${r.failed}，错误 ${r.errors} ·
          平均分 ${r.average_score}
        </div>
        <div class="text-xs text-slate-400 mt-1 flex flex-wrap gap-x-3 gap-y-1">
          ${r.created_at ? `<span title="${escapeHtml(_formatTime(r.created_at))}（北京时间）">创建 ${escapeHtml(_formatTimeShort(r.created_at))}</span>` : ""}
          ${r.started_at ? `<span title="${escapeHtml(_formatTime(r.started_at))}（北京时间）">开始 ${escapeHtml(_formatTimeShort(r.started_at))}</span>` : ""}
          ${r.finished_at ? `<span title="${escapeHtml(_formatTime(r.finished_at))}（北京时间）">完成 ${escapeHtml(_formatTimeShort(r.finished_at))}</span>` : ""}
          ${(r.started_at && r.finished_at) ? `<span>耗时 ${_formatDuration(r.finished_at - r.started_at)}</span>` : ""}
          ${hasTokens ? `<span class="text-amber-700">Token ${((r.tokens_in || 0) + (r.tokens_out || 0)).toLocaleString()}${r.cost_usd ? ` · $${r.cost_usd.toFixed(4)}` : ""}</span>` : ""}
        </div>
        ${r.summary ? `<div class="text-xs text-slate-600 mt-1">${escapeHtml(r.summary)}</div>` : ""}
      </div>
      <div class="flex gap-2 shrink-0 flex-wrap">
        <button class="btn btn-ghost btn-sm" onclick="viewRun('${r.id}')">查看</button>
        <button class="btn btn-primary btn-sm" onclick="viewReport('${r.id}')" ${reportable ? "" : "disabled title='任务完成后可查看报告'"}>测试报告</button>
        <button class="btn btn-danger btn-sm" onclick="deleteRun('${r.id}', ${JSON.stringify(r.name || '(未命名)').replace(/"/g, '&quot;')}, '${r.status}')">删除</button>
      </div>
    </div>
  `;
  }).join("");
  renderPager(pagerEl, info, (patch) => {
    Object.assign(runsPager, patch); renderRunsPage();
  });
}
let currentSSE = null;
let currentSSERetryTimer = null;
function closeCurrentSSE() {
  if (currentSSERetryTimer) { clearTimeout(currentSSERetryTimer); currentSSERetryTimer = null; }
  if (currentSSE) { try { currentSSE.close(); } catch {} currentSSE = null; }
}
window.viewRun = async (run_id) => {
  closeCurrentSSE();
  $("#run-list").classList.add("hidden");
  $("#run-pager").classList.add("hidden");
  const detail = $("#run-detail"); detail.classList.remove("hidden");
  detail.innerHTML = `
    <button class="text-sm text-slate-600 mb-3" onclick="backToRunList()">← 返回任务列表</button>
    <div id="run-header" class="bg-white border rounded p-4 mb-3"></div>
    <div id="run-results" class="space-y-2"></div>
  `;
  const run = await api("/api/runs/" + run_id);
  renderRunHeader(run);
  const results = await api("/api/runs/" + run_id + "/results");
  const resultMap = new Map(results.map((r) => [r.case_id, r]));
  renderResults(Array.from(resultMap.values()));

  if (run.status === "running" || run.status === "pending") {
    let sseRetries = 0;
    const maxRetries = 5;
    function connectSSE() {
      const es = new EventSource("/api/runs/" + run_id + "/stream");
      currentSSE = es;
      es.onmessage = (ev) => { try { sseRetries = 0; handleRunEvent(JSON.parse(ev.data), run, resultMap); } catch {} };
      es.onerror = () => {
        try { es.close(); } catch {};
        currentSSE = null;
        if (sseRetries < maxRetries) {
          sseRetries++;
          currentSSERetryTimer = setTimeout(() => {
            currentSSERetryTimer = null;
            connectSSE();
          }, Math.min(1000 * Math.pow(2, sseRetries), 16000));
        }
      };
    }
    connectSSE();
  }
};
window.backToRunList = () => {
  closeCurrentSSE();
  $("#run-detail").classList.add("hidden"); $("#run-list").classList.remove("hidden");
  loadRuns();
};

// 导出测试报告为 PDF：通过浏览器打印对话框另存为 PDF
// 优势：零依赖、保留中文与彩色 UI、用户可自定义页面/边距
window.exportReportPdf = () => {
  // 临时展开报告内所有折叠的 details，避免 PDF 缺内容
  const detail = $("#run-detail");
  if (!detail) return;
  const closedDetails = [];
  detail.querySelectorAll("details:not([open])").forEach((d) => {
    d.setAttribute("open", "");
    closedDetails.push(d);
  });
  // 启用打印样式
  document.body.classList.add("printing-report");
  // 等一帧让样式应用，再触发打印
  setTimeout(() => {
    window.print();
    // 打印对话框关闭后恢复原状
    document.body.classList.remove("printing-report");
    closedDetails.forEach((d) => d.removeAttribute("open"));
  }, 100);
};

// 删除测试任务（含全部用例结果，级联）
window.deleteRun = async (run_id, run_name, status) => {
  const isRunning = status === "running" || status === "pending";
  const tip = isRunning
    ? `任务「${run_name}」仍在运行，删除后后台执行会自然结束（写库时发现任务已被清除）。`
    : `任务「${run_name}」的全部用例结果将被删除，操作不可撤销。`;
  if (!confirm(tip + "\n\n确定继续？")) return;
  try {
    await api("/api/runs/" + run_id, { method: "DELETE" });
    loadRuns();
  } catch (e) {
    alert("删除失败：" + (e.message || ""));
  }
};

// ============= 测试报告 =============
window.viewReport = async (run_id) => {
  closeCurrentSSE();
  $("#run-list").classList.add("hidden");
  $("#run-pager").classList.add("hidden");
  const detail = $("#run-detail"); detail.classList.remove("hidden");
  detail.innerHTML = `
    <button class="text-sm text-slate-600 mb-3" onclick="backToRunList()">← 返回任务列表</button>
    <div class="text-slate-400 text-center py-12 text-sm">加载报告中...</div>
  `;
  try {
    const data = await api("/api/runs/" + run_id + "/report");
    renderReport(detail, data, run_id);
  } catch (e) {
    detail.innerHTML = `
      <button class="text-sm text-slate-600 mb-3" onclick="backToRunList()">← 返回任务列表</button>
      <div class="bg-white border rounded p-6 text-red-600">报告加载失败：${escapeHtml(e.message || "未知错误")}</div>
    `;
  }
};

function _scoreColor(score) {
  if (score >= 80) return "var(--color-success)";
  if (score >= 60) return "var(--color-warning)";
  return "var(--color-danger)";
}
function _scoreClass(score) {
  if (score >= 80) return "bg-emerald-500";
  if (score >= 60) return "bg-amber-500";
  return "bg-red-500";
}

// 纯 SVG 雷达图
function renderRadar(dimStats) {
  const cx = 140, cy = 140, r = 90;
  const n = dimStats.length;
  if (n === 0) return `<div class="text-slate-400 text-sm py-6 text-center">无维度数据</div>`;
  const angleStep = (2 * Math.PI) / Math.max(n, 3);

  const levels = [0.25, 0.5, 0.75, 1.0];
  let grid = "";
  for (const lv of levels) {
    const pts = dimStats.map((_, i) => {
      const a = -Math.PI / 2 + i * angleStep;
      return `${cx + r * lv * Math.cos(a)},${cy + r * lv * Math.sin(a)}`;
    }).join(" ");
    grid += `<polygon points="${pts}" fill="none" stroke="#e2e8f0" stroke-width="1" />`;
  }
  // 轴线
  let axis = "";
  for (let i = 0; i < n; i++) {
    const a = -Math.PI / 2 + i * angleStep;
    axis += `<line x1="${cx}" y1="${cy}" x2="${cx + r * Math.cos(a)}" y2="${cy + r * Math.sin(a)}" stroke="#e2e8f0" stroke-width="1" />`;
  }
  // 数据多边形
  const dataPts = dimStats.map((d, i) => {
    const a = -Math.PI / 2 + i * angleStep;
    const ratio = Math.max(0, Math.min(1, (d.score || 0) / 100));
    return `${cx + r * ratio * Math.cos(a)},${cy + r * ratio * Math.sin(a)}`;
  }).join(" ");
  // 标签
  let labels = "";
  dimStats.forEach((d, i) => {
    const a = -Math.PI / 2 + i * angleStep;
    const lx = cx + (r + 22) * Math.cos(a);
    const ly = cy + (r + 22) * Math.sin(a);
    labels += `<text x="${lx}" y="${ly}" text-anchor="middle" dominant-baseline="middle" font-size="11" fill="#475569">${escapeHtml(d.label || d.dim)}</text>`;
  });
  return `
    <svg viewBox="0 0 280 280" width="100%" height="100%" style="max-width:280px;max-height:280px;" role="img" aria-label="维度得分雷达图">
      ${grid}
      ${axis}
      <polygon points="${dataPts}" fill="rgba(37,99,235,.18)" stroke="var(--color-primary)" stroke-width="2" />
      ${dimStats.map((d, i) => {
        const a = -Math.PI / 2 + i * angleStep;
        const ratio = Math.max(0, Math.min(1, (d.score || 0) / 100));
        const x = cx + r * ratio * Math.cos(a);
        const y = cy + r * ratio * Math.sin(a);
        return `<circle cx="${x}" cy="${y}" r="3" fill="var(--color-primary)" />`;
      }).join("")}
      ${labels}
    </svg>
  `;
}

function renderReport(root, data, run_id) {
  const { run = {}, agent = {}, summary = {}, dimensions = [], cases = [] } = data || {};
  const startedTxt = run.started_at ? _formatTime(run.started_at) : "-";
  const finishedTxt = run.finished_at ? _formatTime(run.finished_at) : "-";

  root.innerHTML = `
    <button class="text-sm text-slate-600 mb-3" onclick="backToRunList()">← 返回任务列表</button>

    <!-- 顶部概览 -->
    <div class="card p-5 mb-4">
      <div class="flex items-start justify-between gap-3 flex-wrap">
        <div class="min-w-0">
          <h3 class="text-lg font-semibold truncate">测试报告 · ${escapeHtml(agent.name || "")}</h3>
          <div class="text-xs text-slate-500 mt-1">
            ${escapeHtml(run.name || "(未命名)")} · ${escapeHtml(agent.adapter || "")} · ${escapeHtml(agent.industry || "")}
          </div>
          <div class="text-xs text-slate-400 mt-1">开始 ${startedTxt} · 完成 ${finishedTxt}</div>
        </div>
        <div class="flex gap-2 flex-wrap">
          <button class="btn btn-primary btn-sm" onclick="exportReportPdf()" title="通过浏览器打印另存为 PDF">导出 PDF</button>
          <a class="btn btn-ghost btn-sm" href="/api/runs/${run_id}/report?format=md" target="_blank" rel="noopener">下载 Markdown</a>
          <a class="btn btn-ghost btn-sm" href="/api/runs/${run_id}/report?format=json" target="_blank" rel="noopener">下载 JSON</a>
        </div>
      </div>

      <div class="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-5">
        <div class="text-center">
          <div class="text-3xl font-bold" style="color:${_scoreColor(summary.avg_score || 0)}">${summary.avg_score ?? 0}</div>
          <div class="text-xs text-slate-500 mt-1">综合评分</div>
        </div>
        <div class="text-center">
          <div class="text-3xl font-bold text-emerald-600">${summary.pass_rate ?? 0}%</div>
          <div class="text-xs text-slate-500 mt-1">通过率</div>
        </div>
        <div class="text-center">
          <div class="text-3xl font-bold">${summary.total ?? 0}</div>
          <div class="text-xs text-slate-500 mt-1">总用例</div>
        </div>
        <div class="text-center">
          <div class="text-3xl font-bold text-red-600">${(summary.failed ?? 0) + (summary.errors ?? 0)}</div>
          <div class="text-xs text-slate-500 mt-1">失败 + 错误</div>
        </div>
      </div>

      ${(run.tokens_in || run.tokens_out || run.cost_usd) ? `
        <div class="mt-4 p-3 bg-slate-50 border border-slate-200 rounded text-xs text-slate-600 flex items-center gap-4 flex-wrap">
          <span class="font-semibold">Token 用量：</span>
          <span>输入 ${(run.tokens_in || 0).toLocaleString()}</span>
          <span>输出 ${(run.tokens_out || 0).toLocaleString()}</span>
          <span>总计 ${((run.tokens_in || 0) + (run.tokens_out || 0)).toLocaleString()}</span>
          ${run.cost_usd ? `<span class="text-amber-700 font-semibold">成本 $${run.cost_usd.toFixed(4)}</span>` : ""}
        </div>
      ` : ""}

      ${run.summary ? `<div class="mt-4 p-3 bg-slate-50 border border-slate-200 rounded text-sm text-slate-700">${escapeHtml(run.summary)}</div>` : ""}
    </div>

    <!-- 雷达图 + 维度详情 -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-4">
      <div class="bg-white border rounded-lg p-5">
        <div class="text-sm font-medium mb-3">维度得分雷达图</div>
        <div class="flex justify-center">
          ${renderRadar(dimensions)}
        </div>
      </div>
      <div class="bg-white border rounded-lg p-5">
        <div class="text-sm font-medium mb-3">各维度详情</div>
        ${dimensions.length === 0
          ? `<div class="text-slate-400 text-sm py-6 text-center">无维度数据</div>`
          : `<div class="space-y-3">
               ${dimensions.map(d => `
                 <div class="flex items-center justify-between gap-3">
                   <div class="flex items-center gap-2 min-w-0">
                     <span class="dim-badge dim-${d.dim}">${escapeHtml(d.label || d.dim)}</span>
                     <span class="text-xs text-slate-400 shrink-0">${d.passed}/${d.total}</span>
                   </div>
                   <div class="flex items-center gap-2 flex-1 max-w-[60%]">
                     <div class="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
                       <div class="h-full ${_scoreClass(d.score)} transition-all" style="width:${Math.max(0, Math.min(100, d.score))}%"></div>
                     </div>
                     <span class="text-sm font-mono w-10 text-right">${d.score}</span>
                   </div>
                 </div>
               `).join("")}
             </div>`
        }
      </div>
    </div>

    <!-- 维度 Tab + 用例列表 -->
    <div class="bg-white border rounded-lg p-5">
      <div class="flex flex-wrap gap-1 bg-slate-100 p-1 rounded-lg w-max max-w-full mb-3" id="report-dim-tabs">
        <button data-dim="" class="report-dim-tab px-3 py-1 rounded text-sm bg-white shadow text-slate-900 font-medium">全部 (${cases.length})</button>
        ${dimensions.map(d => `
          <button data-dim="${escapeHtml(d.dim)}" class="report-dim-tab px-3 py-1 rounded text-sm text-slate-500 hover:text-slate-700">
            ${escapeHtml(d.label || d.dim)} (${d.passed}/${d.total})
          </button>
        `).join("")}
      </div>
      <div id="report-case-list" class="space-y-2"></div>
    </div>
  `;

  // Tab 切换 + 列表渲染
  const tabs = root.querySelectorAll(".report-dim-tab");
  const renderList = (dim) => {
    const filtered = dim ? cases.filter(c => c.dimension === dim) : cases;
    const listEl = root.querySelector("#report-case-list");
    if (!filtered.length) {
      listEl.innerHTML = `<div class="text-slate-400 text-sm py-6 text-center">该维度下暂无用例</div>`;
      return;
    }
    listEl.innerHTML = filtered.map(c => {
      const mark = c.status === "error" ? "!"
        : (c.passed ? "✓" : "✗");
      const markColor = c.status === "error" ? "text-amber-600"
        : (c.passed ? "text-emerald-600" : "text-red-600");
      return `
        <details class="bg-white border rounded">
          <summary class="p-3 cursor-pointer flex items-center gap-3">
            <span class="${markColor} font-bold w-4 text-center">${mark}</span>
            <span class="dim-badge dim-${c.dimension}">${escapeHtml(c.dimension_label || c.dimension || "")}</span>
            ${c.sub_type ? `<span class="text-xs text-slate-400 shrink-0">${escapeHtml(c.sub_type)}</span>` : ""}
            <span class="text-sm text-slate-700 truncate flex-1 min-w-0">${escapeHtml(c.title || c.last_user_message || "")}</span>
            <span class="text-xs font-mono text-slate-500 shrink-0">${c.score}分</span>
          </summary>
          <div class="border-t p-3 space-y-2 text-sm">
            ${c.expectation ? `<div class="text-xs text-slate-500"><span class="text-slate-400">期望：</span>${escapeHtml(c.expectation)}</div>` : ""}
            ${c.pass_criteria?.length ? `<div class="text-xs text-slate-500"><span class="text-slate-400">通过标准：</span>${c.pass_criteria.map(escapeHtml).join("；")}</div>` : ""}
            <div class="border-t pt-2 space-y-1">
              ${(c.transcript || []).map(m => `
                <div>
                  <span class="text-xs ${m.role === 'user' ? 'text-blue-600' : 'text-emerald-600'}">${m.role === 'user' ? '虚拟用户：' : '智能体：'}</span>
                  <div class="ml-4 text-slate-700 whitespace-pre-wrap">${escapeHtml(m.content || "")}</div>
                </div>
              `).join("") || `<div class="text-xs text-slate-400">无对话轨迹</div>`}
            </div>
            ${c.judge_comment ? `<div class="text-xs text-slate-600"><span class="text-slate-400">评审意见：</span>${escapeHtml(c.judge_comment)}</div>` : ""}
            ${c.reasons?.length ? `<div class="text-xs text-red-600">原因：<ul class="ml-4 list-disc">${c.reasons.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul></div>` : ""}
            ${c.error ? `<div class="text-xs text-red-600">错误：${escapeHtml(c.error)}</div>` : ""}
          </div>
        </details>
      `;
    }).join("");
  };

  tabs.forEach(b => b.addEventListener("click", () => {
    tabs.forEach(x => {
      x.classList.remove("bg-white", "shadow", "text-slate-900", "font-medium");
      x.classList.add("text-slate-500", "hover:text-slate-700");
    });
    b.classList.remove("text-slate-500", "hover:text-slate-700");
    b.classList.add("bg-white", "shadow", "text-slate-900", "font-medium");
    renderList(b.dataset.dim || "");
  }));
  renderList("");
}
function renderRunHeader(run) {
  const total = run.total || 0; const finished = run.finished || 0;
  const pct = total ? Math.round((finished / total) * 100) : 0;
  const reportable = run.status === "completed" && total > 0;
  $("#run-header").innerHTML = `
    <div class="flex justify-between items-start gap-3 flex-wrap">
      <div class="min-w-0">
        <div class="text-lg font-semibold">${escapeHtml(run.name || "(未命名)")}</div>
        <div class="text-sm mt-1">
          状态 <span class="status-${run.status}">${run.status}</span> ·
          进度 ${finished}/${total} ·
          通过 ${run.passed} · 失败 ${run.failed} · 错误 ${run.errors} ·
          平均分 ${run.average_score || 0}
        </div>
        ${run.summary ? `<div class="text-sm text-slate-600 mt-1">${escapeHtml(run.summary)}</div>` : ""}
        ${run.error ? `<div class="text-sm text-red-600 mt-1">${escapeHtml(run.error)}</div>` : ""}
      </div>
      ${reportable
        ? `<button class="btn btn-primary btn-sm" onclick="viewReport('${run.id}')">📊 查看测试报告</button>`
        : ""}
    </div>
    <div class="mt-3 progress-track">
      <div class="progress-bar ${reportable ? 'success' : ''}" style="width: ${pct}%"></div>
    </div>
  `;
}
function renderResults(results) {
  const el = $("#run-results");
  el.innerHTML = results.map((r) => `
    <details class="bg-white border rounded">
      <summary class="p-3 cursor-pointer flex items-center gap-2">
        <span class="status-${r.status} font-semibold">
          ${r.status === "passed" ? "✓" : r.status === "failed" ? "✗" : r.status === "error" ? "!" : "·"}
        </span>
        ${r.dimension ? `<span class="dim-badge dim-${r.dimension}">${escapeHtml(r.dimension_label || r.dimension)}</span>` : ""}
        ${r.sub_type ? `<span class="text-xs text-slate-400">${escapeHtml(r.sub_type)}</span>` : ""}
        <span class="text-sm text-slate-700 truncate flex-1 min-w-0">${escapeHtml(r.title || "")}</span>
        <span class="text-xs font-mono text-slate-500 shrink-0">得分 ${r.score}</span>
      </summary>
      <div class="border-t p-3 space-y-2 text-sm">
        ${r.judge_comment ? `<div class="text-xs text-slate-600"><span class="text-slate-400">评审：</span>${escapeHtml(r.judge_comment)}</div>` : ""}
        ${r.transcript.map((m) => `
          <div><span class="text-xs ${m.role === 'user' ? 'text-blue-600' : 'text-emerald-600'}">${m.role === 'user' ? '虚拟用户：' : '智能体：'}</span>
          <div class="ml-4 text-slate-700">${escapeHtml(m.content || "")}</div></div>
        `).join("")}
        ${r.reasons?.length ? `<div class="text-xs text-red-600">原因：<ul>${r.reasons.map((x) => `<li>• ${escapeHtml(x)}</li>`).join("")}</ul></div>` : ""}
        ${r.error ? `<div class="text-xs text-red-600">错误：${escapeHtml(r.error)}</div>` : ""}
      </div>
    </details>
  `).join("");
}
function handleRunEvent(data, run, resultMap) {
  if (data.type === "snapshot") {
    Object.assign(run, data.run || {});
    for (const r of data.results || []) resultMap.set(r.case_id, r);
    renderRunHeader(run); renderResults(Array.from(resultMap.values()));
  } else if (data.type === "case_done") {
    const progress = data.progress || {};
    Object.assign(run, progress);
    const prev = resultMap.get(data.case_id) || {};
    resultMap.set(data.case_id, {
      ...prev,        // 保留 dimension / dimension_label / sub_type / title 等已附加的元信息
      case_id: data.case_id,
      status: data.status,
      score: data.score,
      passed: data.passed,
      reasons: data.reasons || [],
      judge_comment: data.judge_comment || "",
      error: data.error || "",
      transcript: prev.transcript || [],
    });
    renderRunHeader(run); renderResults(Array.from(resultMap.values()));
  } else if (data.type === "status") {
    run.status = data.status;
    if (data.summary) run.summary = data.summary;
    if (data.error) run.error = data.error;
    renderRunHeader(run);
    if (data.status === "completed") {
      api("/api/runs/" + run.id + "/results").then((list) => {
        for (const x of list) resultMap.set(x.case_id, x);
        renderResults(Array.from(resultMap.values()));
      });
    }
  }
}

})();
