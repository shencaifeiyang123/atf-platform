(function() { 'use strict';

// ============= Schedules（定时任务） =============
let schedules = [];
let schAgents = [];   // schedules 弹窗下拉用
let schDims = [];     // dimension key 列表（取自 loadDimensions）
let editingScheduleId = null;

async function loadSchedules() {
  try {
    schedules = await api("/api/schedules");
  } catch (e) {
    $("#schedule-list").innerHTML = `<div class="text-sm text-rose-600">加载失败：${escapeHtml(e.message || "")}</div>`;
    return;
  }
  renderSchedules();
}

function _fmtTrigger(t) {
  if (!t) return "";
  if (t.type === "interval") return `每 ${t.minutes} 分钟`;
  const hh = String(t.hour || 0).padStart(2, "0");
  const mm = String(t.minute || 0).padStart(2, "0");
  if (t.type === "daily") return `每天 ${hh}:${mm}`;
  if (t.type === "weekly") {
    const wks = ["周一","周二","周三","周四","周五","周六","周日"];
    return `每周 ${wks[t.weekday] || "周一"} ${hh}:${mm}`;
  }
  return JSON.stringify(t);
}

function _fmtSelector(s) {
  if (!s || s.mode === "all") return "全部用例";
  if (s.mode === "dimensions") return `维度：${(s.dimensions || []).join(" / ") || "(空)"}`;
  if (s.mode === "ids") return `指定 ${ (s.ids || []).length } 条用例`;
  return s.mode;
}

function _fmtTs(ms) {
  if (!ms) return "—";
  try { return new Date(ms).toLocaleString(); } catch { return "—"; }
}

function renderSchedules() {
  const wrap = $("#schedule-list");
  if (!schedules.length) {
    wrap.innerHTML = renderEmptyState("暂无定时任务", "点击右上角按钮创建第一个定时任务", "+ 新建定时", "#");
    return;
  }
  wrap.innerHTML = schedules.map((s) => {
    const enabled = !!s.enabled;
    return `
      <div class="card p-4 list-item" data-sid="${escapeHtml(s.id)}">
        <div class="flex items-start justify-between gap-3 flex-wrap">
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-semibold text-base">${escapeHtml(s.name || "(未命名)")}</span>
              <span class="status-badge ${enabled ? 'running' : 'disabled'}">${enabled ? '启用' : '已停用'}</span>
            </div>
            <div class="text-xs text-slate-500 mt-2 space-y-1">
              <div>智能体：${escapeHtml(s.agent_name || s.agent_id)}</div>
              <div>触发：${escapeHtml(_fmtTrigger(s.trigger))} · 并发 ${s.concurrency || 5}</div>
              <div>用例：${escapeHtml(_fmtSelector(s.selector))}</div>
              <div>下次：${escapeHtml(_fmtTs(s.next_run_at))} · 上次：${escapeHtml(_fmtTs(s.last_run_at))} ${s.last_run_status ? `(${escapeHtml(s.last_run_status)})` : ''}</div>
            </div>
          </div>
          <div class="flex items-center gap-2 flex-wrap">
            <button class="btn btn-ghost btn-sm" data-act="run-now">立即触发</button>
            <button class="btn btn-ghost btn-sm" data-act="toggle">${enabled ? '停用' : '启用'}</button>
            <button class="btn btn-ghost btn-sm" data-act="edit">编辑</button>
            <button class="btn btn-ghost btn-sm" data-act="del">删除</button>
            ${s.last_run_id ? `<button class="btn btn-ghost btn-sm" data-act="view-run" data-rid="${escapeHtml(s.last_run_id)}">查看上次</button>` : ''}
          </div>
        </div>
      </div>
    `;
  }).join("");
  // 事件代理
  wrap.querySelectorAll("[data-act]").forEach((b) => {
    b.addEventListener("click", () => onScheduleAction(b));
  });
}

async function onScheduleAction(btn) {
  const card = btn.closest("[data-sid]");
  const sid = card?.dataset.sid;
  const act = btn.dataset.act;
  if (!sid) return;
  const s = schedules.find((x) => x.id === sid);
  if (!s && act !== "view-run") return;
  if (act === "edit") return openScheduleModal(s);
  if (act === "del") {
    if (!confirm(`确定删除定时任务「${s.name}」？`)) return;
    try { await api("/api/schedules/" + sid, { method: "DELETE" }); }
    catch (e) { alert("删除失败：" + e.message); return; }
    loadSchedules();
    return;
  }
  if (act === "toggle") {
    try {
      await api("/api/schedules/" + sid, {
        method: "PUT",
        body: JSON.stringify({ enabled: !s.enabled }),
      });
    } catch (e) { alert("操作失败：" + e.message); return; }
    loadSchedules();
    return;
  }
  if (act === "run-now") {
    btn.disabled = true; btn.textContent = "触发中…";
    try {
      await api("/api/schedules/" + sid + "/run-now", { method: "POST" });
      loadSchedules();
    } catch (e) {
      alert("触发失败：" + e.message);
      btn.disabled = false; btn.textContent = "立即触发";
    }
    return;
  }
  if (act === "view-run") {
    const rid = btn.dataset.rid;
    if (rid) { switchTab("runs"); viewRun(rid); }
    return;
  }
}

// ---------- 弹窗：新建 / 编辑 ----------

async function openScheduleModal(s) {
  editingScheduleId = s ? s.id : null;
  $("#schedule-modal-title").textContent = s ? "编辑定时任务" : "新建定时任务";

  // 拉一份 agents 给下拉（缓存到 schAgents）
  if (!schAgents.length) {
    try { schAgents = await api("/api/agents"); } catch { schAgents = []; }
  }
  const agentSel = $("#sch-f-agent");
  agentSel.innerHTML = schAgents.map((a) =>
    `<option value="${escapeHtml(a.id)}">${escapeHtml(a.name)}</option>`
  ).join("") || `<option value="">(请先创建智能体)</option>`;

  // 维度复选框
  schDims = (dimensionsMeta || []).map((d) => d.key);
  $("#sch-f-dims-wrap").innerHTML = (dimensionsMeta || []).map((d) =>
    `<label class="inline-flex items-center gap-1 px-2 py-1 border rounded-md">
      <input type="checkbox" data-dim="${escapeHtml(d.key)}" />
      <span>${escapeHtml(d.label)}</span>
    </label>`
  ).join("");

  // 默认值
  $("#sch-f-name").value = s?.name || "";
  if (s?.agent_id) agentSel.value = s.agent_id;
  $("#sch-f-trigger-type").value = s?.trigger?.type || "interval";
  $("#sch-f-concurrency").value = s?.concurrency || 5;
  $("#sch-f-enabled").checked = s ? !!s.enabled : true;

  // 维度选择
  const selMode = s?.selector?.mode || "all";
  document.querySelectorAll('input[name="sch-selector"]').forEach((r) => {
    r.checked = (r.value === selMode);
  });
  $("#sch-f-dims-wrap").classList.toggle("hidden", selMode !== "dimensions");
  if (selMode === "dimensions") {
    const set = new Set(s?.selector?.dimensions || []);
    $("#sch-f-dims-wrap").querySelectorAll("[data-dim]").forEach((cb) => {
      cb.checked = set.has(cb.dataset.dim);
    });
  }

  renderTriggerDetail(s?.trigger);

  $("#schedule-modal").classList.remove("hidden");
  $("#schedule-modal").classList.add("flex");
  openModal("#schedule-modal");
}

function closeScheduleModal() {
  $("#schedule-modal").classList.add("hidden");
  $("#schedule-modal").classList.remove("flex");
  closeModal("#schedule-modal");
  editingScheduleId = null;
}

function renderTriggerDetail(t) {
  const type = $("#sch-f-trigger-type").value;
  const det = $("#sch-f-trigger-detail");
  if (type === "interval") {
    det.innerHTML = `
      <label class="block">每隔
        <input id="sch-f-minutes" type="number" min="5" value="${t?.minutes || 60}" class="border border-slate-200 rounded-md px-2 py-1 mx-1 w-24" /> 分钟（最小 5）
      </label>`;
  } else if (type === "daily") {
    det.innerHTML = `
      <label class="block">时间
        <input id="sch-f-hour" type="number" min="0" max="23" value="${t?.hour ?? 9}" class="border border-slate-200 rounded-md px-2 py-1 mx-1 w-16" />:
        <input id="sch-f-minute" type="number" min="0" max="59" value="${t?.minute ?? 0}" class="border border-slate-200 rounded-md px-2 py-1 mx-1 w-16" />
      </label>`;
  } else {
    const wks = ["周一","周二","周三","周四","周五","周六","周日"];
    det.innerHTML = `
      <label class="block">星期
        <select id="sch-f-weekday" class="border border-slate-200 rounded-md px-2 py-1 mx-1 bg-white">
          ${wks.map((w, i) => `<option value="${i}" ${i === (t?.weekday || 0) ? "selected" : ""}>${w}</option>`).join("")}
        </select>
      </label>
      <label class="block">时间
        <input id="sch-f-hour" type="number" min="0" max="23" value="${t?.hour ?? 9}" class="border border-slate-200 rounded-md px-2 py-1 mx-1 w-16" />:
        <input id="sch-f-minute" type="number" min="0" max="59" value="${t?.minute ?? 0}" class="border border-slate-200 rounded-md px-2 py-1 mx-1 w-16" />
      </label>`;
  }
}

$("#btn-new-schedule").addEventListener("click", () => openScheduleModal(null));
$("#btn-refresh-schedules").addEventListener("click", () => loadSchedules());
$("#btn-close-schedule").addEventListener("click", closeScheduleModal);
$("#btn-cancel-schedule").addEventListener("click", closeScheduleModal);
$("#sch-f-trigger-type").addEventListener("change", () => renderTriggerDetail());
document.addEventListener("change", (e) => {
  const r = e.target.closest('input[name="sch-selector"]');
  if (!r) return;
  $("#sch-f-dims-wrap").classList.toggle("hidden", r.value !== "dimensions");
});

$("#btn-confirm-schedule").addEventListener("click", async () => {
  const name = $("#sch-f-name").value.trim();
  const agent_id = $("#sch-f-agent").value;
  if (!name) { alert("请填写名称"); return; }
  if (!agent_id) { alert("请选择智能体"); return; }
  const ttype = $("#sch-f-trigger-type").value;
  const trigger = { type: ttype, minutes: 0, hour: 0, minute: 0, weekday: 0 };
  if (ttype === "interval") {
    trigger.minutes = Math.max(5, parseInt($("#sch-f-minutes").value, 10) || 60);
  } else if (ttype === "daily") {
    trigger.hour = parseInt($("#sch-f-hour").value, 10) || 0;
    trigger.minute = parseInt($("#sch-f-minute").value, 10) || 0;
  } else {
    trigger.hour = parseInt($("#sch-f-hour").value, 10) || 0;
    trigger.minute = parseInt($("#sch-f-minute").value, 10) || 0;
    trigger.weekday = parseInt($("#sch-f-weekday").value, 10) || 0;
  }
  const selMode = (document.querySelector('input[name="sch-selector"]:checked') || {}).value || "all";
  const selector = { mode: selMode, dimensions: [], ids: [] };
  if (selMode === "dimensions") {
    selector.dimensions = Array.from($("#sch-f-dims-wrap").querySelectorAll("[data-dim]"))
      .filter((cb) => cb.checked).map((cb) => cb.dataset.dim);
    if (!selector.dimensions.length) { alert("请至少勾选一个维度"); return; }
  }
  const concurrency = Math.max(1, Math.min(20, parseInt($("#sch-f-concurrency").value, 10) || 5));
  const enabled = $("#sch-f-enabled").checked;
  const payload = { name, agent_id, trigger, selector, concurrency, enabled, on_overlap: "skip" };

  try {
    if (editingScheduleId) {
      await api("/api/schedules/" + editingScheduleId, {
        method: "PUT", body: payload,
      });
    } else {
      await api("/api/schedules", { method: "POST", body: payload });
    }
  } catch (e) {
    alert("保存失败：" + e.message);
    return;
  }
  closeScheduleModal();
  loadSchedules();
});

// ESC 关闭新增的两个 modal（追加到已有 keydown 监听之外）
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const om = $("#optimize-modal");
  if (om && !om.classList.contains("hidden")) { closeOptimizeModal(); return; }
  const cm = $("#config-modal");
  if (cm && !cm.classList.contains("hidden")) { closeConfigModal(); return; }
});

// 初始加载：先拉维度元数据，再渲染列表
loadDimensions().finally(() => switchTab("agents"));

// 页面刷新后从服务端找回所有生成任务（含 running / 最近完成的），
// 还原右上角浮窗，对仍在运行的恢复轮询。
// 服务进程重启不再丢任务：后端 generation_jobs 表持久化 + 启动时把
// 残留的 running 改写为 error（mark_stale_running_jobs_as_error）
(async function restoreActiveGenJobs() {
  try {
    const jobs = await api("/api/cases/generation_jobs?active_only=true");
    if (!Array.isArray(jobs) || !jobs.length) return;
    for (const j of jobs) {
      if (_genJobs.has(j.id)) continue;
      // 拿回原始入参里的 dimensions，浮窗能正确显示「维度：xx、xx」
      const dims = (j.params && Array.isArray(j.params.dimensions)) ? j.params.dimensions : [];
      _genJobs.set(j.id, {
        job_id: j.id,
        agent_id: j.agent_id,
        agent_name: j.agent_name || "",
        planned: j.planned || 0,
        generated: j.generated || 0,
        status: j.status,
        dims,
      });
      if (j.status === "running") pollGenJob(j.id);
    }
    renderGenToasts();
  } catch (e) {
    console.warn("[restoreActiveGenJobs]", e);
  }
})();

// ============= 鉴权（单密码 + cookie session）=============
// 启动时探测 /api/auth/status：
//   enabled=false  → 鉴权关闭，logout 按钮藏起来
//   enabled=true & authenticated=false → 弹蒙层
//   enabled=true & authenticated=true  → 仅显示 logout 按钮
function showLoginOverlay(errMsg) {
  const ov = $("#login-overlay");
  if (!ov) return;
  ov.hidden = false;
  const err = $("#login-err");
  if (errMsg) {
    err.textContent = errMsg;
    err.hidden = false;
  } else {
    err.hidden = true;
  }
  // 聚焦密码框，键盘可直接输入
  setTimeout(() => $("#login-pw")?.focus(), 0);
}
function hideLoginOverlay() {
  const ov = $("#login-overlay");
  if (ov) ov.hidden = true;
  const err = $("#login-err");
  if (err) err.hidden = true;
  const pw = $("#login-pw");
  if (pw) pw.value = "";
}

// 登录表单提交
$("#login-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const pw = $("#login-pw")?.value || "";
  const btn = $("#login-submit");
  btn.disabled = true;
  btn.textContent = "[ ... ]";
  try {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: pw }),
    });
    if (r.ok) {
      hideLoginOverlay();
      $("#btn-logout").hidden = false;
      // 成功后无需整页刷新，但已加载的局部状态可能基于旧 401 失败结果，这里简单刷一下当前 tab
      try { location.reload(); } catch (_) {}
    } else {
      const data = await r.json().catch(() => ({}));
      showLoginOverlay(data.detail || `登录失败（${r.status}）`);
    }
  } catch (err) {
    showLoginOverlay("网络错误：" + (err.message || err));
  } finally {
    btn.disabled = false;
    btn.textContent = "[ enter ]";
  }
});

// 登出按钮
$("#btn-logout")?.addEventListener("click", async () => {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (_) { /* 静默：本地清状态即可 */ }
  showLoginOverlay();
  $("#btn-logout").hidden = true;
});

// 启动探测
(async function bootAuth() {
  try {
    const r = await fetch("/api/auth/status").then((x) => x.json());
    if (!r.enabled) {
      // 鉴权关闭：保持 logout 按钮隐藏即可
      return;
    }
    if (!r.authenticated) {
      showLoginOverlay();
    } else {
      $("#btn-logout").hidden = false;
    }
  } catch (e) {
    // 后端挂了：让用户看到正常的请求错误，不强弹蒙层
    console.warn("[auth status]", e);
  }
})();

})();
