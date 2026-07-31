(function() { 'use strict';

// ============= Templates =============
let tplMeta = { dimensions: [], industries: [], types: [], initialized: false };
let tplState = {
  activeType: "dimension",
  industryFilter: "education",
  dimensionFilter: "",
  list: [],
};

async function loadTemplatesTab() {
  if (!tplMeta.types.length) {
    try { tplMeta = await api("/api/templates/meta"); } catch (e) { alert("加载模板元数据失败：" + e.message); return; }
  }
  renderTplTypeTabs();
  await reloadTemplates();
}

function renderTplTypeTabs() {
  const wrap = $("#tpl-type-tabs");
  wrap.innerHTML = tplMeta.types.map((t) => `
    <button data-type="${t.key}" class="tpl-type-tab px-3 py-1 rounded text-sm ${t.key === tplState.activeType ? "bg-white shadow text-slate-900 font-medium" : "text-slate-500 hover:text-slate-700"}">
      ${escapeHtml(t.label)}
    </button>
  `).join("");
  wrap.querySelectorAll(".tpl-type-tab").forEach((b) => b.addEventListener("click", () => {
    tplState.activeType = b.dataset.type;
    tplState.dimensionFilter = "";
    renderTplTypeTabs();
    reloadTemplates();
  }));
}

function renderTplFilter() {
  const el = $("#tpl-filter");
  if (tplState.activeType === "industry_rule") {
    el.innerHTML = tplMeta.industries.map((opt) => `
      <button data-key="${opt.key}" class="tpl-ind-btn chip" ${opt.key === tplState.industryFilter ? 'data-active="true"' : ''}>
        ${escapeHtml(opt.label)}
      </button>
    `).join("");
    el.querySelectorAll(".tpl-ind-btn").forEach((b) => b.addEventListener("click", () => {
      tplState.industryFilter = b.dataset.key; reloadTemplates();
    }));
  } else if (tplState.activeType === "good_case" || tplState.activeType === "bad_case" || tplState.activeType === "dimension") {
    const all = `<button data-key="" class="tpl-dim-btn chip" ${!tplState.dimensionFilter ? 'data-active="true"' : ''}>全部</button>`;
    const dims = tplMeta.dimensions.map((opt) => `
      <button data-key="${opt.key}" class="tpl-dim-btn chip" ${opt.key === tplState.dimensionFilter ? 'data-active="true"' : ''}>
        ${escapeHtml(opt.label)}
      </button>
    `).join("");
    el.innerHTML = all + dims;
    el.querySelectorAll(".tpl-dim-btn").forEach((b) => b.addEventListener("click", () => {
      tplState.dimensionFilter = b.dataset.key; reloadTemplates();
    }));
  } else {
    el.innerHTML = "";
  }
}

async function reloadTemplates() {
  renderTplFilter();
  const params = new URLSearchParams({ type: tplState.activeType });
  if (tplState.activeType === "industry_rule") params.set("industry", tplState.industryFilter);
  if ((tplState.activeType === "good_case" || tplState.activeType === "bad_case" || tplState.activeType === "dimension") && tplState.dimensionFilter) {
    params.set("dimension", tplState.dimensionFilter);
  }
  try {
    tplState.list = await api("/api/templates?" + params.toString());
  } catch (e) {
    tplState.list = [];
    alert("加载模板失败：" + e.message);
  }
  renderTplList();
}

function renderTplList() {
  const empty = $("#tpl-empty-hint");
  const list = $("#tpl-list");
  if (!tplState.list.length) {
    list.innerHTML = "";
    empty.classList.remove("hidden");
    empty.innerHTML = tplMeta.initialized
      ? `<div>当前筛选下没有模板，点击右上角「+ 新增」</div>`
      : `<div class="space-y-3">
           <div>首次使用，需要初始化默认模板（维度 Prompt、行业规则、Good/Bad Case 示例）</div>
           <button class="btn btn-primary btn-sm" onclick="initDefaultTemplates()">[ + ] 初始化默认模板</button>
         </div>`;
    return;
  }
  empty.classList.add("hidden");
  if (tplState.activeType === "industry_rule") {
    list.innerHTML = tplState.list.map((t) => `
      <div class="bg-white border rounded p-3 flex items-center gap-3">
        <span class="flex-1 text-sm">${escapeHtml(t.content)}</span>
        <span class="text-xs text-slate-400">排序 ${t.sort_order}</span>
        ${tplBadge(t)}
        <button class="btn btn-ghost btn-sm" onclick="editTpl('${t.id}')">编辑</button>
        <button class="btn btn-danger btn-sm" onclick="delTpl('${t.id}')">删除</button>
      </div>
    `).join("");
  } else if (tplState.activeType === "good_case" || tplState.activeType === "bad_case") {
    list.innerHTML = tplState.list.map((t) => {
      let parsed = {}; try { parsed = JSON.parse(t.content); } catch {}
      const userInput = parsed?.turns?.[0]?.content || "";
      return `
        <div class="bg-white border rounded p-3">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1">
              <div class="flex items-center gap-2">
                <span class="dim-badge dim-${t.dimension}">${dimLabel[t.dimension] || t.dimension || "未关联"}</span>
                <span class="font-medium text-sm">${escapeHtml(t.name)}</span>
                ${tplBadge(t)}
              </div>
              <div class="text-sm text-slate-600 mt-1"><span class="text-slate-400">用户：</span>${escapeHtml(userInput.slice(0, 120))}</div>
              ${tplState.activeType === "good_case" && parsed.expectation
                ? `<div class="text-xs text-slate-500"><span class="text-slate-400">期望：</span>${escapeHtml(parsed.expectation)}</div>` : ""}
              ${tplState.activeType === "bad_case" && parsed.reason
                ? `<div class="text-xs text-red-500"><span class="text-slate-400">问题：</span>${escapeHtml(parsed.reason)}</div>` : ""}
            </div>
            <div class="flex flex-col gap-1">
              <button class="btn btn-ghost btn-sm" onclick="editTpl('${t.id}')">编辑</button>
              <button class="btn btn-danger btn-sm" onclick="delTpl('${t.id}')">删除</button>
            </div>
          </div>
        </div>
      `;
    }).join("");
  } else {
    // dimension / system_prompt
    list.innerHTML = tplState.list.map((t) => {
      const preview = (t.content || "").slice(0, 200).replace(/\n/g, " ");
      return `
        <div class="bg-white border rounded p-3 ${t.is_active ? "" : "opacity-60"}">
          <div class="flex items-start justify-between gap-3">
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-medium">${escapeHtml(t.name)}</span>
                ${t.dimension ? `<span class="dim-badge dim-${t.dimension}">${dimLabel[t.dimension] || t.dimension}</span>` : ""}
                ${tplBadge(t)}
              </div>
              ${t.description ? `<div class="text-sm text-slate-500 mt-1">${escapeHtml(t.description)}</div>` : ""}
              <pre class="text-xs text-slate-500 mt-1 truncate">${escapeHtml(preview)}…</pre>
            </div>
            <div class="flex flex-col gap-1 shrink-0">
              <button class="btn btn-ghost btn-sm" onclick="editTpl('${t.id}')">编辑</button>
              <button class="btn btn-ghost btn-sm" onclick="toggleTpl('${t.id}', ${t.is_active ? "false" : "true"})">${t.is_active ? "禁用" : "启用"}</button>
              <button class="btn btn-danger btn-sm" onclick="delTpl('${t.id}')">删除</button>
            </div>
          </div>
        </div>
      `;
    }).join("");
  }
}
function tplBadge(t) {
  return t.is_active
    ? `<span class="tag tag-pass">启用</span>`
    : `<span class="tag tag-pending">禁用</span>`;
}

window.initDefaultTemplates = async () => {
  try {
    const res = await api("/api/templates/init", { method: "POST" });
    alert((res.message || "初始化完成") + "\n" + JSON.stringify(res.added || res.count || ""));
    tplMeta.initialized = true;
    reloadTemplates();
  } catch (e) { alert("初始化失败：" + e.message); }
};
$("#btn-init-tpls").addEventListener("click", () => initDefaultTemplates());

window.toggleTpl = async (id, activate) => {
  await api("/api/templates/" + id, { method: "PUT", body: { is_active: activate } });
  reloadTemplates();
};
window.delTpl = async (id) => {
  if (!confirm("确定删除此模板？")) return;
  await api("/api/templates/" + id, { method: "DELETE" });
  reloadTemplates();
};

// ---------- Template Editor ----------
let editingTpl = null;

$("#btn-new-tpl").addEventListener("click", () => openTplEditor(null));
$("#btn-cancel-tpl").addEventListener("click", () => {
  $("#tpl-modal").classList.add("hidden"); $("#tpl-modal").classList.remove("flex");
  closeModal("#tpl-modal");
});

window.editTpl = async (id) => {
  try { const t = await api("/api/templates/" + id); openTplEditor(t); }
  catch (e) { alert("加载模板失败：" + e.message); }
};

function openTplEditor(t) {
  editingTpl = t;
  $("#tpl-modal-title").textContent = t ? "编辑模板" : "新增模板";

  // 类型下拉
  $("#tpl-f-type").innerHTML = tplMeta.types.map((tp) =>
    `<option value="${tp.key}">${escapeHtml(tp.label)}</option>`
  ).join("");
  $("#tpl-f-type").value = (t && t.type) || tplState.activeType;

  // 维度下拉（含「无」）
  $("#tpl-f-dim").innerHTML = `<option value="">（无）</option>` + tplMeta.dimensions.map((d) =>
    `<option value="${d.key}">${escapeHtml(d.label)} (${d.key})</option>`
  ).join("");
  $("#tpl-f-dim").value = t?.dimension || tplState.dimensionFilter || "";

  // 行业下拉
  $("#tpl-f-industry").innerHTML = tplMeta.industries.map((i) =>
    `<option value="${i.key}">${escapeHtml(i.label)} (${i.key})</option>`
  ).join("");
  $("#tpl-f-industry").value = t?.industry || tplState.industryFilter || "education";

  $("#tpl-f-name").value = t?.name || "";
  $("#tpl-f-desc").value = t?.description || "";
  $("#tpl-f-active").checked = t ? !!t.is_active : true;
  $("#tpl-f-sort").value = t?.sort_order ?? 0;

  // 内容区按类型切换
  fillEditorByType($("#tpl-f-type").value, t);

  $("#tpl-modal").classList.remove("hidden"); $("#tpl-modal").classList.add("flex");
  openModal("#tpl-modal");
}

$("#tpl-f-type").addEventListener("change", (e) => fillEditorByType(e.target.value, null));

function fillEditorByType(type, t) {
  const isCase = type === "good_case" || type === "bad_case";
  $("#tpl-f-content-text-wrap").classList.toggle("hidden", isCase);
  $("#tpl-f-case-wrap").classList.toggle("hidden", !isCase);
  $("#tpl-f-industry-wrap").classList.toggle("hidden", type !== "industry_rule");
  $("#tpl-f-dim-wrap").classList.toggle("hidden", type === "industry_rule");

  if (!isCase) {
    $("#tpl-f-content").value = t?.content || "";
    $("#tpl-f-vars-hint").innerHTML = type === "dimension"
      ? `可用占位符：<code>{core_value}</code> <code>{capabilities}</code> <code>{boundaries}</code> <code>{user_profile}</code> <code>{industry}</code> <code>{industry_rules}</code> <code>{good_cases}</code> <code>{bad_cases}</code> <code>{n}</code> <code>{multi_turn}</code> <code>{system_prompt}</code>`
      : type === "system_prompt"
        ? `系统级 Prompt（如「能力提取」「自审」），生成器读取后用作流水线的步骤。`
        : `行业规则：每个模板填一行规则文本，会被注入到「行业规范」维度生成 Prompt 的 <code>{industry_rules}</code> 占位符。`;
  } else {
    // 解析现有 JSON
    let parsed = {}; try { parsed = JSON.parse(t?.content || "{}"); } catch {}
    $("#tpl-f-case-user").value = parsed?.turns?.[0]?.content || "";
    $("#tpl-f-case-exp").value = parsed?.expectation || "";
    $("#tpl-f-case-criteria").value = (parsed?.passCriteria || []).join("\n");
    $("#tpl-f-case-reason").value = parsed?.reason || "";

    const isGood = type === "good_case";
    $("#tpl-f-case-exp-wrap").classList.toggle("hidden", !isGood);
    $("#tpl-f-case-criteria-wrap").classList.toggle("hidden", !isGood);
    $("#tpl-f-case-reason-wrap").classList.toggle("hidden", isGood);
  }
}

$("#btn-save-tpl").addEventListener("click", async () => {
  const type = $("#tpl-f-type").value;
  const name = $("#tpl-f-name").value.trim();
  if (!name) return alert("名称不能为空");

  let content = "";
  if (type === "good_case") {
    const user = $("#tpl-f-case-user").value.trim();
    if (!user) return alert("用户输入不能为空");
    content = JSON.stringify({
      turns: [{ role: "user", content: user }],
      expectation: $("#tpl-f-case-exp").value.trim(),
      passCriteria: $("#tpl-f-case-criteria").value.split("\n").map((s) => s.trim()).filter(Boolean),
    });
  } else if (type === "bad_case") {
    const user = $("#tpl-f-case-user").value.trim();
    if (!user) return alert("用户输入不能为空");
    content = JSON.stringify({
      turns: [{ role: "user", content: user }],
      reason: $("#tpl-f-case-reason").value.trim(),
    });
  } else {
    content = $("#tpl-f-content").value.trim();
    if (!content) return alert("内容不能为空");
  }

  const payload = {
    type,
    dimension: type === "industry_rule" ? "industry" : ($("#tpl-f-dim").value || ""),
    industry: type === "industry_rule" ? $("#tpl-f-industry").value : "",
    name,
    content,
    description: $("#tpl-f-desc").value.trim(),
    is_active: $("#tpl-f-active").checked,
    sort_order: parseInt($("#tpl-f-sort").value || "0", 10) || 0,
  };

  try {
    if (editingTpl) {
      await api("/api/templates/" + editingTpl.id, { method: "PUT", body: payload });
    } else {
      await api("/api/templates", { method: "POST", body: payload });
    }
    $("#tpl-modal").classList.add("hidden"); $("#tpl-modal").classList.remove("flex");
  closeModal("#tpl-modal");
    // 切换到对应 type tab，看到新建项
    tplState.activeType = type;
    renderTplTypeTabs();
    reloadTemplates();
  } catch (e) {
    alert("保存失败：" + e.message);
  }
});

// ============= 平台配置 Modal =============
function openConfigModal() {
  $("#config-modal").classList.remove("hidden"); $("#config-modal").classList.add("flex");
  openModal("#config-modal");
  api("/api/config").then((cfg) => {
    const g = cfg.generator_llm || {}, j = cfg.judge_llm || {};
    $("#cfg-gen-base").value = g.base_url || "";
    $("#cfg-gen-key").value = "";
    $("#cfg-gen-key-mask").textContent = g.api_key_set ? `当前已配置：${g.api_key_masked || "(已设置)"}` : "（未配置）";
    $("#cfg-gen-model").value = g.model || "";
    $("#cfg-gen-temp").value = g.temperature ?? 0.7;
    $("#cfg-judge-base").value = j.base_url || "";
    $("#cfg-judge-key").value = "";
    $("#cfg-judge-key-mask").textContent = j.api_key_set ? `当前已配置：${j.api_key_masked || "(已设置)"}` : "（未配置）";
    $("#cfg-judge-model").value = j.model || "";
    $("#cfg-judge-temp").value = j.temperature ?? 0.2;
    // 渲染模型单价表
    renderModelPrices(cfg.model_prices || {});
  }).catch((e) => alert("读取配置失败：" + e.message));
}

function renderModelPrices(prices) {
  const container = $("#model-prices-container");
  container.innerHTML = "";
  const models = Object.keys(prices).sort();
  if (models.length === 0) {
    container.innerHTML = '<div class="text-xs text-slate-400 text-center py-2">暂无模型单价配置</div>';
    return;
  }
  models.forEach(model => {
    const p = prices[model] || {};
    const row = document.createElement("div");
    row.className = "grid grid-cols-[1fr_auto_auto_auto] gap-2 items-center border-b pb-2";
    row.innerHTML = `
      <input type="text" class="model-name border border-slate-200 rounded px-2 py-1 text-xs" value="${escapeHtml(model)}" placeholder="模型名" />
      <input type="number" class="model-input-price border border-slate-200 rounded px-2 py-1 text-xs w-24" value="${p.input_price_per_1m || 0}" step="0.01" placeholder="输入价格" />
      <input type="number" class="model-output-price border border-slate-200 rounded px-2 py-1 text-xs w-24" value="${p.output_price_per_1m || 0}" step="0.01" placeholder="输出价格" />
      <button type="button" class="btn-remove-model text-red-500 hover:text-red-700 text-sm px-2">×</button>
    `;
    row.querySelector(".btn-remove-model").addEventListener("click", () => row.remove());
    container.appendChild(row);
  });
}

$("#btn-add-model-price").addEventListener("click", () => {
  const container = $("#model-prices-container");
  // 清空占位提示
  if (container.querySelector(".text-slate-400")) container.innerHTML = "";
  const row = document.createElement("div");
  row.className = "grid grid-cols-[1fr_auto_auto_auto] gap-2 items-center border-b pb-2";
  row.innerHTML = `
    <input type="text" class="model-name border border-slate-200 rounded px-2 py-1 text-xs" placeholder="模型名（如 qwen-plus）" />
    <input type="number" class="model-input-price border border-slate-200 rounded px-2 py-1 text-xs w-24" value="0" step="0.01" placeholder="输入价格" />
    <input type="number" class="model-output-price border border-slate-200 rounded px-2 py-1 text-xs w-24" value="0" step="0.01" placeholder="输出价格" />
    <button type="button" class="btn-remove-model text-red-500 hover:text-red-700 text-sm px-2">×</button>
  `;
  row.querySelector(".btn-remove-model").addEventListener("click", () => row.remove());
  container.appendChild(row);
});

function collectModelPrices() {
  const container = $("#model-prices-container");
  const rows = container.querySelectorAll(".grid");
  const prices = {};
  rows.forEach(row => {
    const name = row.querySelector(".model-name")?.value.trim();
    const inputPrice = parseFloat(row.querySelector(".model-input-price")?.value || 0);
    const outputPrice = parseFloat(row.querySelector(".model-output-price")?.value || 0);
    if (name) {
      prices[name] = {
        input_price_per_1m: inputPrice,
        output_price_per_1m: outputPrice,
      };
    }
  });
  return prices;
}
function closeConfigModal() {
  $("#config-modal").classList.add("hidden"); $("#config-modal").classList.remove("flex");
  closeModal("#config-modal");
}
// 主题切换逻辑已在 common.js 中实现（applyTheme、按钮绑定、跟随系统）
// 这里只绑定配置按钮事件

$("#btn-open-config").addEventListener("click", openConfigModal);
$("#btn-close-config").addEventListener("click", closeConfigModal);
$("#btn-cancel-config").addEventListener("click", closeConfigModal);
$("#btn-save-config").addEventListener("click", async () => {
  const body = {
    generator_llm: {
      base_url: $("#cfg-gen-base").value.trim() || undefined,
      api_key:  $("#cfg-gen-key").value.trim()  || undefined,
      model:    $("#cfg-gen-model").value.trim() || undefined,
      temperature: ($("#cfg-gen-temp").value === "" ? undefined : parseFloat($("#cfg-gen-temp").value)),
    },
    judge_llm: {
      base_url: $("#cfg-judge-base").value.trim() || undefined,
      api_key:  $("#cfg-judge-key").value.trim()  || undefined,
      model:    $("#cfg-judge-model").value.trim() || undefined,
      temperature: ($("#cfg-judge-temp").value === "" ? undefined : parseFloat($("#cfg-judge-temp").value)),
    },
    model_prices: collectModelPrices(),
  };
  const btn = $("#btn-save-config"); const old = btn.textContent;
  btn.disabled = true; btn.textContent = "保存中...";
  try {
    await api("/api/config", { method: "PUT", body });
    btn.textContent = "✓ 已保存";
    setTimeout(closeConfigModal, 600);
  } catch (e) {
    alert("保存失败：" + e.message);
    btn.textContent = old;
  } finally {
    btn.disabled = false;
  }
});
$("#btn-reset-config").addEventListener("click", async () => {
  if (!confirm("确定回退为 .env 默认值？\n（将删除 data/runtime_config.json）")) return;
  try {
    await api("/api/config", { method: "DELETE" });
    openConfigModal();   // 重新拉取展示
  } catch (e) { alert("重置失败：" + e.message); }
});

// ============= 提示词优化 Modal =============
let optimizingAgentId = null;
let lastOptimizeResult = null;
window.openOptimizeModal = async function (agentId) {
  optimizingAgentId = agentId;
  lastOptimizeResult = null;
  $("#opt-direction").value = "";
  $("#opt-constraints").value = "";
  $("#optimize-result").classList.add("hidden");
  $("#optimize-loading").classList.add("hidden");
  $("#opt-prompt").value = "";
  $("#opt-changes").textContent = "";
  // 显示该智能体最近一次任务概要
  let metaText = "基于该智能体最近一次测试任务的失败用例，调用「评审 LLM」生成优化版 system_prompt。";
  try {
    const runs = await api("/api/runs?agent_id=" + agentId);
    if (runs.length) {
      const r = runs[0];
      metaText += `\n最近任务：${r.name || "(未命名)"} · 通过 ${r.passed}/${r.total} · 平均分 ${r.average_score || 0}`;
    } else {
      metaText += `\n⚠️ 该智能体尚无测试任务，将仅基于 system_prompt 做通用优化。`;
    }
  } catch (_) {}
  $("#optimize-meta").textContent = metaText;
  $("#optimize-modal").classList.remove("hidden");
  $("#optimize-modal").classList.add("flex");
  openModal("#optimize-modal");
};
function closeOptimizeModal() {
  $("#optimize-modal").classList.add("hidden");
  $("#optimize-modal").classList.remove("flex");
  closeModal("#optimize-modal");
  optimizingAgentId = null;
}
$("#btn-close-optimize").addEventListener("click", closeOptimizeModal);

$("#btn-run-optimize").addEventListener("click", async () => {
  if (!optimizingAgentId) return;
  const btn = $("#btn-run-optimize"); const old = btn.textContent;
  btn.disabled = true; btn.textContent = "优化中...";
  $("#optimize-loading").classList.remove("hidden");
  $("#optimize-result").classList.add("hidden");
  try {
    const res = await api("/api/agents/" + optimizingAgentId + "/optimize_prompt", {
      method: "POST",
      body: {
        direction: $("#opt-direction").value.trim(),
        constraints: $("#opt-constraints").value.trim(),
      },
    });
    lastOptimizeResult = res;
    $("#opt-prompt").value = res.optimized_prompt || "";
    $("#opt-changes").textContent = res.changes || "(模型未给出改动说明)";
    $("#optimize-result").classList.remove("hidden");
  } catch (e) {
    alert("优化失败：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = old;
    $("#optimize-loading").classList.add("hidden");
  }
});

$("#btn-copy-opt").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("#opt-prompt").value);
    const btn = $("#btn-copy-opt"); const old = btn.textContent;
    btn.textContent = "已复制";
    setTimeout(() => (btn.textContent = old), 1200);
  } catch { alert("复制失败，请手动选择文本"); }
});

$("#btn-apply-opt").addEventListener("click", async () => {
  if (!optimizingAgentId) return;
  const newPrompt = $("#opt-prompt").value.trim();
  if (!newPrompt) return alert("提示词为空");
  if (!confirm("将优化版 system_prompt 应用到该智能体？\n（旧值会被覆盖，建议先复制备份）")) return;
  try {
    const a = await api("/api/agents/" + optimizingAgentId);
    const payload = { ...a, system_prompt: newPrompt };
    await api("/api/agents/" + optimizingAgentId, { method: "PUT", body: payload });
    alert("已应用！可在「智能体」页查看新 system_prompt。");
    closeOptimizeModal();
    loadAgents();
  } catch (e) { alert("应用失败：" + e.message); }
});

})();
