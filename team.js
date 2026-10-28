// 小组空间：独立整页。
// 布局：左半边是小组成员与小组信息（成员按行列出、显示各自上传数），
//       右半边是小组成果，可按成员筛选。
// 这里还负责「个人图片 → 小组库」的小组选择。
// 后端调用一律走 VS.sdk。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, sdk, el, status, runForm, submitButtonOf, displayName, avatarNode, formatSize } = VS;

  const grid = $("#teamGrid");
  const emptyState = $("#teamEmpty");
  const teamSelect = $("#teamSelect");
  const memberList = $("#memberList");
  const resultBox = $("#teamResults");
  const resultFilter = $("#resultFilter");
  const groupDialog = $("#groupDialog");
  const membersDialog = $("#membersDialog");
  const pickerDialog = $("#groupPickerDialog");
  const STORAGE_KEY = "vision_current_group";
  const ROLE_LABEL = { owner: "组长", admin: "管理员", member: "成员" };
  const ROLE_ORDER = { owner: 0, admin: 1, member: 2 };

  let me = null;
  let groups = [];
  let current = null;
  let members = [];
  let membersReady = false;
  let myRole = null;
  let canManage = false;
  let resultCount = 0;
  let editing = null;      // 编辑中的小组；null 表示新建
  let pendingImage = null; // 待上传到小组库的个人图片
  let loaded = false;

  // 成果与筛选
  let groupImages = [];              // 去重后的全部成果
  let memberFilter = "all";          // "all" 或账号 id
  let uploadStats = new Map();       // account_id → 上传张数
  const thumbUrls = new Map();       // image.id → objectURL，切筛选时不必重新下载

  /* ---------------- 读数据 ---------------- */

  async function enter() {
    if (!loaded) {
      try {
        me = await call(() => sdk.me());
      } catch (error) {
        status("#teamMeta", error.message, true);
        return;
      }
    }
    await reload();
    loaded = true;
  }

  async function reload(selectedId) {
    try {
      groups = await call(() => sdk.listGroups());
    } catch (error) {
      showEmpty("小组数据读取失败", error.message);
      renderSwitch();
      return;
    }

    renderSwitch();
    if (!groups.length) {
      current = null;
      showEmpty(
        "还没有加入小组",
        "创建一个小组，把个人图片汇总成小组成果，也让成员看到彼此的进度。",
      );
      return;
    }

    // 优先用调用方指定的，其次用上次看过的，最后退回第一个
    const remembered = Number(localStorage.getItem(STORAGE_KEY));
    const next =
      groups.find((group) => group.id === selectedId) ||
      (current && groups.find((group) => group.id === current.id)) ||
      groups.find((group) => group.id === remembered) ||
      groups[0];
    await selectGroup(next);
  }

  function showEmpty(title, detail) {
    grid.hidden = true;
    emptyState.hidden = false;
    emptyState.querySelector("strong").textContent = title;
    emptyState.querySelector("p").textContent = detail;
  }

  async function selectGroup(group) {
    current = group;
    memberFilter = "all";
    localStorage.setItem(STORAGE_KEY, String(group.id));
    grid.hidden = false;
    emptyState.hidden = true;
    teamSelect.value = String(group.id);
    renderInfo();
    await Promise.all([loadMembers(), loadResults()]);
  }

  /* ---------------- 顶部切换 ---------------- */

  function renderSwitch() {
    teamSelect.replaceChildren();
    if (!groups.length) {
      teamSelect.add(new Option("暂无小组", ""));
      teamSelect.disabled = true;
      return;
    }
    // 不再放"请选择"这种空占位：少一个空选项就少一次"选错了把表单清空"的机会
    teamSelect.disabled = false;
    groups.forEach((group) => teamSelect.add(new Option(group.name, String(group.id))));
    teamSelect.value = current ? String(current.id) : "";
  }

  teamSelect.addEventListener("change", async () => {
    const id = Number(teamSelect.value);
    const next = groups.find((group) => group.id === id);
    if (!next || (current && next.id === current.id)) return;
    await selectGroup(next);
  });

  /* ---------------- 小组信息 ---------------- */

  function renderInfo() {
    $("#teamNameText").textContent = current.name;
    $("#teamBadge").textContent = (current.name.trim()[0] || "组").toUpperCase();
    renderMeta();
  }

  function renderMeta() {
    const parts = [];
    if (myRole) parts.push(`你是${ROLE_LABEL[myRole] || myRole}`);
    if (members.length) parts.push(`${members.length} 名成员`);
    parts.push(`${resultCount} 项成果`);
    $("#teamMeta").textContent = parts.join(" · ");
  }

  /* ---------------- 成员 ---------------- */

  async function loadMembers() {
    membersReady = false;
    memberList.replaceChildren(el("li", "member-empty", "正在读取成员…"));
    try {
      members = await call(() => sdk.listGroupMembers(current.id));
    } catch (error) {
      memberList.replaceChildren(el("li", "member-empty", error.message));
      return;
    }
    membersReady = true;
    const mine = members.find((member) => member.account_id === me?.id);
    myRole = mine ? mine.role : null;
    canManage = myRole === "owner" || myRole === "admin";
    $("#editTeamButton").hidden = !canManage;
    $("#manageMembers").hidden = !canManage;
    renderAll();
  }

  // 每个成员传了多少张，用成果列表自己数：后端没有按成员统计的接口，
  // 这里的数字和右边格子完全对得上，不会出现"显示 3 张只看到 2 张"。
  function computeStats() {
    uploadStats = new Map();
    groupImages.forEach((image) => {
      const key = Number(image.uploader_account_id);
      if (!Number.isFinite(key)) return;
      uploadStats.set(key, (uploadStats.get(key) || 0) + 1);
    });
  }

  function orderedMembers() {
    return members
      .slice()
      .sort((a, b) => (ROLE_ORDER[a.role] ?? 3) - (ROLE_ORDER[b.role] ?? 3));
  }

  function renderMembers() {
    memberList.replaceChildren();
    $("#teamMemberCount").textContent = members.length;
    if (!membersReady) {
      memberList.append(el("li", "member-empty", "正在读取成员…"));
      return;
    }
    if (!members.length) {
      memberList.append(el("li", "member-empty", "还没有成员"));
      return;
    }
    orderedMembers().forEach((member) => {
      const account = member.account || { username: `账号 ${member.account_id}` };
      const count = uploadStats.get(Number(member.account_id)) || 0;
      const row = el("li", "member-line");
      if (member.account_id === me?.id) row.classList.add("is-me");

      const button = el("button", "member-line-button");
      button.type = "button";
      button.setAttribute("aria-pressed", String(memberFilter === String(member.account_id)));
      button.title = count
        ? `只看 ${displayName(account)} 上传的 ${count} 张`
        : `${displayName(account)} 还没有上传图片`;
      if (memberFilter === String(member.account_id)) row.classList.add("is-filtering");

      const copy = el("span", "member-line-copy");
      const nameLine = el("span", "member-line-name");
      nameLine.append(el("strong", "", displayName(account)));
      if (member.role !== "member") {
        nameLine.append(el("em", `role-tag role-${member.role}`, ROLE_LABEL[member.role] || member.role));
      }
      if (member.account_id === me?.id) nameLine.append(el("em", "role-tag role-me", "我"));
      copy.append(nameLine, el("small", "", `@${account.username || "—"}`));

      const badge = el("span", "member-line-count");
      badge.append(el("b", "", String(count)), el("small", "", "张"));

      button.append(avatarNode(account, 30), copy, badge);
      button.addEventListener("click", () => {
        const id = String(member.account_id);
        memberFilter = memberFilter === id ? "all" : id;
        renderAll();
      });
      row.append(button);
      memberList.append(row);
    });
  }

  /* ---------------- 成果筛选 ---------------- */

  function filterLabel(accountId) {
    if (accountId === "all") return "全部成员";
    const member = members.find((item) => String(item.account_id) === String(accountId));
    if (member) return displayName(member.account || { username: `账号 ${accountId}` });
    return "已退出的成员";
  }

  function renderFilter() {
    resultFilter.replaceChildren();
    if (!groupImages.length) {
      resultFilter.hidden = true;
      return;
    }
    resultFilter.hidden = false;

    const total = el("button", "filter-chip");
    total.type = "button";
    total.classList.toggle("is-active", memberFilter === "all");
    total.append(el("span", "", "全部"), el("b", "", String(groupImages.length)));
    total.addEventListener("click", () => { memberFilter = "all"; renderAll(); });
    resultFilter.append(total);

    // 有上传记录的成员 + 当前选中的那个（可能已经退出小组，仍要能取消）
    const ids = [...uploadStats.keys()].map(String);
    if (memberFilter !== "all" && !ids.includes(String(memberFilter))) ids.push(String(memberFilter));

    ids.sort((a, b) => (uploadStats.get(Number(b)) || 0) - (uploadStats.get(Number(a)) || 0));
    ids.forEach((id) => {
      const chip = el("button", "filter-chip");
      chip.type = "button";
      chip.classList.toggle("is-active", memberFilter === id);
      chip.append(el("span", "", filterLabel(id)), el("b", "", String(uploadStats.get(Number(id)) || 0)));
      chip.addEventListener("click", () => {
        memberFilter = memberFilter === id ? "all" : id;
        renderAll();
      });
      resultFilter.append(chip);
    });
  }

  /* ---------------- 右侧：小组成果 ---------------- */

  async function loadResults() {
    clearThumbs();
    resultBox.replaceChildren(el("p", "results-empty", "正在读取小组成果…"));
    try {
      const images = await call(() => sdk.listGroupImages(current.id));
      // 老数据里即使已有重复记录，也只按内容哈希展示一次；没有哈希时退回图片 id。
      groupImages = [...new Map(images.map((image) => [image.content_hash || `id:${image.id}`, image])).values()];
      resultCount = groupImages.length;
      if (memberFilter !== "all" && !groupImages.some((image) => String(image.uploader_account_id) === String(memberFilter))) {
        memberFilter = "all";
      }
      renderAll();
    } catch (error) {
      resultBox.replaceChildren(el("p", "results-empty", error.message));
    }
  }

  function clearThumbs() {
    thumbUrls.forEach((url) => URL.revokeObjectURL(url));
    thumbUrls.clear();
  }

  async function ensureThumb(image) {
    if (thumbUrls.has(image.id)) return thumbUrls.get(image.id);
    const { blob } = await call(() => sdk.imageContent(image.id));
    const url = URL.createObjectURL(blob);
    thumbUrls.set(image.id, url);
    return url;
  }

  // 成员列表、筛选项、成果格子三者共用同一份数据，统一从这里重画
  function renderAll() {
    computeStats();
    renderMembers();
    renderFilter();
    renderResults();
    renderMeta();
  }

  function renderResults() {
    const visible = memberFilter === "all"
      ? groupImages
      : groupImages.filter((image) => String(image.uploader_account_id) === String(memberFilter));

    resultBox.replaceChildren();
    resultBox.classList.toggle("is-empty", !visible.length);
    $("#teamResultsMeta").textContent = !groupImages.length
      ? "还没有成果"
      : memberFilter === "all"
        ? `${groupImages.length} 张图片 · 点击查看`
        : `只看 ${filterLabel(memberFilter)} · ${visible.length} 张`;

    if (!visible.length) {
      resultBox.append(el("p", "results-empty", groupImages.length
        ? `${filterLabel(memberFilter)} 还没有上传图片。`
        : "把成员上传的图片汇总到这里。点右上角「上传图片」，或在个人图像数据里点「上传到小组库」。"));
      return;
    }

    visible.slice().reverse().forEach((image) => {
      const tile = el("button", "result-tile");
      tile.type = "button";
      const uploader = members.find((member) => String(member.account_id) === String(image.uploader_account_id));
      const uploaderName = uploader ? displayName(uploader.account || { username: "成员" }) : "";
      tile.title = `${image.filename} · ${formatSize(image.size_bytes)}${uploaderName ? ` · ${uploaderName}` : ""}`;
      const thumb = el("img");
      thumb.alt = image.filename;
      const name = el("span", "result-name", image.filename);
      tile.append(thumb, name);
      if (uploaderName) tile.append(el("span", "result-uploader", uploaderName));
      tile.addEventListener("click", async () => {
        const personal = await window.VSApp?.findPersonalImage(image);
        window.VSApp?.openImageDetail(image, {
          source: "team",
          analysisImageId: personal?.id || image.id,
          canDelete: image.group_id === current.id && (image.uploader_account_id === me?.id || canManage),
        });
      });
      resultBox.append(tile);
      ensureThumb(image).then((url) => { thumb.src = url; }).catch(() => {});
    });
  }

  /* ---------------- 成员与角色弹窗 ---------------- */

  $("#manageMembers").addEventListener("click", () => {
    renderMemberRows();
    status("#memberStatus", "");
    $("#memberResults").replaceChildren();
    openDialog(membersDialog);
  });

  function renderMemberRows() {
    const box = $("#memberRows");
    box.replaceChildren();
    // 只有组长/管理员能进来，但角色调整权限仍要按当前角色再收一次
    $("#addMemberForm").hidden = !canManage;
    orderedMembers().forEach((member) => {
      const account = member.account || { username: `账号 ${member.account_id}` };
      const row = el("div", "member-row");
      row.append(avatarNode(account, 32));

      const copy = el("div", "member-row-copy");
      copy.append(el("strong", "", displayName(account)), el("small", "", `@${account.username || "—"}`));
      row.append(copy);

      // 组长不可改不可移；管理员不能动其他管理员（后端也会拦，这里先不给入口）
      const locked = member.role === "owner" || (myRole === "admin" && member.role === "admin");
      if (!canManage || locked) {
        row.append(el("span", `role-tag role-${member.role}`, ROLE_LABEL[member.role] || member.role));
      } else {
        const select = el("select", "role-select");
        select.setAttribute("aria-label", `${displayName(account)} 的角色`);
        // 一次 append 两个选项；select.add(option, before) 的第二个参数
        // 必须是已存在的子节点，直接塞 Option 会抛 NotFoundError
        select.append(new Option("成员", "member"), new Option("管理员", "admin"));
        select.value = member.role;
        select.addEventListener("change", async () => {
          try {
            await call(() => sdk.updateGroupMemberRole(current.id, member.account_id, select.value));
            status("#memberStatus", "成员角色已更新");
            await loadMembers();
            renderMemberRows();
          } catch (error) {
            status("#memberStatus", error.message, true);
            select.value = member.role;
          }
        });
        row.append(select);

        if (member.account_id !== me?.id) {
          const remove = el("button", "text-button", "移除");
          remove.type = "button";
          remove.addEventListener("click", async () => {
            const confirmed = await window.confirmDanger({
              kicker: "REMOVE MEMBER",
              title: "移出这名成员？",
              message: `${displayName(account)} 将失去这个小组的访问权限。`,
              detail: "之后仍可重新邀请该成员加入。",
              confirmText: "确认移出",
            });
            if (!confirmed) return;
            try {
              await call(() => sdk.removeGroupMember(current.id, member.account_id));
              status("#memberStatus", "成员已移除");
              await loadMembers();
              renderMemberRows();
            } catch (error) {
              status("#memberStatus", error.message, true);
            }
          });
          row.append(remove);
        }
      }
      box.append(row);
    });
  }

  $("#addMemberForm").hidden = true;

  $("#searchMemberButton").addEventListener("click", async () => {
    const query = $("#memberSearch").value.trim();
    if (query.length < 2) {
      status("#memberStatus", "请输入至少 2 位用户名", true);
      return;
    }
    const target = $("#memberResults");
    target.textContent = "正在查找…";
    try {
      const results = await call(() => sdk.searchAccounts(query));
      target.replaceChildren();
      if (!results.length) target.textContent = "没有找到账号";
      results.forEach((account) => {
        const button = el("button", "member-result");
        button.type = "button";
        button.append(avatarNode(account, 24), el("span", "", `${displayName(account)} · @${account.username}`));
        button.addEventListener("click", async () => {
          try {
            await call(() => sdk.addGroupMember(current.id, account.id, { role: "member" }));
            target.replaceChildren();
            $("#memberSearch").value = "";
            status("#memberStatus", `${displayName(account)} 已加入小组`);
            await loadMembers();
            renderMemberRows();
          } catch (error) {
            status("#memberStatus", error.message, true);
          }
        });
        target.append(button);
      });
      status("#memberStatus", "");
    } catch (error) {
      target.replaceChildren();
      status("#memberStatus", error.message, true);
    }
  });

  /* ---------------- 新建 / 编辑小组 ---------------- */

  function openGroupDialog(group) {
    editing = group || null;
    $("#groupDialogTitle").textContent = group ? "编辑小组资料" : "新建小组";
    $("#groupDialogKicker").textContent = group ? "EDIT GROUP" : "NEW GROUP";
    $("#groupDialogDesc").textContent = group
      ? "修改小组名称，保存后组员会看到最新资料。"
      : "给小组起个名字，之后可以随时修改。";
    $("#saveGroupButton").textContent = group ? "保存" : "创建小组";
    $("#groupName").value = group?.name || "";
    $("#groupDanger").hidden = !group || myRole !== "owner";
    status("#groupStatus", "");
    openDialog(groupDialog);
  }

  $("#editTeamButton").addEventListener("click", () => openGroupDialog(current));
  $("#newTeamButton").addEventListener("click", () => openGroupDialog(null));
  $("#createFirstTeam").addEventListener("click", () => openGroupDialog(null));
  $("#closeGroupDialog").addEventListener("click", () => closeDialog(groupDialog));

  $("#groupForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const name = $("#groupName").value.trim();
    if (!name) {
      status("#groupStatus", "小组名称不能为空", true);
      return;
    }
    const target = editing;
    runForm(submitButtonOf(form, event), "#groupStatus", async () => {
      const saved = await call(() => target
        ? sdk.updateGroup(target.id, { name })
        : sdk.createGroup(name));
      closeDialog(groupDialog);
      await reload(saved.id);
      showUploadToast(target ? "小组资料已保存" : "小组已创建", saved.name);
    });
  });

  $("#dissolveTeamButton").addEventListener("click", async () => {
    if (!editing || myRole !== "owner") return;
    const group = editing;
    const confirmed = await window.confirmDanger({
      kicker: "DISSOLVE GROUP",
      title: "解散这个小组？",
      message: `“${group.name}”解散后，所有成员都会失去访问权限。`,
      detail: "如果小组仍有关联的活动项目，后端会保护数据并拒绝解散。",
      confirmText: "确认解散",
    });
    if (!confirmed) {
      // 确认卡收走后把小组弹窗重新摆出来，否则「解散」点到一半就没得继续了
      if (groupDialog.hidden) openDialog(groupDialog);
      return;
    }
    const button = $("#dissolveTeamButton");
    button.disabled = true;
    button.textContent = "正在解散…";
    try {
      await call(() => sdk.deleteGroup(group.id));
      localStorage.removeItem(STORAGE_KEY);
      current = null;
      editing = null;
      closeDialog(groupDialog);
      await reload();
      showUploadToast("小组已解散", group.name);
    } catch (error) {
      // 失败原因要让人看见：小组弹窗此时已经被确认卡挤掉了，先摆回来再写状态
      if (groupDialog.hidden) {
        openDialog(groupDialog);
        await new Promise((resolve) => window.setTimeout(resolve, 60));
      }
      status("#groupStatus", error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "解散小组";
    }
  });

  /* ---------------- 上传图片到小组 ---------------- */

  $("#uploadToTeam").addEventListener("click", () => $("#teamUploadInput").click());

  $("#teamUploadInput").addEventListener("change", async (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    if (!files.length || !current) return;

    const meta = $("#teamResultsMeta");
    let done = 0;
    let failed = 0;
    // 后端组图接口一次只收一张，逐张上传才能显示「第几张/共几张」
    for (const file of files) {
      meta.textContent = `正在上传 ${done + failed + 1}/${files.length}…`;
      try {
        await call(() => sdk.uploadGroupImages(current.id, file));
        done += 1;
      } catch {
        failed += 1;
      }
    }
    await loadResults();
    if (failed) showUploadToast(`已上传 ${done} 张`, `${failed} 张没有成功，请确认是 JPG/PNG/WEBP 且不超过 50 MB`);
    else showUploadToast(`已上传 ${done} 张`, `已加入「${current.name}」的小组成果`);
  });

  /* ---------------- 个人图片 → 小组库 ---------------- */

  // 用户可能从没打开过小组页就直接点"上传到小组库"，
  // 所以这里必须自己拉一次列表，不能依赖 enter() 留下的缓存。
  async function openPicker(image) {
    pendingImage = image;
    status("#groupPickerStatus", "");
    $("#groupPickerDesc").textContent = `选一个小组，「${image.filename}」会加入小组成果，并沿用现有分析状态。`;
    const list = $("#groupPickerList");
    list.replaceChildren(el("p", "results-empty", "正在读取小组…"));
    openDialog(pickerDialog);

    try {
      groups = await call(() => sdk.listGroups());
    } catch (error) {
      list.replaceChildren(el("p", "results-empty", error.message));
      return;
    }
    if (!pendingImage) return; // 等待期间弹窗被关掉了

    list.replaceChildren();
    if (!groups.length) {
      list.append(el("p", "results-empty", "你还没有加入任何小组。先到「小组空间」创建一个，再回来上传。"));
      return;
    }
    groups.forEach((group) => {
      const button = el("button", "picker-item");
      button.type = "button";
      button.append(el("strong", "", group.name), el("small", "", group.type === "org" ? "组织" : "小组"));
      button.addEventListener("click", () => sendToGroup(group, button));
      list.append(button);
    });
  }

  async function sendToGroup(group, button) {
    if (!pendingImage) return;
    button.disabled = true;
    status("#groupPickerStatus", `正在上传到「${group.name}」…`);
    try {
      const existing = await call(() => sdk.listGroupImages(group.id));
      const alreadyThere = pendingImage.content_hash
        && existing.some((image) => image.content_hash === pendingImage.content_hash);
      if (alreadyThere) {
        status("#groupPickerStatus", `「${group.name}」已经有这张图片`);
        if (current && current.id === group.id) loadResults();
        window.setTimeout(() => {
          pendingImage = null;
          closeDialog(pickerDialog);
        }, 700);
        return;
      }
      // 后端没有"从图库复制到组"的接口，这里取回字节再按普通上传提交；
      // 同组内 sha256 相同会直接复用已有记录，不会重复占存储。
      const { blob } = await call(() => sdk.imageContent(pendingImage.id));
      const file = new File([blob], pendingImage.filename, { type: blob.type || "image/png" });
      await call(() => sdk.uploadGroupImages(group.id, file));
      status("#groupPickerStatus", `已上传到「${group.name}」的小组库`);
      if (current && current.id === group.id) loadResults();
      window.setTimeout(() => {
        pendingImage = null;
        closeDialog(pickerDialog);
      }, 700);
    } catch (error) {
      status("#groupPickerStatus", error.message, true);
      button.disabled = false;
    }
  }

  $("#closeGroupPicker").addEventListener("click", () => closeDialog(pickerDialog));

  /* ---------------- 对外接口 ---------------- */

  $("#closeMembersDialog").addEventListener("click", () => closeDialog(membersDialog));

  // app.js 在删图、改资料后需要让这里跟着刷新
  window.VSTeam = {
    enter,
    openPicker,
    refreshMembers() {
      if (current) loadMembers();
    },
    refreshResults() {
      if (current && !grid.hidden) loadResults();
    },
  };
})();
