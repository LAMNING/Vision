// Vision Studio — 主题管理
// 默认使用「晴蓝」；「澄白」完整保留原有样式。主题只影响外观，不介入业务逻辑。
(() => {
  const STORAGE_KEY = "vision_theme";
  const NAV_KEY = "vision_nav_collapsed";
  const DEFAULT_THEME = "clear-sky";
  const THEMES = new Set(["clear-sky", "pure-white"]);

  function readTheme() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      return THEMES.has(saved) ? saved : DEFAULT_THEME;
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
    document.documentElement.dataset.theme = next;
    if (persist) writeTheme(next);
    syncChoices(next);
    window.dispatchEvent(new CustomEvent("vision:themechange", { detail: { theme: next } }));
    return next;
  }

  // 放在 head 中同步执行，避免页面先闪一下旧主题。
  setTheme(readTheme(), false);

  function readNavCollapsed() {
    try { return localStorage.getItem(NAV_KEY) === "1"; } catch { return false; }
  }

  function setNavCollapsed(collapsed) {
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

    const collapse = document.querySelector("#railCollapse");
    if (collapse) {
      setNavCollapsed(readNavCollapsed());
      collapse.addEventListener("click", () => setNavCollapsed(!document.body.classList.contains("is-nav-collapsed")));
    }

    const quick = document.querySelector("#themeQuickButton");
    if (quick) {
      quick.addEventListener("click", () => {
        document.querySelector("#railProfile")?.click();
        window.setTimeout(() => document.querySelector("[data-theme-choice].is-active")?.focus(), 220);
      });
    }

    syncRailAccount();
    const name = document.querySelector("#appAccountName");
    const avatar = document.querySelector("#appAvatar");
    const observer = new MutationObserver(syncRailAccount);
    if (name) observer.observe(name, { subtree: true, childList: true, characterData: true });
    if (avatar) observer.observe(avatar, { subtree: true, childList: true, attributes: true });
  }

  window.VSTheme = { get: readTheme, set: setTheme, names: { "clear-sky": "晴蓝", "pure-white": "澄白" } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init, { once: true });
  else init();
})();
