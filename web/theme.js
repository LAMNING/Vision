// 灵视标注平台 — 主题管理
// 两套可选主题：经典（默认）与极简。晴蓝已从选择器移除，
// 但 Material 组件层仍作为经典主题的基线保留（见 MATERIAL_BASE），所以样式一行没删。
(() => {
  const STORAGE_KEY = "vision_theme";
  const DEFAULT_VERSION_KEY = "vision_theme_default_v4";
  const NAV_KEY = "vision_nav_collapsed";
  const DEFAULT_THEME = "classic";
  // 经典主题复用 Material 组件层：data-theme 只是「基线样式别名」，恒为 clear-sky。
  // 真实主题只认 data-vision-theme / VSTheme.get()，任何脚本都不要拿 dataset.theme 判断主题。
  const MATERIAL_BASE = "clear-sky";
  const THEMES = new Set(["classic", "pure-white"]);
  // 晴蓝已下线：老用户存在本地的 clear-sky 要落到经典，不能停在一个选不到的主题上。
  const RETIRED = { "clear-sky": "classic" };

  function readTheme() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (RETIRED[saved]) {
        localStorage.setItem(STORAGE_KEY, RETIRED[saved]);
        return RETIRED[saved];
      }
      // 已有显式选择一律尊重，绝不被默认主题迁移覆盖。
      if (THEMES.has(saved)) return saved;
      // 首次访问（或存的旧键值非法）才落到默认主题，并只记一次标记。
      if (localStorage.getItem(DEFAULT_VERSION_KEY) !== "1") {
        localStorage.setItem(DEFAULT_VERSION_KEY, "1");
        localStorage.setItem(STORAGE_KEY, DEFAULT_THEME);
      }
      return DEFAULT_THEME;
    } catch {
      return DEFAULT_THEME;
    }
  }

  function writeTheme(theme) {
    try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* 无存储权限时仍可临时切换 */ }
  }

  function syncChoices(theme) {
    document.querySelectorAll("[data-theme-choice]").forEach((choice) => {
      const active = choice.dataset.themeChoice === theme;
      choice.classList.toggle("is-active", active);
      choice.setAttribute("aria-checked", String(active));
    });
  }

  function setTheme(theme, persist = true) {
    const next = THEMES.has(theme) ? theme : DEFAULT_THEME;
    // 经典复用晴蓝完整的 Material 组件层，再用 data-vision-theme 做紧凑布局覆盖。
    document.documentElement.dataset.visionTheme = next;
    document.documentElement.dataset.theme = next === "classic" ? MATERIAL_BASE : next;
    document.documentElement.dataset.themeBase = MATERIAL_BASE;
    if (persist) writeTheme(next);
    syncChoices(next);
    window.dispatchEvent(new CustomEvent("vision:themechange", { detail: { theme: next } }));
    return next;
  }

  // 放在 head 中同步执行，避免页面先闪一下旧主题。
  setTheme(readTheme(), false);

  // ------------------------------------------------------------------ 深色模式
  // 和「主题」正交：任何主题都能用深色。auto = 跟着系统走（系统切了立刻跟着变），
  // light/dark = 用户手动压过系统，此时不再被系统打断。
  const SCHEME_KEY = "vision_color_scheme";
  const SCHEMES = new Set(["auto", "light", "dark"]);
  const darkQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;

  function readScheme() {
    try {
      const saved = localStorage.getItem(SCHEME_KEY);
      return SCHEMES.has(saved) ? saved : "auto";
    } catch { return "auto"; }
  }

  function resolveScheme(choice) {
    if (choice === "light" || choice === "dark") return choice;
    return darkQuery && darkQuery.matches ? "dark" : "light";
  }

  function syncSchemeChoices(choice) {
    document.querySelectorAll("[data-scheme-choice]").forEach((node) => {
      const active = node.dataset.schemeChoice === choice;
      node.classList.toggle("is-active", active);
      node.setAttribute("aria-checked", String(active));
    });
  }

  function setScheme(choice = "auto", persist = true) {
    const next = SCHEMES.has(choice) ? choice : "auto";
    const resolved = resolveScheme(next);
    const root = document.documentElement;
    // data-color-scheme 给 CSS 用（真实的 light/dark）；data-scheme-choice 记用户选择
    root.dataset.colorScheme = resolved;
    root.dataset.schemeChoice = next;
    // 原生滚动条 / 输入框 / 自动填充也跟着变，否则深色页里会冒出浅色滚动条
    root.style.colorScheme = resolved;
    if (persist) {
      try { localStorage.setItem(SCHEME_KEY, next); } catch { /* 无存储权限时本次会话仍生效 */ }
    }
    syncSchemeChoices(next);
    window.dispatchEvent(new CustomEvent("vision:schemechange", { detail: { choice: next, resolved } }));
    return resolved;
  }

  setScheme(readScheme(), false);

  // 跟随系统：只在用户选 auto 时响应，手动选了浅/深就不再被系统打断
  if (darkQuery) {
    const onSystemChange = () => { if (readScheme() === "auto") setScheme("auto", false); };
    if (darkQuery.addEventListener) darkQuery.addEventListener("change", onSystemChange);
    else if (darkQuery.addListener) darkQuery.addListener(onSystemChange);
  }

  // 收起状态也在这里先落到 <html> 上：head 阶段 body 还不存在，
  // 若等 DOMContentLoaded 再落，刷新时就会先画出展开态再收回，看起来是"闪一下"。
  function readNavCollapsedEarly() {
    try { return localStorage.getItem(NAV_KEY) === "1"; } catch { return false; }
  }
  document.documentElement.classList.toggle("is-nav-collapsed", readNavCollapsedEarly());
  // 首帧禁用过渡：配合 themes.css 的 .is-booting，避免侧栏宽度从 232 滑到 76。
  document.documentElement.classList.add("is-booting");
  // 注意不能挂在 load 上：首页「最近图像」只要有一张图没下载完，load 就一直不触发，
  // is-booting 会一直留着，而它带的是 `.utility-rail * { transition: none !important }`,
  // 结果是全站所有过渡静默失效（hover 全部瞬变）。
  // 两帧后摘掉即可，超时兜底必须直接 remove、不能包在 rAF 里（rAF 在某些环境下不推进）。
  const clearBoot = () => document.documentElement.classList.remove("is-booting");
  const clearBootAfterPaint = () => requestAnimationFrame(() => requestAnimationFrame(clearBoot));
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", clearBootAfterPaint, { once: true });
  else clearBootAfterPaint();
  setTimeout(clearBoot, 1000);

  function readNavCollapsed() {
    return readNavCollapsedEarly();
  }

  function setNavCollapsed(collapsed) {
    document.documentElement.classList.toggle("is-nav-collapsed", collapsed);
    document.body.classList.toggle("is-nav-collapsed", collapsed);
    const button = document.querySelector("#railCollapse");
    if (button) {
      button.setAttribute("aria-expanded", String(!collapsed));
      button.setAttribute("aria-label", collapsed ? "展开侧边栏" : "收起侧边栏");
      const icon = button.querySelector(".material-symbols-rounded");
      const label = button.querySelector(".rail-label");
      if (icon) icon.textContent = collapsed ? "left_panel_open" : "left_panel_close";
      if (label) label.textContent = collapsed ? "展开侧边栏" : "收起侧边栏";
    }
    try { localStorage.setItem(NAV_KEY, collapsed ? "1" : "0"); } catch { /* 同上 */ }
  }

  function syncRailAccount() {
    const sourceName = document.querySelector("#appAccountName");
    const sourceAvatar = document.querySelector("#appAvatar");
    const targetName = document.querySelector("#railProfileName");
    const targetAvatar = document.querySelector("#railProfileAvatar");
    if (sourceName && targetName) targetName.textContent = sourceName.textContent;
    if (sourceAvatar && targetAvatar) targetAvatar.innerHTML = sourceAvatar.innerHTML;
  }

  function init() {
    syncChoices(readTheme());
    document.querySelectorAll("[data-theme-choice]").forEach((choice) => {
      choice.addEventListener("click", () => setTheme(choice.dataset.themeChoice));
    });

    syncSchemeChoices(readScheme());
    document.querySelectorAll("[data-scheme-choice]").forEach((choice) => {
      choice.addEventListener("click", () => setScheme(choice.dataset.schemeChoice));
    });

    const collapse = document.querySelector("#railCollapse");
    if (collapse) {
      setNavCollapsed(readNavCollapsed());
      collapse.addEventListener("click", () => setNavCollapsed(!document.documentElement.classList.contains("is-nav-collapsed")));
    }

    syncRailAccount();
    const name = document.querySelector("#appAccountName");
    const avatar = document.querySelector("#appAvatar");
    const observer = new MutationObserver(syncRailAccount);
    if (name) observer.observe(name, { subtree: true, childList: true, characterData: true });
    if (avatar) observer.observe(avatar, { subtree: true, childList: true, attributes: true });
  }

  window.VSTheme = {
    get: readTheme,
    set: setTheme,
    names: { classic: "经典", "pure-white": "极简" },
    scheme: { get: readScheme, set: setScheme, resolved: resolveScheme },
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
