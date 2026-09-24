const codeElement = document.getElementById("totpCode");
const secondsElement = document.getElementById("secondsLeft");
const progressElement = document.getElementById("totpProgress");
const messageElement = document.getElementById("totpMessage");
const copyButton = document.getElementById("copyTotp");

let currentCode = "";
let period = 30;
let validUntilMs = 0;
let serverOffsetMs = 0;
let lastWindow = null;
let loading = false;

function showMessage(message, kind = "") {
  messageElement.textContent = message;
  messageElement.className = `form-message ${kind}`;
}

function renderCode(code) {
  currentCode = code;
  codeElement.textContent = `${code.slice(0, 3)} ${code.slice(3)}`;
  copyButton.disabled = false;
}

async function loadCode() {
  if (loading) return;
  loading = true;
  try {
    const requestedAt = Date.now();
    const response = await fetch("/api/totp", { cache: "no-store" });
    const receivedAt = Date.now();
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "验证码读取失败");

    period = data.period;
    serverOffsetMs = data.server_time * 1000 + (receivedAt - requestedAt) / 2 - receivedAt;
    validUntilMs = data.valid_until * 1000;
    lastWindow = data.valid_until;
    renderCode(data.code);
    showMessage("验证码已与本机服务端同步", "success");
  } catch (error) {
    currentCode = "";
    codeElement.textContent = "—— —— ——";
    copyButton.disabled = true;
    showMessage(error.message, "error");
  } finally {
    loading = false;
  }
}

function updateTimer() {
  if (!validUntilMs) {
    secondsElement.textContent = "--";
    progressElement.style.width = "0%";
    return;
  }

  const serverNow = Date.now() + serverOffsetMs;
  const remainingMs = Math.max(0, validUntilMs - serverNow);
  const seconds = Math.max(0, Math.ceil(remainingMs / 1000));
  secondsElement.textContent = String(seconds);
  progressElement.style.width = `${Math.min(100, (remainingMs / (period * 1000)) * 100)}%`;

  const currentValidUntil = (Math.floor(serverNow / (period * 1000)) + 1) * period;
  if (lastWindow !== null && currentValidUntil !== lastWindow) loadCode();
}

copyButton.addEventListener("click", async () => {
  if (!currentCode) return;
  try {
    await navigator.clipboard.writeText(currentCode);
    showMessage("验证码已复制", "success");
  } catch (_error) {
    showMessage("复制失败，请手动选择验证码", "error");
  }
});

loadCode();
updateTimer();
setInterval(updateTimer, 200);
setInterval(loadCode, 5000);
