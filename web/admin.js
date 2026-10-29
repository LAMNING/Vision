// 平台管理：概览、账号与平台角色、小组 / 项目、审计日志、存储与模型配置。
// 只有 platform_role=admin 能进（入口隐藏 + 进页再校验一次）。
// 后端调用一律走 VS.sdk，业务代码里不出现 fetch。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, sdk, el, status, runForm, displayName, avatarNode } = VS;

  const PAGE = 50;
  const ROLE_LABEL = { user: "普通用户", admin: "平台管理员" };
  const GROUP_TYPE_LABEL = { personal: "个人空间", team: "小组", org: "组织" };

  /* ---------------- DOM ---------------- */
  const shell = $("#adminShell");
  const denied = $("#adminDenied");
  const statsBox = $("#adminStats");
  const tabs = [...document.querySelectorAll("[data-admin-tab]")];
  const panes = [...document.querySelectorAll("[data-admin-pane]")];
  const refreshButton = $("#adminRefresh");

  const accountRows = $("#adminAccountRows");
  const accountPager = $("#adminAccountPager");
  const accountMeta = $("#adminAccountsMeta");
  const groupRows = $("#adminGroupRows");
  const groupPager = $("#adminGroupPager");
  const groupMeta = $("#adminGroupsMeta");
  const projectRows = $("#adminProjectRows");
  const projectPager = $("#adminProjectPager");
  const projectMeta = $("#adminProjectsMeta");
  const auditRows = $("#adminAuditRows");
  const auditPager = $("#adminAuditPager");
  const auditMeta = $("#adminAuditMeta");

  const passwordDialog = $("#adminPasswordDialog");
  const passwordForm = $("#adminPasswordForm");

  /* ---------------- 状态 ---------------- */
  let me = null;
  let tab = "overview";
  let authorized = false;

  const state = {
    accounts: { offset: 0, total: 0, search: "", role: "" },
    groups: { offset: 0, total: 0, search: "" },
    projects: { offset: 0, total: 0, search: "" },
    audit: { offset: 0, total: 0, prefix: "" },
  };
  const loaded = { overview: false, accounts: false, spaces: false, audit: false, settings: false };

  /* ---------------- 工具 ---------------- */

  function when(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function flag(text, on) {
    return el("span", `admin-flag ${on ? "is-on" : "is-off"}`, text);
  }

  function fact(label, value, tone) {
    const row = el("div", "admin-fact");
    row.append(el("span", "", label), el("strong", tone || "", value));
    return row;
  }

  function renderFacts(node, rows) {
    if (!node) return;
    node.replaceChildren(...rows);
  }

  function renderPager(node, view, reload) {
    node.replaceChildren();
    const from = view.total ? view.offset + 1 : 0;
    const to = Math.min(view.offset + PAGE, view.total);
    node.append(el("span", "", `第 ${from}–${to} 条 · 共 ${view.total} 条`));

    const actions = el("span", "admin-pager-actions");
    const prev = el("button", "mini-button", "上一页");
    const next = el("button", "mini-button", "下一页");
    prev.type = "button";
    next.type = "button";
    prev.disabled = view.offset <= 0;
    next.disabled = view.offset + PAGE >= view.total;
    prev.addEventListener("click", () => {
      view.offset = Math.max(0, view.offset - PAGE);
      reload();
    });
    next.addEventListener("click", () => {
      view.offset += PAGE;
      reload();
    });
    actions.append(prev, next);
    node.append(actions);
  }

  /* ---------------- 进入 / 拒绝 ---------------- */

  async function enter() {
    if (!me) {
      try {
        me = await call(() => sdk.me());
      } catch (error) {
        showDenied(error.message);
        return;
      }
    }
    if (!me || me.platform_role !== "admin") {
      showDenied();
      return;
    }

    authorized = true;
    denied.hidden = true;
    shell.hidden = false;
    switchTab(tab, { force: !loaded[tab] });
  }

  function showDenied(detail) {
    authorized = false;
    shell.hidden = true;
    denied.hidden = false;
    if (detail) {
      const p = denied.querySelector("p");
      if (p) p.textContent = detail;
    }
  }

  /* ---------------- tab ---------------- */

  async function switchTab(next, { force = false } = {}) {
    if (!authorized) return;
    tab = next;
    tabs.forEach((button) => {
      const active = button.dataset.adminTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    panes.forEach((pane) => {
      pane.hidden = pane.dataset.adminPane !== tab;
    });
    if (!force && loaded[tab]) return;

    try {
      if (tab === "overview") await loadOverview();
      if (tab === "accounts") await loadAccounts();
      if (tab === "spaces") {
        await Promise.all([loadGroups(), loadProjects()]);
      }
      if (tab === "audit") await loadAudit();
      if (tab === "settings") await loadSettings();
      loaded[tab] = true;
    } catch (error) {
      status("#adminStatus", `读取失败：${error.message}`, true);
    }
  }

  /* ---------------- 概览 ---------------- */

  const STAT_CELLS = [
    ["accounts", "账号"],
    ["admins", "平台管理员"],
    ["groups", "小组"],
    ["projects", "项目"],
    ["images", "图片"],
    ["tasks", "任务"],
    ["annotations", "标注"],
    ["pending_invitations", "待处理邀请"],
  ];

  async function loadOverview() {
    const [stats, policy, model] = await Promise.all([
      call(() => sdk.adminStats()),
      call(() => sdk.getStoragePolicy()),
      call(() => sdk.getModelConfig()),
    ]);
    renderStats(stats);
    fillStoragePolicy(policy);
    fillModelConfig(model);
  }

  function renderStats(stats) {
    statsBox.replaceChildren();
    STAT_CELLS.forEach(([key, label]) => {
      const cell = el("article", "admin-stat");
      cell.append(el("b", "", String(stats[key] ?? 0)), el("small", "", label));
      statsBox.append(cell);
    });
  }

  function storageFacts(policy) {
    return [
      fact("生效 URI", policy.effective_uri || "—"),
      fact("存储后端", policy.backend || "—"),
      fact("上次更新", when(policy.updated_at)),
    ];
  }

  function modelFacts(model) {
    return [
      fact("检测模型", model.effective_detector_path || "未配置", model.detector_configured ? "" : "is-warn"),
      fact(
        "精度 / 设备",
        `${model.effective_detector_precision || "—"} · ${model.effective_detector_device || "—"}`
      ),
      fact("输入尺寸", `${model.effective_detector_size} / ${model.effective_detector_max_size}`),
      fact(
        "翻译模型",
        model.translator_configured ? model.effective_translator_dir || "已配置" : "未配置",
        model.translator_configured ? "" : "is-warn"
      ),
      fact("运行状态", model.detector_loaded ? "检测模型已载入" : "尚未载入（首次使用时加载）"),
    ];
  }

  function fillStoragePolicy(policy) {
    $("#adminStorageSub").textContent = `${policy.backend || "—"} · ${policy.effective_uri || "—"}`;
    renderFacts($("#adminStorageFacts"), storageFacts(policy));
    renderFacts($("#adminStorageFormFacts"), storageFacts(policy));
    $("#adminStorageUri").value = policy.storage_uri || "";
    $("#adminTeamQuotaImages").value = String(policy.team_quota_images ?? 0);
    $("#adminTeamQuotaStorage").value = String(policy.team_quota_storage_mb ?? 0);
  }

  function fillModelConfig(model) {
    $("#adminModelSub").textContent = model.detector_configured
      ? `${model.effective_detector_precision || "—"} · ${model.effective_detector_device || "—"}`
      : "检测模型未配置";
    renderFacts($("#adminModelFacts"), modelFacts(model));
    renderFacts($("#adminModelFormFacts"), modelFacts(model));

    $("#adminDetectorOnnx").value = model.detector_onnx || "";
    $("#adminDetectorDir").value = model.detector_dir || "";
    $("#adminDetectorPrecision").value = model.detector_precision || "";
    $("#adminDetectorDevice").value = model.detector_device || "";
    $("#adminDetectorSize").value = model.detector_size == null ? "" : String(model.detector_size);
    $("#adminDetectorMaxSize").value =
      model.detector_max_size == null ? "" : String(model.detector_max_size);
    $("#adminTranslatorDir").value = model.translator_dir || "";
    $("#adminTranslatorPrecision").value = model.translator_precision || "";
  }

  /* ---------------- 账号 ---------------- */

  async function loadAccounts() {
    const loading = el("tr");
    const loadingCell = el("td", "admin-empty", "正在读取账号…");
    loadingCell.colSpan = 6;
    loading.append(loadingCell);
    accountRows.replaceChildren(loading);
    status("#adminAccountsStatus", "");
    const page = await call(() =>
      sdk.adminAccounts({
        search: state.accounts.search || undefined,
        platformRole: state.accounts.role || undefined,
        offset: state.accounts.offset,
        limit: PAGE,
      })
    );
    state.accounts.total = page.total;
    accountMeta.textContent = `共 ${page.total} 个账号`;
    renderAccounts(page.items || []);
    renderPager(accountPager, state.accounts, loadAccounts);
  }

  function renderAccounts(rows) {
    accountRows.replaceChildren();
    if (!rows.length) {
      const tr = el("tr");
      const td = el("td", "admin-empty", "没有匹配的账号");
      td.colSpan = 6;
      tr.append(td);
      accountRows.append(tr);
      return;
    }
    rows.forEach((account) => {
      const tr = el("tr");
      tr.append(el("td", "admin-cell-id", String(account.id)));

      const identity = el("td");
      const wrap = el("div", "admin-identity");
      const copy = el("div", "admin-identity-copy");
      copy.append(
        el("strong", "", displayName(account)),
        el("small", "", `@${account.username}`)
      );
      wrap.append(avatarNode(account, 28), copy);
      identity.append(wrap);

      const contact = [account.email, account.phone].filter(Boolean).join(" · ") || "—";
      tr.append(identity, el("td", "admin-cell-muted", contact), roleCell(account), el("td", "admin-cell-time", when(account.created_at)));

      const action = el("td", "admin-cell-action");
      const reset = el("button", "mini-button", "重置密码");
      reset.type = "button";
      reset.addEventListener("click", () => openPasswordDialog(account));
      action.append(reset);
      tr.append(action);
      accountRows.append(tr);
    });
  }

  function roleCell(account) {
    const td = el("td");
    const select = document.createElement("select");
    select.className = "admin-select";
    select.append(
      new Option(ROLE_LABEL.user, "user", false, account.platform_role === "user"),
      new Option(ROLE_LABEL.admin, "admin", false, account.platform_role === "admin")
    );
    const isMe = account.id === me.id;
    select.disabled = isMe;
    select.title = isMe ? "不能修改自己的平台角色" : "";
    select.addEventListener("change", async () => {
      select.disabled = true;
      try {
        await call(() => sdk.setPlatformRole(account.id, select.value));
        await loadAccounts();
        status("#adminAccountsStatus", `已把 ${account.username} 改为${ROLE_LABEL[select.value]}`);
      } catch (error) {
        select.value = account.platform_role;
        status("#adminAccountsStatus", error.message, true);
        select.disabled = isMe;
      }
    });
    td.append(select);
    return td;
  }

  /* ---------------- 小组 / 项目 ---------------- */

  async function loadGroups() {
    const page = await call(() =>
      sdk.adminGroups({
        search: state.groups.search || undefined,
        offset: state.groups.offset,
        limit: PAGE,
      })
    );
    state.groups.total = page.total;
    groupMeta.textContent = `共 ${page.total} 个小组`;
    renderGroups(page.items || []);
    renderPager(groupPager, state.groups, loadGroups);
  }

  function renderGroups(rows) {
    groupRows.replaceChildren();
    if (!rows.length) {
      const tr = el("tr");
      const td = el("td", "admin-empty", "还没有小组");
      td.colSpan = 6;
      tr.append(td);
      groupRows.append(tr);
      return;
    }
    rows.forEach((group) => {
      const tr = el("tr");
      tr.append(el("td", "admin-cell-id", String(group.id)));

      const name = el("td");
      const line = el("div", "admin-name-line");
      line.append(el("strong", "", group.name));
      if (group.deleted_at) line.append(flag("已解散", false));
      name.append(line);

      tr.append(
        name,
        el("td", "admin-cell-muted", GROUP_TYPE_LABEL[group.type] || group.type),
        el("td", "admin-cell-muted", group.owner_username ? `@${group.owner_username}` : `#${group.owner_account_id}`),
        el("td", "admin-cell-num", String(group.member_count)),
        el("td", "admin-cell-time", when(group.created_at))
      );
      groupRows.append(tr);
    });
  }

  async function loadProjects() {
    const page = await call(() =>
      sdk.adminProjects({
        search: state.projects.search || undefined,
        offset: state.projects.offset,
        limit: PAGE,
      })
    );
    state.projects.total = page.total;
    projectMeta.textContent = `共 ${page.total} 个项目`;
    renderProjects(page.items || []);
    renderPager(projectPager, state.projects, loadProjects);
  }

  function renderProjects(rows) {
    projectRows.replaceChildren();
    if (!rows.length) {
      const tr = el("tr");
      const td = el("td", "admin-empty", "还没有项目");
      td.colSpan = 7;
      tr.append(td);
      projectRows.append(tr);
      return;
    }
    rows.forEach((project) => {
      const tr = el("tr");
      tr.append(el("td", "admin-cell-id", String(project.id)));

      const name = el("td");
      const line = el("div", "admin-name-line");
      line.append(el("strong", "", project.name));
      if (project.deleted_at) line.append(flag("已删除", false));
      name.append(line);

      tr.append(
        name,
        el("td", "admin-cell-muted", project.group_name || `#${project.group_id}`),
        el("td", "", project.status === "archived" ? "已归档" : "进行中"),
        el("td", "admin-cell-num", String(project.member_count)),
        el("td", "admin-cell-num", String(project.task_count)),
        el("td", "admin-cell-time", when(project.created_at))
      );
      projectRows.append(tr);
    });
  }

  /* ---------------- 审计日志 ---------------- */

  async function loadAudit() {
    const page = await call(() =>
      sdk.adminAuditLogs({
        actionPrefix: state.audit.prefix || undefined,
        offset: state.audit.offset,
        limit: PAGE,
      })
    );
    state.audit.total = page.total;
    auditMeta.textContent = `共 ${page.total} 条记录`;
    renderAudit(page.items || []);
    renderPager(auditPager, state.audit, loadAudit);
  }

  function renderAudit(rows) {
    auditRows.replaceChildren();
    if (!rows.length) {
      const tr = el("tr");
      const td = el("td", "admin-empty", "没有匹配的日志");
      td.colSpan = 5;
      tr.append(td);
      auditRows.append(tr);
      return;
    }
    rows.forEach((log) => {
      const tr = el("tr");
      const action = el("td");
      action.append(el("code", "admin-action", log.action));
      tr.append(
        el("td", "admin-cell-time", when(log.created_at)),
        action,
        el("td", "admin-cell-muted", `${log.resource_type} #${log.resource_id}`),
        el("td", "admin-cell-muted", log.actor_account_id == null ? "—" : `#${log.actor_account_id}`),
        el("td", "admin-cell-muted", log.group_id == null ? "—" : `#${log.group_id}`)
      );
      auditRows.append(tr);
    });
  }

  /* ---------------- 存储 / 模型表单 ---------------- */

  async function loadSettings() {
    const [policy, model] = await Promise.all([
      call(() => sdk.getStoragePolicy()),
      call(() => sdk.getModelConfig()),
    ]);
    fillStoragePolicy(policy);
    fillModelConfig(model);
    status("#adminStorageStatus", policy.updated_at ? `上次更新：${when(policy.updated_at)}` : "");
    status("#adminModelStatus", model.updated_at ? `上次更新：${when(model.updated_at)}` : "");
  }

  function numberOrNull(selector) {
    const raw = $(selector).value.trim();
    if (!raw) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  }

  $("#adminStorageForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    runForm(button, "#adminStorageStatus", async () => {
      const uri = $("#adminStorageUri").value.trim();
      const policy = await call(() =>
        sdk.updateStoragePolicy({
          storage_uri: uri || null,
          team_quota_images: numberOrNull("#adminTeamQuotaImages") ?? 0,
          team_quota_storage_mb: numberOrNull("#adminTeamQuotaStorage") ?? 0,
        })
      );
      fillStoragePolicy(policy);
      status("#adminStorageStatus", `已保存，当前写入 ${policy.effective_uri}`);
    });
  });

  $("#adminModelForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type="submit"]');
    runForm(button, "#adminModelStatus", async () => {
      const model = await call(() =>
        sdk.updateModelConfig({
          detector_onnx: $("#adminDetectorOnnx").value.trim() || null,
          detector_dir: $("#adminDetectorDir").value.trim() || null,
          detector_precision: $("#adminDetectorPrecision").value || null,
          detector_device: $("#adminDetectorDevice").value || null,
          detector_size: numberOrNull("#adminDetectorSize"),
          detector_max_size: numberOrNull("#adminDetectorMaxSize"),
          translator_dir: $("#adminTranslatorDir").value.trim() || null,
          translator_precision: $("#adminTranslatorPrecision").value || null,
        })
      );
      fillModelConfig(model);
      status(
        "#adminModelStatus",
        model.detector_configured ? "已保存，模型配置已生效" : "已保存，但检测模型仍未配置"
      );
    });
  });

  /* ---------------- 重置密码弹窗 ---------------- */

  let passwordTarget = null;

  function openPasswordDialog(account) {
    passwordTarget = account;
    passwordForm.reset();
    status("#adminPasswordStatus", "");
    $("#adminPasswordDesc").textContent = `为 @${account.username} 设置一个新密码。`;
    openDialog(passwordDialog);
    window.setTimeout(() => $("#adminNewPassword").focus(), 120);
  }

  passwordForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!passwordTarget) return;
    const form = event.currentTarget;
    runForm(form.querySelector('button[type="submit"]'), "#adminPasswordStatus", async () => {
      await call(() => sdk.resetAccountPassword(passwordTarget.id, $("#adminNewPassword").value));
      status("#adminPasswordStatus", "密码已重置，旧登录已失效");
      window.setTimeout(() => closeDialog(passwordDialog), 900);
    });
  });

  /* ---------------- 事件 ---------------- */

  tabs.forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.adminTab));
  });

  document.querySelectorAll("[data-admin-jump]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.adminJump));
  });

  refreshButton.addEventListener("click", () => {
    status("#adminRefresh", "");
    loaded[tab] = false;
    switchTab(tab, { force: true });
  });

  $("#adminAccountQuery").addEventListener("click", () => {
    state.accounts.search = $("#adminAccountSearch").value.trim();
    state.accounts.role = $("#adminAccountRole").value;
    state.accounts.offset = 0;
    loadAccounts().catch((error) => status("#adminAccountsMeta", error.message, true));
  });
  $("#adminAccountSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("#adminAccountQuery").click();
  });

  $("#adminGroupQuery").addEventListener("click", () => {
    state.groups.search = $("#adminGroupSearch").value.trim();
    state.groups.offset = 0;
    loadGroups().catch((error) => status("#adminGroupsMeta", error.message, true));
  });
  $("#adminGroupSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("#adminGroupQuery").click();
  });

  $("#adminProjectQuery").addEventListener("click", () => {
    state.projects.search = $("#adminProjectSearch").value.trim();
    state.projects.offset = 0;
    loadProjects().catch((error) => status("#adminProjectsMeta", error.message, true));
  });
  $("#adminProjectSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("#adminProjectQuery").click();
  });

  $("#adminAuditQuery").addEventListener("click", () => {
    state.audit.prefix = $("#adminAuditAction").value.trim();
    state.audit.offset = 0;
    loadAudit().catch((error) => status("#adminAuditMeta", error.message, true));
  });
  $("#adminAuditAction").addEventListener("keydown", (event) => {
    if (event.key === "Enter") $("#adminAuditQuery").click();
  });

  $("#closeAdminPassword").addEventListener("click", () => closeDialog(passwordDialog));

  window.VSAdmin = { enter };
})();
