const $ = (id) => document.getElementById(id);
let pendingId = "";
let currentPage = 0;
let hasNextPage = false;
let appointmentDraftId = "";
let appointmentContacts = [];
let appointmentConfig = null;

function setMessage(message, kind = "") {
  const el = $("formMessage");
  el.textContent = message || "";
  el.className = `form-message ${kind}`;
}

function setLoading(button, loading, label) {
  button.disabled = loading;
  button.textContent = loading ? "处理中…" : label;
}

function showVerification(summary) {
  $("verificationFields").hidden = false;
  $("otpField").hidden = summary.mode !== "OTP";
  $("smsField").hidden = summary.mode !== "SMS";
  $("captchaField").hidden = !summary.captcha;
  const parts = [];
  if (summary.second_factor) parts.push(summary.mode === "OTP" ? "需要手机令牌" : "需要短信验证码");
  else parts.push("当前未要求二次验证");
  if (summary.captcha) parts.push("需要图形验证码");
  if (summary.trusted_device_option) parts.push("可记住本设备");
  $("verificationState").textContent = parts.join(" · ");
}

async function checkVerification() {
  const username = $("username").value.trim();
  if (!username) {
    setMessage("请先输入用户名", "error");
    return;
  }
  const button = $("checkVerification");
  setLoading(button, true, "检查");
  try {
    const response = await fetch("/api/verification", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "检查失败");
    pendingId = data.pending_id;
    showVerification(data);
    setMessage("验证状态已更新", "success");
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setLoading(button, false, "检查");
  }
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[char]));
}

const labels = {
  shbz: "审核状态", shyj: "审核意见", byyrxm: "被预约人", byyrymzm: "被预约人名族",
  zjlx: "证件类型", zjh: "证件号", byyrzjh: "被预约人证件号", byyrxck: "被预约人出校口",
  byyrhsjc: "被预约人核酸检测", byyrjkb: "被预约人健康宝", byyrlxdh: "被预约人电话",
  lxdh: "联系人电话", xm: "姓名", xh: "学号", xsmc: "学院", xsdm: "学院代码",
  xslb: "学生类别", tjr: "提交人", tjrq: "提交时间", sqrq: "申请时间", sqrqf: "申请日期",
  sqxxid: "申请编号", yyxm: "预约校门", yyrq: "预约日期", yysj: "预约时间", yysy: "预约事由",
  sfqryyxz: "确认预约须知", tjbz: "提交标记", pagehelper_row_id: "记录序号",
};

function renderRecord(record) {
  const title = `${record.yyrq || ""} ${record.yysj || ""}`.trim() || "未填写预约时间";
  const details = Object.entries(record).map(([key, value]) =>
    `<div><span>${escapeHtml(labels[key] || key)}</span><b>${escapeHtml(value)}</b></div>`
  ).join("");
  const canSubmit = record.tjbz !== "y" && record.sqxxid;
  return `<article class="record">
    <div class="record-main">
      <div><p class="record-date">${escapeHtml(title)}</p><h3>${escapeHtml(record.byyrxm || "未填写被预约人")}</h3></div>
      <span class="badge ${record.shbz === "审核通过" ? "approved" : "pending"}">${escapeHtml(record.shbz || "未知状态")}</span>
    </div>
    <div class="record-meta"><span>${escapeHtml(record.yyxm || "未填写校门")}</span><span>${escapeHtml(record.yysy || "未填写事由")}</span><span>${escapeHtml(record.sqxxid || "")}</span></div>
    <details><summary>查看完整字段</summary><div class="detail-grid">${details}</div></details>
    ${canSubmit ? `<button class="button button-secondary record-submit" type="button" data-sqxxid="${escapeHtml(record.sqxxid)}">继续填写并提交</button>` : ""}
  </article>`;
}

async function loadAppointmentDetail(sqxxid) {
  const response = await fetch(`/api/appointment/detail?sqxxid=${encodeURIComponent(sqxxid)}`);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "预约申请读取失败");
  const row = data.row || {};
  appointmentDraftId = sqxxid;
  ["lxdh", "yyrq", "yysj", "yyxm", "yysy", "byyrxm", "byyrzjh", "byyrlxdh"].forEach((key) => {
    if ($(key)) $(key).value = row[key] || "";
  });
  $("appointmentState").textContent = `待提交：${sqxxid}`;
  $("appointmentCard").hidden = false;
  $("appointmentCard").scrollIntoView({ behavior: "smooth", block: "start" });
  appointmentMessage("已载入未提交申请；如需修改请先保存，再提交", "success");
}

async function submitHistoryDraft(sqxxid) {
  try {
    await loadAppointmentDetail(sqxxid);
    const mode = await requestAppointmentVerification();
    if (mode === "byz") await finalSubmitAppointment("");
  } catch (error) {
    appointmentMessage(error.message, "error");
  }
}

function bindRecordActions() {
  document.querySelectorAll(".record-submit").forEach((button) => {
    button.addEventListener("click", () => submitHistoryDraft(button.dataset.sqxxid));
  });
}

async function loadHistory(page) {
  const button = $("loadMoreButton");
  button.disabled = true;
  button.textContent = page === 1 ? "读取中…" : "加载中…";
  try {
    const response = await fetch(`/api/history?page=${page}&pageSize=10`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "读取失败");
    const row = data.row || {};
    const list = row.list || [];
    if (page === 1) $("records").innerHTML = "";
    if (!list.length && page === 1) $("records").innerHTML = '<div class="empty">暂时没有历史预约记录</div>';
    $("records").insertAdjacentHTML("beforeend", list.map(renderRecord).join(""));
    bindRecordActions();
    currentPage = page;
    hasNextPage = Boolean(row.hasNextPage);
    $("summaryRow").textContent = `第 ${page} 页 · 本页 ${list.length} 条`;
    $("pageStatus").textContent = row.isLastPage ? "已加载全部记录" : `当前第 ${page} 页`;
    button.hidden = !hasNextPage;
  } catch (error) {
    $("pageStatus").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "加载更多";
  }
}

function appointmentMessage(message, kind = "") {
  const el = $("appointmentMessage");
  el.textContent = message || "";
  el.className = `form-message ${kind}`;
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
    const item = document.createElement("option");
    item.value = value;
    item.textContent = optionLabel(option) || value;
    item.disabled = Boolean(option && typeof option === "object" && option.disabled);
    select.appendChild(item);
  });
}

function timeToMinutes(value) {
  const match = String(value || "").match(/^(\d{1,2}):(\d{2})/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : NaN;
}

function fillTimeSelect(config) {
  const start = timeToMinutes(config?.ksqrxkssj) || 0;
  const end = timeToMinutes(config?.ksqrxjssj) || 24 * 60;
  const options = [];
  for (let minute = start; minute <= end; minute += 30) {
    const hour = String(Math.floor(minute / 60)).padStart(2, "0");
    const value = `${hour}:${String(minute % 60).padStart(2, "0")}`;
    options.push({ value, label: value });
  }
  fillSelect("yysj", options);
}

function renderStudentInfo(info) {
  const entries = [
    ["姓名", info?.xm], ["学号", info?.xh], ["学院", info?.xsmc], ["学生类别", info?.xslb],
  ].filter(([, value]) => value);
  $("studentInfo").innerHTML = entries.map(([key, value]) =>
    `<span><small>${escapeHtml(key)}</small><b>${escapeHtml(value)}</b></span>`
  ).join("");
}

function renderRecentContacts() {
  const list = $("recentContactList");
  if (!appointmentContacts.length) {
    list.innerHTML = '<div class="empty compact-empty">没有可用的近期预约人</div>';
    return;
  }
  list.innerHTML = appointmentContacts.map((contact, index) => `
    <button class="contact-item" type="button" data-contact-index="${index}">
      <b>${escapeHtml(contact.byyrxm || "")}</b>
      <span>${escapeHtml(contact.byyrzjh || "")}</span>
      <span>${escapeHtml(contact.byyrlxdh || "")}</span>
    </button>
  `).join("");
  list.querySelectorAll("[data-contact-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const contact = appointmentContacts[Number(button.dataset.contactIndex)];
      $("byyrxm").value = contact.byyrxm || "";
      $("byyrzjh").value = contact.byyrzjh || "";
      $("byyrlxdh").value = contact.byyrlxdh || "";
      $("recentContacts").hidden = true;
      appointmentMessage("已填入近期预约人信息", "success");
    });
  });
}

function parseAutoPick() {
  const parts = $("autoPick").value.trim().split(/[\s,，;；]+/).filter(Boolean);
  if (parts.length < 3) {
    appointmentMessage("请按“姓名 证件号 手机号”输入", "error");
    return;
  }
  $("byyrxm").value = parts[0];
  $("byyrzjh").value = parts[1];
  $("byyrlxdh").value = parts[2];
  appointmentMessage("已自动提取被预约人信息", "success");
}

async function loadAppointmentConfig() {
  try {
    const response = await fetch("/api/appointment/config");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约配置读取失败");
    const row = data.status?.row || {};
    appointmentConfig = row;
    appointmentContacts = Array.isArray(data.contacts?.row)
      ? data.contacts.row
      : (Array.isArray(data.contacts?.data) ? data.contacts.data : []);
    fillSelect("yyrq", row.ksqrq || []);
    fillSelect("yyxm", row.ksqxm || []);
    fillTimeSelect(row.glsz || {});
    renderStudentInfo(row.jbxx || {});
    if (row.jbxx?.lxdh) $("lxdh").value = row.jbxx.lxdh;
    $("appointmentHint").textContent = row.yysjTips || "请按学校允许的日期、时间和校门填写。";
    $("appointmentCard").hidden = false;
    appointmentMessage("预约配置已加载，可以填写申请", "success");
  } catch (error) {
    $("appointmentCard").hidden = false;
    appointmentMessage(error.message, "error");
  }
}

function appointmentPayload() {
  return {
    sqxxid: appointmentDraftId,
    lxdh: $("lxdh").value.trim(),
    yyrq: $("yyrq").value,
    yysj: $("yysj").value,
    yyxm: $("yyxm").value,
    yysy: $("yysy").value.trim(),
    byyrxm: $("byyrxm").value.trim(),
    byyrzjh: $("byyrzjh").value.trim(),
    byyrlxdh: $("byyrlxdh").value.trim(),
  };
}

function validateAppointment() {
  const payload = appointmentPayload();
  const required = [
    ["lxdh", "请填写预约人联系电话"], ["yyrq", "请选择预约日期"],
    ["yysj", "请选择预约时间"], ["yyxm", "请选择预约校门"],
    ["byyrxm", "请填写被预约人姓名"], ["byyrzjh", "请填写被预约人证件号"],
    ["byyrlxdh", "请填写被预约人联系电话"], ["yysy", "请填写预约事由"],
  ];
  for (const [key, message] of required) {
    if (!payload[key]) {
      appointmentMessage(message, "error");
      return false;
    }
  }
  if (!/^[a-zA-Z0-9]{6,18}$/.test(payload.byyrzjh)) {
    appointmentMessage("被预约人证件号应为 6—18 位字母或数字", "error");
    return false;
  }
  if (!/^\d{11}$/.test(payload.byyrlxdh)) {
    appointmentMessage("被预约人联系电话应为 11 位手机号", "error");
    return false;
  }
  return true;
}

async function saveAppointment() {
  if (!validateAppointment()) return false;
  const button = $("saveAppointmentButton");
  setLoading(button, true, "保存草稿");
  try {
    const response = await fetch("/api/appointment/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(appointmentPayload()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "预约保存失败");
    const row = data.row;
    appointmentDraftId = typeof row === "object" ? (row.sqxxid || row.id || "") : String(row || appointmentDraftId);
    if (!appointmentDraftId) throw new Error("保存成功但没有返回申请编号");
    $("appointmentState").textContent = `已保存：${appointmentDraftId}`;
    appointmentMessage("申请草稿已保存；提交后才会进入审核", "success");
    return true;
  } catch (error) {
    appointmentMessage(error.message, "error");
    return false;
  } finally {
    setLoading(button, false, "保存草稿");
  }
}

function showCodePrompt(type, message) {
  const descriptions = {
    opt: "请输入手机令牌",
    sms: "请输入门户绑定手机收到的短信验证码",
    wx: "请输入北京大学信息门户公众号发送的验证码",
  };
  $("appointmentCodeLabel").firstChild.textContent = `${descriptions[type] || "请输入验证码"} `;
  $("appointmentCode").value = "";
  $("appointmentCodeBox").hidden = false;
  appointmentMessage(message || descriptions[type] || "请输入验证码后确认提交");
  $("appointmentCode").focus();
}

async function requestAppointmentVerification() {
  if (!appointmentDraftId) {
    appointmentMessage("请先保存申请", "error");
    return;
  }
  const response = await fetch("/api/appointment/send-code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sqxxid: appointmentDraftId }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "发送预约验证码失败");
  const row = data.row || {};
  if (row.type === "byz") return "byz";
  if (!row.flag) throw new Error(row.errmsg || "验证码发送失败");
  showCodePrompt(row.type, row.errmsg || "");
  return "code";
}

async function finalSubmitAppointment(code = "") {
  const response = await fetch("/api/appointment/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sqxxid: appointmentDraftId, code }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || data.msg || "预约提交失败");
  if (data.success === false && data.code !== 1) throw new Error(data.msg || "预约提交失败");
  $("appointmentCodeBox").hidden = true;
  $("appointmentState").textContent = "已提交，等待审核";
  appointmentMessage("预约提交成功", "success");
  await loadHistory(1);
}

async function saveAndSubmitAppointment() {
  const button = $("submitAppointmentButton");
  setLoading(button, true, "保存并提交");
  try {
    if (!(await saveAppointment())) return;
    const mode = await requestAppointmentVerification();
    if (mode === "byz") await finalSubmitAppointment("");
  } catch (error) {
    appointmentMessage(error.message, "error");
  } finally {
    setLoading(button, false, "保存并提交");
  }
}

$("checkVerification").addEventListener("click", checkVerification);
$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("loginButton");
  setLoading(button, true, "登录并读取记录");
  setMessage("正在登录并建立业务会话…");
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("username").value.trim(),
        password: $("password").value,
        pending_id: pendingId,
        otp_code: $("otpCode").value,
        sms_code: $("smsCode").value,
        rand_code: $("randCode").value,
        trust_device: $("trustDevice").checked,
      }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "登录失败");
    $("loginCard").hidden = true;
    $("recordsCard").hidden = false;
    $("statusPill").innerHTML = "<span class='online'></span>已登录";
    await Promise.all([loadHistory(1), loadAppointmentConfig()]);
  } catch (error) {
    setMessage(error.message, "error");
  } finally {
    setLoading(button, false, "登录并读取记录");
  }
});

$("loadMoreButton").addEventListener("click", () => {
  if (hasNextPage) loadHistory(currentPage + 1);
});

$("saveAppointmentButton").addEventListener("click", saveAppointment);
$("submitAppointmentButton").addEventListener("click", saveAndSubmitAppointment);
$("confirmSubmitButton").addEventListener("click", async () => {
  const code = $("appointmentCode").value.trim();
  if (!code) {
    appointmentMessage("请输入验证码或手机令牌", "error");
    return;
  }
  const button = $("confirmSubmitButton");
  setLoading(button, true, "确认提交");
  try {
    await finalSubmitAppointment(code);
  } catch (error) {
    appointmentMessage(error.message, "error");
  } finally {
    setLoading(button, false, "确认提交");
  }
});

$("recentContactsButton").addEventListener("click", () => {
  renderRecentContacts();
  $("recentContacts").hidden = false;
});
$("closeRecentContacts").addEventListener("click", () => { $("recentContacts").hidden = true; });
$("toggleAutoPickButton").addEventListener("click", () => {
  const input = $("autoPick");
  input.hidden = !input.hidden;
  if (!input.hidden) input.focus();
});
$("autoPick").addEventListener("change", parseAutoPick);
$("yyrq").addEventListener("change", async () => {
  const date = $("yyrq").value;
  if (!date) return;
  try {
    const response = await fetch(`/api/appointment/check-date?date=${encodeURIComponent(date)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || data.msg || "预约日期不可用");
    appointmentMessage("预约日期可用", "success");
  } catch (error) {
    appointmentMessage(error.message, "error");
  }
});
$("refreshAppointmentButton").addEventListener("click", loadAppointmentConfig);

$("logoutButton").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  window.location.reload();
});
