/*
 * 通知中心：左栏入口 + 右侧抽屉。
 *
 * 后端一直在为「任务指派 / 待标注 / 待复核 / 逾期 / 邀请 / 系统」写站内通知，
 * 但界面此前没有任何入口 —— 任务派给你、复核轮到你，用户都看不到。
 * 这里把 listNotifications / unreadCount / markNotificationRead /
 * markAllNotificationsRead 四个接口接起来，并按 resource_type 跳到对应位置：
 *   task       → 找到任务所在项目并打开项目页
 *   invitation → 行内直接「接受 / 拒绝」（这是邀请唯一的前端入口）
 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { sdk, call, el, status } = VS;

  const PAGE = 20;
  const POLL_MS = 30_000;

  // 每种通知给一个图标 + 中文标签，落在列表左侧的小方块里
  const KIND_META = {
    task_assigned: { icon: "assignment_ind", label: "任务指派" },
    task_ready: { icon: "draw", label: "待标注" },
    task_review: { icon: "fact_check", label: "待复核" },
    task_overdue: { icon: "schedule", label: "已逾期" },
    invitation: { icon: "mail", label: "邀请" },
    system: { icon: "campaign", label: "系统" },
  };

  const state = {
    items: [],
    total: 0,
    offset: 0,
    unreadOnly: false,
    loading: false,
    poller: 0,
  };

  /* ---------------- 小工具 ---------------- */

  /** 后端时间是不带时区的 UTC，直接 new Date() 会当成本地时间，差出一个时区。 */
  function parseTime(value) {
    if (!value) return null;
    const text = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function timeText(value) {
    const date = parseTime(value);
    if (!date) return "";
    const minutes = Math.round((Date.now() - date.getTime()) / 60_000);
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    const hours = Math.round(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days} 天前`;
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  /** 按钮防抖：跑完再恢复，异常统一落到状态行。 */
  async function withButton(button, task) {
    if (button) {
      if (button.disabled) return;
      button.disabled = true;
    }
    try {
      await task();
    } catch (error) {
      status("#notifStatus", error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function isOpen() {
    const drawer = $("#notifDrawer");
    return Boolean(drawer) && !drawer.hidden;
  }

  /* ---------------- 未读数 ---------------- */

  async function refreshBadge() {
    if (!VS.hasToken()) return;
    let count = 0;
    try {
      const result = await sdk.unreadCount();
      count = Number(result?.count ?? 0);
    } catch (error) {
      return; // 未读数拉不到不该打扰用户
    }
    const badge = $("#notifBadge");
    if (badge) {
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.hidden = count <= 0;
    }
    const counter = $("#notifCount");
    if (counter) counter.textContent = count > 0 ? `${count} 条未读` : "全部已读";
    const rail = $("#railNotifications");
    if (rail) rail.classList.toggle("has-unread", count > 0);
  }

  function startPolling() {
    stopPolling();
    state.poller = window.setInterval(() => {
      if (document.hidden || !VS.hasToken()) return;
      refreshBadge();
    }, POLL_MS);
  }

  function stopPolling() {
    if (state.poller) {
      window.clearInterval(state.poller);
      state.poller = 0;
    }
  }

  /* ---------------- 列表 ---------------- */

  async function load({ append = false } = {}) {
    if (state.loading) return;
    state.loading = true;
    const offset = append ? state.offset : 0;
    try {
      const page = await call(() =>
        sdk.listNotifications({ unreadOnly: state.unreadOnly, offset, limit: PAGE })
      );
      const items = Array.isArray(page?.items) ? page.items : [];
      state.items = append ? state.items.concat(items) : items;
      state.total = Number(page?.total ?? state.items.length);
      state.offset = offset + items.length;
      status("#notifStatus", "");
      render();
    } catch (error) {
      status("#notifStatus", error.message, true);
    } finally {
      state.loading = false;
    }
  }

  function render() {
    const list = $("#notifList");
    if (!list) return;
    list.replaceChildren();
    state.items.forEach((item) => list.append(renderItem(item)));

    const empty = $("#notifEmpty");
    if (empty) {
      empty.hidden = state.items.length > 0;
      empty.textContent = state.unreadOnly
        ? "没有未读通知。"
        : "还没有通知。任务指派、复核提醒和邀请都会出现在这里。";
    }
    const more = $("#notifMore");
    if (more) more.hidden = state.items.length >= state.total;
  }

  function renderItem(item) {
    const meta = KIND_META[item.kind] || KIND_META.system;
    const unread = !item.read_at;
    const actionable = item.resource_type === "task" || item.resource_type === "invitation";

    const row = el("li", "notif-item");
    row.dataset.kind = item.kind;
    row.dataset.unread = unread ? "1" : "0";
    if (!actionable) row.classList.add("is-static");

    const icon = el("span", "notif-icon");
    icon.append(el("span", "material-symbols-rounded", meta.icon));

    const main = el("div", "notif-main");
    const head = el("div", "notif-title-row");
    head.append(el("strong", "notif-title", item.title));
    if (unread) head.append(el("span", "notif-dot"));
    head.append(el("span", "notif-time", timeText(item.created_at)));
    main.append(head);
    if (item.body) main.append(el("p", "notif-text", item.body));

    if (item.resource_type === "invitation" && unread) {
      const actions = el("div", "notif-actions");
      const accept = el("button", "mini-button is-accent", "接受");
      accept.type = "button";
      const reject = el("button", "mini-button is-reject", "拒绝");
      reject.type = "button";
      accept.addEventListener("click", (event) => {
        event.stopPropagation();
        decideInvitation(item, true, accept);
      });
      reject.addEventListener("click", (event) => {
        event.stopPropagation();
        decideInvitation(item, false, reject);
      });
      actions.append(accept, reject);
      main.append(actions);
    }

    row.append(icon, main);

    if (actionable) {
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      row.addEventListener("click", () => activate(item));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate(item);
        }
      });
    }
    return row;
  }

  /* ---------------- 交互 ---------------- */

  async function markReadIfNeeded(item) {
    if (item.read_at) return;
    try {
      await call(() => sdk.markNotificationRead(item.id));
      item.read_at = new Date().toISOString();
      await refreshBadge();
    } catch (error) {
      // 已读失败不该挡住跳转
    }
  }

  /** 任务通知：先在自己能看到的任务里找到它，再从项目列表里取出项目对象。 */
  async function openTask(item) {
    const taskId = Number(item.resource_id);
    if (!taskId) return false;
    const mine = await call(() => sdk.myTasks({ limit: 200 }));
    const tasks = Array.isArray(mine) ? mine : mine?.items || [];
    const task = tasks.find((row) => row.id === taskId);
    if (!task) return false;
    const projects = await call(() => sdk.listVisibleProjects());
    const project = (projects || []).find((row) => row.id === task.project_id);
    if (!project) return false;
    close();
    window.VSProjects?.open?.(project);
    return true;
  }

  async function activate(item) {
    await markReadIfNeeded(item);
    if (item.resource_type === "task") {
      try {
        const opened = await openTask(item);
        if (!opened) {
          status("#notifStatus", "这个任务可能已完成或被删除，去对应空间的「任务」里看看。", true);
        }
      } catch (error) {
        status("#notifStatus", error.message, true);
      }
      await load();
      return;
    }
    if (item.resource_type === "invitation") {
      // 邀请靠行内按钮处理，点整行只标已读
      await load();
      return;
    }
    await load();
  }

  async function decideInvitation(item, accept, button) {
    const invitationId = Number(item.resource_id);
    if (!invitationId) return;
    await withButton(button, async () => {
      await call(() => (accept ? sdk.acceptInvitation(invitationId) : sdk.rejectInvitation(invitationId)));
      await markReadIfNeeded(item);
      await refreshBadge();
      await load(); // load() 会清空状态行，所以提示必须写在它后面
      status("#notifStatus", accept ? "已接受邀请，对应空间已经出现这个项目。" : "已拒绝邀请。");
    });
  }

  async function readAll(button) {
    await withButton(button, async () => {
      const result = await call(() => sdk.markAllNotificationsRead());
      const updated = Number(result?.updated ?? 0);
      await refreshBadge();
      await load(); // load() 会清空状态行，所以提示必须写在它后面
      status("#notifStatus", updated > 0 ? `已标记 ${updated} 条为已读` : "没有未读通知");
    });
  }

  function setTab(unreadOnly) {
    if (state.unreadOnly === unreadOnly) return;
    state.unreadOnly = unreadOnly;
    $("#notifTabAll")?.classList.toggle("is-active", !unreadOnly);
    $("#notifTabUnread")?.classList.toggle("is-active", unreadOnly);
    $("#notifTabAll")?.setAttribute("aria-selected", String(!unreadOnly));
    $("#notifTabUnread")?.setAttribute("aria-selected", String(unreadOnly));
    state.items = [];
    state.offset = 0;
    load();
  }

  /* ---------------- 抽屉开关 ---------------- */

  function open() {
    const drawer = $("#notifDrawer");
    if (!drawer || !drawer.hidden) return;
    // 同时只开一个抽屉，否则两层遮罩会叠在一起
    window.VSProjects?.closeDrawer?.();
    drawer.hidden = false;
    document.body.classList.add("is-drawer-open");
    status("#notifStatus", "");
    refreshBadge();
    load();
  }

  function close() {
    const drawer = $("#notifDrawer");
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    document.body.classList.remove("is-drawer-open");
  }

  /* ---------------- 装配 ---------------- */

  function bind() {
    $("#railNotifications")?.addEventListener("click", () => (isOpen() ? close() : open()));
    $("#notifClose")?.addEventListener("click", close);
    $("#notifScrim")?.addEventListener("click", close);
    $("#notifReadAll")?.addEventListener("click", (event) => readAll(event.currentTarget));
    $("#notifTabAll")?.addEventListener("click", () => setTab(false));
    $("#notifTabUnread")?.addEventListener("click", () => setTab(true));
    $("#notifMore")?.addEventListener("click", () => load({ append: true }));

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && isOpen()) {
        event.stopPropagation();
        close();
      }
    }, true);

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshBadge();
    });

    // 退出登录 / 登录态失效后停止轮询
    document.addEventListener("vision:signedout", stopPolling);
  }

  bind();
  if (VS.hasToken()) {
    refreshBadge();
    startPolling();
  }

  window.VSNotifications = {
    open,
    close,
    refresh: refreshBadge,
    startPolling,
    stopPolling,
  };
})();
