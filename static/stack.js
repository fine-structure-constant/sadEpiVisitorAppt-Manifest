const $ = (id) => document.getElementById(id);
const statusLabels = { pending: "等待执行", retrying: "等待重试", running: "正在执行", completed: "提交成功", failed: "执行失败", cancelled: "已取消" };
const mailLabels = { pending: "邮件待发送", sent: "成功邮件已发送", failed: "成功邮件发送失败", skipped: "未配置成功邮件" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

function localInput(iso) { return iso ? iso.slice(0, 19) : ""; }

function beijingInputValue(date) {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date).replace(" ", "T");
}

function renderJob(job) {
  const payload = job.payload || {};
  const mutable = ["pending", "retrying"].includes(job.status);
  const retryable = ["failed", "cancelled"].includes(job.status);
  const deletable = ["completed", "failed", "cancelled"].includes(job.status);
  const canEmail = job.status === "completed" && job.email_status !== "sent";
  return `<article class="stack-item" data-job="${job.id}">
    <div class="stack-item-head"><div><span class="stack-id">TASK #${job.id}</span><h2 class="stack-item-title">${escapeHtml(payload.byyrxm || "未填写姓名")}</h2></div><span class="stack-badge ${escapeHtml(job.status)}">${escapeHtml(statusLabels[job.status] || job.status)}</span></div>
    <div class="stack-meta">
      <div><span>计划提交</span><b>${escapeHtml(job.scheduled_at || "")}</b></div>
      <div><span>预约时间</span><b>${escapeHtml(`${payload.yyrq || ""} ${payload.yysj || ""}`.trim())}</b></div>
      <div><span>校门</span><b>${escapeHtml(payload.yyxm || "")}</b></div>
      <div><span>尝试次数</span><b>${job.attempts} / ${job.max_attempts}</b></div>
      <div><span>联系电话</span><b>${escapeHtml(payload.byyrlxdh || "")}</b></div>
      <div><span>申请编号</span><b>${escapeHtml(job.remote_id || "尚未生成")}</b></div>
      <div><span>创建时间</span><b>${escapeHtml(job.created_at || "")}</b></div>
      <div><span>下次重试</span><b>${escapeHtml(job.next_attempt_at || "—")}</b></div>
    </div>
    ${job.last_error ? `<p class="stack-error">${escapeHtml(job.last_error)}</p>` : ""}
    ${job.email_status ? `<p class="stack-mail">${escapeHtml(mailLabels[job.email_status] || job.email_status)}${job.email_error ? `：${escapeHtml(job.email_error)}` : ""}</p>` : ""}
    <div class="stack-controls">
      ${mutable ? `<label>修改提交时间<input type="datetime-local" step="1" value="${escapeHtml(localInput(job.scheduled_at))}" data-schedule /></label><button class="button button-quiet" data-action="reschedule">改期</button><button class="button danger-button" data-action="cancel">取消</button>` : ""}
      ${retryable ? '<button class="button button-secondary" data-action="retry">立即重试</button>' : ""}
      ${canEmail ? '<button class="button button-quiet" data-action="email">重发成功邮件</button>' : ""}
      ${deletable ? '<button class="button danger-button" data-action="delete">删除记录</button>' : ""}
    </div>
  </article>`;
}

async function api(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { "Content-Type": "application/json", ...(options.headers || {}) } });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "操作失败");
  return data;
}

async function loadStatus() {
  try {
    const status = await api("/api/app-status");
    const items = [
      ["账户配置", status.account_configured, status.account_configured ? "已就绪" : "未配置"],
      ["TOTP", status.totp_configured, status.totp_configured ? "已就绪" : "未配置"],
      ["成功邮件", status.smtp_configured, status.smtp_configured ? "已就绪" : "未配置"],
      ["后台调度", status.scheduler_running, status.scheduler_running ? "运行中" : "未运行"],
    ];
    $("statusGrid").innerHTML = items.map(([name, ok, text]) => `<div class="status-tile"><span>${name}</span><strong class="${ok ? "ok" : "bad"}">${text}</strong></div>`).join("");
    const warnings = [status.account_error, status.totp_error, status.smtp_error, status.scheduler_running ? "" : "后台调度未启动，请使用 python pku_web.py 启动应用。"].filter(Boolean);
    $("configWarning").hidden = !warnings.length;
    $("configWarning").textContent = warnings.join("；");
  } catch (error) { $("stackMessage").textContent = error.message; }
}

async function loadJobs() {
  try {
    const data = await api("/api/stack");
    $("stackList").innerHTML = data.jobs.length ? data.jobs.map(renderJob).join("") : '<div class="machine-empty">提交队列为空，请从上方历史记录中选择预约草稿。</div>';
  } catch (error) { $("stackMessage").textContent = error.message; }
}

async function loadCandidates() {
  const select = $("historyCandidate");
  $("candidateMessage").textContent = "正在读取历史预约…";
  try {
    const data = await api("/api/history?page=1&pageSize=50");
    const records = Array.isArray(data.row?.list) ? data.row.list : [];
    const candidates = records.filter((record) => record.sqxxid && String(record.tjbz || "").toLowerCase() !== "y");
    select.innerHTML = '<option value="">请选择尚未提交的预约</option>' + candidates.map((record) => {
      const label = `${record.byyrxm || "未填写姓名"} · ${record.yyrq || ""} ${record.yysj || ""} · ${record.yyxm || ""} · ${record.sqxxid}`;
      return `<option value="${escapeHtml(record.sqxxid)}">${escapeHtml(label)}</option>`;
    }).join("");
    const requested = new URLSearchParams(window.location.search).get("select");
    if (requested && candidates.some((record) => String(record.sqxxid) === requested)) select.value = requested;
    $("candidateMessage").textContent = candidates.length ? `找到 ${candidates.length} 条尚未提交的历史预约` : "历史记录中没有可加入队列的草稿";
  } catch (error) {
    select.innerHTML = '<option value="">历史记录读取失败</option>';
    $("candidateMessage").textContent = error.message;
  }
}

async function addFromHistory() {
  const sqxxid = $("historyCandidate").value;
  const scheduledAt = $("candidateScheduledAt").value;
  if (!sqxxid) return $("candidateMessage").textContent = "请选择一条历史预约草稿";
  if (!scheduledAt) return $("candidateMessage").textContent = "请选择自动提交时间";
  const button = $("addFromHistory");
  button.disabled = true;
  try {
    const data = await api("/api/stack/from-history", {
      method: "POST",
      body: JSON.stringify({ sqxxid, scheduled_at: scheduledAt, max_attempts: Number($("candidateMaxAttempts").value) }),
    });
    $("candidateMessage").textContent = `申请 ${data.job.remote_id} 已加入任务 #${data.job.id}`;
    await Promise.all([loadJobs(), loadStatus(), loadCandidates()]);
  } catch (error) {
    $("candidateMessage").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function act(jobId, action, card) {
  try {
    $("stackMessage").textContent = "正在处理…";
    if (action === "reschedule") {
      const scheduledAt = card.querySelector("[data-schedule]").value;
      await api(`/api/stack/${jobId}`, { method: "PATCH", body: JSON.stringify({ scheduled_at: scheduledAt }) });
    } else if (action === "delete") {
      if (!window.confirm(`确定删除任务 #${jobId} 的本地记录吗？`)) return;
      await api(`/api/stack/${jobId}`, { method: "DELETE" });
    } else {
      await api(`/api/stack/${jobId}/${action}`, { method: "POST", body: "{}" });
    }
    $("stackMessage").textContent = "操作已完成";
    await Promise.all([loadJobs(), loadStatus()]);
  } catch (error) { $("stackMessage").textContent = error.message; }
}

$("stackList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const card = button.closest("[data-job]");
  act(Number(card.dataset.job), button.dataset.action, card);
});

$("candidateScheduledAt").value = beijingInputValue(new Date(Date.now() + 5 * 60 * 1000));
$("refreshCandidates").addEventListener("click", loadCandidates);
$("addFromHistory").addEventListener("click", addFromHistory);
Promise.all([loadStatus(), loadJobs(), loadCandidates()]);
setInterval(() => { if (!document.hidden) Promise.all([loadStatus(), loadJobs()]); }, 3000);
