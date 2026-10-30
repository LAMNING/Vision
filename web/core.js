// Vision Studio — 公共层
// 全站唯一的后端入口：sdk.js。业务脚本不再自己写 fetch。
// 这里只做四件事：建 SDK 实例、把错误翻成中文、缓存头像、提供几个 DOM 小工具。
window.VS = (() => {
  // 默认同源（后端用 web/ 托管前端）。要临时指向别的后端，在 sdk.js 之前写
  // <script>window.__VS_BASE__ = "http://host:port";</script>
  const BASE = window.__VS_BASE__ || location.origin;
  const TOKEN_KEY = "vision-studio.token";
  const LEGACY_KEY = "vision_access_token";

  // 旧版前端把 token 存在另一个键上。升级后迁移一次，
  // 免得已经在用的人莫名其妙被登出。
  try {
    const legacy = localStorage.getItem(LEGACY_KEY);
    if (legacy && !localStorage.getItem(TOKEN_KEY)) {
      localStorage.setItem(TOKEN_KEY, legacy);
    }
    localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* 隐私模式下 localStorage 可能不可写，忽略即可 */
  }

  function toLogin() {
    if (location.pathname.endsWith("login.html")) return;
    location.replace("login.html");
  }

  const sdk = new ImageLabelSDK({
    baseUrl: BASE,
    storage: window.localStorage,
    tokenKey: TOKEN_KEY,
    timeout: 60000,
    // 401 统一回登录页。但登录/注册失败后端也返回 401，
    // 那时 SDK 里还没有 token，不能把人从登录页再踢回登录页。
    onUnauthorized: () => {
      if (!sdk.getToken()) return;
      sdk.setToken(null);
      toLogin();
    },
  });

  /* ---------------- 错误翻译 ---------------- */

  // 后端有些错误是给开发者看的英文原文（例如 "email 'a@b.com' already
  // registered"），直接摊给用户很突兀。这里把常见几类翻成中文，
  // 认不出来的才退回通用文案，绝不把英文原文露出来。
  const FRIENDLY = [
    [/email .* already registered/i, "这个邮箱已经被其他账号使用"],
    [/phone .* already registered/i, "这个手机号已经被其他账号使用"],
    [/email \/ phone already registered/i, "邮箱或手机号已经被其他账号使用"],
    [/username .* already (registered|exists|taken)/i, "这个用户名已被占用"],
    [/(old )?password.*(incorrect|invalid|wrong)|旧密码不正确/i, "当前密码不正确"],
    [/not a member/i, "你还不是这个小组的成员"],
    [/only (the )?owner/i, "只有组长可以执行这个操作"],
    [/admin.*(role|member)/i, "只有组长可以调整其他管理员"],
    [/owner.*(remove|transfer)/i, "不能移除组长，请先转让所有权"],
    [/group.*(not empty|has .*project)|active project.*group|cannot delete.*group/i,
      "小组仍有关联数据，处理完成后才能解散"],
    [/quota/i, "已达到上限，无法继续添加"],
    // 图片还挂在分析任务里时后端拒绝删除，原文是
    // "image 7 is referenced by active task(s) [7, 8]; cancel or complete them first"。
    // 注意后端的终态只有 approved / cancelled，模型跑完（auto_annotated）
    // 仍然算活动任务，所以不能说「等分析完成就能删」。
    // 删除失败时 app.js 会接着问「要不要取消任务」，所以这里只陈述事实。
    [/referenced by (active )?task|referenced by .*annotation/i,
      "这张图片还关联着分析任务，取消任务后才能删除"],
    [/not found/i, "找不到对应的数据，请刷新后重试"],
    [/头像|avatar/i, "头像处理失败，请换一张图片试试"],
    [/unsupported|invalid image|not an image/i, "这个文件不是可用的图片，请换 JPG、PNG 或 WEBP"],
    [/file too large|too large/i, "文件太大了，请压缩后再上传"],
    [/permission|forbidden/i, "你没有执行这个操作的权限"],
  ];

  function friendly(raw) {
    if (!raw) return "";
    for (const [pattern, text] of FRIENDLY) {
      if (pattern.test(raw)) return text;
    }
    return "";
  }

  /**
   * 判断是不是 SDK 抛的错。
   * 不只用 instanceof：跨脚本、或 SDK 被加载多次时，类身份可能对不上，
   * 用 name + status 兜一层，避免翻译层被静默跳过。
   */
  function isSdkError(error) {
    if (!error) return false;
    if (typeof SDKError === "function" && error instanceof SDKError) return true;
    return error.name === "SDKError" && typeof error.status === "number";
  }

  /** 把 SDKError / 网络错误统一变成可以直接给用户看的中文 Error。 */
  function translate(error) {
    // 非 SDK 异常（TypeError、RangeError 之类）的 message 是英文的开发者信息，
    // 直接显示会很突兀。界面统一给中文，原文留在控制台方便排查。
    if (!isSdkError(error)) {
      if (error && error.message) console.warn("[Vision Studio] 未预期的异常：", error);
      return new Error("操作没有成功，请稍后重试");
    }
    if (error.status === 401) return new Error("登录已失效，请重新登录");
    if (error.status === 502) return new Error("连不上后端，请确认服务已启动");
    if (error.status === 408) return new Error("请求超时，请稍后重试");

    const { detail } = error;
    // 422 的 detail 是 [{ loc, msg, type }]，只取 msg 拼起来
    const raw = Array.isArray(detail)
      ? detail.map((item) => item && item.msg).filter(Boolean).join("；")
      : typeof detail === "string" ? detail : "";
    const text = friendly(raw) || raw;
    // 后端有些 detail 是英文原文，认不出来的不要直接摊给用户；
    // 只要不是中文，就退回带状态码的通用文案。
    return new Error(text && /[\u4e00-\u9fa5]/.test(text) ? text : `操作失败（${error.status}）`);
  }

  /** 所有业务调用都包一层：await call(() => sdk.xxx())。 */
  async function call(task) {
    try {
      return await task();
    } catch (error) {
      throw translate(error);
    }
  }

  /**
   * 尚未封装成 SDK 方法的端点走这里。
   * 内部仍然经过 sdk，超时/token/错误处理和其他调用完全一致。
   */
  function request(method, path, opts) {
    return call(() => sdk.request(method, path, opts));
  }

  /* ---------------- 登录态 ---------------- */

  function hasToken() {
    return Boolean(sdk.getToken());
  }

  function signOut() {
    // 先本地清干净再发请求：即使后端不可达，也要能退出去
    const token = sdk.getToken();
    sdk.setToken(null);
    forgetAvatar();
    localStorage.removeItem("vision_account_name");
    if (token) sdk.signout().catch(() => {});
    document.dispatchEvent(new Event("vision:signedout"));
    toLogin();
  }

  /* ---------------- 后端能力探测 ---------------- */

  // 后端有两套在跑：上游原版（78 条路径，账号只有 email/phone、没有头像）
  // 和扩展版（82 条路径，多了展示名称、头像和小组介绍）。
  // 与其写死，不如开页探测一次：缺哪个能力就把对应入口藏起来，
  // 这样同一个前端连哪个后端都不会报 422 / 404。
  const caps = { profile: false, avatar: false, groupDescription: false };

  async function probe() {
    let me = null;
    try {
      me = await call(() => sdk.me());
      caps.profile = Object.prototype.hasOwnProperty.call(me, "display_name");
      caps.avatar = Object.prototype.hasOwnProperty.call(me, "has_avatar");
    } catch {
      /* 探测失败就按最低能力跑，功能入口会自己藏起来 */
    }
    try {
      const groups = await call(() => sdk.listGroups());
      if (groups.length) {
        caps.groupDescription = Object.prototype.hasOwnProperty.call(groups[0], "description");
      }
    } catch {
      /* 同上 */
    }
    return { me, caps };
  }

  /* ---------------- 头像 ---------------- */

  // 头像接口要带 Authorization，不能直接塞进 <img src>，
  // 所以统一取 blob 转 objectURL 并按账号 id 缓存，避免列表里重复请求。
  const avatarCache = new Map();
  const avatarPending = new Map();

  function avatarUrl(account) {
    if (!account || !account.has_avatar || account.id == null) return Promise.resolve(null);
    const id = account.id;
    if (avatarCache.has(id)) return Promise.resolve(avatarCache.get(id));
    if (avatarPending.has(id)) return avatarPending.get(id);

    const task = call(() => sdk.avatarBlob(id))
      .then(({ blob }) => {
        const url = URL.createObjectURL(blob);
        avatarCache.set(id, url);
        return url;
      })
      .catch(() => {
        // 失败也记一笔，否则每次渲染都要重试一遍
        avatarCache.set(id, null);
        return null;
      })
      .finally(() => avatarPending.delete(id));

    avatarPending.set(id, task);
    return task;
  }

  /** 传 accountId 只清一个；不传清全部。换头像/删头像后必须调用。 */
  function forgetAvatar(accountId) {
    const drop = (id) => {
      const url = avatarCache.get(id);
      if (url) URL.revokeObjectURL(url);
      avatarCache.delete(id);
    };
    if (accountId == null) [...avatarCache.keys()].forEach(drop);
    else drop(accountId);
  }

  function displayName(account) {
    return (account && (account.display_name || account.username)) || "成员";
  }

  function initials(account) {
    return (displayName(account).trim()[0] || "·").toUpperCase();
  }

  /** 有头像就换成图片，没有就保留首字。 */
  function fillAvatar(element, account) {
    if (!element) return;
    element.textContent = initials(account);
    if (!account || !account.has_avatar || !caps.avatar) return;
    avatarUrl(account).then((url) => {
      if (!url) return;
      const img = document.createElement("img");
      img.src = url;
      img.alt = "";
      element.replaceChildren(img);
    });
  }

  function avatarNode(account, size = 28) {
    const node = document.createElement("span");
    node.className = `avatar avatar-${size}`;
    fillAvatar(node, account);
    return node;
  }

  /* ---------------- DOM / 表单 ---------------- */

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function formatSize(bytes) {
    return bytes < 1024 * 1024
      ? `${Math.max(1, Math.round(bytes / 1024))} KB`
      : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function status(target, message, error = false) {
    const node = typeof target === "string" ? document.querySelector(target) : target;
    if (!node) return;
    node.textContent = message;
    node.classList.toggle("is-error", error);
  }

  /** 提交按钮置灰 + 统一「正在保存…」→ 成功/失败文案，多个表单共用。 */
  async function runForm(button, statusTarget, task) {
    if (button) button.disabled = true;
    status(statusTarget, "正在保存…");
    try {
      await task();
    } catch (error) {
      status(statusTarget, error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  // 表单里可能没有 submitter（脚本提交 / 浏览器差异），兜底找提交按钮，
  // 免得 runForm 里 button.disabled 直接抛错
  function submitButtonOf(form, event) {
    return event.submitter || form.querySelector('button[type="submit"]');
  }

  return {
    sdk, base: BASE, call, request, hasToken, signOut, translate, isSdkError, caps, probe,
    formatSize, displayName, initials, avatarUrl, forgetAvatar, fillAvatar, avatarNode,
    el, status, runForm, submitButtonOf,
  };
})();
