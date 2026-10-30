// 个人资料、头像与账号安全。小组相关逻辑在 team.js。
// 所有请求走 VS.sdk，不在这里拼 URL。
(() => {
  const $ = (selector) => document.querySelector(selector);
  // 资料、外观、密码现在是同一个设置弹窗里的三节
  const settingsDialog = $("#settingsDialog");
  const { status, runForm, submitButtonOf, sdk, call, forgetAvatar, fillAvatar } = VS;
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

    const desc = profile
      ? "个人资料、外观与账号安全都在这里。用户名作为登录账号暂不更改。"
      : "可以修改联系方式与登录密码。用户名作为登录账号暂不更改。";
    const descNode = $("#profileDescription");
    if (descNode) descNode.textContent = desc;
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

  $("#removeAvatar").addEventListener("click", async () => {
    if (!me) return;
    const confirmed = await window.confirmDanger({
      kicker: "REMOVE AVATAR",
      title: "移除当前头像？",
      message: "移除后，头像位置会显示用户名首字。",
      detail: "账号资料和已上传的图像不会受到影响。",
      confirmText: "确认移除",
    });
    if (!confirmed) return;
    const button = $("#removeAvatar");
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
})();
