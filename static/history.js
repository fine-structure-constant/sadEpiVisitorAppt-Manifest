const $ = (id) => document.getElementById(id);
let currentPage = 0;
let hasNextPage = false;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;",
  }[character]));
}

const labels = {
  shbz: "审核状态", shyj: "审核意见", byyrxm: "被预约人", zjlx: "证件类型",
  zjh: "证件号", byyrzjh: "被预约人证件号", byyrlxdh: "被预约人电话",
  lxdh: "联系人电话", xm: "姓名", xh: "学号", xsmc: "学院", xslb: "学生类别",
  tjr: "提交人", tjrq: "提交时间", sqrq: "申请时间", sqrqf: "申请日期",
  sqxxid: "申请编号", yyxm: "预约校门", yyrq: "预约日期", yysj: "预约时间",
  yysy: "预约事由", tjbz: "提交标记",
};

function renderRecord(record) {
  const title = `${record.yyrq || ""} ${record.yysj || ""}`.trim() || "未填写预约时间";
  const details = Object.entries(record).map(([key, value]) =>
    `<div><span>${escapeHtml(labels[key] || key)}</span><b>${escapeHtml(value)}</b></div>`
  ).join("");
  return `<article class="record">
    <div class="record-main">
      <div><p class="record-date">${escapeHtml(title)}</p><h3>${escapeHtml(record.byyrxm || "未填写被预约人")}</h3></div>
      <span class="badge ${record.shbz === "审核通过" ? "approved" : "pending"}">${escapeHtml(record.shbz || "未知状态")}</span>
    </div>
    <div class="record-meta"><span>${escapeHtml(record.yyxm || "未填写校门")}</span><span>${escapeHtml(record.yysy || "未填写事由")}</span><span>${escapeHtml(record.sqxxid || "")}</span></div>
    <details><summary>查看完整字段</summary><div class="detail-grid">${details}</div></details>
  </article>`;
}

async function loadHistory(page = 1) {
  const button = $("loadMoreButton");
  button.disabled = true;
  $("pageStatus").textContent = "正在自动登录并读取…";
  try {
    const response = await fetch(`/api/history?page=${page}&pageSize=10`, { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "历史记录读取失败");
    const row = data.row || {};
    const list = Array.isArray(row.list) ? row.list : [];
    if (page === 1) $("records").innerHTML = "";
    if (!list.length && page === 1) $("records").innerHTML = '<div class="machine-empty">暂时没有历史预约记录</div>';
    $("records").insertAdjacentHTML("beforeend", list.map(renderRecord).join(""));
    currentPage = page;
    hasNextPage = Boolean(row.hasNextPage);
    $("summaryRow").textContent = `第 ${page} 页 · 本页 ${list.length} 条`;
    $("pageStatus").textContent = row.isLastPage || !hasNextPage ? "已加载全部记录" : `当前第 ${page} 页`;
    button.hidden = !hasNextPage;
  } catch (error) {
    $("pageStatus").textContent = error.message;
    if (page === 1) $("records").innerHTML = `<div class="machine-empty">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
  }
}

$("loadMoreButton").addEventListener("click", () => hasNextPage && loadHistory(currentPage + 1));
$("refreshHistory").addEventListener("click", () => loadHistory(1));
loadHistory(1);
