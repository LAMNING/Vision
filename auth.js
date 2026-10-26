// 登录 / 注册：唯一入口是 sdk.signin / sdk.signup，token 由 SDK 自己持久化。
const form = document.querySelector("#authForm");
const modeToggle = document.querySelector("#modeToggle");
const title = document.querySelector("#authTitle");
const submitButton = document.querySelector("#submitButton");
const status = document.querySelector("#authStatus");
const authShell = document.querySelector(".auth-shell");
const loginRipple = document.querySelector(".login-ripple");
let isRegisterMode = false;

if (VS.hasToken()) location.replace("index.html");

function setMode(registerMode) {
  isRegisterMode = registerMode;
  title.textContent = registerMode ? "注册后开始上传。" : "登录后开始上传。";
  submitButton.textContent = registerMode ? "注册并进入" : "登录";
  modeToggle.textContent = registerMode ? "已有账号？直接登录" : "没有账号？注册一个";
  status.textContent = "";
}

function beginLoginTransition() {
  const buttonRect = submitButton.getBoundingClientRect();
  const rippleX = buttonRect.left + buttonRect.width / 2;
  const rippleY = buttonRect.top + buttonRect.height / 2;
  const farthestCorner = Math.max(
    Math.hypot(rippleX, rippleY),
    Math.hypot(window.innerWidth - rippleX, rippleY),
    Math.hypot(rippleX, window.innerHeight - rippleY),
    Math.hypot(window.innerWidth - rippleX, window.innerHeight - rippleY),
  );
  const svgNamespace = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNamespace, "svg");
  svg.setAttribute("viewBox", `0 0 ${window.innerWidth} ${window.innerHeight}`);
  svg.setAttribute("preserveAspectRatio", "none");

  [
    { delay: "0s", stroke: "rgba(0,113,227,.82)", width: "2.4", opacity: ".9" },
    { delay: ".07s", stroke: "rgba(53,145,235,.46)", width: "1.4", opacity: ".72" },
  ].forEach(({ delay, stroke, width, opacity }) => {
    const ring = document.createElementNS(svgNamespace, "circle");
    ring.setAttribute("cx", rippleX);
    ring.setAttribute("cy", rippleY);
    ring.setAttribute("r", "21");
    ring.setAttribute("fill", "none");
    ring.setAttribute("stroke", stroke);
    ring.setAttribute("stroke-width", width);
    ring.setAttribute("vector-effect", "non-scaling-stroke");

    const radius = document.createElementNS(svgNamespace, "animate");
    radius.setAttribute("attributeName", "r");
    radius.setAttribute("from", "21");
    radius.setAttribute("to", `${Math.ceil(farthestCorner + 400)}`);
    radius.setAttribute("dur", ".72s");
    radius.setAttribute("begin", delay);
    radius.setAttribute("fill", "freeze");

    const fade = document.createElementNS(svgNamespace, "animate");
    fade.setAttribute("attributeName", "opacity");
    fade.setAttribute("values", `${opacity};${opacity};0`);
    fade.setAttribute("keyTimes", "0;.82;1");
    fade.setAttribute("dur", ".72s");
    fade.setAttribute("begin", delay);
    fade.setAttribute("fill", "freeze");
    ring.append(radius, fade);
    svg.append(ring);
  });
  loginRipple.replaceChildren(svg);
  sessionStorage.setItem("vision_enter_transition", "1");
  authShell.classList.add("is-leaving");
  window.setTimeout(() => location.replace("index.html"), 820);
}

modeToggle.addEventListener("click", () => setMode(!isRegisterMode));

// 登录页的 401 是「账号密码不对」，不是「登录过期」。
// 注意这里不能走 VS.call：call 会把 SDKError 翻成通用文案，
// 拿回来就分不清 401 到底是「密码错」还是「登录过期」了。
function describe(error) {
  if (VS.isSdkError(error) && error.status === 401) {
    return isRegisterMode ? "注册失败，请换一个用户名再试" : "用户名或密码不正确";
  }
  return VS.translate(error).message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const data = new FormData(form);
  const username = String(data.get("username") || "").trim();
  const password = String(data.get("password") || "");

  submitButton.disabled = true;
  status.classList.remove("is-success");
  status.textContent = isRegisterMode ? "正在注册…" : "正在登录…";

  try {
    // 直接调 SDK，保留原始 SDKError 好判断 401
    const account = isRegisterMode
      ? (await VS.sdk.signup(username, password)).account
      : await VS.sdk.signin(username, password);

    // SDK 已经把 token 写进 localStorage，这里只存一个名字给顶栏兜底
    localStorage.setItem("vision_account_name", account.username);
    status.classList.add("is-success");
    status.textContent = "登录成功，正在进入上传页面…";
    beginLoginTransition();
  } catch (error) {
    status.textContent = describe(error);
  } finally {
    submitButton.disabled = false;
  }
});
