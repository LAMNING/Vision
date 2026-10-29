// 空间与项目：三个空间页只列项目；项目页统一五个 tab（概览/图像/任务/成员/设置）。
// 空间级功能（小组成员与图库、企业审核/公告/日报）收进右侧「空间设置」抽屉，
// 里面直接复用 team.js / enterprise.js 原有的 DOM，不重写一套。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, sdk, el, status, runForm, displayName, avatarNode } = VS;

  const SPACE_LABEL = { personal: "个人空间", team: "团队空间", enterprise: "企业空间" };
  const ROLE_LABEL = {
    owner: "所有者", admin: "管理员", manager: "项目管理员",
    annotator: "标注员", reviewer: "审核员", member: "成员", guest: "只读",
  };
  const STATUS_LABEL = {
    draft: "草稿", queued: "排队中", auto_annotating: "标注中", auto_annotated: "待复核",
    approved: "已通过", rejected_fix: "退回修改", rejected_reannotate: "退回重标",
    failed: "失败", cancelled: "已取消",
  };
  const TERMINAL = new Set(["approved", "cancelled"]);

  /* ---------------- 状态 ---------------- */
  let activeSpace = "personal";
  let personalGroupId = null;
  let teamGroupId = null;
  let enterpriseGroupId = null;
  let current = null;            // 当前项目（ProjectListItem）
  let tab = "overview";
  let spaceItems = [];
  let enterprises = [];
  let createTarget = null;       // { kind, groupId }
  const loaded = { overview: false, tasks: false, members: false, settings: false };

  /* ---------------- 把旧面板搬进新容器 ---------------- */

  function relocate() {
    // 个人空间的上传 / 选择 / 图像列表 → 项目页「图像」tab
    const imagesPane = $("#projectImagesPane");
    const personal = document.querySelector('[data-view="personal"]');
    if (imagesPane && personal) {
      [personal.querySelector(".upload-hero"), $("#selectionPanel"), personal.querySelector(".personal-data")]
        .filter(Boolean)
        .forEach((node) => imagesPane.append(node));
    }

    // 小组的成员与图库 → 空间设置抽屉
    const teamPane = $("#teamSettingsPane");
    const teamGrid = $("#teamGrid");
    const teamEmpty = $("#teamEmpty");
    if (teamPane && teamGrid) teamPane.append(teamGrid);
    if (teamPane && teamEmpty) teamPane.append(teamEmpty);
    const newTeam = $("#newTeamButton");
    if (teamPane && newTeam) {
      const head = el("div", "drawer-subhead");
      head.append(el("strong", "", "小组成员与图库"), newTeam);
      teamPane.prepend(head);
    }

    // 企业的成员 / 图库 / 审核 / 记录 / 日报 → 空间设置抽屉
    const entPane = $("#enterpriseSettingsPane");
    const entGrid = $("#enterpriseGrid");
    const entEmpty = $("#enterpriseEmpty");
    if (entPane && entGrid) entPane.append(entGrid);
    if (entPane && entEmpty) entPane.append(entEmpty);
  }

  /* ---------------- 空间列表 ---------------- */

  async function enterSpace(kind) {
    activeSpace = kind;
    if (kind === "personal") await loadPersonal();
    if (kind === "team") await loadTeam();
    if (kind === "enterprise") await loadEnterprise();
  }

  async function loadPersonal() {
    const box = $("#personalProjectList");
    if (!personalGroupId) {
      const space = await call(() => sdk.mySpace());
      personalGroupId = space.id;
    }
    spaceItems = await call(() => sdk.listVisibleProjects({ groupId: personalGroupId }));
    renderList("personal", spaceItems);
  }

  async function loadTeam() {
    // #teamSelect 由 team.js 维护（它还要管成员和图库），这里只读它的选择
    await window.VSTeam?.enter?.();
    const select = $("#teamSelect");
    teamGroupId = Number(select.value) || null;
    if (!teamGroupId) {
      spaceItems = [];
      $("#newTeamProject").disabled = true;
      renderList("team", []);
      return;
    }
    $("#newTeamProject").disabled = false;
    spaceItems = await call(() => sdk.listVisibleProjects({ groupId: teamGroupId }));
    renderList("team", spaceItems);
  }

  async function loadEnterprise() {
    // #enterpriseSelect 由 enterprise.js 维护，值是企业 id，列表要用背后的 group_id
    await window.VSTeamEnterprise?.enter?.();
    enterprises = await call(() => sdk.listEnterprises());
    const select = $("#enterpriseSelect");
    const selected = enterprises.find((ent) => String(ent.id) === String(select.value));
    enterpriseGroupId = selected ? selected.group_id : null;
    if (!enterpriseGroupId) {
      spaceItems = [];
      $("#newEnterpriseProject").disabled = true;
      renderList("enterprise", []);
      return;
    }
    $("#newEnterpriseProject").disabled = false;
    spaceItems = await call(() =>
      sdk.listVisibleProjects({ groupId: enterpriseGroupId })
    );
    renderList("enterprise", spaceItems);
  }

  function groupLabel(project) {
    if (project.group_type === "enterprise" || project.group_type === "org") {
      return (project.group_name || "").replace(/^企业：/, "");
    }
    return project.group_name || "";
  }

  function renderList(kind, items) {
    const box = $(`#${kind}ProjectList`);
    if (!box) return;
    box.replaceChildren();
    if (!items.length) {
      const noSpace = (kind === "team" && !teamGroupId)
        || (kind === "enterprise" && !enterpriseGroupId);
      const empty = el("div", "empty-state project-empty");
      empty.append(
        el("strong", "", noSpace
          ? (kind === "team" ? "还没有小组" : "还没有加入企业")
          : "还没有项目"),
        el("p", "", noSpace
          ? (kind === "team" ? "点右上角「空间设置」，在里面新建小组。" : "先用右上角创建企业，或凭企业码加入。")
          : "项目是图片、任务和成员的容器。点右上角「新建项目」开始。")
      );
      box.append(empty);
      return;
    }
    items.forEach((project) => {
      const row = el("button", "project-row");
      row.type = "button";
      row.dataset.projectId = String(project.id);

      const icon = el("span", "project-row-icon", (project.name || "项").trim()[0] || "项");
      const copy = el("span", "project-row-copy");
      const title = el("span", "project-row-title");
      title.append(el("strong", "", project.name));
      if (project.status === "archived") title.append(el("em", "admin-flag is-off", "已归档"));
      copy.append(
        title,
        el("span", "project-row-desc", project.description || "暂无描述"),
        el("span", "project-row-meta", `${groupLabel(project)} · ${ROLE_LABEL[project.role] || project.role} · ${when(project.created_at)}`)
      );
      row.append(icon, copy, el("span", "project-row-go", "›"));
      row.addEventListener("click", () => openProject(project));
      box.append(row);
    });
  }

  /* ---------------- 项目页 ---------------- */

  async function openProject(project) {
    current = project;
    tab = "overview";
    loaded.overview = loaded.tasks = loaded.members = loaded.settings = false;

    $("#projectName").textContent = project.name;
    $("#projectDesc").textContent = project.description || "暂无描述";
    $("#projectGroupLabel").textContent = `${SPACE_LABEL[labelOf(project)]} · ${groupLabel(project)}`;
    $("#projectRole").textContent = ROLE_LABEL[project.role] || project.role || "";
    $("#projectArchive").hidden = !project.can_manage || project.status !== "active";
    $("#projectDangerArchive").disabled = !project.can_manage;

    showView("project");
    syncTabs();
    await loadOverview();

    // 图像 tab 直接复用 app.js 的上传 / 列表
    window.VSApp?.loadProject?.(project);
  }

  function labelOf(project) {
    if (project.group_type === "personal") return "personal";
    if (project.group_type === "org") return "enterprise";
    return "team";
  }

  function syncTabs() {
    document.querySelectorAll("[data-project-tab]").forEach((button) => {
      const active = button.dataset.projectTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    document.querySelectorAll("[data-project-pane]").forEach((pane) => {
      pane.hidden = pane.dataset.projectPane !== tab;
    });
  }

  async function switchTab(next) {
    if (!current) return;
    tab = next;
    syncTabs();
    try {
      if (next === "overview") await loadOverview();
      if (next === "tasks") await loadTasks();
      if (next === "members") await loadMembers();
      if (next === "settings") await loadSettings();
      if (next === "images") window.VSApp?.loadProject?.(current, { force: true });
    } catch (error) {
      status("#projectRecentMeta", error.message, true);
    }
  }

  async function loadOverview() {
    if (loaded.overview) return;
    const [stats, tasks] = await Promise.all([
      call(() => sdk.projectStats(current.id)),
      call(() => sdk.listProjectTasks(current.id)),
    ]);
    const cells = [
      ["图片", stats.images_total],
      ["任务", stats.tasks_total],
      ["标注", stats.annotations_current],
      ["已通过", stats.approved_tasks],
      ["通过率", stats.reviewed_tasks ? formatRate(stats.review_pass_rate) : "—"],
      ["待处理", (stats.tasks_by_status?.queued || 0) + (stats.tasks_by_status?.auto_annotating || 0)],
    ];
    const box = $("#projectStats");
    box.replaceChildren();
    cells.forEach(([label, value]) => {
      const cell = el("article", "admin-stat");
      cell.append(el("b", "", String(value ?? 0)), el("small", "", label));
      box.append(cell);
    });

    const recent = tasks.slice().reverse().slice(0, 5);
    $("#projectRecentMeta").textContent = tasks.length ? `共 ${tasks.length} 个任务` : "还没有任务";
    const list = $("#projectRecentTasks");
    list.replaceChildren();
    if (!recent.length) {
      list.append(el("div", "project-line is-muted", "还没有任务，去「任务」tab 用图片新建一个。"));
    } else {
      recent.forEach((task) => {
        const line = el("div", "project-line");
        line.append(
          el("span", "", `#${task.id} · ${STATUS_LABEL[task.status] || task.status}`),
          el("strong", "", task.name || (task.mode === "manual" ? "人工标注任务" : "自动标注任务"))
        );
        list.append(line);
      });
    }
    loaded.overview = true;
  }

  function formatRate(value) {
    const rate = value <= 1 ? value * 100 : value;
    return `${Math.round(rate)}%`;
  }

  /* ---------------- 任务 ---------------- */

  async function loadTasks() {
    const tasks = await call(() => sdk.listProjectTasks(current.id));
    $("#projectTasksMeta").textContent = `共 ${tasks.length} 个任务`;
    const box = $("#projectTaskList");
    box.replaceChildren();
    if (!tasks.length) {
      box.append(el("div", "empty-state project-empty", "还没有任务。用项目里的图片新建一个自动标注任务。"));
      return;
    }
    tasks.forEach((task) => {
      const row = el("div", "project-task");
      const copy = el("div", "project-task-copy");
      copy.append(
        el("strong", "", task.name || `任务 #${task.id}`),
        el("small", "", `${STATUS_LABEL[task.status] || task.status} · ${task.mode === "manual" ? "人工" : "自动"} · ${when(task.created_at)}`)
      );
      row.append(copy);
      const actions = el("div", "project-task-actions");
      if (task.status === "draft") {
        const submit = el("button", "mini-button is-pass", "提交");
        submit.type = "button";
        submit.addEventListener("click", () => act(() => sdk.submitTask(current.id, task.id), submit));
        actions.append(submit);
      }
      if (!TERMINAL.has(task.status)) {
        const cancel = el("button", "mini-button is-reject", "取消");
        cancel.type = "button";
        cancel.addEventListener("click", () => act(() => sdk.cancelTask(current.id, task.id), cancel));
        actions.append(cancel);
      }
      if (!actions.children.length) actions.append(el("span", "project-task-done", "已结束"));
      row.append(actions);
      box.append(row);
    });
  }

  async function act(task, button) {
    button.disabled = true;
    try {
      await task();
      await loadTasks();
      loaded.overview = false;
    } catch (error) {
      status("#projectTaskStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function createTask() {
    const button = $("#projectNewTask");
    button.disabled = true;
    try {
      const page = await call(() => sdk.listProjectImages(current.id, { limit: 200 }));
      const items = page.items || [];
      if (!items.length) throw new Error("项目里还没有图片，先去「图像」上传");
      await call(() => sdk.createTask(current.id, {
        imageIds: items.map((image) => image.id),
        mode: "auto",
      }));
      status("#projectTaskStatus", `已用 ${items.length} 张图片创建任务`);
      await loadTasks();
      loaded.overview = false;
    } catch (error) {
      status("#projectTaskStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  /* ---------------- 成员 ---------------- */

  async function loadMembers() {
    const rows = await call(() => sdk.listProjectMembers(current.id));
    $("#projectMembersMeta").textContent = `共 ${rows.length} 名成员`;
    const list = $("#projectMemberList");
    list.replaceChildren();
    if (!rows.length) {
      list.append(el("li", "member-empty", "还没有单独加成员，空间里的管理者默认可见。"));
      return;
    }
    rows.forEach((member) => {
      const li = el("li", "project-member");
      const account = member.account || { username: `#${member.account_id}` };
      li.append(avatarNode(account, 28));
      const copy = el("div", "project-member-copy");
      copy.append(
        el("strong", "", displayName(account)),
        el("small", "", `@${account.username || member.account_id} · ${(member.roles || []).map((role) => ROLE_LABEL[role] || role).join(" / ")}`)
      );
      li.append(copy);
      if (current.can_manage) {
        const remove = el("button", "mini-button is-reject", "移除");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          remove.disabled = true;
          try {
            await call(() => sdk.removeProjectMember(current.id, member.account_id));
            await loadMembers();
          } catch (error) {
            status("#projectMemberStatus", error.message, true);
            remove.disabled = false;
          }
        });
        li.append(remove);
      }
      list.append(li);
    });
  }

  async function addMember() {
    const username = $("#projectMemberName").value.trim();
    const role = $("#projectMemberRole").value;
    if (!username) return;
    const button = $("#projectAddMember");
    button.disabled = true;
    status("#projectMemberStatus", "");
    try {
      const found = await call(() => sdk.searchAccounts(username));
      const account = found.find((row) => row.username === username) || found[0];
      if (!account) throw new Error("没有找到这个用户名");
      await call(() => sdk.addProjectMember(current.id, account.id, { role }));
      $("#projectMemberName").value = "";
      status("#projectMemberStatus", `已添加 @${account.username}`);
      await loadMembers();
    } catch (error) {
      status("#projectMemberStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  /* ---------------- 设置 ---------------- */

  async function loadSettings() {
    $("#projectNameInput").value = current.name;
    $("#projectDescInput").value = current.description || "";
    $("#projectReviewerInput").value = current.default_reviewer || "submitter";
    const categories = await call(() =>
      sdk.listCategories(current.id, { includeDisabled: true })
    );
    const list = $("#projectCategories");
    list.replaceChildren();
    if (!categories.length) {
      list.append(el("li", "project-line is-muted", "还没有类目，自动标注前先加一个。"));
    } else {
      categories.forEach((category) => {
        const li = el("li", "project-category");
        li.append(
          el("span", "", `${category.zh_name}${category.en_name ? ` · ${category.en_name}` : ""}`)
        );
        const remove = el("button", "mini-button is-reject", "删除");
        remove.type = "button";
        remove.addEventListener("click", async () => {
          remove.disabled = true;
          try {
            await call(() => sdk.deleteCategory(current.id, category.id));
            await loadSettings();
          } catch (error) {
            status("#projectCategoryStatus", error.message, true);
            remove.disabled = false;
          }
        });
        li.append(remove);
        list.append(li);
      });
    }
    loaded.settings = true;
  }

  function saveSettings(event) {
    event.preventDefault();
    runForm(event.currentTarget.querySelector('button[type="submit"]'), "#projectSettingsStatus", async () => {
      const updated = await call(() => sdk.updateProject(current.id, {
        name: $("#projectNameInput").value.trim(),
        description: $("#projectDescInput").value.trim() || null,
        default_reviewer: $("#projectReviewerInput").value,
      }));
      current = { ...current, ...updated };
      $("#projectName").textContent = current.name;
      $("#projectDesc").textContent = current.description || "暂无描述";
      status("#projectSettingsStatus", "已保存");
      loaded.overview = false;
    });
  }

  async function addCategory() {
    const input = $("#projectNewCategory");
    const name = input.value.trim();
    if (!name) return;
    const button = $("#projectCategoryAdd");
    button.disabled = true;
    try {
      await call(() => sdk.createCategory(current.id, { zhName: name }));
      input.value = "";
      status("#projectCategoryStatus", "已添加");
      await loadSettings();
    } catch (error) {
      status("#projectCategoryStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  async function archiveProject() {
    const confirmed = await window.confirmDanger({
      kicker: "ARCHIVE",
      title: "归档这个项目？",
      message: "归档后默认列表里不再显示，项目和图片都会保留。",
      confirmText: "归档项目",
    });
    if (!confirmed) return;
    try {
      await call(() => sdk.archiveVisibleProject(current.id));
      await enterSpace(activeSpace);
      showView(activeSpace);
    } catch (error) {
      status("#projectSettingsStatus", error.message, true);
    }
  }

  /* ---------------- 新建项目 ---------------- */

  function openCreate() {
    const groupId =
      activeSpace === "personal" ? personalGroupId
        : activeSpace === "team" ? teamGroupId
          : enterpriseGroupId;
    if (!groupId) {
      return;
    }
    createTarget = { kind: activeSpace, groupId };
    $("#projectCreateForm").reset();
    status("#projectCreateStatus", "");
    $("#projectCreateDesc").textContent =
      `项目会建在${SPACE_LABEL[activeSpace]}里，之后可以上传图片、建标注任务。`;
    openDialog($("#projectCreateDialog"));
    window.setTimeout(() => $("#projectCreateName").focus(), 120);
  }

  function submitCreate(event) {
    event.preventDefault();
    const name = $("#projectCreateName").value.trim();
    if (!name) {
      status("#projectCreateStatus", "请填写项目名称", true);
      return;
    }
    runForm(event.currentTarget.querySelector('button[type="submit"]'), "#projectCreateStatus", async () => {
      const project = await call(() =>
        sdk.createProjectInGroup(createTarget.groupId, name, {
          description: $("#projectCreateNote").value.trim() || null,
        })
      );
      closeDialog($("#projectCreateDialog"));
      await enterSpace(createTarget.kind);
      await openProject(project);
    });
  }

  /* ---------------- 空间设置抽屉 ---------------- */

  function openDrawer(kind) {
    const drawer = $("#spaceDrawer");
    drawer.hidden = false;
    $("#spaceDrawerKicker").textContent = kind === "team" ? "TEAM SETTINGS" : "ENTERPRISE SETTINGS";
    $("#spaceDrawerTitle").textContent = kind === "team" ? "小组空间设置" : "企业空间设置";
    $("#teamSettingsPane").hidden = kind !== "team";
    $("#enterpriseSettingsPane").hidden = kind !== "enterprise";
    document.body.classList.add("is-drawer-open");
    if (kind === "team") window.VSTeam?.enter();
    if (kind === "enterprise") window.VSTeamEnterprise?.enter();
  }

  function closeDrawer() {
    $("#spaceDrawer").hidden = true;
    $("#teamSettingsPane").hidden = true;
    $("#enterpriseSettingsPane").hidden = true;
    document.body.classList.remove("is-drawer-open");
    window.VSTeamEnterprise?.leave();
  }

  /* ---------------- 工具 ---------------- */

  function when(iso) {
    if (!iso) return "—";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  /* ---------------- 事件 ---------------- */

  relocate();

  $("#projectBack").addEventListener("click", () => showView(activeSpace));
  document.querySelectorAll("[data-project-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.projectTab));
  });
  $("#projectJumpTasks").addEventListener("click", () => switchTab("tasks"));
  $("#projectNewTask").addEventListener("click", createTask);
  $("#projectAddMember").addEventListener("click", addMember);
  $("#projectSettingsForm").addEventListener("submit", saveSettings);
  $("#projectCategoryAdd").addEventListener("click", addCategory);
  $("#projectDangerArchive").addEventListener("click", archiveProject);
  $("#projectArchive").addEventListener("click", archiveProject);

  $("#newPersonalProject").addEventListener("click", openCreate);
  $("#newTeamProject").addEventListener("click", openCreate);
  $("#newEnterpriseProject").addEventListener("click", openCreate);
  $("#projectCreateForm").addEventListener("submit", submitCreate);
  $("#closeProjectCreate").addEventListener("click", () => closeDialog($("#projectCreateDialog")));

  $("#teamSpaceSettings").addEventListener("click", () => openDrawer("team"));
  $("#enterpriseSpaceSettings").addEventListener("click", () => openDrawer("enterprise"));
  $("#spaceDrawerClose").addEventListener("click", closeDrawer);
  $("#spaceDrawerScrim").addEventListener("click", closeDrawer);

  $("#teamSelect").addEventListener("change", async () => {
    teamGroupId = Number($("#teamSelect").value) || null;
    if (!teamGroupId) return;
    $("#newTeamProject").disabled = false;
    spaceItems = await call(() => sdk.listVisibleProjects({ groupId: teamGroupId }));
    renderList("team", spaceItems);
  });
  $("#enterpriseSelect").addEventListener("change", async () => {
    const ent = enterprises.find(
      (item) => String(item.id) === String($("#enterpriseSelect").value)
    );
    enterpriseGroupId = ent ? ent.group_id : null;
    if (!enterpriseGroupId) return;
    $("#newEnterpriseProject").disabled = false;
    spaceItems = await call(() =>
      sdk.listVisibleProjects({ groupId: enterpriseGroupId })
    );
    renderList("enterprise", spaceItems);
  });

  window.VSProjects = {
    enter: enterSpace,
    current: () => current,
    activeSpace: () => activeSpace,
    open: openProject,
    closeDrawer,
  };

  // 首屏默认停在个人空间；app.js 启动时 projects.js 还没加载，这里补一次
  enterSpace("personal").catch((error) => {
    const box = $("#personalProjectList");
    if (box) box.replaceChildren(el("div", "empty-state project-empty", error.message));
  });
})();
