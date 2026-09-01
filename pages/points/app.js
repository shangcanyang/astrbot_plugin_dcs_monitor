// DCS 位号管理页逻辑：通过 AstrBotPluginPage bridge 与后端 Web API 通信
const bridge = window.AstrBotPluginPage;

const state = {
  points: [],
  editingName: null, // 正在编辑的原点位名，null 表示新增模式
};

const els = {
  form: document.getElementById("point-form"),
  formTitle: document.getElementById("form-title"),
  btnSubmit: document.getElementById("btn-submit"),
  btnCancel: document.getElementById("btn-cancel"),
  name: document.getElementById("f-name"),
  desc: document.getElementById("f-desc"),
  low: document.getElementById("f-low"),
  high: document.getElementById("f-high"),
  interval: document.getElementById("f-interval"),
  tbody: document.getElementById("point-tbody"),
  prefix: document.getElementById("prefix-label"),
  monitorStatus: document.getElementById("monitor-status"),
  countLabel: document.getElementById("count-label"),
  toast: document.getElementById("toast"),
};

let toastTimer = null;

function showToast(message, type = "success") {
  els.toast.textContent = message;
  els.toast.className = `toast ${type}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    els.toast.className = "toast hidden";
  }, 3000);
}

function formatValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function renderStatus() {
  els.monitorStatus.textContent = state.monitorRunning
    ? "监控运行中"
    : "监控未运行";
  els.monitorStatus.className = `badge ${state.monitorRunning ? "running" : "stopped"}`;
}

function renderTable() {
  els.countLabel.textContent = `共 ${state.points.length} 个点位`;
  if (!state.points.length) {
    els.tbody.innerHTML =
      '<tr><td colspan="7" class="muted center">暂无点位，请在上方添加</td></tr>';
    return;
  }
  els.tbody.innerHTML = state.points
    .map(
      (p) => `
    <tr>
      <td class="name" title="${escapeHtml(p.point_id)}">${escapeHtml(p.name)}</td>
      <td>${escapeHtml(p.description || "—")}</td>
      <td>${formatValue(p.low_threshold)}</td>
      <td>${formatValue(p.high_threshold)}</td>
      <td>${p.check_interval ?? "—"}</td>
      <td><span class="state-dot ${p.last_alert_state}"></span>${stateLabel(p.last_alert_state)}</td>
      <td>
        <button class="link edit" data-name="${escapeAttr(p.name)}">编辑</button>
        <button class="link delete" data-name="${escapeAttr(p.name)}">删除</button>
      </td>
    </tr>`,
    )
    .join("");
}

function stateLabel(st) {
  return { normal: "正常", low: "低", high: "高" }[st] || st || "—";
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(text) {
  return escapeHtml(text).replaceAll("'", "&#39;");
}

async function loadPoints() {
  const data = await bridge.apiGet("points");
  state.points = data.points || [];
  state.monitorRunning = Boolean(data.running);
  els.prefix.textContent = data.prefix || "";
  renderStatus();
  renderTable();
}

function resetForm() {
  state.editingName = null;
  els.form.reset();
  els.formTitle.textContent = "添加点位";
  els.btnSubmit.textContent = "添加";
  els.btnCancel.classList.add("hidden");
}

function fillForm(point) {
  state.editingName = point.name;
  els.name.value = point.name;
  els.desc.value = point.description || "";
  els.low.value = point.low_threshold ?? "";
  els.high.value = point.high_threshold ?? "";
  els.interval.value = point.check_interval ?? "";
  els.formTitle.textContent = `编辑点位：${point.name}`;
  els.btnSubmit.textContent = "保存修改";
  els.btnCancel.classList.remove("hidden");
  els.name.focus();
}

function collectForm() {
  const interval = els.interval.value.trim();
  return {
    name: els.name.value.trim(),
    description: els.desc.value.trim(),
    low_threshold: els.low.value.trim(),
    high_threshold: els.high.value.trim(),
    check_interval: interval ? Number(interval) : null,
  };
}

els.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const payload = collectForm();
  if (!payload.name) {
    showToast("位号不能为空", "error");
    return;
  }
  try {
    if (state.editingName) {
      await bridge.apiPost("points/update", {
        old_name: state.editingName,
        data: payload,
      });
      showToast(`点位 ${payload.name} 已更新`);
    } else {
      await bridge.apiPost("points", payload);
      showToast(`点位 ${payload.name} 已添加`);
    }
    resetForm();
    await loadPoints();
  } catch (err) {
    showToast(err.message || "操作失败", "error");
  }
});

els.btnCancel.addEventListener("click", resetForm);

els.tbody.addEventListener("click", async (e) => {
  const btn = e.target.closest("button.link");
  if (!btn) return;
  const name = btn.dataset.name;
  if (btn.classList.contains("edit")) {
    const point = state.points.find((p) => p.name === name);
    if (point) fillForm(point);
  } else if (btn.classList.contains("delete")) {
    if (!confirm(`确定删除点位「${name}」吗？`)) return;
    try {
      await bridge.apiPost("points/delete", { name });
      showToast(`点位 ${name} 已删除`);
      if (state.editingName === name) resetForm();
      await loadPoints();
    } catch (err) {
      showToast(err.message || "删除失败", "error");
    }
  }
});

async function init() {
  try {
    await bridge.ready();
    await loadPoints();
  } catch (err) {
    showToast("初始化失败：" + (err.message || err), "error");
  }
}

init();
