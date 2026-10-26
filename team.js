// 小组空间：独立整页。
// 布局：左上小组信息卡（管理员可编辑）、左下小组聊天（可展开全屏）、右侧小组成果。
// 这里还负责「个人图片 → 小组库」的小组选择。
// 后端调用一律走 VS.sdk。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, sdk, el, status, runForm, submitButtonOf, displayName, avatarNode, formatSize } = VS;

  const grid = $("#teamGrid");
  const emptyState = $("#teamEmpty");
  const teamSelect = $("#teamSelect");
  const memberStrip = $("#memberStrip");
  const resultBox = $("#teamResults");
  const chatCard = $("#teamChatCard");
  const chatScrim = $("#chatScrim");
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
  let myRole = null;
  let canManage = false;
  let resultCount = 0;
  let resultUrls = [];
  let editing = null;      // 编辑中的小组；null 表示新建
  let pendingImage = null; // 待上传到小组库的个人图片
  let loaded = false;
  let chatAvailable = null;
  let chatMessages = [];
  let chatPollTimer = 0;

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
    localStorage.setItem(STORAGE_KEY, String(group.id));
    grid.hidden = false;
    emptyState.hidden = true;
    teamSelect.value = String(group.id);
    renderInfo();
    await Promise.all([loadMembers(), loadResults(), loadChat(true)]);
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

  /* ---------------- 左上：小组信息 ---------------- */

  function renderInfo() {
    $("#teamNameText").textContent = current.name;
    $("#teamBadge").textContent = (current.name.trim()[0] || "组").toUpperCase();
    const desc = $("#teamDesc");
    desc.textContent = current.description || (current.type === "org" ? "尚未填写组织介绍" : "尚未填写小组介绍");
    desc.classList.toggle("is-empty", !current.description);
    renderMeta();
  }

  function renderMeta() {
    const parts = [];
    if (myRole) parts.push(ROLE_LABEL[myRole] || myRole);
    if (members.length) parts.push(`${members.length} 名成员`);
    parts.push(`${resultCount} 项成果`);
    $("#teamMeta").textContent = parts.join(" · ");
  }

  /* ---------------- 成员 ---------------- */

  async function loadMembers() {
    memberStrip.replaceChildren(el("li", "member-empty", "正在读取成员…"));
    try {
      members = await call(() => sdk.listGroupMembers(current.id));
    } catch (error) {
      memberStrip.replaceChildren(el("li", "member-empty", error.message));
      return;
    }
    const mine = members.find((member) => member.account_id === me.id);
    myRole = mine ? mine.role : null;
    canManage = myRole === "owner" || myRole === "admin";
    $("#editTeamButton").hidden = !canManage;
    $("#manageMembers").hidden = !canManage;
    renderMembers();
    renderMeta();
  }

  function renderMembers() {
    memberStrip.replaceChildren();
    const ordered = members
      .slice()
      .sort((a, b) => (ROLE_ORDER[a.role] ?? 3) - (ROLE_ORDER[b.role] ?? 3));
    ordered.forEach((member) => {
      const account = member.account || { username: `账号 ${member.account_id}` };
      const chip = el("li", "member-chip");
      if (member.account_id === me?.id) chip.classList.add("is-me");
      chip.title = `${displayName(account)}${ROLE_LABEL[member.role] ? ` · ${ROLE_LABEL[member.role]}` : ""}`;
      chip.append(avatarNode(account, 26), el("span", "member-name", displayName(account)));
      if (member.role !== "member") {
        chip.append(el("em", `role-tag role-${member.role}`, ROLE_LABEL[member.role] || member.role));
      }
      memberStrip.append(chip);
    });
    $("#teamMemberCount").textContent = members.length;
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
    members.forEach((member) => {
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
      ? "修改小组名称与介绍，保存后组员会看到最新资料。"
      : "给小组起个名字，之后可以随时修改。";
    $("#saveGroupButton").textContent = group ? "保存" : "创建小组";
    $("#groupName").value = group?.name || "";
    $("#groupDescription").value = group?.description || "";
    const descOn = VS.caps.groupDescription;
    $("#groupDescription").disabled = !descOn;
    $("#groupDescription").placeholder = descOn
      ? "写下小组的任务或分工（可选）"
      : "当前局域网后端尚未开放小组介绍字段";
    const descLabel = document.querySelector('label[for="groupDescription"]');
    if (descLabel) descLabel.hidden = false;
    $("#groupDanger").hidden = !group || myRole !== "owner";
    status("#groupStatus", descOn ? "" : "介绍功能需要局域网后端增加 description 字段", !descOn);
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
    // 后端不支持介绍时不要往 payload 里塞 description，否则整单 422
    const description = VS.caps.groupDescription
      ? ($("#groupDescription").value.trim() || null)
      : undefined;
    runForm(submitButtonOf(form, event), "#groupStatus", async () => {
      const saved = await call(() => target
        ? sdk.updateGroup(target.id, description === undefined ? { name } : { name, description })
        : sdk.createGroup(name, description === undefined ? {} : { description }));
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
    if (!confirmed) return;
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
      status("#groupStatus", error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "解散小组";
    }
  });

  /* ---------------- 右侧：小组成果 ---------------- */

  async function loadResults() {
    clearResultUrls();
    resultBox.replaceChildren(el("p", "results-empty", "正在读取小组成果…"));
    try {
      renderResults(await call(() => sdk.listGroupImages(current.id)));
    } catch (error) {
      resultBox.replaceChildren(el("p", "results-empty", error.message));
    }
  }

  function clearResultUrls() {
    resultUrls.forEach((url) => URL.revokeObjectURL(url));
    resultUrls = [];
  }

  function renderResults(images) {
    // 老数据里即使已有重复记录，也只按内容哈希展示一次；没有哈希时退回图片 id。
    const unique = [...new Map(images.map((image) => [image.content_hash || `id:${image.id}`, image])).values()];
    resultCount = unique.length;
    resultBox.replaceChildren();
    resultBox.classList.toggle("is-empty", !unique.length);
    $("#teamResultsMeta").textContent = unique.length ? `${unique.length} 张图片 · 点击查看` : "还没有成果";

    if (!unique.length) {
      resultBox.append(el("p", "results-empty",
        "把成员上传的图片汇总到这里。点右上角「上传图片」，或在个人图像数据里点「上传到小组库」。"));
      renderMeta();
      return;
    }

    unique.slice().reverse().forEach((image) => {
      const tile = el("button", "result-tile");
      tile.type = "button";
      tile.title = `${image.filename} · ${formatSize(image.size_bytes)}`;
      const thumb = el("img");
      thumb.alt = image.filename;
      tile.append(thumb, el("span", "result-name", image.filename));
      tile.addEventListener("click", async () => {
        const personal = await window.VSApp?.findPersonalImage(image);
        window.VSApp?.openImageDetail(image, {
          source: "team",
          analysisImageId: personal?.id || image.id,
          canDelete: image.group_id === current.id && (image.uploader_account_id === me?.id || canManage),
        });
      });
      resultBox.append(tile);
      loadResultThumb(image, thumb);
    });
    renderMeta();
  }

  async function loadResultThumb(image, target) {
    try {
      const { blob } = await call(() => sdk.imageContent(image.id));
      const url = URL.createObjectURL(blob);
      resultUrls.push(url);
      target.src = url;
    } catch {
      // 缩略图失败不影响点开看原图
    }
  }

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
        if (current && current.id === group.id) renderResults(existing);
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

  /* ---------------- 聊天：真实接口 + 轻量轮询 ---------------- */

  function stopChatPoll() {
    clearTimeout(chatPollTimer);
    chatPollTimer = 0;
  }

  function messageTime(value) {
    if (!value) return "刚刚";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "刚刚";
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  function renderChat() {
    const body = $("#chatBody");
    body.replaceChildren();
    if (!chatMessages.length) {
      body.append(el("p", "chat-empty", chatAvailable === false
        ? "当前局域网后端还没有聊天接口。前端不会用本地假消息冒充小组聊天。"
        : "还没有消息，发一句话开始讨论。"));
      return;
    }
    chatMessages.forEach((message) => {
      const mine = Number(message.account_id) === Number(me?.id);
      const row = el("article", `chat-message${mine ? " is-me" : ""}`);
      const account = message.account || { id: message.account_id, username: `账号 ${message.account_id}` };
      if (!mine) row.append(avatarNode(account, 26));
      const copy = el("div", "chat-message-copy");
      const meta = el("p", "chat-message-meta");
      meta.append(el("span", "", mine ? "我" : displayName(account)), el("time", "", messageTime(message.created_at)));
      copy.append(meta, el("p", "chat-bubble", message.content || ""));
      row.append(copy);
      body.append(row);
    });
    body.scrollTop = body.scrollHeight;
  }

  function scheduleChatPoll() {
    stopChatPoll();
    if (!chatAvailable || !current) return;
    chatPollTimer = window.setTimeout(async () => {
      await loadChat(false);
    }, 4000);
  }

  async function loadChat(reset = false) {
    stopChatPoll();
    if (!current) return;
    if (reset) {
      chatMessages = [];
      $("#chatBody").replaceChildren(el("p", "chat-empty", "正在读取小组消息…"));
    }
    const lastId = reset || !chatMessages.length ? null : chatMessages[chatMessages.length - 1].id;
    try {
      const incoming = await call(() => sdk.listGroupMessages(current.id, { afterId: lastId, limit: 100 }));
      chatAvailable = true;
      if (reset) chatMessages = incoming;
      else {
        const known = new Set(chatMessages.map((message) => message.id));
        chatMessages.push(...incoming.filter((message) => !known.has(message.id)));
      }
      $("#chatStatus").textContent = "小组成员实时同步";
      $("#chatInput").disabled = false;
      $("#sendChat").disabled = false;
      renderChat();
      scheduleChatPoll();
    } catch (error) {
      chatAvailable = false;
      $("#chatStatus").textContent = "等待后端开放消息接口";
      $("#chatInput").disabled = true;
      $("#sendChat").disabled = true;
      renderChat();
    }
  }

  $("#chatForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!current || !chatAvailable) return;
    const input = $("#chatInput");
    const content = input.value.trim();
    if (!content) return;
    const button = $("#sendChat");
    input.disabled = true;
    button.disabled = true;
    try {
      const message = await call(() => sdk.sendGroupMessage(current.id, content));
      chatMessages.push(message);
      input.value = "";
      renderChat();
    } catch (error) {
      $("#chatStatus").textContent = error.message;
    } finally {
      input.disabled = !chatAvailable;
      button.disabled = !chatAvailable;
      input.focus();
      scheduleChatPoll();
    }
  });

  /* ---------------- 聊天：展开到全屏 ---------------- */

  function setChatExpanded(expanded) {
    chatCard.classList.toggle("is-expanded", expanded);
    chatScrim.hidden = !expanded;
    document.body.classList.toggle("chat-open", expanded);
    $("#expandChat").querySelector(".material-symbols-rounded").textContent =
      expanded ? "close_fullscreen" : "open_in_full";
    $("#expandChat").setAttribute("aria-label", expanded ? "收起聊天" : "展开聊天");
  }

  $("#expandChat").addEventListener("click", () => {
    setChatExpanded(!chatCard.classList.contains("is-expanded"));
  });
  chatScrim.addEventListener("click", () => setChatExpanded(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && chatCard.classList.contains("is-expanded")) setChatExpanded(false);
  });

  $("#closeMembersDialog").addEventListener("click", () => closeDialog(membersDialog));

  /* ---------------- 对外接口 ---------------- */

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
