// 企业空间：独立整页。
// 左栏 = 企业自身信息（配额条、公告、我的在线状态、成员列含在线点与上传数）
// 右栏 = 企业图库 / 待审核 / 操作记录 / 日报，四个 tab 共用一个面板。
// 与小组的区别：企业有角色分级、上传审核流、配额、排名、公告与留痕。
// 后端调用一律走 VS.sdk，业务代码里不出现 fetch。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, sdk, el, status, runForm, displayName, avatarNode, formatSize } = VS;

  /* ---------------- DOM ---------------- */
  const grid = $("#enterpriseGrid");
  const emptyState = $("#enterpriseEmpty");
  const entSelect = $("#enterpriseSelect");
  const idChip = $("#entIdChip");
  const badge = $("#entBadge");
  const nameText = $("#entNameText");
  const metaText = $("#entMeta");
  const quotaText = $("#entQuotaText");
  const quotaFill = $("#entQuotaFill");
  const quotaStorage = $("#entQuotaStorage");
  const quotaMembers = $("#entQuotaMembers");
  const noticeBox = $("#entNotice");
  const noticeText = $("#entNoticeText");
  const presenceDot = $("#entPresenceDot");
  const presenceText = $("#entPresenceText");
  const invisibleBox = $("#entInvisible");
  const memberList = $("#entMemberList");
  const memberCountNode = $("#entMemberCount");
  const manageBtn = $("#entManageMembers");
  const uploadBtn = $("#entUploadButton");
  const uploadInput = $("#entUploadInput");
  const resultsMeta = $("#entResultsMeta");
  const resultsBox = $("#entResults");
  const filterBox = $("#entFilter");
  const reviewBadge = $("#entReviewBadge");
  const tabs = [...document.querySelectorAll("[data-ent-tab]")];
  const reviewPane = $("#entReviewPane");
  const logPane = $("#entLogPane");
  const reportPane = $("#entReportPane");

  const createDialog = $("#enterpriseDialog");
  const createForm = $("#enterpriseForm");
  const joinDialog = $("#enterpriseJoinDialog");
  const joinForm = $("#enterpriseJoinForm");
  const settingsDialog = $("#enterpriseSettingsDialog");
  const membersDialog = $("#entMembersDialog");

  const ROLE_LABEL = { owner: "所有者", admin: "管理员", reviewer: "审核员", member: "成员", guest: "只读访客" };
  const ROLE_ORDER = { owner: 0, admin: 1, reviewer: 2, member: 3, guest: 4 };
  const RANK_TIERS = ["owner", "admin", "reviewer", "member", "guest"];
  const STORAGE_KEY = "vision_current_enterprise";

  /* ---------------- 状态 ---------------- */
  let me = null;
  let list = [];             // 我加入的企业（含 my_role）
  let current = null;        // 当前企业
  let members = [];
  let images = [];
  let reviews = [];
  let logs = [];
  let report = null;
  let reportDate = "";
  let memberFilter = "all";
  let tab = "gallery";
  let loaded = false;
  let unsupported = false;
  let invisible = false;
  let heartbeatTimer = 0;
  let entering = false;

  const thumbUrls = new Map();   // image.image_id → objectURL

  const roleOf = () => (current && current.my_role) || null;
  const rank = (role) => (role == null ? -1 : 4 - ROLE_ORDER[role]);
  const isAtLeast = (need) => rank(roleOf()) >= rank(need);
  const canStaff = () => isAtLeast("admin");
  const entId = () => (current ? current.id : null);

  /* ---------------- 工具 ---------------- */

  function when(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const pad = (n) => String(n).padStart(2, "0");
    const today = new Date();
    const sameDay = date.toDateString() === today.toDateString();
    return sameDay
      ? `${pad(date.getHours())}:${pad(date.getMinutes())}`
      : `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function entLabel(ent) {
    return ent ? `ENT-${ent.id}` : "—";
  }

  // 企业图的 id 是关联行 id，取图要用它指向的 Image id
  function thumbFor(image, node) {
    if (!image || !node) return;
    const key = image.image_id;
    const cached = thumbUrls.get(key);
    if (cached) {
      node.src = cached;
      return;
    }
    call(() => sdk.imageContent(key))
      .then(({ blob }) => {
        const url = URL.createObjectURL(blob);
        thumbUrls.set(key, url);
        if (node.isConnected) node.src = url;
      })
      .catch(() => {
        node.classList.add("is-broken");
      });
  }

  function clearThumbs() {
    thumbUrls.forEach((url) => URL.revokeObjectURL(url));
    thumbUrls.clear();
  }

  function readStoredId() {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }

  // 后端没有企业接口时，创建/加入按钮点了只会 404，直接禁掉并说明原因
  function setCreateEnabled(on) {
    ["#newEnterpriseButton", "#createFirstEnterprise", "#joinFirstEnterprise"].forEach((selector) => {
      const button = $(selector);
      if (!button) return;
      button.disabled = !on;
      button.title = on ? "" : "当前后端还没有企业接口";
    });
  }

  function storeId(id) {
    try {
      localStorage.setItem(STORAGE_KEY, String(id));
    } catch {
      /* 隐私模式忽略 */
    }
  }

  /* ---------------- 进入 / 离开 ---------------- */

  async function enter() {
    if (!loaded) {
      try {
        me = await call(() => sdk.me());
      } catch (error) {
        showEmpty("无法确认登录状态", error.message);
        return;
      }
    }
    await reload();
    loaded = true;
    startHeartbeat();
  }

  function leave() {
    stopHeartbeat();
  }

  function startHeartbeat() {
    stopHeartbeat();
    if (!current) return;
    heartbeatTimer = window.setInterval(beat, 20000);
  }

  function stopHeartbeat() {
    if (heartbeatTimer) window.clearInterval(heartbeatTimer);
    heartbeatTimer = 0;
  }

  async function beat() {
    if (!entId()) return;
    try {
      const out = await call(() => sdk.enterpriseHeartbeat(entId(), invisible));
      paintPresence(out.online, out.invisible);
      // 别人的在线状态只有这里会刷新
      await loadMembers({ quiet: true });
      renderMembers();
    } catch {
      /* 心跳失败不打扰用户 */
    }
  }

  async function reload(selectedId) {
    clearThumbs();
    let rows;
    try {
      rows = await call(() => sdk.listEnterprises());
    } catch (error) {
      const text = error && error.message ? error.message : "";
      unsupported = /404|不存在|未开放/.test(text);
      list = [];
      renderSwitch();
      setCreateEnabled(!unsupported);
      showEmpty(
        unsupported ? "当前后端还没有企业接口" : "企业数据读取失败",
        unsupported
          ? "当前连接的后端没有企业接口，可能是版本过旧。"
          : text
      );
      return;
    }

    unsupported = false;
    setCreateEnabled(true);
    list = rows || [];
    renderSwitch();

    if (!list.length) {
      current = null;
      showEmpty(
        "还没有加入企业",
        "创建一个企业，或用企业码申请加入。企业空间支持权限分级、上传审核与成员排名。"
      );
      return;
    }

    const want = String(selectedId || readStoredId() || "");
    const pick = list.find((item) => String(item.id) === want) || list[0];
    await selectEnterprise(pick.id);
  }

  async function selectEnterprise(id) {
    if (!id) return;
    const ent = list.find((item) => String(item.id) === String(id));
    if (!ent) return;
    current = ent;
    storeId(ent.id);
    memberFilter = "all";
    report = null;
    reportDate = "";
    showGrid();

    entSelect.value = String(ent.id);
    await Promise.all([loadMembers(), loadImages(), loadReviews()]);
    invisible = !!(members.find((m) => m.account_id === me?.id) || {}).invisible;
    invisibleBox.checked = invisible;
    paintPresence(!invisible, invisible);
    renderAll();
    startHeartbeat();
    // 首屏不阻塞：日志与日报进来后再补
    loadLogs().then(renderLogs).catch(() => {});
    loadReport().then(renderReport).catch(() => {});
  }

  function showGrid() {
    grid.hidden = false;
    emptyState.hidden = true;
  }

  function showEmpty(title, detail) {
    grid.hidden = true;
    emptyState.hidden = false;
    const strong = emptyState.querySelector("strong");
    const para = emptyState.querySelector("p");
    if (strong) strong.textContent = title;
    if (para) para.textContent = detail;
  }

  /* ---------------- 读数据 ---------------- */

  async function loadMembers({ quiet = false } = {}) {
    if (!entId()) return;
    try {
      members = (await call(() => sdk.listEnterpriseMembers(entId()))) || [];
    } catch (error) {
      if (!quiet) memberList.replaceChildren(el("li", "member-empty", error.message));
      return;
    }
  }

  async function loadImages() {
    if (!entId()) return;
    try {
      const page = await call(() => sdk.listEnterpriseImages(entId(), { limit: 200 }));
      images = (page && page.items) || [];
    } catch {
      images = [];
    }
  }

  async function loadReviews() {
    if (!entId()) return;
    try {
      reviews = (await call(() => sdk.listEnterpriseReview(entId()))) || [];
    } catch {
      reviews = [];
    }
  }

  async function loadLogs() {
    if (!entId()) return;
    const page = await call(() => sdk.listEnterpriseLogs(entId(), { limit: 100 }));
    logs = (page && page.items) || [];
  }

  async function loadReport() {
    if (!entId()) return;
    report = await call(() => sdk.enterpriseReport(entId(), reportDate || undefined));
    reportDate = report.date;
  }

  /* ---------------- 渲染 ---------------- */

  function renderSwitch() {
    entSelect.replaceChildren();
    if (!list.length) {
      entSelect.append(new Option(unsupported ? "后端未开放企业功能" : "还没有企业", ""));
      return;
    }
    list.forEach((ent) => {
      entSelect.append(new Option(`${ent.name} · ${ROLE_LABEL[ent.my_role] || "成员"}`, String(ent.id)));
    });
    entSelect.value = current ? String(current.id) : String(list[0].id);
  }

  function renderAll() {
    renderHead();
    renderQuota();
    renderNotice();
    renderMembers();
    renderResults();
    renderReview();
    renderLogs();
    renderReport();
    renderTabs();
  }

  function renderHead() {
    if (!current) return;
    const role = roleOf();
    nameText.textContent = current.name;
    badge.textContent = (current.name || "企").trim()[0] || "企";
    metaText.textContent = `${ROLE_LABEL[role] || "成员"} · ${current.member_count} 名成员 · ${current.image_count} 项成果`;
    idChip.hidden = false;
    idChip.textContent = entLabel(current);
    idChip.title = `企业 ID ${entLabel(current)} · 企业码 ${current.code}`;
    manageBtn.hidden = !canStaff();

    const root = document.documentElement;
    root.classList.add("has-enterprise");
    if (window.VSBrand) window.VSBrand.setEnterprise(current);
  }

  function renderQuota() {
    if (!current) return;
    const used = current.image_count || 0;
    const quota = current.quota_images || 0;
    const percent = quota ? Math.min(100, Math.round((used / quota) * 100)) : 0;
    quotaText.textContent = `${used} / ${quota} 张`;
    // 配额很大时百分比会四舍五入成 0，条子完全看不见，看起来像没生效。
    // 只要用过图，就保底画一条 6px 的可见线。
    quotaFill.style.width = used > 0 ? `max(${percent}%, 6px)` : "0%";
    quotaFill.classList.toggle("is-warn", percent >= 80 && percent < 95);
    quotaFill.classList.toggle("is-full", percent >= 95);
    // formatSize(0) 会显示 1 KB，新企业不该出现这种「凭空占了一点空间」的观感
    quotaStorage.textContent = current.used_bytes ? `已用 ${formatSize(current.used_bytes)}` : "已用 0 KB";
    quotaMembers.textContent = `成员 ${current.member_count} / ${current.quota_members}`;
  }

  function renderNotice() {
    const text = (current && current.notice_text) || "";
    noticeBox.hidden = !text;
    noticeText.textContent = text;
  }

  function paintPresence(online, hidden) {
    presenceDot.classList.toggle("is-online", !!online && !hidden);
    presenceDot.classList.toggle("is-hidden", !!hidden);
    presenceText.textContent = hidden ? "隐身中 · 其他成员看到你离线" : online ? "在线 · 成员能看到你" : "离线";
  }

  function renderMembers() {
    memberList.replaceChildren();
    if (!members.length) {
      memberList.append(el("li", "member-empty", "还没有成员"));
      return;
    }

    // 上传数前三名给徽章（0 张不占名额）
    const ranked = members
      .filter((m) => m.upload_count > 0)
      .slice()
      .sort((a, b) => b.upload_count - a.upload_count)
      .slice(0, 3);
    const medal = new Map(ranked.map((m, index) => [m.account_id, index + 1]));

    members
      .slice()
      .sort((a, b) => (ROLE_ORDER[a.role] - ROLE_ORDER[b.role]) || (b.upload_count - a.upload_count))
      .forEach((member) => {
        const account = member.account || { username: `user_${member.account_id}` };
        const row = el("li", "member-line ent-member-line");
        const filtering = memberFilter === String(member.account_id);
        if (filtering) row.classList.add("is-filtering");
        if (member.account_id === me?.id) row.classList.add("is-me");

        const button = el("button", "member-line-button");
        button.type = "button";
        button.setAttribute("aria-pressed", String(filtering));
        button.title = member.upload_count
          ? `只看 ${displayName(account)} 上传的 ${member.upload_count} 张`
          : `${displayName(account)} 还没有上传图片`;

        const dot = el("span", "presence-dot");
        dot.classList.toggle("is-online", !!member.online);
        if (member.online) row.classList.add("is-online");
        if (member.invisible && member.account_id === me?.id) dot.classList.add("is-hidden");

        const avatarWrap = el("span", "member-avatar-wrap");
        avatarWrap.append(avatarNode(account, 28));

        const copy = el("span", "member-line-copy");
        const nameLine = el("span", "member-line-name");
        nameLine.append(el("strong", "", displayName(account)));
        const tier = medal.get(member.account_id);
        if (tier) nameLine.append(el("em", `rank-medal rank-${tier}`, String(tier)));
        if (member.role !== "member") {
          nameLine.append(el("em", `role-tag role-${member.role}`, ROLE_LABEL[member.role] || member.role));
        }
        if (member.account_id === me?.id) nameLine.append(el("em", "role-tag role-me", "我"));
        copy.append(nameLine, el("small", "", `@${account.username || "—"}`));

        const count = el("span", "member-line-count");
        count.append(el("b", "", String(member.upload_count || 0)), el("small", "", "张"));

        button.append(dot, avatarWrap, copy, count);
        button.addEventListener("click", () => {
          memberFilter = filtering ? "all" : String(member.account_id);
          if (tab !== "gallery") setTab("gallery");
          renderMembers();
          renderResults();
        });
        row.append(button);
        memberList.append(row);
      });

    memberCountNode.textContent = members.length;
  }

  function renderFilter() {
    const uploaders = new Map();
    images.forEach((image) => {
      const id = image.uploader_account_id;
      uploaders.set(id, (uploaders.get(id) || 0) + 1);
    });
    filterBox.replaceChildren();
    if (uploaders.size <= 1) {
      filterBox.hidden = true;
      return;
    }
    filterBox.hidden = false;

    const chips = [{ id: "all", label: "全部", count: images.length }];
    uploaders.forEach((count, id) => {
      const account = (members.find((m) => m.account_id === id) || {}).account;
      chips.push({ id: String(id), label: account ? displayName(account) : `用户 ${id}`, count });
    });

    chips.forEach((chip) => {
      const button = el("button", "filter-chip", chip.label);
      button.type = "button";
      button.append(el("b", "", String(chip.count)));
      if (memberFilter === chip.id) button.classList.add("is-active");
      button.addEventListener("click", () => {
        memberFilter = chip.id;
        renderMembers();
        renderResults();
      });
      filterBox.append(button);
    });
  }

  function renderResults() {
    if (!current) return;
    if (tab !== "gallery") return;
    renderFilter();

    const filtered = memberFilter === "all"
      ? images
      : images.filter((image) => String(image.uploader_account_id) === memberFilter);

    const who = memberFilter === "all"
      ? ""
      : ` · 只看 ${displayName((members.find((m) => String(m.account_id) === memberFilter) || {}).account || {})}`;
    resultsMeta.textContent = `${filtered.length} 项成果${who}`;
    resultsBox.replaceChildren();

    if (!filtered.length) {
      const empty = el("div", "empty-state ent-empty-inline");
      empty.append(
        el("strong", "", memberFilter === "all" ? "还没有企业成果" : "这位成员还没有上传"),
        el("p", "", memberFilter === "all" ? "点右上角「上传图片」，成员上传的图片会进入审核队列。" : "换个人看看，或点左侧成员取消筛选。")
      );
      resultsBox.append(empty);
      return;
    }

    filtered.forEach((image) => {
      const tile = el("button", "result-tile");
      tile.type = "button";
      const thumb = el("img", "result-thumb");
      thumb.alt = "";
      const name = el("span", "result-name", image.filename);
      const up = members.find((m) => m.account_id === image.uploader_account_id);
      const uploader = el("span", "result-uploader", up ? displayName(up.account) : `用户 ${image.uploader_account_id}`);
      tile.append(thumb, name, uploader);

      if (image.review_status === "pending") tile.append(el("span", "review-flag is-pending", "待审"));
      if (image.review_status === "rejected") tile.append(el("span", "review-flag is-rejected", "已驳回"));

      tile.addEventListener("click", () => {
        // 企业图的删除接口与个人图不同（/enterprises/{id}/images/{iid}），
        // 详情页那个删除按钮走的是个人图端点，点下去只会 404，所以这里不给它。
        window.VSApp?.openImageDetail(image, {
          source: "enterprise",
          analysisImageId: Number(image.image_id),
          canDelete: false,
        });
      });
      resultsBox.append(tile);
      thumbFor(image, thumb);
    });
  }

  function renderReview() {
    const pending = current ? current.review_pending || 0 : 0;
    reviewBadge.hidden = pending === 0;
    reviewBadge.textContent = String(pending);
    if (tab !== "review") return;

    reviewPane.replaceChildren();
    if (!isAtLeast("reviewer")) {
      reviewPane.append(el("div", "empty-state ent-empty-inline", ""));
      reviewPane.firstChild.append(
        el("strong", "", "你没有审核权限"),
        el("p", "", "只有审核员及以上角色可以审批企业图片。")
      );
      return;
    }
    if (!reviews.length) {
      const done = el("div", "empty-state ent-empty-inline");
      done.append(el("strong", "", "没有待审图片"), el("p", "", "成员上传后，图片会出现在这里等待审批。"));
      reviewPane.append(done);
      return;
    }

    reviews.forEach((image) => {
      const row = el("article", "review-row");
      const thumb = el("img", "review-thumb");
      thumb.alt = "";
      const up = members.find((m) => m.account_id === image.uploader_account_id);
      const copy = el("div", "review-copy");
      copy.append(
        el("strong", "", image.filename),
        el("small", "", `${up ? displayName(up.account) : `用户 ${image.uploader_account_id}`} · ${when(image.created_at)} · ${image.width || "?"} × ${image.height || "?"}`)
      );
      const actions = el("div", "review-actions");
      const pass = el("button", "mini-button is-pass", "通过");
      const drop = el("button", "mini-button is-reject", "驳回");
      const note = el("button", "mini-button", "查看");
      pass.type = "button";
      drop.type = "button";
      note.type = "button";
      pass.addEventListener("click", () => decide(image, "approve", pass));
      drop.addEventListener("click", () => decide(image, "reject", drop));
      note.addEventListener("click", () => window.VSApp?.openImageDetail(image, { source: "enterprise", analysisImageId: Number(image.image_id), canDelete: false }));
      actions.append(note, drop, pass);
      row.append(thumb, copy, actions);
      reviewPane.append(row);
      thumbFor(image, thumb);
    });
  }

  async function decide(image, decision, button) {
    if (button) button.disabled = true;
    try {
      await call(() => sdk.reviewEnterpriseImage(entId(), image.image_id, decision));
      await refreshAfterChange();
    } catch (error) {
      status("#entResultsMeta", error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function renderLogs() {
    if (tab !== "logs") return;
    logPane.replaceChildren();
    if (!logs.length) {
      logPane.append(el("div", "empty-state ent-empty-inline", "还没有操作记录"));
      return;
    }
    const list = el("ol", "log-list");
    logs.forEach((row) => {
      const item = el("li", "log-item");
      const dot = el("span", "log-dot");
      const body = el("div", "log-body");
      body.append(
        el("strong", "", row.detail || row.action),
        el("small", "", `${row.actor_name || `用户 ${row.actor_account_id}`} · ${when(row.created_at)}`)
      );
      item.append(dot, body);
      list.append(item);
    });
    logPane.append(list);
  }

  function renderReport() {
    if (tab !== "report") return;
    reportPane.replaceChildren();
    if (!report) {
      reportPane.append(el("div", "empty-state ent-empty-inline", "正在生成日报…"));
      return;
    }

    const head = el("div", "report-head");
    const dateInput = el("input", "report-date");
    dateInput.type = "date";
    dateInput.value = report.date || new Date().toISOString().slice(0, 10);
    dateInput.addEventListener("change", async () => {
      reportDate = dateInput.value;
      try {
        report = await call(() => sdk.enterpriseReport(entId(), reportDate));
        renderReport();
      } catch (error) {
        status("#entResultsMeta", error.message, true);
      }
    });
    const exportBtn = el("button", "secondary-button", "导出 CSV");
    exportBtn.type = "button";
    exportBtn.addEventListener("click", exportReport);
    head.append(dateInput, exportBtn);

    const stats = el("div", "report-stats");
    const cells = [
      { label: "上传", value: report.uploads, tone: "" },
      { label: "通过", value: report.approved, tone: "is-pass" },
      { label: "驳回", value: report.rejected, tone: "is-reject" },
      { label: "待审", value: report.pending, tone: "is-pending" },
      { label: "通过率", value: report.pass_rate == null ? "—" : `${report.pass_rate}%`, tone: "" },
      { label: "活跃成员", value: report.active_members, tone: "" },
    ];
    cells.forEach((cell) => {
      const box = el("div", `report-cell ${cell.tone}`);
      box.append(el("b", "", String(cell.value)), el("small", "", cell.label));
      stats.append(box);
    });

    const table = el("table", "report-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["成员", "上传", "通过", "驳回", "待审"].forEach((label) => headRow.append(el("th", "", label)));
    thead.append(headRow);
    const tbody = document.createElement("tbody");
    if (!report.by_member.length) {
      const tr = document.createElement("tr");
      const td = el("td", "", "这一天还没有上传");
      td.colSpan = 5;
      tr.append(td);
      tbody.append(tr);
    } else {
      report.by_member.forEach((row) => {
        const tr = document.createElement("tr");
        tr.append(
          el("td", "", row.username),
          el("td", "", String(row.uploads)),
          el("td", "is-pass", String(row.approved)),
          el("td", "is-reject", String(row.rejected)),
          el("td", "is-pending", String(row.pending))
        );
        tbody.append(tr);
      });
    }
    table.append(thead, tbody);
    reportPane.append(head, stats, table);
  }

  function exportReport() {
    if (!report) return;
    const lines = [["日期", report.date], ["上传数", report.uploads], ["通过", report.approved],
      ["驳回", report.rejected], ["待审", report.pending], ["通过率", report.pass_rate == null ? "-" : `${report.pass_rate}%`], [],
      ["成员", "上传", "通过", "驳回", "待审"]];
    report.by_member.forEach((row) => {
      lines.push([row.username, row.uploads, row.approved, row.rejected, row.pending]);
    });
    const csv = lines.map((row) => row.map((cell) => `"${String(cell ?? "")}"`).join(",")).join("\r\n");
    const blob = new Blob([`\ufeff${csv}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${current.name}-日报-${report.date}.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 4000);
  }

  function renderTabs() {
    tabs.forEach((button) => {
      const active = button.dataset.entTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    // 标题跟着 tab 走，否则切到审核还写着「企业图库」会很怪
    const titles = { gallery: "企业图库", review: "待审核", logs: "操作记录", report: "企业日报" };
    const paneTitle = $("#entPaneTitle");
    if (paneTitle) paneTitle.textContent = titles[tab] || "企业图库";
    if (tab === "review") resultsMeta.textContent = `${reviews.length} 张等待审批`;
    if (tab === "logs") resultsMeta.textContent = `最近 ${logs.length} 条`;
    if (tab === "report") resultsMeta.textContent = report ? `${report.date} · 上传 ${report.uploads} 张` : "正在生成…";

    resultsBox.hidden = tab !== "gallery";
    filterBox.hidden = tab !== "gallery" || filterBox.dataset.empty === "1";
    reviewPane.hidden = tab !== "review";
    logPane.hidden = tab !== "logs";
    reportPane.hidden = tab !== "report";
  }

  function setTab(next) {
    tab = next;
    renderTabs();
    if (tab === "gallery") renderResults();
    if (tab === "review") renderReview();
    if (tab === "logs") {
      loadLogs().then(renderLogs).catch(() => {});
    }
    if (tab === "report") {
      if (!report) loadReport().then(renderReport).catch(() => {});
      else renderReport();
    }
  }

  /* ---------------- 写操作 ---------------- */

  async function refreshAfterChange() {
    if (!entId()) return;
    const fresh = await call(() => sdk.getEnterprise(entId()));
    current = fresh;
    const index = list.findIndex((item) => item.id === fresh.id);
    if (index >= 0) list[index] = fresh;
    await Promise.all([loadMembers(), loadImages(), loadReviews()]);
    renderSwitch();
    renderAll();
  }

  /* ---------------- 事件 ---------------- */

  entSelect.addEventListener("change", () => {
    selectEnterprise(entSelect.value).catch((error) => status("#entResultsMeta", error.message, true));
  });

  tabs.forEach((button) => {
    button.addEventListener("click", () => setTab(button.dataset.entTab));
  });

  invisibleBox.addEventListener("change", async () => {
    invisible = invisibleBox.checked;
    paintPresence(!invisible, invisible);
    try {
      const out = await call(() => sdk.enterpriseHeartbeat(entId(), invisible));
      paintPresence(out.online, out.invisible);
      await loadMembers({ quiet: true });
      renderMembers();
    } catch (error) {
      status("#entResultsMeta", error.message, true);
    }
  });

  uploadBtn.addEventListener("click", () => uploadInput.click());
  uploadInput.addEventListener("change", async () => {
    const files = [...uploadInput.files];
    uploadInput.value = "";
    if (!files.length) return;
    uploadBtn.disabled = true;
    const original = uploadBtn.textContent;
    uploadBtn.textContent = "上传中…";
    try {
      await call(() => sdk.uploadEnterpriseImages(entId(), files));
      await refreshAfterChange();
      uploadBtn.textContent = `已上传 ${files.length} 张`;
      window.setTimeout(() => { uploadBtn.textContent = original; }, 1600);
    } catch (error) {
      status("#entResultsMeta", error.message, true);
      uploadBtn.textContent = original;
    } finally {
      uploadBtn.disabled = false;
    }
  });

  /* ---------------- 创建企业 ---------------- */

  function openCreate() {
    createForm.reset();
    status("#enterpriseStatus", "");
    openDialog(createDialog);
    window.setTimeout(() => $("#enterpriseName")?.focus(), 120);
  }

  createForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = createForm.querySelector('button[type="submit"]');
    const name = $("#enterpriseName").value.trim();
    const email = $("#enterpriseEmail").value.trim();
    if (name.length < 2) {
      status("#enterpriseStatus", "企业名称至少 2 个字", true);
      return;
    }
    if (!email.includes("@")) {
      status("#enterpriseStatus", "请填写企业邮箱", true);
      return;
    }
    runForm(button, "#enterpriseStatus", async () => {
      const created = await call(() => sdk.createEnterprise(name, email));
      closeDialog(createDialog);
      await reload(created.id);
      status("#entResultsMeta", `已创建「${created.name}」，企业码 ${created.code}`);
    });
  });

  /* ---------------- 加入企业 ---------------- */

  function openJoin() {
    joinForm.reset();
    status("#enterpriseJoinStatus", "");
    openDialog(joinDialog);
    window.setTimeout(() => $("#enterpriseCode")?.focus(), 120);
  }

  joinForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = joinForm.querySelector('button[type="submit"]');
    const code = $("#enterpriseCode").value.trim().toUpperCase();
    if (!code) {
      status("#enterpriseJoinStatus", "请填写企业码", true);
      return;
    }
    runForm(button, "#enterpriseJoinStatus", async () => {
      const out = await call(() => sdk.joinEnterprise(code, $("#enterpriseMessage").value.trim()));
      if (out.status === "pending") {
        status("#enterpriseJoinStatus", "申请已提交，等待企业管理员审批");
        return;
      }
      closeDialog(joinDialog);
      await reload(out.enterprise && out.enterprise.id);
      status("#entResultsMeta", "已加入企业");
    });
  });

  /* ---------------- 设置弹窗 ---------------- */

  function openSettings() {
    if (!current) return;
    $("#entCodeValue").textContent = current.code;
    $("#entInviteName").value = "";
    $("#entInviteRole").value = "member";
    status("#entInviteStatus", "");
    $("#entQuotaImages").value = String(current.quota_images || 0);
    $("#entQuotaMembersInput").value = String(current.quota_members || 0);
    $("#entAutoApprove").checked = !!current.auto_approve_staff;
    $("#entOpenJoin").checked = current.join_mode === "open";
    $("#entNoticeInput").value = current.notice_text || "";
    status("#entNoticeStatus", "");
    status("#entQuotaStatus", "");

    const staff = canStaff();
    const owner = roleOf() === "owner";
    // 只有所有者和管理员能邀请/发公告；配额只有所有者能改
    $("#entInviteForm").hidden = !staff;
    $("#entNoticeForm").hidden = !staff;
    const quotaForm = $("#entQuotaForm");
    quotaForm.hidden = !staff;
    [...quotaForm.querySelectorAll("input")].forEach((input) => { input.disabled = !owner; });
    $("#saveEntQuota").hidden = !owner;

    $("#entDissolveButton").hidden = !owner;
    $("#entLeaveButton").hidden = owner;
    $("#entDangerHint").textContent = owner
      ? "所有者不能直接退出。可以先把企业转交他人，或解散企业（不可恢复）。"
      : "退出后，你上传的图片会留在企业空间。";

    openDialog(settingsDialog);
    loadRequests();
  }

  async function loadRequests() {
    const block = $("#entRequestsBlock");
    if (!canStaff()) {
      block.hidden = true;
      return;
    }
    const box = $("#entRequestList");
    box.replaceChildren(el("p", "settings-help", "正在读取申请…"));
    try {
      const rows = await call(() => sdk.listEnterpriseRequests(entId()));
      if (Array.isArray(rows)) {
        $("#entRequestCount").textContent = String(rows.length);
        block.hidden = rows.length === 0;
        box.replaceChildren();
        if (!rows.length) return;
        rows.forEach((row) => {
          const item = el("div", "ent-request");
          const copy = el("div", "ent-request-copy");
          copy.append(
            el("strong", "", displayName(row.account || {})),
            el("small", "", row.message || "没有留言"),
            el("small", "ent-request-time", when(row.applied_at))
          );
          const actions = el("div", "review-actions");
          const pass = el("button", "mini-button is-pass", "通过");
          const drop = el("button", "mini-button is-reject", "拒绝");
          pass.type = "button";
          drop.type = "button";
          pass.addEventListener("click", () => decideRequest(row.account_id, "approve", pass));
          drop.addEventListener("click", () => decideRequest(row.account_id, "reject", drop));
          actions.append(drop, pass);
          item.append(copy, actions);
          box.append(item);
        });
      }
    } catch (error) {
      block.hidden = false;
      box.replaceChildren(el("p", "settings-help", error.message));
    }
  }

  async function decideRequest(accountId, decision, button) {
    if (button) button.disabled = true;
    try {
      await call(() => sdk.decideEnterpriseRequest(entId(), accountId, decision));
      await refreshAfterChange();
      await loadRequests();
    } catch (error) {
      status("#entInviteStatus", error.message, true);
    } finally {
      if (button) button.disabled = false;
    }
  }

  $("#entInviteForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = $("#saveEntInvite");
    const username = $("#entInviteName").value.trim();
    if (!username) {
      status("#entInviteStatus", "请填写用户名", true);
      return;
    }
    runForm(button, "#entInviteStatus", async () => {
      await call(() => sdk.inviteEnterpriseMember(entId(), username, $("#entInviteRole").value));
      $("#entInviteName").value = "";
      await refreshAfterChange();
      status("#entInviteStatus", `已邀请 ${username}`);
    });
  });

  $("#entQuotaForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = $("#saveEntQuota");
    runForm(button, "#entQuotaStatus", async () => {
      await call(() => sdk.updateEnterprise(entId(), {
        quota_images: Number($("#entQuotaImages").value) || undefined,
        quota_members: Number($("#entQuotaMembersInput").value) || undefined,
        auto_approve_staff: $("#entAutoApprove").checked,
        join_mode: $("#entOpenJoin").checked ? "open" : "approval",
      }));
      await refreshAfterChange();
      status("#entQuotaStatus", "已保存");
    });
  });

  $("#entNoticeForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = $("#saveEntNotice");
    runForm(button, "#entNoticeStatus", async () => {
      await call(() => sdk.updateEnterprise(entId(), { notice_text: $("#entNoticeInput").value.trim() || null }));
      await refreshAfterChange();
      status("#entNoticeStatus", "已发布");
    });
  });

  $("#copyEntCode").addEventListener("click", async () => {
    const code = current ? current.code : "";
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      status("#entInviteStatus", `已复制 ${code}`);
    } catch {
      // 无剪贴板权限时退回选中
      const range = document.createRange();
      range.selectNodeContents($("#entCodeValue"));
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status("#entInviteStatus", "已选中企业码，按 Ctrl+C 复制");
    }
  });

  $("#entLeaveButton").addEventListener("click", async () => {
    if (!current) return;
    closeDialog(settingsDialog);
    const ok = await window.confirmDanger({
      kicker: "LEAVE",
      title: "退出这个企业？",
      message: `退出「${current.name}」后，你上传的图片会留在企业空间，但不再能看到新内容。`,
      detail: "想重新加入需要再次申请。",
      confirmText: "退出企业",
    });
    if (!ok) return;
    try {
      await call(() => sdk.leaveEnterprise(entId()));
      await reload();
    } catch (error) {
      status("#entResultsMeta", error.message, true);
    }
  });

  $("#entDissolveButton").addEventListener("click", async () => {
    if (!current) return;
    const name = current.name;
    const id = entId();
    const summary = `企业 ID ${entLabel({ id })} · ${current.member_count} 名成员 · ${current.image_count} 项成果`;
    closeDialog(settingsDialog);
    const ok = await window.confirmDanger({
      kicker: "DISSOLVE",
      title: "解散整个企业？",
      message: `「${name}」的企业空间、成员关系、审核记录都会被清空，且无法恢复。`,
      detail: summary,
      confirmText: "解散企业",
    });
    if (!ok) return;
    try {
      await call(() => sdk.deleteEnterprise(id));
      await reload();
    } catch (error) {
      status("#entResultsMeta", error.message, true);
    }
  });

  /* ---------------- 成员管理弹窗 ---------------- */

  function openMembers() {
    if (!current) return;
    status("#entMemberStatus", "");
    const box = $("#entMemberManage");
    box.replaceChildren();
    members.forEach((member) => {
      const row = el("div", "ent-member-manage-row");
      const copy = el("div", "ent-member-manage-copy");
      copy.append(
        el("strong", "", displayName(member.account || {})),
        el("small", "", `@${(member.account || {}).username || "—"} · 上传 ${member.upload_count} 张`)
      );
      row.append(copy);

      if (member.role === "owner" || member.account_id === me?.id) {
        row.append(el("span", "role-tag", ROLE_LABEL[member.role] || member.role));
      } else {
        const select = document.createElement("select");
        select.className = "ent-role-select";
        RANK_TIERS.forEach((role) => {
          if (role === "owner") return;
          select.append(new Option(ROLE_LABEL[role], role, false, role === member.role));
        });
        select.addEventListener("change", async () => {
          select.disabled = true;
          try {
            await call(() => sdk.updateEnterpriseMember(entId(), member.account_id, { role: select.value }));
            await refreshAfterChange();
            status("#entMemberStatus", `已把 ${displayName(member.account || {})} 改为${ROLE_LABEL[select.value]}`);
            openMembers();
          } catch (error) {
            status("#entMemberStatus", error.message, true);
          } finally {
            select.disabled = false;
          }
        });
        const remove = el("button", "mini-button is-reject", "移除");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          closeDialog(membersDialog);
          const ok = await window.confirmDanger({
            kicker: "REMOVE",
            title: "移除这名成员？",
            message: `${displayName(member.account || {})} 将无法再进入企业空间。`,
            detail: "已上传的图片会保留在企业图库。",
            confirmText: "移除成员",
          });
          if (!ok) return;
          try {
            await call(() => sdk.removeEnterpriseMember(entId(), member.account_id));
            await refreshAfterChange();
          } catch (error) {
            status("#entMemberStatus", error.message, true);
          }
        });
        row.append(select, remove);
      }
      box.append(row);
    });
    openDialog(membersDialog);
  }

  /* ---------------- 对外接口 ---------------- */

  $("#closeEnterpriseDialog").addEventListener("click", () => closeDialog(createDialog));
  $("#closeEnterpriseJoin").addEventListener("click", () => closeDialog(joinDialog));
  $("#closeEnterpriseSettings").addEventListener("click", () => closeDialog(settingsDialog));
  $("#closeEntMembers").addEventListener("click", () => closeDialog(membersDialog));
  $("#newEnterpriseButton").addEventListener("click", openCreate);
  $("#createFirstEnterprise").addEventListener("click", openCreate);
  $("#joinFirstEnterprise").addEventListener("click", openJoin);
  $("#entSettingsButton").addEventListener("click", openSettings);
  $("#entManageMembers").addEventListener("click", openMembers);

  // app.js 在改资料 / 删图后让这里跟着刷新
  window.VSTeamEnterprise = {
    enter,
    leave,
    openCreate,
    openJoin,
    /** 给「上传目标询问」用：我加入的企业列表 */
    myEnterprises: () => list,
    /** 把文件直接写进当前企业图库，返回上传结果 */
    async uploadFiles(files) {
      if (!current) throw new Error("还没有进入企业空间");
      const out = await call(() => sdk.uploadEnterpriseImages(current.id, files));
      await refreshAfterChange();
      return out;
    },
    refresh() {
      if (current) refreshAfterChange().catch(() => {});
    },
  };
})();
