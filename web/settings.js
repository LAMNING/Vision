// 个人资料、头像与账号安全。小组相关逻辑在 team.js。
// 所有请求走 VS.sdk，不在这里拼 URL。
(() => {
  const $ = (selector) => document.querySelector(selector);
  // 资料、外观、密码现在是同一个设置弹窗里的三节
  const settingsDialog = $("#settingsDialog");
  const { status, runForm, submitButtonOf, sdk, call, forgetAvatar, fillAvatar, el } = VS;
  let me = null;

  const AVATAR_LIMIT = 4 * 1024 * 1024;

  // 原版后端没有展示名称/头像，连上去时把这几块藏掉，
  // 而不是让用户填完再吃一个 422。说明文案也要跟着改，
  // 不能写着「可以修改展示名称」却看不到那个输入框。
  function applyCapabilities() {
    const { profile, avatar } = VS.caps;
    [["profileDisplayName", profile]].forEach(([id, on]) => {
      const input = $(`#${id}`);
      input.hidden = !on;
      const label = document.querySelector(`label[for="${id}"]`);
      if (label) label.hidden = !on;
    });
    const editor = $(".avatar-editor");
    if (editor) editor.hidden = !avatar;
  }

  function renderProfile() {
    $("#profileUsername").value = me.username;
    $("#profileDisplayName").value = me.display_name || "";
    $("#profileEmail").value = me.email || "";
    $("#profilePhone").value = me.phone || "";
    fillAvatar($("#profileAvatar"), me);
    $("#removeAvatar").hidden = !me.has_avatar;
  }

  async function loadProfile() {
    status("#profileStatus", "正在读取资料…");
    try {
      me = await call(() => sdk.me());
      applyCapabilities();
      renderProfile();
      status("#profileStatus", "");
    } catch (error) {
      status("#profileStatus", error.message, true);
    }
  }

  settingsDialog.addEventListener("vision:open", loadProfile);

  // 资料改完要同步三处：顶栏、小组页成员条、本弹窗自己的头像区
  function broadcast() {
    if (typeof setAccountBadge === "function") setAccountBadge(me);
    if (window.VSTeam) window.VSTeam.refreshMembers();
  }

  /* ---------------- 资料 ---------------- */

  $("#profileForm").addEventListener("submit", (event) => {
    event.preventDefault();
    // 必须在第一个 await 之前把 form 存下来：事件派发结束后
    // event.currentTarget 会被浏览器置为 null，之后再用就是 TypeError
    const form = event.currentTarget;
    runForm(submitButtonOf(form, event), "#profileStatus", async () => {
      const payload = {
        email: $("#profileEmail").value.trim() || null,
        phone: $("#profilePhone").value.trim() || null,
      };
      // 只有后端认这两个字段时才提交，否则原版后端会整单 422
      if (VS.caps.profile) {
        payload.display_name = $("#profileDisplayName").value.trim() || null;
      }
      me = await call(() => sdk.updateMe(payload));
      broadcast();
      status("#profileStatus", "资料已保存");
    });
  });

  /* ---------------- 头像 ---------------- */

  $("#pickAvatar").addEventListener("click", () => $("#avatarInput").click());

  $("#avatarInput").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    event.target.value = "";
    if (!file) return;
    if (file.size > AVATAR_LIMIT) {
      status("#profileStatus", "图片不能超过 4 MB", true);
      return;
    }

    const button = $("#pickAvatar");
    button.disabled = true;
    status("#profileStatus", "正在上传头像…");
    try {
      me = await call(() => sdk.uploadAvatar(file));
      // 不先清缓存的话，成员条和顶栏还会拿到上一张图的 objectURL
      forgetAvatar(me.id);
      renderProfile();
      broadcast();
      status("#profileStatus", "头像已更新");
    } catch (error) {
      status("#profileStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  // 移除头像是个可逆的小动作（重新上传即可），不值得弹红色危险卡。
  // 和删图一致：按钮原地进入确认态，再点一次才真正执行，4.5 秒没点就自己恢复。
  let removeArmed = false;
  let removeTimer = 0;
  const removeAvatarButton = $("#removeAvatar");
  const removeAvatarLabel = removeAvatarButton ? removeAvatarButton.textContent : "";

  function resetRemoveArm() {
    if (!removeAvatarButton) return;
    removeArmed = false;
    clearTimeout(removeTimer);
    removeAvatarButton.classList.remove("is-armed");
    removeAvatarButton.textContent = removeAvatarLabel;
  }

  removeAvatarButton?.addEventListener("click", async () => {
    if (!me) return;
    if (!removeArmed) {
      removeArmed = true;
      removeAvatarButton.textContent = "确认移除";
      removeAvatarButton.classList.add("is-armed");
      clearTimeout(removeTimer);
      removeTimer = window.setTimeout(resetRemoveArm, 4500);
      return;
    }
    resetRemoveArm();
    const button = removeAvatarButton;
    button.disabled = true;
    try {
      await call(() => sdk.deleteAvatar());
      forgetAvatar(me.id);
      me.has_avatar = false;
      renderProfile();
      broadcast();
      status("#profileStatus", "头像已移除");
    } catch (error) {
      status("#profileStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  });

  /* ---------------- 改密码 ---------------- */

  $("#passwordForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const oldPassword = $("#oldPassword").value;
    const newPassword = $("#newPassword").value;
    if (newPassword !== $("#confirmPassword").value) {
      status("#passwordStatus", "两次输入的新密码不一致", true);
      return;
    }
    runForm(submitButtonOf(form, event), "#passwordStatus", async () => {
      await call(() => sdk.changePassword(oldPassword, newPassword));
      form.reset();
      status("#passwordStatus", "密码已修改，正在返回登录页…");
      // 改完密码后端会让所有 token 失效，本地也清掉再跳
      window.setTimeout(VS.signOut, 900);
    });
  });

  /* ---------------- 抽屉分区 ---------------- */

  const hubBody = document.querySelector(".settings-hub-body");

  function showPane(name) {
    if (!hubBody) return;
    hubBody.querySelectorAll(".settings-nav-item").forEach((button) => {
      const on = button.dataset.settingsPane === name;
      button.classList.toggle("is-active", on);
      if (on) button.setAttribute("aria-current", "true");
      else button.removeAttribute("aria-current");
    });
    hubBody.querySelectorAll(".settings-pane").forEach((pane) => {
      pane.classList.toggle("is-active", pane.dataset.pane === name);
    });
    if (name === "space") loadSpaces();
  }

  if (hubBody) {
    hubBody.querySelector(".settings-nav")?.addEventListener("click", (event) => {
      const button = event.target.closest("[data-settings-pane]");
      if (button) showPane(button.dataset.settingsPane);
    });
  }

  /* ---------------- 企业与小组 ---------------- */

  const ROLE_TEXT = {
    owner: "所有者", admin: "管理员", manager: "项目管理员",
    reviewer: "复核员", annotator: "标注员", member: "成员", guest: "只读访客",
  };

  // 后端 app/data 里的角色等级是写死的，这里如实说明，不做成假开关
  const PERMISSION_ROWS = [
    ["所有者 / 管理员", true, true, true],
    ["复核员", true, true, false],
    ["标注员", true, false, false],
    ["成员", true, false, false],
    ["只读访客", false, false, false],
  ];

  let spacesLoaded = false;

  function permissionTable() {
    const wrap = el("div", "settings-perm-wrap");
    const table = el("table", "settings-perm-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    ["角色", "上传", "复核", "管成员"].forEach((label) => headRow.append(el("th", "", label)));
    thead.append(headRow);
    const tbody = document.createElement("tbody");
    PERMISSION_ROWS.forEach(([role, upload, review, manage]) => {
      const tr = document.createElement("tr");
      tr.append(el("td", "", role));
      [upload, review, manage].forEach((ok) => {
        tr.append(el("td", ok ? "settings-perm-yes" : "settings-perm-no", ok ? "可" : "—"));
      });
      tbody.append(tr);
    });
    table.append(thead, tbody);
    wrap.append(table);
    wrap.append(el("p", "settings-perm-note", "角色等级由后端固定，这里只说明谁能做什么，不能直接改。"));
    return wrap;
  }

  function spaceCard(title, meta, onOpen) {
    const card = el("div", "settings-space-card");
    const copy = el("span", "settings-space-card-copy");
    copy.append(el("strong", "", title), el("small", "", meta));
    const button = el("button", "secondary-button", "空间设置");
    button.type = "button";
    button.addEventListener("click", onOpen);
    card.append(copy, button);
    return card;
  }

  function heading(text) {
    const row = el("div", "settings-space-heading");
    row.append(el("strong", "", text));
    return row;
  }

  /** 统一抽屉里不重做一套空间设置，直接把人送回原空间并把那边的设置打开。 */
  function jumpToSpace(kind) {
    document.querySelector("#closeSettingsDialog")?.click();
    const rail = document.querySelector(kind === "enterprise" ? "#railEnterprise" : "#railTeam");
    rail?.click();
    window.setTimeout(() => {
      if (kind === "enterprise") {
        document.querySelector("#entSettingsButton")?.click();
        return;
      }
      // 小组 header 现在是齿轮直接开设置，不用先点「更多」再点菜单项
      document.querySelector("#teamSpaceSettings")?.click();
    }, 800);
  }

  async function loadSpaces(force = false) {
    const body = document.querySelector("#settingsSpaceBody");
    if (!body) return;
    if (spacesLoaded && !force) return;
    spacesLoaded = true;
    body.replaceChildren(el("p", "settings-status", "正在读取你所在的企业与小组…"));

    const [enterprises, groups] = await Promise.all([
      call(() => sdk.listEnterprises()).catch(() => []),
      call(() => sdk.listGroups()).catch(() => []),
    ]);

    const frag = document.createDocumentFragment();

    const entBlock = el("div", "settings-space-group");
    entBlock.append(heading("企业"));
    if (!(enterprises || []).length) {
      entBlock.append(el("p", "settings-space-empty", "还没有加入任何企业"));
    } else {
      enterprises.forEach((ent) => {
        const meta = [
          ROLE_TEXT[ent.my_role] || ent.my_role || "成员",
          `${ent.member_count ?? 0} 名成员`,
          `企业码 ${ent.code}`,
        ].join(" · ");
        entBlock.append(spaceCard(ent.name, meta, () => jumpToSpace("enterprise")));
      });
    }
    frag.append(entBlock);

    const teamBlock = el("div", "settings-space-group");
    teamBlock.append(heading("小组"));
    if (!(groups || []).length) {
      teamBlock.append(el("p", "settings-space-empty", "还没有加入任何小组"));
    } else {
      groups.forEach((group) => {
        const meta = `${group.type === "enterprise" ? "企业小组" : "小组"} · 图片配额 ${group.quota_images ?? "—"} 张`;
        teamBlock.append(spaceCard(group.name, meta, () => jumpToSpace("team")));
      });
    }
    frag.append(teamBlock);

    const permBlock = el("div", "settings-space-group");
    permBlock.append(heading("成员权限"), permissionTable());
    frag.append(permBlock);

    body.replaceChildren(frag);
  }

  window.VSSettings = {
    showPane,
    refreshSpaces: () => loadSpaces(true),
  };
})();
