(function() {
'use strict';

// ============= Agents =============
let agents = [];
const agentsPager = { page: 1, pageSize: 12 };
let agentsQuery = "";

// ---- 百炼导入进度 ----
let bailianImport = { phase: "idle", message: "", total: 0, current: 0, imported: 0, skipped: 0, errors: 0, agents: [] };
let bailianPollTimer = null;

async function loadAgents() {
  agents = await api("/api/agents");
  renderAgentsPage();
}

// ---- 百炼导入功能 ----
async function openBailianImportModal() {
  // 恢复原始表单内容
  const panel = $("#bailian-import-modal .modal-panel");
  panel.innerHTML = `
    <div class="space-y-3">
      <h3 class="text-lg font-semibold">🌐 从百炼导入智能体</h3>
      <p class="text-sm text-slate-600">
        通过 Selenium 附加到已运行的 Edge 浏览器，自动抓取百炼控制台中的智能体名称、app_id 和 system prompt，
        并批量导入到本平台。
      </p>
      <div class="space-y-3">
        <div>
          <label class="text-xs text-slate-500">Edge 调试端口</label>
          <input id="bailian-debug-port" type="number" value="9222" class="w-full mt-1" placeholder="9222" />
        </div>
        <div>
          <label class="text-xs text-slate-500">最大爬取页数</label>
          <input id="bailian-max-pages" type="number" value="10" class="w-full mt-1" placeholder="10" />
        </div>
        <div>
          <label class="text-xs text-slate-500">百炼 API Key（可选，用于导入后配置）</label>
          <input id="bailian-api-key" type="password" class="w-full mt-1" placeholder="sk-..." />
        </div>
      </div>
      <div id="bailian-progress-bar" class="hidden">
        <div class="text-sm text-slate-600 mb-1" id="bailian-progress-msg">连接中...</div>
        <div class="w-full bg-slate-100 rounded-full h-2 overflow-hidden">
          <div id="bailian-progress-fill" class="h-full bg-primary transition-all duration-300" style="width:0%"></div>
        </div>
        <div class="text-xs text-slate-500 mt-1">
          已抓取 <span id="bailian-current">0</span> /
          新增 <span id="bailian-imported">0</span> /
          跳过 <span id="bailian-skipped">0</span> /
          错误 <span id="bailian-errors">0</span>
        </div>
      </div>
      <div class="flex justify-end gap-2 pt-2">
        <button class="btn btn-ghost" onclick="closeModal('#bailian-import-modal')">取消</button>
        <button id="bailian-start-btn" class="btn btn-primary" onclick="startBailianImport()">开始导入</button>
      </div>
    </div>
  `;
  openModal("#bailian-import-modal");
}

async function startBailianImport() {
  const debugPort = parseInt($("#bailian-debug-port").value) || 9222;
  const maxPages = parseInt($("#bailian-max-pages").value) || 10;
  const apiKey = $("#bailian-api-key").value;

  const btn = $("#bailian-start-btn");
  btn.disabled = true;
  btn.textContent = "启动中...";
  $("#bailian-progress-bar").classList.remove("hidden");

  try {
    await api(`/api/agents/import_bailian?debug_port=${debugPort}&max_pages=${maxPages}&api_key=${encodeURIComponent(apiKey)}`, {
      method: "POST",
    });
    // 开始轮询进度
    pollBailianProgress();
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "开始导入";
    // 恢复原始表单
    openBailianImportModal();
    // 显示错误信息
    const msg = e.message || "未知错误";
    const errDiv = document.createElement("div");
    errDiv.className = "text-sm text-danger bg-red-50 border border-red-200 rounded p-3 mt-2";
    errDiv.textContent = `❌ ${msg}`;
    const panel = $("#bailian-import-modal .modal-panel");
    panel.querySelector(".space-y-3").appendChild(errDiv);
  }
}

function pollBailianProgress() {
  if (bailianPollTimer) clearInterval(bailianPollTimer);
  bailianPollTimer = setInterval(async () => {
    try {
      const prog = await api("/api/agents/import_bailian/progress");
      bailianImport = prog;
      updateBailianProgressUI(prog);
      if (prog.phase === "done" || prog.phase === "error") {
        clearInterval(bailianPollTimer);
        bailianPollTimer = null;
        if (prog.phase === "done") {
          showBailianResult(prog);
        } else {
          closeModal();
          alert(`导入失败: ${prog.message}`);
          await api("/api/agents/import_bailian/reset", { method: "POST" });
        }
      }
    } catch (e) {
      // 忽略轮询错误
    }
  }, 800);
}

function updateBailianProgressUI(prog) {
  const msg = $("#bailian-progress-msg");
  const fill = $("#bailian-progress-fill");
  if (msg) msg.textContent = prog.message || "";
  if (fill && prog.total > 0) {
    fill.style.width = `${Math.min(100, Math.round((prog.current / prog.total) * 100))}%`;
  }
  const cur = $("#bailian-current");
  const imp = $("#bailian-imported");
  const skp = $("#bailian-skipped");
  const err = $("#bailian-errors");
  if (cur) cur.textContent = prog.current;
  if (imp) imp.textContent = prog.imported;
  if (skp) skp.textContent = prog.skipped;
  if (err) err.textContent = prog.errors;
}

function showBailianResult(prog) {
  // 关闭旧内容，显示结果
  closeModal("#bailian-import-modal");

  const agentListHtml = prog.agents.slice(0, 20).map(a =>
    `<div class="text-sm py-1 border-b border-slate-100 last:border-0">
      <span class="font-medium">${escapeHtml(a.name)}</span>
      <span class="text-xs text-slate-500 ml-2">app_id: ${escapeHtml(a.app_id)}</span>
      <span class="text-xs text-slate-400 ml-auto">${a.prompt_length} 字</span>
    </div>`
  ).join("");

  const more = prog.agents.length > 20 ? `<div class="text-xs text-slate-500 mt-1">... 以及 ${prog.agents.length - 20} 个更多</div>` : "";

  // 复用 modal，替换内容为结果
  const panel = $("#bailian-import-modal .modal-panel");
  panel.innerHTML = `
    <div class="space-y-3">
      <h3 class="text-lg font-semibold">导入完成</h3>
      <p class="text-sm text-slate-600">${escapeHtml(prog.message)}</p>
      <div class="max-h-60 overflow-y-auto border rounded p-3 bg-slate-50">${agentListHtml}${more}</div>
      <div class="flex justify-end gap-2 pt-2">
        <button class="btn btn-primary" onclick="closeModal('#bailian-import-modal'); loadAgents();">完成</button>
      </div>
    </div>
  `;
  openModal("#bailian-import-modal");
  // 刷新列表
  loadAgents();
}

async function resetBailianImport() {
  try {
    await api("/api/agents/import_bailian/reset", { method: "POST" });
  } catch (e) {}
}
function _filterAgents(list, q) {
  const s = (q || "").trim().toLowerCase();
  if (!s) return list;
  return list.filter((a) => {
    return (a.name || "").toLowerCase().includes(s)
      || (a.description || "").toLowerCase().includes(s)
      || (a.industry || "").toLowerCase().includes(s)
      || (a.adapter || "").toLowerCase().includes(s);
  });
}
function renderAgentsPage() {
  const el = $("#agent-list");
  const pagerEl = $("#agent-pager");
  const filtered = _filterAgents(agents, agentsQuery);
  if (!agents.length) {
    el.innerHTML = renderEmptyState("暂无智能体", "点击右上角按钮创建第一个智能体", "+ 新建智能体", "#");
    pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
    return;
  }
  if (!filtered.length) {
    el.innerHTML = renderEmptyState("没有匹配结果", `没有找到包含 "${escapeHtml(agentsQuery)}" 的智能体`);
    pagerEl.classList.add("hidden"); pagerEl.innerHTML = "";
    return;
  }
  const info = paginate(filtered, agentsPager);
  el.innerHTML = info.items.map((a) => `
    <div class="card p-4 list-item flex justify-between items-start gap-4">
      <div class="flex-1">
        <div class="flex items-center gap-2">
          <div class="font-semibold text-base">${escapeHtml(a.name)}</div>
          <span class="text-xs px-2 py-0.5 rounded bg-slate-100 text-slate-600">${a.adapter}</span>
          <span class="dim-badge">${escapeHtml(a.industry || "")}</span>
        </div>
        <div class="text-sm text-slate-600 mt-1">${escapeHtml(a.description || "")}</div>
        <details class="mt-2">
          <summary class="text-xs text-slate-500 cursor-pointer">System Prompt 预览</summary>
          <pre class="text-xs text-slate-600 mt-1 bg-slate-50 p-2 rounded max-h-60 overflow-y-auto">${escapeHtml(a.system_prompt || "")}</pre>
        </details>
      </div>
      <div class="flex flex-row gap-2 shrink-0 flex-wrap">
        <button class="btn btn-ghost btn-sm" onclick="editAgent('${escapeHtml(a.id)}')">编辑</button>
        <button class="btn btn-primary btn-sm" onclick="genForAgent('${escapeHtml(a.id)}')">生成用例</button>
        <button class="btn btn-ghost btn-sm" title="基于最近一次测试结果生成优化版 system_prompt" onclick="openOptimizeModal('${escapeHtml(a.id)}')">💡 提示词优化</button>
        <button class="btn btn-danger btn-sm" onclick="delAgent('${escapeHtml(a.id)}')">删除</button>
      </div>
    </div>
  `).join("");
  renderPager(pagerEl, info, (patch) => {
    Object.assign(agentsPager, patch); renderAgentsPage();
  });
}

let editingAgent = null;
// 新建智能体时来自后端 .env 的预填值；编辑现有智能体时不使用，避免覆盖真实 key
let agentDefaults = {};
// 搜索框：输入即时过滤（页码重置到 1）
$("#agent-search").addEventListener("input", (e) => {
  agentsQuery = e.target.value || "";
  agentsPager.page = 1;
  renderAgentsPage();
});
$("#btn-new-agent").addEventListener("click", async () => {
  editingAgent = null;
  $("#agent-modal-title").textContent = "新建智能体";
  $("#f-name").value = "";
  $("#f-description").value = "";
  $("#f-system-prompt").value = ""; $("#f-adapter").value = "bailian";
  // 拉取后端预填的默认 api_key（来自服务端 .env，不写死在前端）
  try { agentDefaults = await api("/api/agent_defaults") || {}; } catch (_) { agentDefaults = {}; }
  // 仅当 adapter=bailian 时注入预填（其他 adapter 不预填）
  renderAdapterConfig("bailian", buildAdapterPresetCfg("bailian")); renderVars({});
  await loadIndustries(); renderIndustrySelect("education");
  $("#agent-modal").classList.remove("hidden"); $("#agent-modal").classList.add("flex");
  openModal("#agent-modal");
});
window.editAgent = async (id) => {
  const a = agents.find((x) => x.id === id);
  if (!a) return;
  editingAgent = a;
  $("#agent-modal-title").textContent = "编辑智能体";
  $("#f-name").value = a.name;
  $("#f-description").value = a.description || ""; $("#f-system-prompt").value = a.system_prompt || "";
  $("#f-adapter").value = a.adapter; renderAdapterConfig(a.adapter, a.config || {}); renderVars(a.variables || {});
  await loadIndustries(); renderIndustrySelect(a.industry || "general");
  $("#agent-modal").classList.remove("hidden"); $("#agent-modal").classList.add("flex");
  openModal("#agent-modal");
};
window.delAgent = async (id) => {
  if (!confirm("确定删除？关联的用例和记录会一起删除。")) return;
  await api("/api/agents/" + id, { method: "DELETE" });
  loadAgents();
};
window.genForAgent = async (id) => {
  // 先确保「测试用例」页第一层数据加载，再进入指定智能体的二级视图
  switchTab("cases");
  // 尝试拉概览（loadCasesTab 内已触发，但可能还没拿到结果，这里兜底）
  if (typeof casesAgentOverview !== "undefined" && !casesAgentOverview.length) {
    try { casesAgentOverview = await api("/api/agents_overview"); } catch {}
  }
  enterCasesForAgent(id);
};

$("#btn-cancel-agent").addEventListener("click", () => {
  $("#agent-modal").classList.add("hidden"); $("#agent-modal").classList.remove("flex");
  closeModal("#agent-modal");
});
// 新建场景下，仅当 adapter=bailian 时注入 .env 提供的默认 api_key；
// 其他 adapter 或编辑模式都返回空对象（不覆盖现有值）。
function buildAdapterPresetCfg(adapter) {
  if (editingAgent) return {};
  if (adapter === "bailian" && agentDefaults && agentDefaults.api_key) {
    return { api_key: agentDefaults.api_key };
  }
  return {};
}
$("#f-adapter").addEventListener("change", (e) => {
  renderAdapterConfig(e.target.value, buildAdapterPresetCfg(e.target.value));
});

function renderAdapterConfig(adapter, cfg) {
  const el = $("#f-config");
  const input = (k, ph, v = "") =>
    `<label class="text-sm block"><span class="text-slate-500">${k}</span>
     <input data-cfg="${k}" value="${escapeHtml(v)}" placeholder="${ph}" class="border rounded w-full px-2 py-1 mt-1" /></label>`;
  if (adapter === "openai") {
    el.innerHTML = input("base_url", "https://api.openai.com/v1", cfg.base_url)
      + input("api_key", "sk-...", cfg.api_key)
      + input("model", "gpt-4o-mini / qwen-plus / deepseek-chat ...", cfg.model);
  } else if (adapter === "bailian") {
    el.innerHTML = input("api_key", "百炼 API Key", cfg.api_key)
      + input("app_id", "百炼 App ID", cfg.app_id)
      + input("endpoint", "https://dashscope.aliyuncs.com", cfg.endpoint || "https://dashscope.aliyuncs.com");
  } else if (adapter === "coze") {
    el.innerHTML = input("api_key", "Coze API Key", cfg.api_key)
      + input("bot_id", "Bot ID", cfg.bot_id)
      + input("endpoint", "https://api.coze.cn 或 https://api.coze.com", cfg.endpoint || "https://api.coze.cn");
  }
}

function renderVars(vars) {
  const ta = $("#f-vars-json"); const err = $("#f-vars-err");
  err.classList.add("hidden"); err.textContent = "";
  const v = vars || {};
  ta.value = Object.keys(v).length ? JSON.stringify(v, null, 2) : "";
}
function parseVars() {
  const ta = $("#f-vars-json"); const err = $("#f-vars-err");
  const raw = ta.value.trim();
  if (!raw) { err.classList.add("hidden"); return {}; }
  try {
    const obj = JSON.parse(raw);
    if (obj === null || typeof obj !== "object" || Array.isArray(obj)) {
      throw new Error("顶层必须是 JSON 对象");
    }
    err.classList.add("hidden"); return obj;
  } catch (e) {
    err.textContent = "JSON 解析失败：" + e.message; err.classList.remove("hidden"); throw e;
  }
}
$("#btn-format-vars").addEventListener("click", () => {
  try {
    const obj = parseVars();
    $("#f-vars-json").value = Object.keys(obj).length ? JSON.stringify(obj, null, 2) : "";
  } catch {}
});
// 一键填充：写入一份用于本地测试的虚拟业务参数样例（百炼智能体应用结构）
// 数据为脱敏后的虚拟值，用户可直接编辑或保存
const SAMPLE_VARS = {
  stream: true,
  background: false,
  n: 1,
  session_id: "6c46141021194b649c5f39278f18bad8",
  biz_params: {
    user_prompt_params: {
      course_info: {
        course_custom: "这是课程的的自定义参数",
        course_description: "这是这个课程的描述",
        course_name: "从三皇五帝到春秋战国",
      },
      level_config: {
        lecture_material: "本篇内容主要围绕\\u4e0d\\u597d\\u9b54\\u738b\\u76f4\\u64ad\\u95f4\\u5c55\\u5f00\\uff0c\\u901a\\u8fc7\\u5927\\u5496\\u7238\\u7238\\u548c\\u5c0f\\u5496\\u513f\\u5b50\\u4e58\\u5750\\u65f6\\u5149\\u673a\\u7a7f\\u8d8a\\u65f6\\u7a7a\\u7684\\u89c6\\u89d2\\uff0c\\u751f\\u52a8\\u52fe\\u52d2\\u4e86\\u4e2d\\u56fd\\u8fdc\\u53e4\\u795e\\u8bdd\\u4e0e\\u4f20\\u8bf4\\u65f6\\u4ee3\\u7684\\u5f00\\u7aef\\u3002",
        level_custom: "关卡自定义参数",
        level_index: 1,
        level_name: "成语猜猜（百炼）",
        pack_name: "第1节_上_三皇始祖伏羲",
      },
      user_info: {
        age: -1,
        city: "",
        gender: "男",
        grade: "",
        name: "cUcTzxWZkK",
        user_id: 4117711,
      },
    },
  },
  enable_system_time: true,
  scope: "publish",
};
$("#btn-fill-vars").addEventListener("click", () => {
  const ta = $("#f-vars-json");
  const err = $("#f-vars-err");
  const cur = ta.value.trim();
  if (cur && !confirm("当前已有业务参数，是否覆盖为示例数据？")) return;
  ta.value = JSON.stringify(SAMPLE_VARS, null, 2);
  err.classList.add("hidden"); err.textContent = "";
});

$("#btn-save-agent").addEventListener("click", async () => {
  let variables;
  try { variables = parseVars(); } catch { return; }
  const payload = {
    name: $("#f-name").value.trim(),
    industry: $("#f-industry").value.trim() || "通用",
    description: $("#f-description").value.trim(),
    system_prompt: $("#f-system-prompt").value.trim(),
    adapter: $("#f-adapter").value,
    config: {},
    variables,
  };
  if (!payload.name || !payload.system_prompt) { alert("名称和 System Prompt 不能为空"); return; }
  $$("#f-config [data-cfg]").forEach((i) => { if (i.value) payload.config[i.dataset.cfg] = i.value.trim(); });
  if (editingAgent) {
    await api("/api/agents/" + editingAgent.id, { method: "PUT", body: payload });
  } else {
    await api("/api/agents", { method: "POST", body: payload });
  }
  $("#agent-modal").classList.add("hidden"); $("#agent-modal").classList.remove("flex");
  closeModal("#agent-modal");
  loadAgents();
});

// 暴露全局
window.agents = agents;
window.agentsPager = agentsPager;
window.editingAgent = editingAgent;
window.agentDefaults = agentDefaults;
window.loadAgents = loadAgents;
window.openBailianImportModal = openBailianImportModal;
window.startBailianImport = startBailianImport;
window.resetBailianImport = resetBailianImport;

})();
