const $ = (id) => document.getElementById(id);
let appointmentContacts = [];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

function message(text, kind = "") {
  $("appointmentMessage").textContent = text || "";
  $("appointmentMessage").className = `form-message ${kind}`;
}

function setLoading(button, loading, label) {
  button.disabled = loading;
  button.textContent = loading ? "处理中…" : label;
}

function optionValue(option) {
  if (option == null) return "";
  if (typeof option !== "object") return String(option);
  return String(option.value ?? option.id ?? option.code ?? option.label ?? "");
}

function optionLabel(option) {
  if (option == null) return "";
  if (typeof option !== "object") return String(option);
  return String(option.label ?? option.name ?? option.text ?? option.value ?? "");
}

function fillSelect(id, options) {
  const select = $(id);
  select.innerHTML = '<option value="">请选择</option>';
  (options || []).forEach((option) => {
    const value = optionValue(option);
    if (!value) return;
    const element = document.createElement("option");
    element.value = value;
    element.textContent = optionLabel(option) || value;
    element.disabled = Boolean(option && typeof option === "object" && option.disabled);
    select.appendChild(element);
  });
}

function timeToMinutes(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : NaN;
}

function fillTimeSelect(config) {
  const parsedStart = timeToMinutes(config?.ksqrxkssj);
  const parsedEnd = timeToMinutes(config?.ksqrxjssj);
  const start = Number.isFinite(parsedStart) ? parsedStart : 0;
  const end = Number.isFinite(parsedEnd) ? parsedEnd : 24 * 60 - 30;
  const options = [];
  for (let minute = start; minute <= end; minute += 30) {
    const hour = String(Math.floor(minute / 60)).padStart(2, "0");
    const value = `${hour}:${String(minute % 60).padStart(2, "0")}`;
    options.push({ value, label: value });
  }
  fillSelect("yysj", options);
}

function renderStudentInfo(info) {
  const entries = [["姓名", info?.xm], ["学号", info?.xh], ["学院", info?.xsmc], ["学生类别", info?.xslb]].filter(([, value]) => value);
  $("studentInfo").innerHTML = entries.map(([key, value]) => `<span><small>${escapeHtml(key)}</small><b>${escapeHtml(value)}</b></span>`).join("");
}

function payload() {
  return {
    lxdh: $("lxdh").value.trim(), yyrq: $("yyrq").value, yysj: $("yysj").value,
    yyxm: $("yyxm").value, yysy: $("yysy").value.trim(), byyrxm: $("byyrxm").value.trim(),
    byyrzjh: $("byyrzjh").value.trim(), byyrlxdh: $("byyrlxdh").value.trim(),
  };
}

function validate() {
  if (!$("appointmentForm").reportValidity()) return false;
  if (!/^[a-zA-Z0-9]{6,18}$/.test($("byyrzjh").value.trim())) {
    message("被预约人证件号应为 6—18 位字母或数字", "error"); return false;
  }
  if (!/^\d{11}$/.test($("byyrlxdh").value.trim())) {
    message("被预约人联系电话应为 11 位手机号", "error"); return false;
  }
  return true;
}

async function saveDraft() {
  if (!validate()) return;
  const button = $("saveDraftButton");
  const label = "保存为草稿";
  setLoading(button, true, label);
  try {
    const response = await fetch("/api/appointment/save", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约草稿保存失败");
    const row = data.row;
    const remoteId = typeof row === "object" ? (row.sqxxid || row.id || "") : String(row || "");
    if (!remoteId) throw new Error("保存成功，但学校系统没有返回申请编号");
    message(`草稿 ${remoteId} 已保存，可到任务队列选择并定时提交`, "success");
    return remoteId;
  } catch (error) {
    message(error.message, "error");
    return "";
  } finally {
    setLoading(button, false, label);
  }
}

async function saveAndSubmit() {
  if (!validate()) return;
  const button = $("submitNowButton");
  setLoading(button, true, "保存并立即提交");
  try {
    const remoteId = await saveDraft();
    if (!remoteId) return;
    const response = await fetch("/api/appointment/submit-automatic", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sqxxid: remoteId }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约提交失败");
    const mail = data.email_status === "sent" ? "，成功邮件已发送" : (data.email_error ? `，但邮件失败：${data.email_error}` : "");
    message(`预约 ${remoteId} 已提交${mail}`, data.email_status === "failed" ? "error" : "success");
  } catch (error) {
    message(error.message, "error");
  } finally {
    setLoading(button, false, "保存并立即提交");
  }
}

async function loadConfig() {
  try {
    const response = await fetch("/api/appointment/config", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约配置读取失败");
    const row = data.status?.row || {};
    appointmentContacts = Array.isArray(data.contacts?.row) ? data.contacts.row : (Array.isArray(data.contacts?.data) ? data.contacts.data : []);
    fillSelect("yyrq", row.ksqrq || []);
    fillSelect("yyxm", row.ksqxm || []);
    fillTimeSelect(row.glsz || {});
    renderStudentInfo(row.jbxx || {});
    if (row.jbxx?.lxdh) $("lxdh").value = row.jbxx.lxdh;
    $("appointmentHint").textContent = row.yysjTips || "请按学校当前允许的日期、时间和校门填写。";
    message("学校预约配置已加载", "success");
  } catch (error) {
    $("appointmentHint").textContent = error.message;
    message(error.message, "error");
  }
}

function renderContacts() {
  $("recentContactList").innerHTML = appointmentContacts.length ? appointmentContacts.map((contact, index) => `
    <button class="contact-item" type="button" data-index="${index}"><b>${escapeHtml(contact.byyrxm || "")}</b><span>${escapeHtml(contact.byyrzjh || "")}</span><span>${escapeHtml(contact.byyrlxdh || "")}</span></button>
  `).join("") : '<div class="machine-empty">没有近期预约人</div>';
  document.querySelectorAll("[data-index]").forEach((button) => button.addEventListener("click", () => {
    const contact = appointmentContacts[Number(button.dataset.index)];
    $("byyrxm").value = contact.byyrxm || ""; $("byyrzjh").value = contact.byyrzjh || ""; $("byyrlxdh").value = contact.byyrlxdh || "";
    $("recentContacts").hidden = true;
  }));
}

$("appointmentForm").addEventListener("submit", (event) => { event.preventDefault(); saveDraft(); });
$("submitNowButton").addEventListener("click", saveAndSubmit);
$("recentContactsButton").addEventListener("click", () => { renderContacts(); $("recentContacts").hidden = false; });
$("closeRecentContacts").addEventListener("click", () => { $("recentContacts").hidden = true; });
$("toggleAutoPickButton").addEventListener("click", () => { $("autoPick").hidden = !$("autoPick").hidden; if (!$("autoPick").hidden) $("autoPick").focus(); });
$("autoPick").addEventListener("change", () => {
  const parts = $("autoPick").value.trim().split(/[\s,，;；]+/).filter(Boolean);
  if (parts.length < 3) return message("请按“姓名 证件号 手机号”输入", "error");
  $("byyrxm").value = parts[0]; $("byyrzjh").value = parts[1]; $("byyrlxdh").value = parts[2];
});
$("yyrq").addEventListener("change", async () => {
  if (!$("yyrq").value) return;
  try {
    const response = await fetch(`/api/appointment/check-date?date=${encodeURIComponent($("yyrq").value)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.msg || "预约日期不可用");
    message("预约日期可用", "success");
  } catch (error) { message(error.message, "error"); }
});
loadConfig();
