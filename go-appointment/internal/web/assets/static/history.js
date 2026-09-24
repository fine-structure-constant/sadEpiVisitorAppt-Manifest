let currentPage = 0;
let hasNextPage = false;
const labels = { sqxxid: "申请编号", byyrxm: "被预约人", byyrzjh: "证件号", byyrlxdh: "被预约人电话", lxdh: "联系人电话", yyxm: "预约校门", yyrq: "预约日期", yysj: "预约时间", yysy: "预约事由", shbz: "审核状态", tjbz: "提交标记", tjrq: "提交时间" };

function recordHTML(record) {
  const e = App.escape;
  const details = Object.entries(record).map(([key, value]) => `<div><span>${e(labels[key] || key)}</span><b>${e(value)}</b></div>`).join("");
  return `<article class="record"><div class="record-main"><div><p class="record-date">${e(`${record.yyrq || ""} ${record.yysj || ""}`)}</p><h3>${e(record.byyrxm || "未填写被预约人")}</h3></div><span class="badge ${record.shbz === "审核通过" ? "approved" : ""}">${e(record.shbz || (String(record.tjbz).toLowerCase() === "y" ? "已提交" : "草稿"))}</span></div><div class="record-meta"><span>${e(record.yyxm || "未填写校门")}</span><span>${e(record.yysy || "未填写事由")}</span><span>${e(record.sqxxid || "")}</span></div><details><summary>查看完整字段</summary><div class="detail-grid">${details}</div></details></article>`;
}
async function load(page) {
  const button = App.id("loadMoreButton");
  button.disabled = true;
  App.id("pageStatus").textContent = "正在自动登录并读取…";
  try {
    const data = await App.api(`/api/history?page=${page}&pageSize=10`);
    const list = App.rows(data);
    if (page === 1) App.id("records").innerHTML = "";
    if (!list.length && page === 1) App.id("records").innerHTML = '<div class="machine-empty">暂时没有历史预约记录</div>';
    App.id("records").insertAdjacentHTML("beforeend", list.map(recordHTML).join(""));
    currentPage = page;
    hasNextPage = Boolean(data.row?.hasNextPage);
    App.id("summaryRow").textContent = `第 ${page} 页 · 本页 ${list.length} 条`;
    App.id("pageStatus").textContent = hasNextPage ? `当前第 ${page} 页` : "已加载全部记录";
    button.hidden = !hasNextPage;
  } catch (error) {
    App.id("pageStatus").textContent = error.message;
    if (page === 1) App.id("records").textContent = error.message;
  } finally { button.disabled = false; }
}
App.id("loadMoreButton").addEventListener("click", () => hasNextPage && load(currentPage + 1));
App.id("refreshHistory").addEventListener("click", () => load(1));
load(1);
