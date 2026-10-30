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
  let memberMap = null;          // 成员 id → 显示名，供任务行显示负责人
  let taskPickImages = [];       // 新建任务对话框里的图片清单
  let taskCreateBlocked = false; // 没有启用类目时拦住「创建任务」
  const loaded = { overview: false, tasks: false, members: false, categories: false, settings: false };

  /* ---------------- 把旧面板搬进新容器 ---------------- */

  function relocate() {
    // 个人空间的选择区 / 图像列表 → 项目页「图像」tab
    // （原来的 .upload-hero 已经删掉，「图像分析与标注」大标题不再存在）
    const imagesPane = $("#projectImagesPane");
    const personal = document.querySelector('[data-view="personal"]');
    if (imagesPane && personal) {
      [personal.querySelector("#selectionPanel"), personal.querySelector(".personal-data")]
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

  async function openProject(project) {
    current = project;
    tab = "overview";
    loaded.overview = loaded.tasks = loaded.members = loaded.categories = loaded.settings = false;
    // 换项目要重置列表态，否则会带着上个项目的筛选与成员缓存
    taskFilter = "";
    memberMap = null;
    taskPickImages = [];
    if ($("#projectTaskFilter")) $("#projectTaskFilter").value = "";

    // 标题区已经挪到顶栏（#appContextTitle / #appContextKicker）
    // 项目描述按需求从顶栏撤掉了，只留在「设置」里编辑
    $("#appContextTitle").textContent = project.name;
    $("#appContextKicker").textContent = `${SPACE_LABEL[labelOf(project)]} · ${groupLabel(project)}`;
    $("#projectRole").textContent = ROLE_LABEL[project.role] || project.role || "";
    $("#projectRole").hidden = false;
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
      steps.push({ label: "上传第一批图片", hint: "图片是任务的原料，先传几张。", action: () => switchTab("images") });
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
      rows.forEach((member) => {
        const account = member.account || {};
        memberMap.set(member.account_id, displayName(account) || `#${member.account_id}`);
      });
    } catch (error) {
      memberMap = new Map();
    }
    return memberMap;
  }

  async function loadTasks() {
    const [tasks, mine] = await Promise.all([
      call(() => sdk.listProjectTasks(current.id)),
      viewerOf(),
    ]);
    const names = await memberNames();
    const visible = taskFilter ? tasks.filter((task) => task.status === taskFilter) : tasks;
    // 工具行左边只放计数，像 GitHub 仓库页的「1 Branch · 0 Tags」那样一口气说完状态
    const metaBits = [`共 ${tasks.length} 个任务`];
    if (taskFilter) metaBits.push(`筛出 ${visible.length} 个`);
    const waitingAnnotate = visible.filter((task) => task.status === "queued").length;
    const waitingReview = visible.filter((task) => task.status === "auto_annotated").length;
    if (waitingAnnotate) metaBits.push(`${waitingAnnotate} 个待标注`);
    if (waitingReview) metaBits.push(`${waitingReview} 个待复核`);
    $("#projectTasksMeta").textContent = metaBits.join(" · ");
    const box = $("#projectTaskList");
    box.replaceChildren();
    if (!tasks.length) {
      box.append(el("div", "empty-state project-empty", "还没有任务。点右上角「新建任务」，选好图片和标注方式就能开始。"));
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
      meta.append(el("span", "task-chip", statusLabel(task)));
      meta.append(el("span", "", task.mode === "manual" ? "人工" : "自动"));
      if (task.image_total) {
        meta.append(el("span", "", `已标注 ${task.image_annotated} / ${task.image_total}`));
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
    $("#taskCreateSubmit").disabled = false;
    status("#taskCreateStatus", "");
    taskPickImages = [];
    const list = $("#taskPickList");
    list.replaceChildren(el("p", "task-pick-empty", "正在读取项目图片…"));
    $("#taskPickCount").textContent = "—";
    openDialog($("#taskCreateDialog"));
    window.setTimeout(() => $("#taskCreateName").focus(), 120);
    // 没有启用类目时提交会被后端挡回来（"项目还没有启用的类目"），先在这里说清楚
    const categories = await call(() => sdk.listCategories(current.id)).catch(() => []);
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
    const button = $("#taskCreateSubmit");
    button.disabled = true;
    status("#taskCreateStatus", "正在创建…");
    try {
      const created = await call(() => sdk.createTask(current.id, {
        name: $("#taskCreateName").value.trim() || undefined,
        imageIds,
        mode,
        reviewRequired,
      }));
      // 建出来还是 draft，这里顺手提交，省掉「建完再去列表里点提交」这一步。
      const task = await call(() => sdk.submitTask(current.id, created.id));
      closeDialog($("#taskCreateDialog"));
      const tail = reviewRequired ? "，标完进复核队列" : "，标完直接通过";
      status("#projectTaskStatus", `已提交${kind}任务「${task.name || `任务 #${task.id}`}」，共 ${imageIds.length} 张图片${tail}`);
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
      $("#projectDesc").textContent = current.description || "暂无描述";
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
    /** 标注工作台保存 / 结束任务后回来，让任务列表跟上最新状态。 */
    refreshTasks: () => {
      loaded.overview = false;
      if (!current) return Promise.resolve();
      return loadTasks().catch(() => {});
    },
  };

  // 首屏默认停在个人空间；app.js 启动时 projects.js 还没加载，这里补一次
  enterSpace("personal").catch((error) => {
    const box = $("#personalProjectList");
    if (box) box.replaceChildren(el("div", "empty-state project-empty", error.message));
  });
})();
