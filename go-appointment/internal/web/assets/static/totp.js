const code = App.id("totpCode");
const message = App.id("totpMessage");
const copy = App.id("copyTotp");
let currentCode = "";
let offset = 0;
let lastPeriod = -1;

async function refresh() {
  try {
    const data = await App.api("/api/totp");
    currentCode = data.code;
    code.textContent = `${data.code.slice(0, 3)} ${data.code.slice(3)}`;
    offset = data.server_time * 1000 - Date.now();
    copy.disabled = false;
    message.textContent = "验证码来自本机服务端";
    message.className = "form-message success";
  } catch (error) {
    currentCode = "";
    copy.disabled = true;
    message.textContent = error.message;
    message.className = "form-message error";
  }
}
function tick() {
  const serverSeconds = Math.floor((Date.now() + offset) / 1000);
  const period = Math.floor(serverSeconds / 30);
  const seconds = 30 - serverSeconds % 30;
  App.id("secondsLeft").textContent = seconds;
  App.id("totpProgress").style.width = `${seconds / 30 * 100}%`;
  if (lastPeriod !== -1 && period !== lastPeriod) refresh();
  lastPeriod = period;
}
copy.addEventListener("click", async () => {
  await navigator.clipboard.writeText(currentCode);
  message.textContent = "已复制";
});
refresh(); tick(); setInterval(tick, 250);
