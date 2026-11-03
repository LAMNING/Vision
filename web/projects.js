// 空间与项目：三个空间页只列项目；项目页统一五个 tab（概览/图像/任务/成员/设置）。
// 空间级功能（小组成员与图库、企业审核/公告/日报）收进右侧「空间设置」抽屉，
// 里面直接复用 team.js / enterprise.js 原有的 DOM，不重写一套。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, sdk, el, status, runForm, displayName, avatarNode, formatSize } = VS;

  const SPACE_LABEL = { personal: "个人空间", team: "小组空间", enterprise: "企业空间" };
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
  let viewer = null;             // 当前账号，用来判断「标注」入口要不要露出来
  let taskFilter = "";           // 任务列表的状态筛选，空串 = 全部
  let taskMine = false;          // 「只看我的」：交给后端 assigned_to_me 过滤，跨分页也准
  let memberRows = null;         // 项目成员原始行，新建任务时用来填「指派 / 复核人」
  let memberMap = null;          // 成员 id → 显示名，供任务行显示负责人
  let taskPickImages = [];       // 新建任务对话框里的图片清单
  let taskCreateBlocked = false; // 没有启用类目时拦住「创建任务」
  const loaded = { overview: false, tasks: false, members: false, categories: false, settings: false };

  /* ---------------- 空间设置抽屉里放什么 ---------------- */

  function relocate() {
    // 这里原来把 #teamGrid / #enterpriseGrid 整块 append 进空间设置抽屉，连带把
    // header 的「新建小组」也搬了进去。结果是：小组成果 / 企业图库——空间页最主要的
    // 内容——要「更多 → 空间设置」两级才看得到，而设置里根本不该有图库。
    // 现在成果留在页面上，抽屉只放真正的设置入口。
    buildSettingsPane($("#teamSettingsPane"), {
      title: "小组设置",
      hint: "改资料、管成员。成果已经回到空间页，不用再进这里。",
      items: [
        { label: "编辑小组资料", detail: "改名称、简介，或删除小组", target: "#editTeamButton" },
        { label: "管理成员", detail: "邀请、调整角色、移除成员", target: "#manageMembers" },
      ],
    });
  }

  // 抽屉里的设置入口：一行一个，点开就是原来的对话框
  function buildSettingsPane(pane, { title, hint, items }) {
    if (!pane) return;
    pane.textContent = "";
    const wrap = el("div", "settings-shortcut");
    wrap.append(el("strong", "settings-shortcut-title", title));
    wrap.append(el("p", "settings-shortcut-hint", hint));
    items.forEach((item) => {
      const button = el("button", "settings-shortcut-item");
      button.type = "button";
      const copy = el("span", "settings-shortcut-copy");
      copy.append(el("strong", "", item.label), el("small", "", item.detail));
      button.append(copy, el("span", "material-symbols-rounded", "chevron_right"));
      button.addEventListener("click", () => {
        const trigger = $(item.target);
        closeDrawer();
        // 抽屉有收起过渡，等它走完再开对话框，否则两层遮罩叠一起
        window.setTimeout(() => trigger?.click(), 200);
      });
      wrap.append(button);
    });
    pane.append(wrap);
  }

  /* ---------------- 空间列表 ---------------- */

  async function enterSpace(kind) {
    activeSpace = kind;
    if (kind === "personal") await loadPersonal();
    if (kind === "team") await loadTeam();
    if (kind === "enterprise") await loadEnterprise();
    // 企业徽标跟着空间走（只在企业空间 / 企业项目里出现）
    window.VSBrand?.sync?.();
  }

  /* ---------------- 默认项目：一个空间只有一份工作区 ---------------- */

  // 用户不再「新建项目」。进入空间时自动确保有一个默认项目，
  // 之后的图片、任务、类目全挂在它下面；列表里也只有这一行。
  const DEFAULT_PROJECT_NAME = {
    personal: "我的标注空间",
    team: "小组标注空间",
    enterprise: "企业标注空间",
  };
  const spaceProject = { personal: null, team: null, enterprise: null };

  async function ensureSpaceProject(kind, groupId, { create = true } = {}) {
    if (!groupId) return null;
    const items = (await call(() => sdk.listVisibleProjects({ groupId }))) || [];
    const alive = items.filter((item) => item.status !== "archived");
    const remembered = alive.find((item) => item.id === spaceProject[kind]);
    const found = remembered || alive[0];
    if (found) {
      spaceProject[kind] = found.id;
      return found;
    }
    if (!create) return null;
    try {
      const created = await call(() =>
        sdk.createProjectInGroup(groupId, DEFAULT_PROJECT_NAME[kind])
      );
      spaceProject[kind] = created.id;
      return created;
    } catch {
      // 普通成员没有建项目的权限，交给空间管理员处理，页面显示等待态
      return null;
    }
  }

  /* ---------------- 个人空间：总结 + 我的任务 + 我的图片 ---------------- */

  // 个人空间允许有多个项目。规则：
  //   0 个 → 空状态 + 新建（新账号默认走这里，不再偷偷建一个默认项目）
  //   1 个 → 直接打开
  //   多个 → 先选，不自动进（用户明确要求「需要单独选择」）
  let personalProjects = [];

  function personalView() {
    return document.querySelector('[data-view="personal"]');
  }

  /** 选项目阶段：把下面的任务/图片区整块藏起来，避免点不动又占地方。 */
  function setPersonalChoosing(choosing) {
    const view = personalView();
    if (view) view.classList.toggle("is-choosing", choosing);
  }

  async function listPersonalProjects() {
    const items = (await call(() => sdk.listVisibleProjects({ groupId: personalGroupId }))) || [];
    return items.filter((item) => item.status !== "archived");
  }

  async function createPersonalProject(name) {
    const clean = String(name || "").trim();
    if (!clean) throw new Error("给项目起个名字吧");
    const created = await call(() => sdk.createProjectInGroup(personalGroupId, clean));
    spaceProject.personal = created.id;
    return created;
  }

  /** 删除项目。后端是软删除：项目从列表消失，图片和任务记录留着但访问不到。 */
  async function removeProject(project, { statusSelector } = {}) {
    const name = project.name || `项目 #${project.id}`;
    const confirmed = await window.confirmDanger({
      kicker: "DELETE PROJECT",
      title: `删除「${name}」？`,
      message: "项目会从列表里移除，里面的图片和任务记录都访问不到了。",
      detail: "这一步不能撤销。只想暂时收起来的话，进项目在「设置」里归档。",
      confirmText: "删除项目",
    });
    if (!confirmed) return;
    try {
      await call(() => sdk.deleteProject(project.id));
    } catch (error) {
      if (statusSelector) status(statusSelector, error.message, true);
      return;
    }
    // 记住的项目 id 一起清掉，否则下次进来还拿这个已删除的 id 去找
    Object.keys(spaceProject).forEach((kind) => {
      if (spaceProject[kind] === project.id) spaceProject[kind] = null;
    });
    spaceItems = spaceItems.filter((item) => item.id !== project.id);
    if (activeSpace === "personal") {
      await loadPersonal();
      window.VSApp?.refreshUploadOptions?.();
    } else {
      await enterSpace(activeSpace);
    }
  }

  async function enterPersonalProject(project) {
    spaceProject.personal = project.id;
    spaceItems = [project];
    setPersonalChoosing(false);
    await renderSummary(project);
    window.VSApp?.refreshUploadOptions?.();
    await window.VSApp?.focusProject?.(project);
    await loadPersonalTasks(project);
  }

  async function loadPersonal() {
    if (!personalGroupId) {
      const space = await call(() => sdk.mySpace());
      personalGroupId = space.id;
    }
    personalProjects = await listPersonalProjects();
    if (!personalProjects.length) {
      spaceItems = [];
      setPersonalChoosing(true);
      renderPersonalPicker({ empty: true });
      return;
    }
    if (personalProjects.length === 1) {
      await enterPersonalProject(personalProjects[0]);
      return;
    }
    // 多个项目：如果当前正在看其中某个（比如上传后刷新），保持不动；否则让用户选
    const stillThere = personalProjects.find((item) => item.id === spaceProject.personal);
    if (stillThere) {
      await enterPersonalProject(stillThere);
      return;
    }
    spaceItems = personalProjects;
    setPersonalChoosing(true);
    renderPersonalPicker({ empty: false });
  }

  /** 选项目 / 还没项目时的首屏。 */
  function renderPersonalPicker({ empty }) {
    const box = $("#personalSummary");
    if (!box) return;
    box.replaceChildren();

    const head = el("div", "personal-summary-head");
    const copy = el("div", "personal-summary-copy");
    copy.append(
      el("h2", "", "个人空间"),
      el(
        "p",
        "personal-summary-note",
        empty
          ? "还没有项目。建一个项目，图片、标注任务和结果都会放进去。"
          : "你有多个项目，选一个继续。"
      )
    );
    head.append(copy);
    box.append(head);

    if (empty) {
      const card = el("div", "project-picker-empty");
      card.append(
        el("span", "material-symbols-rounded project-picker-empty-icon", "create_new_folder"),
        el("strong", "", "建第一个项目"),
        el("p", "", "项目是图片和任务的分组，之后随时可以再加。")
      );
      const form = el("form", "project-picker-form");
      const input = el("input", "project-picker-input");
      input.type = "text";
      input.maxLength = 40;
      input.placeholder = "例如：街景数据 11 月";
      input.setAttribute("aria-label", "新项目名称");
      const submit = el("button", "primary-button", "创建项目");
      submit.type = "submit";
      form.append(input, submit);

      const tip = el("p", "project-picker-tip");
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        submit.disabled = true;
        tip.textContent = "";
        try {
          const created = await createPersonalProject(input.value);
          await enterPersonalProject(created);
        } catch (error) {
          tip.textContent = error.message;
        } finally {
          submit.disabled = false;
        }
      });
      card.append(form, tip);
      box.append(card);
      return;
    }

    const grid = el("div", "project-picker-grid");
    box.append(grid);
    personalProjects.forEach((project) => {
      // 卡片改成 div 包两个按钮：左边进项目，右边删除。
      // 原来整张卡是一个 <button>，里面塞不下第二个按钮，多项目就一直删不掉。
      const card = el("div", "project-picker-card");
      card.dataset.projectId = String(project.id);

      const open = el("button", "project-picker-open");
      open.type = "button";
      const icon = el("span", "material-symbols-rounded project-picker-card-icon", "folder");
      icon.setAttribute("aria-hidden", "true");
      const body = el("span", "project-picker-card-body");
      body.append(
        el("strong", "", project.name || `项目 #${project.id}`),
        el("small", "project-picker-card-meta", "正在读取…")
      );
      open.append(icon, body);
      open.addEventListener("click", () => {
        open.disabled = true;
        enterPersonalProject(project).catch((error) => {
          open.disabled = false;
          status("#personalSummary", error.message, true);
        });
      });

      const remove = el("button", "project-picker-delete");
      remove.type = "button";
      remove.title = "删除项目";
      remove.setAttribute("aria-label", `删除项目 ${project.name || project.id}`);
      remove.append(el("span", "material-symbols-rounded", "delete"));
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeProject(project, { statusSelector: "#personalSummary" });
      });

      card.append(open, remove);
      grid.append(card);

      // 统计慢慢补，别为了数字卡住整页
      call(() => sdk.projectStats(project.id))
        .then((stats) => {
          if (!stats) return;
          body.lastChild.textContent = `${stats.images_total} 张图片 · ${stats.tasks_total} 个任务`;
        })
        .catch(() => {
          body.lastChild.textContent = "打开看看";
        });
    });

    const add = el("button", "project-picker-add", "新建项目");
    add.type = "button";
    add.addEventListener("click", async () => {
      const name = window.prompt("新项目名称", "");
      if (name === null) return;
      add.disabled = true;
      try {
        const created = await createPersonalProject(name);
        await enterPersonalProject(created);
      } catch (error) {
        status("#personalSummary", error.message, true);
      } finally {
        add.disabled = false;
      }
    });
    box.append(add);
  }

  async function renderSummary(project) {
    const box = $("#personalSummary");
    if (!box) return;
    box.replaceChildren();
    if (!project) {
      const empty = el("div", "empty-state project-empty");
      empty.append(
        el("strong", "", "还没有可用的工作区"),
        el("p", "", "默认工作区没能创建成功。刷新一次试试，或让管理员帮你开通。")
      );
      box.append(empty);
      return;
    }

    const head = el("div", "personal-summary-head");
    const copy = el("div", "personal-summary-copy");
    copy.append(
      el("h2", "", "个人空间"),
      el("p", "personal-summary-note", `当前项目：${project.name || "未命名"} · 图片、标注任务和结果都在这里`)
    );
    const upload = el("button", "primary-button", "上传图片");
    upload.type = "button";
    upload.addEventListener("click", () => $("#imageUpload")?.click());
    if (personalProjects.length > 1) {
      const back = el("button", "secondary-button", `切换项目 · ${project.name || "当前"}`);
      back.type = "button";
      back.addEventListener("click", () => {
        spaceProject.personal = null;
        setPersonalChoosing(true);
        renderPersonalPicker({ empty: false });
      });
      head.append(copy, back, upload);
    } else {
      head.append(copy, upload);
    }
    box.append(head);

    const stats = await call(() => sdk.projectStats(project.id)).catch(() => null);
    const byStatus = stats?.tasks_by_status || {};
    const cards = [
      ["图片", stats ? stats.images_total : "—", "photo_library"],
      ["任务", stats ? stats.tasks_total : "—", "task_alt"],
      ["待复核", stats ? (byStatus.auto_annotated || 0) : "—", "fact_check"],
      ["已通过", stats ? stats.approved_tasks : "—", "verified"],
    ];
    const row = el("div", "personal-stats");
    cards.forEach(([label, value, icon]) => {
      const card = el("div", "personal-stat");
      const iconNode = el("span", "material-symbols-rounded personal-stat-icon", icon);
      iconNode.setAttribute("aria-hidden", "true");
      card.append(iconNode, el("strong", "", String(value)), el("small", "", label));
      row.append(card);
    });
    box.append(row);
  }

  async function loadPersonalTasks(project) {
    const box = $("#personalTaskList");
    const section = $("#personalTasksSection");
    if (!box || !section) return;
    let tasks = [];
    let projects = [];
    try {
      [tasks, projects] = await Promise.all([
        call(() => sdk.myTasks({ role: "assignee", limit: 60 })).catch(() => []),
        call(() => sdk.listVisibleProjects()).catch(() => []),
      ]);
    } catch {
      tasks = [];
    }
    const projectMap = new Map((projects || []).map((item) => [item.id, item]));
    // 只看还没结案的：已通过 / 已取消的任务留在项目页，不占个人空间的版面
    const live = (tasks || []).filter((task) => !TERMINAL.has(task.status));
    box.replaceChildren();
    section.hidden = !live.length;
    if (!live.length) return;

    live.forEach((task) => {
      // 行里要放真按钮（复核），所以不能用 <button> 当容器
      const row = el("div", "personal-task-row");
      row.tabIndex = 0;
      row.setAttribute("role", "button");
      const dot = el("span", `personal-task-dot is-${task.status}`);
      const copy = el("span", "personal-task-copy");
      const title = el("span", "personal-task-title");
      title.append(el("strong", "", task.name || `任务 #${task.id}`));
      title.append(el("em", `task-state is-${task.status}`, STATUS_LABEL[task.status] || task.status));
      const where = projectMap.get(task.project_id);
      const spaceName = where
        ? (where.group_type === "personal" ? "个人空间" : where.group_name || "项目")
        : "项目";
      copy.append(
        title,
        el("small", "", `${spaceName} · 共 ${task.image_total || 0} 张 · 已标 ${task.image_annotated || 0} 张`)
      );
      row.append(dot, copy);

      // 待复核的任务给一个直达复核台的按钮，其余状态点整行走原来的路径
      const reviewable = task.status === "auto_annotated";
      const actionLabel = reviewable ? "任务复核" : (task.mode === "manual" ? "去标注" : "查看");
      if (reviewable) {
        const reviewButton = el("button", "personal-task-action is-review", actionLabel);
        reviewButton.type = "button";
        reviewButton.addEventListener("click", (event) => {
          event.stopPropagation();
          openPersonalReview(task);
        });
        row.append(reviewButton);
      } else {
        row.append(el("span", "personal-task-go", actionLabel));
      }

      row.setAttribute("aria-label", `${task.name || `任务 #${task.id}`}，${actionLabel}`);
      row.addEventListener("click", () => jumpToTask(task));
      row.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        jumpToTask(task);
      });
      box.append(row);
    });
  }

  /** 个人空间直接开任务复核台，裁完回来刷新统计和列表。 */
  function openPersonalReview(task) {
    if (!window.VSTaskReview) {
      status("#personalTaskList", "复核面板没有加载，刷新页面后再试", true);
      return;
    }
    window.VSTaskReview.open({
      projectId: task.project_id,
      taskId: task.id,
      onDone: (decision) => {
        const message = decision === "approve" ? "任务已通过复核。" : "已退回，等标注员处理。";
        status("#imageStatus", message);
        refreshPersonal();
      },
    });
  }

  async function jumpToTask(task) {
    try {
      // 人工任务直接开标注台：个人空间里点一下就能画框，不用先跳项目页再找入口
      if (task.mode === "manual" && window.VSAnnotate) {
        const detail = await call(() => sdk.getTask(task.project_id, task.id));
        const rows = detail.images || [];
        const next = rows.find((row) => row.annotation_status !== "annotated") || rows[0];
        if (next) {
          window.VSAnnotate.open({
            projectId: task.project_id,
            taskId: task.id,
            taskImageId: next.id,
          });
          return;
        }
      }
      const all = await call(() => sdk.listVisibleProjects());
      const project = all.find((item) => item.id === task.project_id);
      if (!project) {
        status("#personalTaskList", "找不到这个任务所在的项目", true);
        return;
      }
      await openProject(project, { tab: "tasks" });
    } catch (error) {
      status("#personalTaskList", error.message, true);
    }
  }

  async function loadTeam() {
    // #teamSelect 由 team.js 维护（它还要管成员和图库），这里只读它的选择
    await window.VSTeam?.enter?.();
    const select = $("#teamSelect");
    teamGroupId = Number(select.value) || null;
    if (!teamGroupId) {
      spaceItems = [];
      renderList("team", []);
      return;
    }
    const project = await ensureSpaceProject("team", teamGroupId);
    spaceItems = project ? [project] : [];
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
      renderList("enterprise", []);
      return;
    }
    const project = await ensureSpaceProject("enterprise", enterpriseGroupId);
    spaceItems = project ? [project] : [];
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
    const noSpace = (kind === "team" && !teamGroupId)
      || (kind === "enterprise" && !enterpriseGroupId);
    // 还没有小组 / 企业时整节「项目」都不出现，否则页面上下两个空状态在说同一件事
    const section = $(`#${kind}ProjectSection`);
    if (section) section.hidden = noSpace;
    box.replaceChildren();
    if (!items.length) {
      const empty = el("div", "empty-state project-empty");
      empty.append(
        el("strong", "", noSpace
          ? (kind === "team" ? "还没有小组" : "还没有加入企业")
          : "还没有项目"),
        el("p", "", noSpace
          ? (kind === "team"
            ? "点右上角「新建小组」开始，建好后就能一起攒成果。"
            : "点右上角「⋯」→「创建企业」，或凭企业码加入。")
          : "项目是图片、任务和成员的容器。点右上角「新建项目」开始。")
      );
      // 个人空间第一次进来时把整条链路摆出来，否则用户建完项目就不知道下一步了
      if (kind === "personal" && !noSpace) {
        const chain = el("ol", "first-run-steps");
        [
          "新建项目",
          "「图像」tab 上传图片",
          "「类目」tab 加识别类目",
          "「任务」tab 新建任务",
          "自动或人工标注",
          "复核通过后导出",
        ].forEach((step) => chain.append(el("li", "", step)));
        empty.append(chain);
      }
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

  async function openProject(project, { tab: wanted = "overview" } = {}) {
    current = project;
    tab = wanted;
    loaded.overview = loaded.tasks = loaded.members = loaded.categories = loaded.settings = false;
    // 换项目要重置列表态，否则会带着上个项目的筛选与成员缓存
    taskFilter = "";
    taskMine = false;
    taskPollCount = 0;
    stopTaskPoll();
    memberMap = null;
    memberRows = null;
    taskPickImages = [];
    if ($("#projectTaskFilter")) $("#projectTaskFilter").value = "";
    syncMineToggle();

    // 标题区已经挪到顶栏（#appContextTitle / #appContextKicker）
    // 项目描述按需求从顶栏撤掉了，改在「概览」第一行与「设置」里显示 / 编辑
    $("#appContextTitle").textContent = project.name;
    $("#appContextKicker").textContent = `${SPACE_LABEL[labelOf(project)]} · ${groupLabel(project)}`;
    const desc = $("#projectDesc");
    if (desc) {
      desc.textContent = project.description || "";
      desc.hidden = !project.description;
    }
    $("#projectRole").textContent = ROLE_LABEL[project.role] || project.role || "";
    $("#projectRole").hidden = false;
    $("#projectArchive").hidden = !project.can_manage || project.status !== "active";
    $("#projectDangerArchive").disabled = !project.can_manage;
    // 删除只在个人空间开放：小组 / 企业项目删掉会连累别人的数据，那边只给归档
    const deletePanel = $("#projectDeletePanel");
    if (deletePanel) {
      deletePanel.hidden = !(
        project.can_manage && project.status === "active" && labelOf(project) === "personal"
      );
    }

    // 项目可能不属于当前停留的空间（通知里点开别的空间的任务就是这种情况），
    // 先对齐空间，否则侧栏高亮和「← 返回空间」会指向错误的地方。
    activeSpace = labelOf(project);

    showView("project");
    syncTabs();
    if (tab === "tasks") await loadTasks();
    else await loadOverview();
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
      if (next === "categories") await loadCategories();
      if (next === "settings") await loadSettings();
      if (next === "images") window.VSApp?.loadProject?.(current, { force: true });
    } catch (error) {
      status("#projectRecentMeta", error.message, true);
    }
  }

  /**
   * 概览顶部那条「下一步」。项目卡在「有项目没图」「有图没类目」「有类目没任务」时
   * 界面原本一片安静，用户不知道还差什么才能跑起来。
   */
  function renderNextStep(stats, tasks, categories) {
    const strip = $("#projectNext");
    if (!strip) return;
    const steps = [];
    if (!stats.images_total) {
      steps.push({ label: "去空间首页上传图片", hint: "图片统一在空间首页上传，这里只做任务与复核。", action: () => showView(activeSpace) });
    }
    if (!categories.length) {
      steps.push({ label: "添加识别类目", hint: "自动标注靠它出框，人工标注也从这里取色板。", action: () => switchTab("categories") });
    }
    if (!tasks.length) {
      steps.push({ label: "新建标注任务", hint: "选图片 + 选自动或人工，就能开跑。", action: () => openTaskCreate() });
    }
    const waiting = tasks.filter((task) => task.status === "queued" || task.status === "auto_annotated");
    if (tasks.length && waiting.length) {
      steps.push({ label: "处理待办任务", hint: `有 ${waiting.length} 个任务在等你。`, action: () => switchTab("tasks") });
    }
    strip.replaceChildren();
    if (!steps.length) {
      strip.hidden = true;
      return;
    }
    strip.hidden = false;
    strip.append(el("span", "project-next-kicker", "下一步"));
    steps.slice(0, 2).forEach((step) => {
      const button = el("button", "project-next-step");
      button.type = "button";
      button.append(el("strong", "", step.label), el("small", "", step.hint));
      button.addEventListener("click", step.action);
      strip.append(button);
    });
  }

  async function loadOverview() {
    if (loaded.overview) return;
    const [stats, tasks, categories] = await Promise.all([
      call(() => sdk.projectStats(current.id)),
      call(() => sdk.listProjectTasks(current.id)),
      call(() => sdk.listCategories(current.id)).catch(() => []),
    ]);
    const cells = [
      ["图片", stats.images_total],
      ["类目", categories.length],
      ["任务", stats.tasks_total],
      ["标注", stats.annotations_current],
      ["已通过", stats.approved_tasks],
      ["通过率", stats.reviewed_tasks ? formatRate(stats.review_pass_rate) : "—"],
    ];
    const box = $("#projectStats");
    box.replaceChildren();
    cells.forEach(([label, value]) => {
      const cell = el("article", "admin-stat");
      cell.append(el("b", "", String(value ?? 0)), el("small", "", label));
      box.append(cell);
    });

    renderNextStep(stats, tasks, categories);

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
          el("span", "", `#${task.id} · ${statusLabel(task)}`),
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

  /**
   * 同一个 queued 对两种任务含义不同：自动任务是「等 worker 取走」，
   * 人工任务却是「等人来画」。列表按模式换词，否则会出现「排队中 · 人工」配一颗「标注」按钮。
   */
  function statusLabel(task) {
    if (task.status === "queued" && task.mode === "manual") return "待标注";
    return STATUS_LABEL[task.status] || task.status;
  }

  /** 成员 id → 显示名：任务行要显示负责人，光有 account_id 没法看。 */
  async function memberNames() {
    if (memberMap) return memberMap;
    memberMap = new Map();
    try {
      const rows = await call(() => sdk.listProjectMembers(current.id));
      memberRows = rows;
      rows.forEach((member) => {
        const account = member.account || {};
        memberMap.set(member.account_id, displayName(account) || `#${member.account_id}`);
      });
    } catch (error) {
      memberMap = new Map();
    }
    return memberMap;
  }

  /** 「只看我的」按钮的按下态：后端的 assigned_to_me 才是真过滤，这里只反映状态。 */
  function syncMineToggle() {
    const node = $("#projectTaskMine");
    if (!node) return;
    node.classList.toggle("is-on", taskMine);
    node.setAttribute("aria-pressed", taskMine ? "true" : "false");
  }

  // 任务状态只在「排队 / 分析中」时才有必要盯；跑完就停，不留一个永远转的定时器。
  // 以前这里完全不自刷新，用户点进来看到的一直是打开那一刻的旧状态。
  const TASK_POLL_MS = 4000;
  const TASK_POLL_LIMIT = 90;
  let taskPollTimer = 0;
  let taskPollCount = 0;

  function stopTaskPoll() {
    if (taskPollTimer) window.clearTimeout(taskPollTimer);
    taskPollTimer = 0;
  }

  function scheduleTaskPoll(tasks) {
    stopTaskPoll();
    // 人工任务的 queued 是「等人来标」，不会自己变；盯着刷只是白请求
    const running = (tasks || []).filter((t) =>
      (t.status === "queued" && t.mode !== "manual") || t.status === "auto_annotating"
    );
    if (!running.length) { taskPollCount = 0; return; }
    if (taskPollCount >= TASK_POLL_LIMIT) return;
    taskPollTimer = window.setTimeout(async () => {
      taskPollTimer = 0;
      // 用户已经切走就别在后台一直请求
      if (currentView !== "project" || tab !== "tasks") { taskPollCount = 0; return; }
      taskPollCount += 1;
      try {
        await loadTasks();
      } catch {
        // 网络抖一下不该让刷新链断掉，下一轮接着试（有 TASK_POLL_LIMIT 兜底）
        scheduleTaskPoll(running);
      }
    }, TASK_POLL_MS);
  }

  async function loadTasks() {
    syncMineToggle();
    const [tasks, mine] = await Promise.all([
      call(() => sdk.listProjectTasks(current.id, taskMine ? { assignedToMe: true } : {})),
      viewerOf(),
    ]);
    const names = await memberNames();
    const visible = taskFilter ? tasks.filter((task) => task.status === taskFilter) : tasks;
    // 工具行左边只放计数，像 GitHub 仓库页的「1 Branch · 0 Tags」那样一口气说完状态
    const metaBits = [taskMine ? `我的 ${tasks.length} 个任务` : `共 ${tasks.length} 个任务`];
    if (taskFilter) metaBits.push(`筛出 ${visible.length} 个`);
    const waitingAnnotate = visible.filter((task) => task.status === "queued").length;
    const waitingReview = visible.filter((task) => task.status === "auto_annotated").length;
    if (waitingAnnotate) metaBits.push(`${waitingAnnotate} 个待标注`);
    if (waitingReview) metaBits.push(`${waitingReview} 个待复核`);
    $("#projectTasksMeta").textContent = metaBits.join(" · ");
    const box = $("#projectTaskList");
    box.replaceChildren();
    if (!tasks.length) {
      box.append(
        el(
          "div",
          "empty-state project-empty",
          taskMine
            ? "没有指派给你的任务。点「只看我的」旁边的按钮可以看全部任务。"
            : "还没有任务。点右上角「新建任务」，选好图片和标注方式就能开始。"
        )
      );
      return;
    }
    if (!visible.length) {
      box.append(el("div", "empty-state project-empty", `没有「${STATUS_LABEL[taskFilter] || taskFilter}」状态的任务，换个筛选看看。`));
      return;
    }
    visible.forEach((task) => {
      const row = el("div", "project-task");
      const copy = el("div", "project-task-copy");
      copy.append(el("strong", "", task.name || `任务 #${task.id}`));

      const meta = el("small", "project-task-meta");
      // 待复核的 chip 本身就是入口：审核员扫列表时最先看到的是状态，点它直达复核台
      if (reviewEntryVisible(task, mine)) {
        const chip = el("button", "task-chip is-action", statusLabel(task));
        chip.type = "button";
        chip.title = "进入复核工作台";
        chip.addEventListener("click", () => openReviewWorkbench(task));
        meta.append(chip);
      } else {
        meta.append(el("span", "task-chip", statusLabel(task)));
      }
      meta.append(el("span", "", task.mode === "manual" ? "人工" : "自动"));
      if (task.image_total) {
        meta.append(el("span", "", `已标注 ${task.image_annotated} / ${task.image_total}`));
      }
      if (task.status === "auto_annotated" && task.image_total) {
        // 逐图复核的进度：只写「待复核」看不出审到哪了
        const reviewed = `复核 ${task.review_approved} / ${task.image_total} 张`;
        meta.append(
          el("span", "task-review-count", task.review_rejected ? `${reviewed} · 退回 ${task.review_rejected}` : reviewed)
        );
      }
      const ownerId = task.assignee_account_id || task.submitter_account_id;
      if (ownerId) {
        const who = names.get(ownerId) || `#${ownerId}`;
        meta.append(el("span", "", `${task.assignee_account_id ? "负责" : "提交"} ${who}`));
      }
      meta.append(el("span", "", when(task.created_at)));
      copy.append(meta);

      if (task.image_total) {
        const bar = el("div", "task-progress");
        const fill = el("span", "task-progress-fill");
        const ratio = task.image_annotated / task.image_total;
        fill.style.width = `${Math.round(ratio * 100)}%`;
        if (ratio >= 1) fill.classList.add("is-full");
        bar.append(fill);
        copy.append(bar);
      }

      row.append(copy);
      const actions = el("div", "project-task-actions");
      const annotateLabel = annotateEntryLabel(task, mine);
      if (annotateLabel) {
        const annotate = el("button", "mini-button is-accent", annotateLabel);
        annotate.type = "button";
        annotate.addEventListener("click", () => window.VSAnnotate?.open({ projectId: current.id, taskId: task.id }));
        actions.append(annotate);
      }
      if (reviewEntryVisible(task, mine)) {
        const review = el("button", "mini-button is-pass", "复核");
        review.type = "button";
        review.addEventListener("click", () => openReviewWorkbench(task));
        actions.append(review);
      }
      if (task.status === "draft") {
        const submit = el("button", "mini-button", "提交");
        submit.type = "button";
        submit.addEventListener("click", () => act(() => sdk.submitTask(current.id, task.id), submit));
        actions.append(submit);
      }
      if (!TERMINAL.has(task.status)) {
        const cancel = el("button", "mini-button is-reject", "取消");
        cancel.type = "button";
        cancel.addEventListener("click", async () => {
          // 取消是终态，误点就回不来了；和「归档」用同一套确认卡。
          const ok = await window.confirmDanger({
            kicker: "CANCEL",
            title: "取消这个任务？",
            message: "取消后不能再继续标注，需要的话只能重新建任务。",
            detail: task.name || `任务 #${task.id}`,
            confirmText: "取消任务",
          });
          if (ok) act(() => sdk.cancelTask(current.id, task.id), cancel);
        });
        actions.append(cancel);
      }
      if (!actions.children.length) actions.append(el("span", "project-task-done", "已结束"));
      row.append(actions);
      box.append(row);
    });
    scheduleTaskPoll(visible);
  }

  /**
   * 「复核」入口：后端要求项目里是 reviewer/manager 才有资格，指派了具体复核人的
   * 任务只有那个人（或 manager）能裁决 —— 与 review.py 的 _assert_reviewer_allowed 对齐。
   * 身份没取到时照常露出，交给后端判定并给出中文提示。
   * 能管项目就一定看得到（后端对 manager 放行）；这样即便 ProjectListItem 没带
   * can_review（老接口 / 空间项目列表），入口也不会莫名消失。
   */
  function reviewEntryVisible(task, mine) {
    if (task.status !== "auto_annotated") return false;
    if (!current) return false;
    if (!current.can_review && !current.can_manage) return false;
    if (current.can_manage || !mine) return true;
    return !task.reviewer_account_id || task.reviewer_account_id === mine.id;
  }

  /** 打开复核工作台；裁决回来把统计与列表一起刷新。 */
  function openReviewWorkbench(task) {
    window.VSTaskReview?.open({
      projectId: current.id,
      taskId: task.id,
      onDone: (decision) => {
        // 裁决会改写任务状态和统计，两个缓存一起作废
        loaded.overview = false;
        status(
          "#projectTaskStatus",
          decision === "approve" ? "任务已通过复核。" : "已退回，等标注员处理。"
        );
        loadTasks();
      },
    });
  }

  /** 该不该在这个任务上露出「标注 / 修正标注」入口；返回按钮文案或 null。 */
  function annotateEntryLabel(task, mine) {
    const manual = task.mode === "manual" && task.status === "queued";
    const fixing = task.status === "rejected_fix" || task.status === "rejected_reannotate";
    if (!manual && !fixing) return null;
    // 后端 _can_edit_annotations 的口径：能管项目、是标注员、是提交人或被指派人
    const allowed = current?.can_manage
      || current?.role === "annotator"
      || !mine
      || task.submitter_account_id === mine.id
      || task.assignee_account_id === mine.id;
    if (!allowed) return null;
    return manual ? "标注" : "修正标注";
  }

  /** 只在需要时拉一次 /auth/me，失败就当拿不到身份（入口照常显示，交给后端判定）。 */
  async function viewerOf() {
    if (viewer) return viewer;
    try {
      viewer = await call(() => sdk.me());
    } catch (error) {
      viewer = null;
    }
    return viewer;
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

  /* ---------------- 新建任务 ---------------- */

  /** 打开对话框时拉一次项目图片，渲染成可勾选清单（默认全选）。 */
  async function openTaskCreate() {
    if (!current) return;
    $("#taskCreateName").value = "";
    if ($("#taskCreateAssignee")) $("#taskCreateAssignee").value = "";
    if ($("#taskCreateReviewer")) $("#taskCreateReviewer").value = "";
    $("#taskCreateSubmit").disabled = false;
    status("#taskCreateStatus", "");
    taskPickImages = [];
    const list = $("#taskPickList");
    list.replaceChildren(el("p", "task-pick-empty", "正在读取项目图片…"));
    $("#taskPickCount").textContent = "—";
    openDialog($("#taskCreateDialog"));
    window.setTimeout(() => $("#taskCreateName").focus(), 120);
    // 没有启用类目时提交会被后端挡回来（"项目还没有启用的类目"），先在这里说清楚
    const [categories, members] = await Promise.all([
      call(() => sdk.listCategories(current.id)).catch(() => []),
      call(() => sdk.listProjectMembers(current.id)).catch(() => []),
    ]);
    memberRows = members;
    fillTaskAssignees(members);
    syncTaskAssignMode();
    taskCreateBlocked = !categories.filter((category) => category.enabled !== false).length;
    const hint = $("#taskCreateHint");
    if (hint) {
      hint.hidden = !taskCreateBlocked;
      if (taskCreateBlocked) hint.textContent = "这个项目还没有识别类目，提交任务会被拦下。先去「类目」tab 加一个。";
    }
    if (taskCreateBlocked) $("#taskCreateSubmit").disabled = true;
    try {
      const page = await call(() => sdk.listProjectImages(current.id, { limit: 200 }));
      taskPickImages = page.items || [];
    } catch (error) {
      list.replaceChildren(el("p", "task-pick-empty", error.message));
      return;
    }
    if (!taskPickImages.length) {
      list.replaceChildren(el("p", "task-pick-empty", "项目里还没有图片。先去「图像」tab 上传，再回来建任务。"));
      updatePickCount();
      return;
    }
    list.replaceChildren();
    taskPickImages.forEach((image) => {
      const item = el("label", "task-pick-item");
      item.dataset.imageId = String(image.id);
      // 「只选未标注」靠这个标记筛：已经标过（或标完待审核）的图默认不选
      item.dataset.annotated = image.annotated ? "1" : "";
      const box = el("input", "task-pick-box");
      box.type = "checkbox";
      box.value = String(image.id);
      box.checked = true;
      box.addEventListener("change", updatePickCount);
      item.append(
        box,
        el("span", "task-pick-name", image.filename || `#${image.id}`),
        el("small", "", `${image.width || "?"}×${image.height || "?"} · ${formatSize(image.size_bytes)}`)
      );
      list.append(item);
    });
    updatePickCount();
  }

  /** 「只选未标注」：把已经标注过的图片取消勾选，方便直接建一个补标任务。 */
  function pickUnannotated() {
    document.querySelectorAll("#taskPickList .task-pick-item").forEach((item) => {
      const box = item.querySelector(".task-pick-box");
      if (!box) return;
      box.checked = !item.dataset.annotated;
    });
    const picked = pickedImageIds().length;
    if (!picked) status("#taskCreateStatus", "这些图片都已经标注过了，没有可补标的", true);
    else status("#taskCreateStatus", `已选中 ${picked} 张还没标注的图片`);
    updatePickCount();
  }

  function pickedImageIds() {
    return Array.from(document.querySelectorAll("#taskPickList .task-pick-box"))
      .filter((node) => node.checked)
      .map((node) => Number(node.value));
  }

  function updatePickCount() {
    const total = taskPickImages.length;
    const picked = pickedImageIds().length;
    $("#taskPickCount").textContent = total ? `已选 ${picked} / ${total} 张` : "项目里还没有图片";
    const button = $("#taskCreateSubmit");
    if (button) button.disabled = !picked || taskCreateBlocked;
  }

  function pickAll(checked) {
    document.querySelectorAll("#taskPickList .task-pick-box").forEach((node) => {
      node.checked = checked;
    });
    updatePickCount();
  }

  function chosenMode() {
    const node = document.querySelector('input[name="taskCreateMode"]:checked');
    return node ? node.value : "auto";
  }

  /**
   * 「指派标注员 / 复核人」：后端建任务就吃 assignee_account_id 与 reviewer_account_id，
   * 但界面一直没给选择器，团队里的人工任务只能谁有空谁去翻列表。
   * 候选按角色过滤 —— 与 tasks.py 的 _can_edit_annotations / _require_reviewable 对齐。
   */
  function fillTaskAssignees(rows) {
    const assignee = $("#taskCreateAssignee");
    const reviewer = $("#taskCreateReviewer");
    const memberLabel = (member) => {
      const account = member.account || {};
      const roles = (member.roles || []).map((role) => ROLE_LABEL[role] || role).join(" / ");
      return `${displayName(account) || `#${member.account_id}`}${roles ? ` · ${roles}` : ""}`;
    };
    const hasRole = (member, wanted) => (member.roles || []).some((role) => wanted.includes(role));
    if (assignee) {
      assignee.replaceChildren(new Option("不指定 —— 谁有空谁标", ""));
      rows
        .filter((member) => hasRole(member, ["annotator", "manager"]))
        .forEach((member) => assignee.append(new Option(memberLabel(member), String(member.account_id))));
    }
    if (reviewer) {
      reviewer.replaceChildren(new Option("按项目默认", ""));
      rows
        .filter((member) => hasRole(member, ["reviewer", "manager"]))
        .forEach((member) => reviewer.append(new Option(memberLabel(member), String(member.account_id))));
    }
  }

  /** 指派只对人工任务有意义，复核人只在勾了「需要审核」时用得上 —— 灰掉而不是藏着。 */
  function syncTaskAssignMode() {
    const manual = chosenMode() === "manual";
    const needReview = $("#taskReviewRequired") ? $("#taskReviewRequired").checked : true;
    const assigneeRow = $("#taskAssigneeRow");
    const assignee = $("#taskCreateAssignee");
    const reviewerRow = $("#taskReviewerRow");
    const reviewer = $("#taskCreateReviewer");
    if (assigneeRow) assigneeRow.dataset.disabled = manual ? "" : "1";
    if (assignee) assignee.disabled = !manual;
    if (reviewerRow) reviewerRow.dataset.disabled = needReview ? "" : "1";
    if (reviewer) reviewer.disabled = !needReview;
  }

  async function submitTaskCreate() {
    const imageIds = pickedImageIds();
    if (!imageIds.length) {
      status("#taskCreateStatus", "至少选一张图片", true);
      return;
    }
    const mode = chosenMode();
    const kind = mode === "manual" ? "人工标注" : "自动标注";
    const reviewNode = $("#taskReviewRequired");
    const reviewRequired = reviewNode ? reviewNode.checked : true;
    const assigneeNode = $("#taskCreateAssignee");
    const reviewerNode = $("#taskCreateReviewer");
    const assigneeId = mode === "manual" && assigneeNode ? assigneeNode.value : "";
    const reviewerId = reviewRequired && reviewerNode ? reviewerNode.value : "";
    const button = $("#taskCreateSubmit");
    button.disabled = true;
    status("#taskCreateStatus", "正在创建…");
    try {
      const created = await call(() => sdk.createTask(current.id, {
        name: $("#taskCreateName").value.trim() || undefined,
        imageIds,
        mode,
        reviewRequired,
        assigneeAccountId: assigneeId ? Number(assigneeId) : undefined,
        reviewerAccountId: reviewerId ? Number(reviewerId) : undefined,
      }));
      // 建出来还是 draft，这里顺手提交，省掉「建完再去列表里点提交」这一步。
      const task = await call(() => sdk.submitTask(current.id, created.id));
      closeDialog($("#taskCreateDialog"));
      const tail = reviewRequired ? "，标完进复核队列" : "，标完直接通过";
      const assigned = task.assignee_account_id
        ? `，已指派给 ${(await memberNames()).get(task.assignee_account_id) || `#${task.assignee_account_id}`}`
        : "";
      status("#projectTaskStatus", `已提交${kind}任务「${task.name || `任务 #${task.id}`}」，共 ${imageIds.length} 张图片${tail}${assigned}`);
      loaded.overview = false;
      await loadTasks();
    } catch (error) {
      status("#taskCreateStatus", error.message, true);
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

  /* ---------------- 类目 ---------------- */

  async function loadCategories() {
    const categories = await call(() =>
      sdk.listCategories(current.id, { includeDisabled: true })
    );
    const list = $("#projectCategories");
    list.replaceChildren();
    if (!categories.length) {
      list.append(el("li", "project-line is-muted", "还没有类目。自动标注要靠它出框，人工标注也从这里取色板，先加一个。"));
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
            loaded.overview = false;
            await loadCategories();
          } catch (error) {
            status("#projectCategoryStatus", error.message, true);
            remove.disabled = false;
          }
        });
        li.append(remove);
        list.append(li);
      });
    }
    const meta = $("#projectCategoriesMeta");
    if (meta) {
      if (!categories.length) {
        meta.textContent = "还没有类目，先加一个再建任务。";
      } else {
        const on = categories.filter((item) => item.enabled !== false).length;
        const translated = categories.filter((item) => !item.en_name).length;
        const bits = [`共 ${categories.length} 个类目`, `${on} 个启用`];
        if (translated) bits.push(`${translated} 个留空自动翻译`);
        meta.textContent = bits.join(" · ");
      }
    }
    loaded.categories = true;
  }

  /* ---------------- 设置 ---------------- */

  async function loadSettings() {
    $("#projectNameInput").value = current.name;
    $("#projectDescInput").value = current.description || "";
    $("#projectReviewerInput").value = current.default_reviewer || "submitter";
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
      $("#appContextTitle").textContent = current.name;
      const desc = $("#projectDesc");
      if (desc) {
        desc.textContent = current.description || "";
        desc.hidden = !current.description;
      }
      status("#projectSettingsStatus", "已保存");
      loaded.overview = false;
    });
  }

  async function addCategory() {
    const input = $("#projectNewCategory");
    const enInput = $("#projectNewCategoryEn");
    const name = input.value.trim();
    const enName = enInput ? enInput.value.trim() : "";
    if (!name) return;
    const button = $("#projectCategoryAdd");
    button.disabled = true;
    try {
      // 填了英文就按填的检测（不留翻译的余地），留空则由后端翻译中文名。
      await call(() => sdk.createCategory(current.id, { zhName: name, enName }));
      input.value = "";
      if (enInput) enInput.value = "";
      status("#projectCategoryStatus", enName ? `已添加，检测提示词「${enName}」` : "已添加，将自动翻译中文名");
      loaded.overview = false;
      await loadCategories();
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
    // 同时只开一个抽屉，否则两层遮罩会叠在一起
    window.VSNotifications?.close?.();
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

  /* ---------------- 空间 header 的「更多」菜单 ---------------- */

  // 空间设置 / 创建企业这类低频动作用一个菜单收起来。
  // 打开一个就关掉其它的，点外面或按 Esc 也收起，避免菜单挂着挡住项目列表。
  const spaceMenus = [];

  function closeSpaceMenus() {
    spaceMenus.forEach((close) => close());
  }

  function bindSpaceMenu(triggerId, listId) {
    const trigger = $(triggerId);
    const list = $(listId);
    if (!trigger || !list) return;
    const close = () => {
      list.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    };
    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = list.hidden;
      closeSpaceMenus();
      if (!open) return;
      list.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
    });
    // 点中菜单项就收起菜单；动作本身由各自的监听器处理
    list.addEventListener("click", (event) => {
      if (event.target.closest(".space-menu-item")) close();
    });
    document.addEventListener("mousedown", (event) => {
      if (list.hidden) return;
      if (list.contains(event.target) || trigger.contains(event.target)) return;
      close();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || list.hidden) return;
      close();
      trigger.focus();
    });
    spaceMenus.push(close);
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
  $("#projectNewTask").addEventListener("click", openTaskCreate);
  $("#taskPickAll").addEventListener("click", () => pickAll(true));
  $("#taskPickNone").addEventListener("click", () => pickAll(false));
  $("#taskPickUnannotated")?.addEventListener("click", pickUnannotated);
  $("#taskCreateSubmit").addEventListener("click", submitTaskCreate);
  $("#closeTaskCreate").addEventListener("click", () => closeDialog($("#taskCreateDialog")));
  $("#projectTaskFilter").addEventListener("change", () => {
    taskFilter = $("#projectTaskFilter").value;
    loadTasks().catch((error) => status("#projectTaskStatus", error.message, true));
  });
  $("#projectTaskMine").addEventListener("click", () => {
    taskMine = !taskMine;
    syncMineToggle();
    loadTasks().catch((error) => status("#projectTaskStatus", error.message, true));
  });
  document.querySelectorAll('input[name="taskCreateMode"]').forEach((node) => {
    node.addEventListener("change", syncTaskAssignMode);
  });
  $("#taskReviewRequired")?.addEventListener("change", syncTaskAssignMode);
  $("#projectAddMember").addEventListener("click", addMember);
  $("#projectSettingsForm").addEventListener("submit", saveSettings);
  $("#projectCategoryAdd").addEventListener("click", addCategory);
  ["#projectNewCategory", "#projectNewCategoryEn"].forEach((selector) => {
    $(selector)?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addCategory();
      }
    });
  });
  $("#projectDangerArchive").addEventListener("click", archiveProject);
  $("#projectDangerDelete").addEventListener("click", async () => {
    if (!current) return;
    await removeProject(current, { statusSelector: "#projectSettingsStatus" });
    // 项目已经没了，回个人空间的选择 / 新建那一屏
    if (activeSpace === "personal") showView("personal");
  });
  $("#projectArchive").addEventListener("click", archiveProject);

  // 不再有「新建项目」：每个空间进入时自动准备一个默认工作区
  $("#projectCreateForm").addEventListener("submit", submitCreate);
  $("#closeProjectCreate").addEventListener("click", () => closeDialog($("#projectCreateDialog")));

  // 设置入口：小组走 header 的齿轮，企业走左栏企业卡右上角的齿轮，都不再套「更多」菜单
  $("#teamSpaceSettings").addEventListener("click", () => openDrawer("team"));
  $("#spaceDrawerClose").addEventListener("click", closeDrawer);
  $("#spaceDrawerScrim").addEventListener("click", closeDrawer);

  $("#teamSelect").addEventListener("change", async () => {
    teamGroupId = Number($("#teamSelect").value) || null;
    if (!teamGroupId) return;
    const project = await ensureSpaceProject("team", teamGroupId);
    spaceItems = project ? [project] : [];
    renderList("team", spaceItems);
  });
  $("#enterpriseSelect").addEventListener("change", async () => {
    const ent = enterprises.find(
      (item) => String(item.id) === String($("#enterpriseSelect").value)
    );
    enterpriseGroupId = ent ? ent.group_id : null;
    if (!enterpriseGroupId) return;
    const project = await ensureSpaceProject("enterprise", enterpriseGroupId);
    spaceItems = project ? [project] : [];
    renderList("enterprise", spaceItems);
  });

  window.VSProjects = {
    enter: enterSpace,
    current: () => current,
    activeSpace: () => activeSpace,
    open: openProject,
    closeDrawer,
    /** 上传流程要用：拿到某个空间的默认工作区（没有就建一个）。 */
    ensureProjectFor: (kind, groupId) => ensureSpaceProject(kind, groupId),
    /** 上传完成后刷新个人空间的总结与「我的任务」。 */
    refreshPersonal: () => {
      if (activeSpace !== "personal") return Promise.resolve();
      return loadPersonal().catch(() => {});
    },
    /** 切栏目时把「更多」菜单收起，免得它挂在新页面边上 */
    closeMenus: closeSpaceMenus,
    /** 标注工作台保存 / 结束任务后回来，让任务列表跟上最新状态。 */
    refreshTasks: () => {
      loaded.overview = false;
      if (!current) return Promise.resolve();
      return loadTasks().catch(() => {});
    },
  };

  // 首屏默认停在个人空间；app.js 启动时 projects.js 还没加载，这里补一次
  enterSpace("personal").catch((error) => {
    const box = $("#personalSummary");
    if (box) box.replaceChildren(el("div", "empty-state project-empty", error.message));
  });
})();
