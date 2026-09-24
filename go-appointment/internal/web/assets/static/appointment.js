let contacts = [];
function message(text, kind = "") { const el = App.id("appointmentMessage"); el.textContent = text || ""; el.className = `form-message ${kind}`; }
function loading(button, active, label) { button.disabled = active; button.textContent = active ? "处理中…" : label; }
function values(value) { return Array.isArray(value) ? value : []; }
function optionValue(item) { return typeof item === "object" && item !== null ? String(item.value ?? item.id ?? item.code ?? item.label ?? "") : String(item ?? ""); }
function optionLabel(item) { return typeof item === "object" && item !== null ? String(item.label ?? item.name ?? item.text ?? item.value ?? "") : String(item ?? ""); }
function fillSelect(id, options) {
  const select = App.id(id); select.innerHTML = '<option value="">请选择</option>';
  values(options).forEach((item) => { const value = optionValue(item); if (!value) return; const option = document.createElement("option"); option.value = value; option.textContent = optionLabel(item) || value; option.disabled = Boolean(item?.disabled); select.appendChild(option); });
}
function minutes(value) { const match = String(value || "").match(/^(\d{1,2}):(\d{2})/); return match ? Number(match[1]) * 60 + Number(match[2]) : NaN; }
function fillTimes(config) {
  const parsedStart = minutes(config?.ksqrxkssj), parsedEnd = minutes(config?.ksqrxjssj);
  const start = Number.isFinite(parsedStart) ? parsedStart : 0, end = Number.isFinite(parsedEnd) ? parsedEnd : 23 * 60 + 30;
  const options = []; for (let minute = start; minute <= end; minute += 30) { const h = String(Math.floor(minute / 60)).padStart(2, "0"); const text = `${h}:${String(minute % 60).padStart(2, "0")}`; options.push(text); } fillSelect("yysj", options);
}
function payload() { return { lxdh: App.id("lxdh").value.trim(), yyrq: App.id("yyrq").value, yysj: App.id("yysj").value, yyxm: App.id("yyxm").value, yysy: App.id("yysy").value.trim(), byyrxm: App.id("byyrxm").value.trim(), byyrzjh: App.id("byyrzjh").value.trim(), byyrlxdh: App.id("byyrlxdh").value.trim() }; }
function validate() {
  if (!App.id("appointmentForm").reportValidity()) return false;
  if (!/^[a-zA-Z0-9]{6,18}$/.test(App.id("byyrzjh").value.trim())) { message("被预约人证件号应为 6—18 位字母或数字", "error"); return false; }
  if (!/^\d{11}$/.test(App.id("byyrlxdh").value.trim())) { message("被预约人联系电话应为 11 位手机号", "error"); return false; }
  return true;
}
async function saveDraft(manageButton = true) {
  if (!validate()) return "";
  const button = App.id("saveDraftButton"); if (manageButton) loading(button, true, "保存为草稿");
  try {
    const data = await App.api("/api/appointment/save", { method: "POST", body: JSON.stringify(payload()) });
    const row = data.row; const remoteId = typeof row === "object" ? (row?.sqxxid || row?.id || "") : String(row || "");
    if (!remoteId) throw new Error("保存成功，但学校系统没有返回申请编号");
    message(`草稿 ${remoteId} 已保存，可到任务队列安排定时提交`, "success"); return String(remoteId);
  } catch (error) { message(error.message, "error"); return ""; }
  finally { if (manageButton) loading(button, false, "保存为草稿"); }
}
async function submitNow() {
  if (!validate()) return; const button = App.id("submitNowButton"); loading(button, true, "保存并立即提交");
  try {
    const id = await saveDraft(false); if (!id) return;
    const data = await App.api("/api/appointment/submit-automatic", { method: "POST", body: JSON.stringify({ sqxxid: id }) });
    const suffix = data.email_status === "sent" ? "，成功邮件已发送" : (data.email_error ? `，但邮件未发送：${data.email_error}` : "");
    message(`预约 ${id} 已提交${suffix}`, data.email_status === "failed" ? "error" : "success");
  } catch (error) { message(error.message, "error"); }
  finally { loading(button, false, "保存并立即提交"); }
}
function renderContacts() {
  const target = App.id("recentContactList");
  if (!contacts.length) { target.innerHTML = '<div class="machine-empty">没有近期预约人</div>'; return; }
  target.innerHTML = contacts.map((item, index) => `<button class="contact-item" type="button" data-index="${index}"><b>${App.escape(item.byyrxm)}</b><span>${App.escape(item.byyrzjh)}</span><span>${App.escape(item.byyrlxdh)}</span></button>`).join("");
  target.querySelectorAll("[data-index]").forEach((button) => button.addEventListener("click", () => { const item = contacts[Number(button.dataset.index)]; App.id("byyrxm").value = item.byyrxm || ""; App.id("byyrzjh").value = item.byyrzjh || ""; App.id("byyrlxdh").value = item.byyrlxdh || ""; App.id("recentContacts").hidden = true; }));
}
async function loadConfig() {
  try {
    const data = await App.api("/api/appointment/config"); const row = data.status?.row || {};
    contacts = App.rows(data.contacts); fillSelect("yyrq", row.ksqrq); fillSelect("yyxm", row.ksqxm); fillTimes(row.glsz || {});
    const info = row.jbxx || {}; App.id("studentInfo").innerHTML = [["姓名", info.xm], ["学号", info.xh], ["学院", info.xsmc], ["学生类别", info.xslb]].filter(([, value]) => value).map(([key, value]) => `<span><small>${App.escape(key)}</small><b>${App.escape(value)}</b></span>`).join("");
    if (info.lxdh) App.id("lxdh").value = info.lxdh; App.id("appointmentHint").textContent = row.yysjTips || "请按学校当前允许的日期、时间和校门填写。"; message("学校预约配置已加载", "success");
  } catch (error) { App.id("appointmentHint").textContent = error.message; message(error.message, "error"); }
}
App.id("appointmentForm").addEventListener("submit", (event) => { event.preventDefault(); saveDraft(); });
App.id("submitNowButton").addEventListener("click", submitNow);
App.id("recentContactsButton").addEventListener("click", () => { renderContacts(); App.id("recentContacts").hidden = false; });
App.id("closeRecentContacts").addEventListener("click", () => { App.id("recentContacts").hidden = true; });
App.id("toggleAutoPickButton").addEventListener("click", () => { App.id("autoPick").hidden = !App.id("autoPick").hidden; });
App.id("autoPick").addEventListener("change", () => { const parts = App.id("autoPick").value.trim().split(/[\s,，;；]+/).filter(Boolean); if (parts.length < 3) return message("请按“姓名 证件号 手机号”输入", "error"); [App.id("byyrxm").value, App.id("byyrzjh").value, App.id("byyrlxdh").value] = parts; });
App.id("yyrq").addEventListener("change", async () => { if (!App.id("yyrq").value) return; try { await App.api(`/api/appointment/check-date?date=${encodeURIComponent(App.id("yyrq").value)}`); message("预约日期可用", "success"); } catch (error) { message(error.message, "error"); } });
loadConfig();
