// Vision Studio — 页面逻辑
// 主线：校验登录态 → 上传图片 → 读取图像列表 → 查看 / 删除详情
// 交互：右侧工具轨悬停展开，退出按钮分裂成卡片、三段式收回
// 后端调用一律走 VS.sdk，本文件不出现 fetch。

const { sdk, call } = VS;
const el = VS.el;

// 页面一次可暂存 50 张；后端项目上传接口每次接收 20 张，确认时自动分批。
const MAX_BATCH_FILES = 50;
const API_BATCH_FILES = 20;

const imageUpload = document.querySelector("#imageUpload");
const imageDataState = document.querySelector("#imageDataState");
const uploadZone = document.querySelector(".upload-zone");
const uploadFeedback = document.querySelector("#uploadFeedback");
const selectionPanel = document.querySelector("#selectionPanel");
const selectionTitle = document.querySelector("#selectionTitle");
const selectedFileList = document.querySelector("#selectedFileList");
const selectionSummary = document.querySelector("#selectionSummary");
const analysisTargets = document.querySelector("#analysisTargets");
const confirmAnalysis = document.querySelector("#confirmAnalysis");
const clearSelection = document.querySelector("#clearSelection");
const uploadToast = document.querySelector("#uploadToast");
const uploadToastTitle = document.querySelector("#uploadToastTitle");
const uploadToastDetail = document.querySelector("#uploadToastDetail");
const goToData = document.querySelector("#goToData");
const content = document.querySelector(".content");
const detailView = document.querySelector("#imageDetailView");
const detailImage = document.querySelector("#detailImage");
const annotationOverlay = document.querySelector("#annotationOverlay");
const detailTitle = document.querySelector("#detailTitle");
const detailDimensions = document.querySelector("#detailDimensions");
const detailSize = document.querySelector("#detailSize");
const detailAnalysis = document.querySelector("#detailAnalysis");
const detailAnnotate = document.querySelector("#detailAnnotate");
const deleteImageButton = document.querySelector("#deleteImageButton");
const sendToGroup = document.querySelector("#sendToGroup");
const backToImages = document.querySelector("#backToImages");
const railSpace = document.querySelector("#railSpace");
const railTeam = document.querySelector("#railTeam");
const railEnterprise = document.querySelector("#railEnterprise");
const railAdmin = document.querySelector("#railAdmin");
const railProfile = document.querySelector("#railProfile");
const backToUpload = document.querySelector("#backToUpload");
const dangerDialog = document.querySelector("#dangerDialog");
const dangerKicker = document.querySelector("#dangerKicker");
const dangerTitle = document.querySelector("#dangerTitle");
const dangerMessage = document.querySelector("#dangerMessage");
const dangerDetail = document.querySelector("#dangerDetail");
const cancelDanger = document.querySelector("#cancelDanger");
const confirmDangerButton = document.querySelector("#confirmDanger");
const targetPlanTitle = document.querySelector("#targetPlanTitle");
const targetPlanDetail = document.querySelector("#targetPlanDetail");
const openTargetSettings = document.querySelector("#openTargetSettings");
const targetSettingsDialog = document.querySelector("#targetSettingsDialog");
const closeTargetSettings = document.querySelector("#closeTargetSettings");
const targetSettingsForm = document.querySelector("#targetSettingsForm");
const projectTargetsStatus = document.querySelector("#projectTargetsStatus");
const saveProjectTargets = document.querySelector("#saveProjectTargets");
const targetRows = document.querySelector("#targetRows");
const targetEmpty = document.querySelector("#targetEmpty");
const targetNewZh = document.querySelector("#targetNewZh");
const targetNewEn = document.querySelector("#targetNewEn");
const targetAddButton = document.querySelector("#targetAddButton");
const projectBoxThreshold = document.querySelector("#projectBoxThreshold");
const projectBoxThresholdValue = document.querySelector("#projectBoxThresholdValue");

// 与后端 DEFAULT_BOX_THRESHOLD 保持一致；滑块上的值永远以读回来的配置为准。
const FALLBACK_BOX_THRESHOLD = 0.3;
let currentBoxThreshold = FALLBACK_BOX_THRESHOLD;

function formatThreshold(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(2) : FALLBACK_BOX_THRESHOLD.toFixed(2);
}

function setBoxThreshold(value) {
  currentBoxThreshold = Number(value);
  const text = formatThreshold(value);
  if (projectBoxThreshold) projectBoxThreshold.value = text;
  if (projectBoxThresholdValue) projectBoxThresholdValue.textContent = text;
}

const utilityRail = document.querySelector(".utility-rail");
const logoutChip = document.querySelector("#railLogout");
const confirmLogout = document.querySelector("#confirmLogout");
const appAccount = document.querySelector("#appAccount");
const appAvatar = document.querySelector("#appAvatar");
const appAccountName = document.querySelector("#appAccountName");
const railSettings = document.querySelector("#railSettings");
const settingsDialog = document.querySelector("#settingsDialog");
const closeSettingsDialog = document.querySelector("#closeSettingsDialog");

const imageToolbar = document.querySelector("#imageToolbar");
const selectAllImages = document.querySelector("#selectAllImages");
const selectionCount = document.querySelector("#selectionCount");
const downloadSelected = document.querySelector("#downloadSelected");
const downloadAnnotated = document.querySelector("#downloadAnnotated");
const classicAnalyzingCount = document.querySelector("#classicAnalyzingCount");
const classicCompletedCount = document.querySelector("#classicCompletedCount");
const classicWaitingCount = document.querySelector("#classicWaitingCount");

const allDialogs = [...document.querySelectorAll(".dialog-backdrop")];

let currentView = "personal";
let previewUrl = null;
let currentImage = null;
let currentImageContext = { source: "personal", analysisImageId: null, canDelete: true };
let thumbnailUrls = [];
let selectedFiles = [];
let selectionPreviewUrls = [];
let activeProject = null;
let loadedImages = [];
let imageLoadPromise = null;
let imagesLoaded = false;
let analysisPollTimer = 0;
let deleteConfirmTimer = 0;
let deleteArmed = false;
let analysisTargetsDirty = false;
const analysisByImage = new Map();

function themeDelay(regular, classic) {
  return window.VSTheme?.get() === "classic" ? classic : regular;
}

/* ---------------- 启动 ---------------- */

if (!VS.hasToken()) location.replace("login.html");
if (sessionStorage.getItem("vision_enter_transition") === "1") {
  sessionStorage.removeItem("vision_enter_transition");
  document.body.classList.add("is-arriving");
}
requestAnimationFrame(() => document.body.classList.add("motion-ready"));
loadProjectTargets();

/* ---------------- 顶栏账号 ---------------- */

// 展示名称优先，没有就退回登录用户名；有头像就显示头像，否则用首字。
// settings.js 保存资料或换头像后会调用这里，顶栏立刻跟着变。
function setAccountBadge(account) {
  const fallback = localStorage.getItem("vision_account_name");
  const view = account || { username: fallback || "参赛成员" };
  appAccountName.textContent = VS.displayName(view);
  VS.fillAvatar(appAvatar, account);
}

// 探测后端能力（头像 / 展示名称 / 小组介绍是否支持），
// 顺便把顶栏的账号信息填上，避免多打一次 /auth/me。
VS.probe()
  .then(({ me }) => {
    if (!me) return;
    setAccountBadge(me);
    // 平台管理入口只给管理员；admin.js 里还会再校验一次
    railAdmin.hidden = me.platform_role !== "admin";
  })
  .catch(() => {});

/* ---------------- 企业标识：Logo 右侧分隔栏 + 头像徽章 ---------------- */

// 徽章本身是 CSS 画的（html.has-enterprise 时给头像右下角加 V），
// 这里只负责「有没有企业身份」和分隔栏文案。
const brandEnterprise = document.querySelector("#brandEnterprise");
const brandEntName = document.querySelector("#brandEntName");
const brandEntId = document.querySelector("#brandEntId");

function setEnterpriseBrand(ent) {
  const on = !!(ent && ent.name);
  document.documentElement.classList.toggle("has-enterprise", on);
  brandEnterprise.hidden = !on;
  if (!on) {
    brandEntName.textContent = "—";
    brandEntId.textContent = "—";
    return;
  }
  brandEntName.textContent = ent.name;
  brandEntId.textContent = `ENT-${ent.id}`;
  brandEnterprise.title = `${ent.name} · 企业 ID ENT-${ent.id}（${ent.my_role || "成员"}）`;
}

window.VSBrand = { setEnterprise: setEnterpriseBrand };

appAccount.addEventListener("click", () => openSettings("profile"));
setAccountBadge(null);

/* ---------------- 工具轨：悬停 0.8 秒展开，离开 300ms 收起 ---------------- */

let railExpandTimer = null;
let railCollapseTimer = null;

// closed | opening | open | closing —— 用显式状态，动画被打断也能收敛回正常形态
let chipState = "closed";
let chipTimers = [];
let chipRaf = 0;

function expandRail() {
  clearTimeout(railCollapseTimer);
  clearTimeout(railExpandTimer);
  // 停留稍久就展开，让不熟悉的人也能发现图标含义；熟练用户直接点图标即可
  railExpandTimer = setTimeout(() => utilityRail.classList.add("is-expanded"), 800);
}

function collapseRail() {
  clearTimeout(railExpandTimer);
  clearTimeout(railCollapseTimer);
  railCollapseTimer = setTimeout(() => {
    // 退出卡片还开着或正在收，先不收，等它收完自己判断
    if (chipState !== "closed") return;
    utilityRail.classList.remove("is-expanded");
  }, 300);
}

utilityRail.addEventListener("mouseenter", expandRail);
utilityRail.addEventListener("mouseleave", collapseRail);
// 只有键盘聚焦（Tab）才展开；鼠标点击不算，否则点一下图标就会立刻展开
utilityRail.addEventListener("focusin", (event) => {
  if (event.target.matches(":focus-visible")) utilityRail.classList.add("is-expanded");
});
utilityRail.addEventListener("focusout", collapseRail);

/* ---------------- 栏目切换 ---------------- */

// 工具轨高亮集中在一处算，避免每个入口各自 toggle 一遍、状态互相打架
function syncRail() {
  // 项目页归属它来自的空间，侧栏高亮跟着走
  const space = currentView === "project" ? window.VSProjects?.activeSpace() : currentView;
  railSpace.classList.toggle("is-active", space === "personal");
  railTeam.classList.toggle("is-active", space === "team");
  railEnterprise.classList.toggle("is-active", space === "enterprise");
  railAdmin.classList.toggle("is-active", currentView === "admin");
  // 「个人与账号」和「设置」打开的是同一个弹窗，高亮统一记在「设置」上
  railSettings.classList.toggle("is-active", !settingsDialog.hidden);
}

function showView(view) {
  currentView = view;
  // 图片详情和标注工作台都是盖在内容区上面的独立层。不先收掉它们，
  // 点工具轨切栏目时内容区仍是 hidden，看起来就是"点了没反应"。
  if (!detailView.hidden) closeImageDetail(true);
  window.VSAnnotate?.close?.(true);
  allDialogs.forEach((dialog) => {
    if (!dialog.hidden) closeDialog(dialog);
  });
  document.querySelectorAll(".app-view").forEach((item) => {
    item.hidden = item.dataset.view !== view;
  });
  // 空间页与项目页都是多栏布局，需要比首页更宽的画布
  content.classList.toggle("is-wide", ["team", "enterprise", "admin", "project"].includes(view));
  syncRail();
  // 切栏目时收起空间设置抽屉，免得它盖在新页面上
  window.VSProjects?.closeDrawer?.();
  if (view === "personal") window.VSProjects?.enter("personal");
  if (view === "team") window.VSProjects?.enter("team");
  if (view === "enterprise") window.VSProjects?.enter("enterprise");
  if (view === "admin" && window.VSAdmin) window.VSAdmin.enter();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

railSpace.addEventListener("click", () => {
  clearTimeout(railExpandTimer);
  showView("personal");
});
railTeam.addEventListener("click", () => {
  clearTimeout(railExpandTimer);
  showView("team");
});
railEnterprise.addEventListener("click", () => {
  clearTimeout(railExpandTimer);
  showView("enterprise");
});
railAdmin.addEventListener("click", () => {
  clearTimeout(railExpandTimer);
  showView("admin");
});
// 上传区和图像列表在同一页，这两个按钮改成页内定位
backToUpload.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

/* ---------------- 弹窗 ---------------- */

// 每个弹窗记自己的关闭定时器：连续点两次时，上一次的收尾不会把新弹窗关掉
const dialogTimers = new WeakMap();

function openDialog(dialog) {
  clearTimeout(dialogTimers.get(dialog));
  clearTimeout(railExpandTimer);
  // 同一时间只留一个弹窗，避免两个浮层叠在一起
  allDialogs.forEach((other) => {
    if (other !== dialog && !other.hidden) closeDialog(other);
  });
  dialog.hidden = false;
  syncRail();
  requestAnimationFrame(() => dialog.classList.add("is-visible"));
  dialog.dispatchEvent(new Event("vision:open"));
}

function closeDialog(dialog) {
  dialog.classList.remove("is-visible");
  clearTimeout(dialogTimers.get(dialog));
  dialogTimers.set(dialog, window.setTimeout(() => {
    dialog.hidden = true;
    syncRail();
  }, themeDelay(180, 110)));
  syncRail();
}

/* 自定义危险操作卡片。所有调用方都 await 一个布尔值，不再使用系统 confirm。 */
let dangerResolve = null;

function finishDanger(confirmed) {
  if (!dangerResolve) return;
  const resolve = dangerResolve;
  dangerResolve = null;
  closeDialog(dangerDialog);
  resolve(confirmed);
}

function confirmDanger({ kicker = "DELETE", title = "确认删除？", message, detail = "", confirmText = "确认删除" }) {
  if (dangerResolve) finishDanger(false);
  dangerKicker.textContent = kicker;
  dangerTitle.textContent = title;
  dangerMessage.textContent = message;
  dangerDetail.textContent = detail;
  dangerDetail.hidden = !detail;
  confirmDangerButton.textContent = confirmText;
  confirmDangerButton.disabled = false;
  openDialog(dangerDialog);
  window.setTimeout(() => confirmDangerButton.focus(), 80);
  return new Promise((resolve) => { dangerResolve = resolve; });
}

cancelDanger.addEventListener("click", () => finishDanger(false));
confirmDangerButton.addEventListener("click", () => finishDanger(true));
dangerDialog.addEventListener("click", (event) => {
  if (event.target === dangerDialog) finishDanger(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && dangerResolve) finishDanger(false);
});
window.confirmDanger = confirmDanger;

// 普通删图不弹大窗：按钮原地进入确认态，再点一次才真正执行。
function resetInlineDelete() {
  clearTimeout(deleteConfirmTimer);
  deleteConfirmTimer = 0;
  deleteArmed = false;
  deleteImageButton.classList.remove("is-armed");
  deleteImageButton.setAttribute("aria-label", "删除图片");
  if (!deleteImageButton.disabled) deleteImageButton.textContent = "删除图片";
}

function armInlineDelete() {
  deleteArmed = true;
  deleteImageButton.classList.add("is-armed");
  deleteImageButton.textContent = "确认删除";
  deleteImageButton.setAttribute("aria-label", "再次点击确认删除图片");
  clearTimeout(deleteConfirmTimer);
  deleteConfirmTimer = window.setTimeout(resetInlineDelete, 4500);
}

// 同一个图标再点一次就是关闭，符合直觉
function toggleDialog(dialog) {
  if (dialog.hidden) openDialog(dialog);
  else closeDialog(dialog);
}

/* 设置是同一个弹窗，两个入口各自定位到对应小节：
   「个人与账号」跳个人资料，「设置」跳外观与主题。 */
function openSettings(section) {
  const target = { profile: "#settingsProfileSection", theme: "#settingsThemeSection", security: "#settingsSecuritySection" }[section];
  openDialog(settingsDialog);
  const node = target && document.querySelector(target);
  if (!node) return;
  window.requestAnimationFrame(() => node.scrollIntoView({ block: "nearest", behavior: "smooth" }));
}

railProfile.addEventListener("click", () => openSettings("profile"));
railSettings.addEventListener("click", () => openSettings("theme"));
closeSettingsDialog.addEventListener("click", () => closeDialog(settingsDialog));
openTargetSettings.addEventListener("click", () => {
  openDialog(targetSettingsDialog);
  loadProjectTargets({ forEditor: true });
});
closeTargetSettings.addEventListener("click", () => closeDialog(targetSettingsDialog));
allDialogs.forEach((dialog) => dialog.addEventListener("click", (event) => {
  if (event.target === dialog) closeDialog(dialog);
}));
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  allDialogs.forEach((dialog) => {
    if (!dialog.hidden) closeDialog(dialog);
  });
});

/* ---------------- 退出卡片：分裂 → 变红延长 → 确定 / 粘回 ---------------- */

function clearChipTimers() {
  chipTimers.forEach(clearTimeout);
  chipTimers = [];
}

// 卡片"正在打开"也算活跃：否则刚点完就点空白，卡片会继续打开、收不回来
function isChipActive() {
  return chipState === "opening" || chipState === "open";
}

function openLogoutChip() {
  if (chipState === "opening" || chipState === "open") return;
  clearChipTimers();
  cancelAnimationFrame(chipRaf);
  chipState = "opening";
  // 如果是在收起途中重新点开，先把褪色状态撤掉
  logoutChip.classList.remove("is-fading");
  // 取消可能还在排队的展开，避免卡片播到一半工具轨突然变宽
  clearTimeout(railExpandTimer);
  clearTimeout(railCollapseTimer);
  // 按当前形态直接播：未展开就从 36px 小图标分裂，已展开就从整行分裂
  utilityRail.classList.add("is-detaching");
  // 等一帧再上 is-open，确保浏览器先记录初始状态，过渡才会真正播放
  chipRaf = requestAnimationFrame(() => {
    chipRaf = requestAnimationFrame(() => {
      // 这两帧之内可能已经被取消（点空白 / 再点一次），取消后就不要再打开
      if (chipState !== "opening") return;
      logoutChip.classList.add("is-open");
      chipState = "open";
      // 第二段：卡片变红并向右延长，让出确定按钮的位置
      chipTimers.push(setTimeout(() => {
        if (chipState !== "open") return;
        logoutChip.classList.add("is-confirming");
      }, themeDelay(360, 120)));
    });
  });
}

function closeLogoutChip() {
  if (chipState === "closed" || chipState === "closing") return;
  clearChipTimers();
  cancelAnimationFrame(chipRaf);
  chipState = "closing";
  // 第一拍：形状和位置都锁住，只把红色褪成和工具轨一样的浅色
  logoutChip.classList.add("is-fading");
  // 第二拍：缩回小卡片。transform 在 CSS 里带 240ms 延迟，所以这一拍卡片
  // 只横向收缩，人还停在下方没动
  chipTimers.push(setTimeout(() => {
    if (chipState !== "closing") return;
    logoutChip.classList.remove("is-open", "is-confirming", "is-fading");
    utilityRail.classList.remove("is-expanded");
  }, themeDelay(200, 80)));
  // 第三拍：上移归位。正好是 transform 延迟结束的时刻，
  // 槽位和分隔线在同一时刻开始复原，三者一起合拢
  chipTimers.push(setTimeout(() => {
    if (chipState !== "closing") return;
    utilityRail.classList.remove("is-detaching");
    chipState = "closed";
  }, themeDelay(440, 170)));
}

logoutChip.addEventListener("click", (event) => {
  // 点确定按钮由它自己处理
  if (confirmLogout.contains(event.target)) return;
  if (isChipActive()) closeLogoutChip();
  else openLogoutChip();
});

// 点击卡片外部收起
document.addEventListener("click", (event) => {
  if (!isChipActive()) return;
  if (logoutChip.contains(event.target)) return;
  closeLogoutChip();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && isChipActive()) closeLogoutChip();
});

function doLogout() {
  if (document.body.classList.contains("is-leaving")) return;
  clearChipTimers();
  cancelAnimationFrame(chipRaf);
  chipState = "closed";
  document.body.classList.add("is-leaving");
  setTimeout(VS.signOut, themeDelay(380, 160));
}

confirmLogout.addEventListener("click", (event) => {
  event.stopPropagation();
  doLogout();
});
confirmLogout.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    event.stopPropagation();
    doLogout();
  }
});

/* ---------------- 图像数据 ---------------- */

const formatSize = VS.formatSize;

// 列表区的提示文案统一走这里，避免到处拼 innerHTML
function showStatus(title, detail) {
  imageDataState.classList.add("empty-state");
  imageDataState.classList.remove("image-data-list-wrap");
  imageDataState.replaceChildren();
  imageDataState.append(VS.el("strong", "", title), VS.el("p", "", detail));
}

function classicImageState(imageId) {
  const state = analysisByImage.get(Number(imageId));
  if (state?.kind === "result") return { key: "completed", label: "已完成" };
  if (state?.kind === "task" && ACTIVE_TASK_STATES.has(state.status)) return { key: "analyzing", label: "分析中" };
  if (state?.kind === "task" && ["auto_annotated", "approved"].includes(state.status)) return { key: "completed", label: "已完成" };
  if (state?.kind === "task" && state.status === "failed") return { key: "failed", label: "分析失败" };
  return { key: "waiting", label: "待分析" };
}

function syncAnalysisCounters() {
  const totals = { analyzing: 0, completed: 0, waiting: 0 };
  loadedImages.forEach((image) => {
    const state = classicImageState(image.id);
    if (state.key === "completed") totals.completed += 1;
    else if (state.key === "analyzing") totals.analyzing += 1;
    else totals.waiting += 1;
  });
  classicAnalyzingCount.textContent = String(totals.analyzing);
  classicCompletedCount.textContent = String(totals.completed);
  classicWaitingCount.textContent = String(totals.waiting);
}

function renderImages(images) {
  loadedImages = images;
  thumbnailUrls.forEach((url) => URL.revokeObjectURL(url));
  thumbnailUrls = [];
  // 删掉图片后勾选里可能还留着已经不存在的 id，先清一遍
  [...pickedImages].forEach((id) => {
    if (!images.some((image) => Number(image.id) === Number(id))) pickedImages.delete(id);
  });
  if (!images.length) {
    showStatus("还没有图像数据", "上传第一张图片，开始分析与标注。");
    syncImageToolbar();
    syncAnalysisCounters();
    return;
  }

  imageDataState.classList.remove("empty-state");
  imageDataState.classList.add("image-data-list-wrap");

  const list = document.createElement("div");
  list.className = "image-list";
  // 后端按上传顺序返回，列表倒序显示最新的在最上面
  images.slice().reverse().forEach((image) => {
    // 行本身是 role=button，不再用 <button> 包着：按钮里不能再嵌 checkbox
    const row = document.createElement("div");
    row.className = "image-row";
    row.dataset.imageId = String(image.id);
    row.style.setProperty("--row-index", list.children.length);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute("aria-label", `打开 ${image.filename}`);
    row.innerHTML = "<label class=\"image-pick\"><input type=\"checkbox\" aria-label=\"勾选这张图片\" /><span></span></label>"
      + "<img class=\"image-thumb\" alt=\"\" /><span class=\"image-copy\"><strong></strong><small></small></span>";
    row.querySelector("strong").textContent = image.filename;
    const meta = row.querySelector("small");
    meta.dataset.base = `${image.width || "?"} × ${image.height || "?"} · ${formatSize(image.size_bytes)}`;
    meta.textContent = `${meta.dataset.base} · ${analysisText(image.id)}`;
    row.addEventListener("click", () => openImageDetail(image));
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      openImageDetail(image);
    });

    const pick = row.querySelector(".image-pick");
    const input = pick.querySelector("input");
    input.checked = pickedImages.has(String(image.id));
    // 勾选只改变选择状态，不该顺带把详情页打开
    pick.addEventListener("click", (event) => event.stopPropagation());
    pick.addEventListener("keydown", (event) => event.stopPropagation());
    input.addEventListener("change", () => {
      if (input.checked) pickedImages.add(String(image.id));
      else pickedImages.delete(String(image.id));
      row.classList.toggle("is-picked", input.checked);
      syncImageToolbar();
    });
    row.classList.toggle("is-picked", input.checked);

    list.append(row);
    loadThumbnail(image, row.querySelector(".image-thumb"));
  });
  imageDataState.replaceChildren(list);
  syncImageToolbar();
  syncAnalysisCounters();
}

/* ---------------- 勾选与批量下载 ---------------- */

const pickedImages = new Set();

function pickedList() {
  return loadedImages.filter((image) => pickedImages.has(String(image.id)));
}

function syncImageToolbar() {
  const total = loadedImages.length;
  imageToolbar.hidden = total === 0;
  const picked = pickedList();
  const annotated = picked.filter((image) => annotationItems(image.id).length).length;
  selectionCount.textContent = picked.length
    ? `已选 ${picked.length} 张${annotated ? ` · 其中 ${annotated} 张有标注` : ""}`
    : "还没有选中图片";
  downloadSelected.disabled = picked.length === 0;
  selectAllImages.checked = total > 0 && picked.length === total;
  selectAllImages.indeterminate = picked.length > 0 && picked.length < total;
}

selectAllImages.addEventListener("change", () => {
  pickedImages.clear();
  if (selectAllImages.checked) loadedImages.forEach((image) => pickedImages.add(String(image.id)));
  document.querySelectorAll(".image-row[data-image-id]").forEach((row) => {
    const on = pickedImages.has(String(row.dataset.imageId));
    row.classList.toggle("is-picked", on);
    const input = row.querySelector(".image-pick input");
    if (input) input.checked = on;
  });
  syncImageToolbar();
});

/* ---------------- 标注图导出 ---------------- */

function annotationItems(imageId) {
  const state = analysisByImage.get(Number(imageId));
  return state?.kind === "result" ? (state.items || []) : [];
}

// 把原图和标注框一起画进 canvas，导出成 PNG。
// 坐标就是原图像素坐标（overlay 的 viewBox 用的也是同一套），不需要缩放换算。
function drawAnnotated(source, items) {
  const canvas = document.createElement("canvas");
  canvas.width = source.width;
  canvas.height = source.height;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(source.node, 0, 0, source.width, source.height);

  const unit = Math.min(source.width, source.height);
  const lineWidth = Math.max(2, Math.round(unit * 0.0035));
  const fontSize = Math.max(14, Math.round(unit * 0.026));

  items.forEach((item) => {
    const box = item.payload?.box;
    if (!Array.isArray(box) || box.length !== 4) return;
    const [x1, y1, x2, y2] = box.map(Number);
    if (![x1, y1, x2, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) return;

    ctx.fillStyle = "rgba(0, 113, 227, .07)";
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeStyle = "#0071e3";
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);

    const confidence = item.confidence == null ? "" : ` ${Math.round(item.confidence * 100)}%`;
    const text = `${item.label || "目标"}${confidence}`;
    ctx.font = `650 ${fontSize}px "PingFang SC", "Microsoft YaHei", system-ui, sans-serif`;
    ctx.textBaseline = "alphabetic";
    // canvas 没有 paint-order，先描一遍深色再填白，效果和网页里的标签一致
    ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(3, Math.round(fontSize * 0.34));
    ctx.strokeStyle = "rgba(0, 70, 145, .92)";
    const textX = Math.max(6, x1 + 4);
    const textY = Math.max(fontSize + 4, y1 - 6);
    ctx.strokeText(text, textX, textY);
    ctx.fillStyle = "#fff";
    ctx.fillText(text, textX, textY);
  });
  return canvas;
}

// 用 <img> 解码而不是 createImageBitmap：兼容性更好，
// 而且不会在部分无头环境下卡在解码那一步（本项目踩过）。
function decodeBlob(blob) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(blob);
    const node = new Image();
    node.onload = () => resolve({
      node,
      width: node.naturalWidth,
      height: node.naturalHeight,
      release: () => URL.revokeObjectURL(url),
    });
    node.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("这张图片无法解码"));
    };
    node.src = url;
  });
}

async function annotatedBlob(image, analysisImageId) {
  const { blob } = await call(() => sdk.imageContent(image.id));
  const source = await decodeBlob(blob);
  try {
    const items = annotationItems(analysisImageId || image.id);
    const canvas = drawAnnotated(source, items);
    const png = await new Promise((resolve, reject) => {
      canvas.toBlob((result) => (result ? resolve(result) : reject(new Error("导出失败"))), "image/png");
    });
    return { png, annotated: items.length > 0 };
  } finally {
    source.release();
  }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  // 立刻 revoke 会让部分浏览器的下载中断，留一点余量
  window.setTimeout(() => URL.revokeObjectURL(url), 20000);
}

function exportName(image, annotated) {
  const base = String(image.filename || `image-${image.id}`).replace(/\.[^.]+$/, "") || `image-${image.id}`;
  return `${base}-${annotated ? "标注" : "原图"}.png`;
}

async function exportOne(image, analysisImageId) {
  const { png, annotated } = await annotatedBlob(image, analysisImageId);
  saveBlob(png, exportName(image, annotated));
  return annotated;
}

downloadAnnotated.addEventListener("click", async () => {
  if (!currentImage) return;
  const label = downloadAnnotated.textContent;
  downloadAnnotated.disabled = true;
  downloadAnnotated.textContent = "正在生成…";
  try {
    const annotated = await exportOne(currentImage, currentImageContext.analysisImageId);
    showUploadToast("已保存标注图", annotated ? "标注框已画在图上" : "这张图还没有标注，导出的是原图");
  } catch (error) {
    showUploadToast("导出没有完成", error.message || "请稍后重试");
  } finally {
    downloadAnnotated.disabled = false;
    downloadAnnotated.textContent = label;
  }
});

downloadSelected.addEventListener("click", async () => {
  const list = pickedList();
  if (!list.length) return;
  const label = downloadSelected.textContent;
  downloadSelected.disabled = true;
  let done = 0;
  let failed = 0;
  try {
    for (const image of list) {
      downloadSelected.textContent = `正在导出 ${done + failed + 1}/${list.length}…`;
      try {
        await exportOne(image);
        done += 1;
      } catch {
        failed += 1;
      }
      // 连续触发下载浏览器会要求"允许多个文件"，留出间隔更像人点的
      if (done + failed < list.length) await new Promise((resolve) => window.setTimeout(resolve, 420));
    }
    showUploadToast(
      `已导出 ${done} 张标注图`,
      failed ? `${failed} 张失败，请稍后重试` : "文件已保存到浏览器的下载目录",
    );
  } finally {
    downloadSelected.disabled = false;
    downloadSelected.textContent = label;
    syncImageToolbar();
  }
});

async function loadThumbnail(image, target) {
  try {
    const { blob } = await call(() => sdk.imageContent(image.id));
    const url = URL.createObjectURL(blob);
    thumbnailUrls.push(url);
    target.src = url;
  } catch {
    // 缩略图失败不影响列表和原图详情，保留默认占位即可。
  }
}

async function loadImages({ force = false } = {}) {
  if (imageLoadPromise) return imageLoadPromise;
  if (imagesLoaded && !force) return loadedImages;
  imageLoadPromise = (async () => {
    const project = await ensureImageProject();
    const [page] = await Promise.all([
      call(() => sdk.listProjectImages(project.id, { limit: 200 })),
      loadAnalysisState(project),
    ]);
    const images = Array.isArray(page) ? page : (page.items || []);
    imagesLoaded = true;
    renderImages(images);
    return images;
  })().finally(() => { imageLoadPromise = null; });
  return imageLoadPromise;
}

// 图片、任务、类目都挂在项目上。项目页打开哪个项目，这里就用哪个。
function ensureImageProject() {
  if (!activeProject) return Promise.reject(new Error("请先打开一个项目"));
  return Promise.resolve(activeProject);
}

/** 项目页切到某个项目时调用：重置缓存并重新拉图片与分析状态。 */
function loadProject(project, { force = true } = {}) {
  activeProject = project;
  imagesLoaded = false;
  imageLoadPromise = null;
  loadProjectTargets().catch(() => {});
  return loadImages({ force });
}

const ACTIVE_TASK_STATES = new Set(["draft", "queued", "auto_annotating"]);
const TASK_LABELS = {
  draft: "正在准备分析",
  queued: "等待模型处理",
  auto_annotating: "模型分析中",
  auto_annotated: "分析完成",
  approved: "分析已通过",
  rejected_fix: "等待修正",
  rejected_reannotate: "等待重新分析",
  failed: "分析失败",
  cancelled: "分析已取消",
};

function analysisText(imageId) {
  const state = analysisByImage.get(Number(imageId));
  if (!state) return "等待提交分析";
  if (state.kind === "result") {
    if (!state.count) return "分析完成 · 未发现目标";
    const labels = state.labels.slice(0, 3).join("、");
    return `分析完成 · ${state.count} 个标注${labels ? ` · ${labels}` : ""}`;
  }
  return state.error ? `${TASK_LABELS[state.status] || state.status} · ${state.error}` : (TASK_LABELS[state.status] || state.status);
}

function updateAnalysisLabels() {
  document.querySelectorAll(".image-row[data-image-id]").forEach((row) => {
    const meta = row.querySelector("small");
    meta.textContent = `${meta.dataset.base} · ${analysisText(row.dataset.imageId)}`;
  });
  if (currentImage && detailAnalysis) {
    const analysisId = currentImageContext.analysisImageId || currentImage.id;
    const hasState = analysisByImage.has(Number(analysisId));
    detailAnalysis.textContent = currentImageContext.source === "team" && !hasState
      ? "小组图片 · 尚未分析"
      : analysisText(analysisId);
    renderAnnotationOverlay(analysisId);
  }
  syncAnalysisCounters();
}

function renderAnnotationOverlay(imageId) {
  annotationOverlay.replaceChildren();
  const state = analysisByImage.get(Number(imageId));
  if (state?.kind !== "result" || !state.items?.length) return;
  const width = detailImage.naturalWidth || currentImage?.width;
  const height = detailImage.naturalHeight || currentImage?.height;
  if (!width || !height) return;
  annotationOverlay.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const svgNS = "http://www.w3.org/2000/svg";
  state.items.forEach((item) => {
    const box = item.payload?.box;
    if (!Array.isArray(box) || box.length !== 4) return;
    const [x1, y1, x2, y2] = box.map(Number);
    if (![x1, y1, x2, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) return;
    const rectangle = document.createElementNS(svgNS, "rect");
    rectangle.setAttribute("x", x1);
    rectangle.setAttribute("y", y1);
    rectangle.setAttribute("width", x2 - x1);
    rectangle.setAttribute("height", y2 - y1);
    rectangle.setAttribute("rx", "3");
    const label = document.createElementNS(svgNS, "text");
    label.setAttribute("x", Math.max(4, x1 + 4));
    label.setAttribute("y", Math.max(16, y1 - 6));
    const confidence = item.confidence == null ? "" : ` ${Math.round(item.confidence * 100)}%`;
    label.textContent = `${item.label || "目标"}${confidence}`;
    annotationOverlay.append(rectangle, label);
  });
}

function applyTaskState(task) {
  (task.images || []).forEach((row) => {
    const existing = analysisByImage.get(row.image_id);
    if (existing?.kind === "result" && ["auto_annotated", "approved"].includes(task.status)) return;
    // 任务已经跑完、这张图也标成 annotated，却一条标注都没有：
    // 说明模型确实没检出目标，写「未发现目标」比停在「分析完成」更准确。
    const finished = ["auto_annotated", "approved"].includes(task.status);
    if (finished && row.annotation_status === "annotated") {
      analysisByImage.set(row.image_id, { kind: "result", count: 0, labels: [], items: [] });
      return;
    }
    analysisByImage.set(row.image_id, {
      kind: "task",
      status: task.status,
      error: task.error_message || (row.annotation_status === "failed" ? "该图片处理失败" : ""),
    });
  });
}

async function loadAnnotationResults(projectId) {
  const page = await call(() => sdk.listAnnotations(projectId, { limit: 200 }));
  const grouped = new Map();
  (page.items || []).forEach((item) => {
    if (!item.image_id) return;
    const group = grouped.get(item.image_id) || { count: 0, labels: new Set(), items: [] };
    group.count += 1;
    if (item.label) group.labels.add(item.label);
    group.items.push(item);
    grouped.set(item.image_id, group);
  });
  grouped.forEach((value, imageId) => {
    analysisByImage.set(imageId, { kind: "result", count: value.count, labels: [...value.labels], items: value.items || [] });
  });
}

async function loadAnalysisState(project) {
  analysisByImage.clear();
  await loadAnnotationResults(project.id).catch(() => {});

  // 先看本机记着的那次任务；换设备或清过缓存时它不在，
  // 就回后端查这个项目最近的任务，免得进度显示成「等待提交分析」。
  let task = null;
  const taskId = Number(localStorage.getItem(`vision_recent_task_${project.id}`));
  if (taskId) {
    task = await call(() => sdk.getTask(project.id, taskId)).catch(() => null);
    if (!task) localStorage.removeItem(`vision_recent_task_${project.id}`);
  }
  if (!task) {
    const tasks = await call(() => sdk.listProjectTasks(project.id)).catch(() => []);
    // 后端按「新 → 旧」返回，但顺序不是接口契约：这里按 id 取最大，
    // 否则会挑到最早那次任务，没被它覆盖的图片就显示成「等待提交分析」。
    // 优先级：正在跑的 > 有结果的（非已取消）> 兜底取最新。
    // 若不跳过 cancelled，取消一个较新的任务后，它没覆盖的图片会被误标成「等待提交分析」。
    const newest = (list) => list.reduce((best, item) => (!best || item.id > best.id ? item : best), null);
    const pick = newest(tasks.filter((item) => ACTIVE_TASK_STATES.has(item.status)))
      || newest(tasks.filter((item) => item.status !== "cancelled"))
      || newest(tasks);
    if (pick) {
      task = await call(() => sdk.getTask(project.id, pick.id)).catch(() => null);
      if (task) localStorage.setItem(`vision_recent_task_${project.id}`, String(task.id));
    }
  }

  if (!task) return;
  applyTaskState(task);
  if (ACTIVE_TASK_STATES.has(task.status)) scheduleTaskPoll(project.id, task.id);
}

function scheduleTaskPoll(projectId, taskId, failures = 0) {
  clearTimeout(analysisPollTimer);
  analysisPollTimer = window.setTimeout(async () => {
    try {
      const task = await call(() => sdk.getTask(projectId, taskId));
      applyTaskState(task);
      if (["auto_annotated", "approved"].includes(task.status)) {
        await loadAnnotationResults(projectId);
        showUploadToast("分析完成", "结果与标注已写入图像数据");
      }
      updateAnalysisLabels();
      if (ACTIVE_TASK_STATES.has(task.status)) scheduleTaskPoll(projectId, taskId, 0);
      else if (task.status === "failed") showUploadToast("分析失败", task.error_message || "请检查模型服务后重试");
    } catch {
      if (failures < 12) scheduleTaskPoll(projectId, taskId, failures + 1);
      else showUploadToast("状态更新中断", "网络恢复后重新打开图像数据即可继续查看");
    }
  }, 2500);
}

function parseTargets(value) {
  return [...new Set(value.split(/[，,、;；\n]+/).map((item) => item.trim()).filter(Boolean))].slice(0, 20);
}

function selectedTargets() {
  return parseTargets(analysisTargets.value);
}

function renderProjectTargets(targets, { unavailable = false } = {}) {
  if (unavailable) {
    targetPlanTitle.textContent = "暂时无法读取识别方案";
    targetPlanDetail.textContent = "仍可在选择图片后手动填写，不影响上传。";
    syncAnalysisCounters();
    return;
  }
  targetPlanTitle.textContent = targets.length ? targets.join("、") : "尚未设置识别目标";
  targetPlanDetail.textContent = targets.length
    ? `已保存 ${targets.length} 项，置信度 ${formatThreshold(currentBoxThreshold)}，后续上传会自动带出。`
    : "设置一次，后续上传会自动复用。";
  syncAnalysisCounters();
}

/* ---------------- 识别目标逐条编辑 ---------------- */

// 保存时用这份快照算「哪些被删掉了」：编辑框里没有的、且原本存在的，就是要删的。
let targetSnapshot = [];

function targetRowNode({ id = null, zh = "", en = "" }) {
  const row = el("li", "target-row");
  if (id != null) row.dataset.id = String(id);
  row.dataset.zh = zh;
  row.dataset.en = en;

  const zhInput = el("input", "target-zh");
  zhInput.type = "text";
  zhInput.maxLength = 50;
  zhInput.placeholder = "中文名";
  zhInput.value = zh;
  zhInput.autocomplete = "off";
  zhInput.setAttribute("aria-label", "目标中文名");

  const enInput = el("input", "target-en");
  enInput.type = "text";
  enInput.maxLength = 100;
  enInput.placeholder = "英文提示词（留空自动翻译）";
  enInput.value = en;
  enInput.autocomplete = "off";
  enInput.setAttribute("aria-label", "目标英文提示词");

  const remove = el("button", "target-remove", "×");
  remove.type = "button";
  remove.title = "移除这一条";
  remove.setAttribute("aria-label", `移除 ${zh || "这一条目标"}`);
  remove.addEventListener("click", () => {
    row.remove();
    syncTargetRows();
    const dropped = droppedTargetCount();
    projectTargetsStatus.classList.remove("is-error");
    projectTargetsStatus.textContent = dropped
      ? `已移除 ${dropped} 条，保存后生效`
      : "点「保存识别方案」才会写回项目";
  });

  for (const input of [zhInput, enInput]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addTargetFromInputs();
      }
    });
  }

  row.append(zhInput, enInput, remove);
  return row;
}

function syncTargetRows() {
  if (targetEmpty) targetEmpty.hidden = targetRows.children.length > 0;
}

function renderTargetRows(categories) {
  targetSnapshot = categories.map((item) => ({ id: item.id, zh: item.zh_name, en: item.en_name }));
  targetRows.replaceChildren();
  for (const item of targetSnapshot) {
    targetRows.append(targetRowNode({ id: item.id, zh: item.zh, en: item.en }));
  }
  syncTargetRows();
}

function collectTargetRows() {
  return [...targetRows.children]
    .map((row) => ({
      id: row.dataset.id ? Number(row.dataset.id) : null,
      zh: (row.querySelector(".target-zh")?.value || "").trim(),
      en: (row.querySelector(".target-en")?.value || "").trim(),
      wasZh: row.dataset.zh || "",
      wasEn: row.dataset.en || "",
    }))
    .filter((row) => row.zh || row.en);
}

function droppedTargetCount() {
  const kept = new Set(collectTargetRows().map((row) => row.id).filter(Boolean));
  return targetSnapshot.filter((item) => !kept.has(item.id)).length;
}

function addTargetFromInputs() {
  const zh = targetNewZh.value.trim();
  const en = targetNewEn.value.trim();
  if (!zh) {
    projectTargetsStatus.classList.add("is-error");
    projectTargetsStatus.textContent = "先填中文名，英文可以留空";
    targetNewZh.focus();
    return;
  }
  const clash = collectTargetRows().some((row) => row.zh === zh);
  if (clash) {
    projectTargetsStatus.classList.add("is-error");
    projectTargetsStatus.textContent = `「${zh}」已经在列表里了`;
    targetNewZh.focus();
    return;
  }
  targetRows.append(targetRowNode({ zh, en }));
  syncTargetRows();
  targetNewZh.value = "";
  targetNewEn.value = "";
  targetNewZh.focus();
  projectTargetsStatus.classList.remove("is-error");
  projectTargetsStatus.textContent = `已添加「${zh}」，保存后生效`;
}

targetAddButton?.addEventListener("click", addTargetFromInputs);
for (const input of [targetNewZh, targetNewEn]) {
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addTargetFromInputs();
    }
  });
}

async function loadProjectTargets({ forEditor = false } = {}) {
  if (forEditor) {
    projectTargetsStatus.classList.remove("is-error");
    projectTargetsStatus.textContent = "正在读取项目设置…";
    saveProjectTargets.disabled = true;
  }
  try {
    const project = await ensureImageProject();
    const categories = await call(() => sdk.listCategories(project.id, { includeDisabled: true }));
    // 阈值顺带读一次：方案卡片和对话框滑块都用它，读不到就退回默认值。
    const config = await call(() => sdk.getAutoConfig(project.id)).catch(() => null);
    setBoxThreshold(config?.box_threshold ?? FALLBACK_BOX_THRESHOLD);
    const targets = categories.filter((item) => item.enabled).map((item) => item.zh_name).filter(Boolean);
    renderProjectTargets(targets);
    if (!analysisTargetsDirty) analysisTargets.value = targets.join("、");
    if (forEditor) {
      renderTargetRows(categories.filter((item) => item.enabled));
      projectTargetsStatus.textContent = targets.length ? `已读取 ${targets.length} 项识别目标` : "还没有保存识别目标";
    }
    return targets;
  } catch (error) {
    renderProjectTargets([], { unavailable: true });
    if (forEditor) {
      projectTargetsStatus.classList.add("is-error");
      projectTargetsStatus.textContent = error.message === "Failed to fetch" ? "暂时无法连接后端" : error.message;
    }
    return [];
  } finally {
    if (forEditor) saveProjectTargets.disabled = false;
  }
}

// 用户填的识别目标要变成项目类目：新词建类目，旧词按需启停，
// 再打开自动标注与中文翻译。这样模型才知道要检测什么。
// boxThreshold 只在「识别方案」保存时显式传入：上传流程不传，免得悄悄改掉老项目的阈值。
async function syncAnalysisConfig(projectId, targets, { boxThreshold } = {}) {
  const existing = await call(() => sdk.listCategories(projectId, { includeDisabled: true }));
  const selected = new Set(targets);
  for (const category of existing) {
    const shouldEnable = selected.has(category.zh_name);
    if (category.enabled !== shouldEnable) {
      await call(() => sdk.updateCategory(projectId, category.id, { enabled: shouldEnable }));
    }
    selected.delete(category.zh_name);
  }
  for (const name of selected) {
    await call(() => sdk.createCategory(projectId, { zhName: name, enabled: true }));
  }
  const fields = {
    auto_annotate_enabled: true,
    translate_enabled: true,
  };
  if (boxThreshold !== undefined) fields.box_threshold = boxThreshold;
  await call(() => sdk.updateAutoConfig(projectId, fields));
}

async function submitAnalysisTask(project, images, targets) {
  await syncAnalysisConfig(project.id, targets);
  const task = await call(() => sdk.createTask(project.id, {
    name: `自动分析 ${new Date().toLocaleString("zh-CN", { hour12: false })}`,
    imageIds: images.map((image) => image.id),
    mode: "auto",
  }));
  const submitted = await call(() => sdk.submitTask(project.id, task.id));
  localStorage.setItem(`vision_recent_task_${project.id}`, String(task.id));
  images.forEach((image) => analysisByImage.set(image.id, { kind: "task", status: submitted.status }));
  if (ACTIVE_TASK_STATES.has(submitted.status)) scheduleTaskPoll(project.id, task.id);
  return submitted;
}

analysisTargets.addEventListener("input", () => { analysisTargetsDirty = true; });

projectBoxThreshold?.addEventListener("input", () => {
  if (projectBoxThresholdValue) projectBoxThresholdValue.textContent = formatThreshold(projectBoxThreshold.value);
});

targetSettingsForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const rows = collectTargetRows();
  projectTargetsStatus.classList.remove("is-error");

  if (!rows.length) {
    projectTargetsStatus.classList.add("is-error");
    projectTargetsStatus.textContent = "请至少添加一个识别目标";
    targetNewZh.focus();
    return;
  }
  const missingZh = rows.find((row) => !row.zh);
  if (missingZh) {
    projectTargetsStatus.classList.add("is-error");
    projectTargetsStatus.textContent = "有一条只填了英文，请补上中文名";
    return;
  }
  const seen = new Set();
  for (const row of rows) {
    if (seen.has(row.zh)) {
      projectTargetsStatus.classList.add("is-error");
      projectTargetsStatus.textContent = `「${row.zh}」重复了，删掉其中一条`;
      return;
    }
    seen.add(row.zh);
  }

  saveProjectTargets.disabled = true;
  saveProjectTargets.textContent = "正在保存…";
  projectTargetsStatus.textContent = "正在同步到项目…";
  try {
    const project = await ensureImageProject();
    const boxThreshold = Number(projectBoxThreshold.value);
    const keptIds = new Set(rows.map((row) => row.id).filter(Boolean));
    const removed = targetSnapshot.filter((item) => !keptIds.has(item.id));

    let created = 0;
    let updated = 0;
    for (const row of rows) {
      if (row.id == null) {
        // 新加的一条：英文留空交给后端翻译
        await call(() => sdk.createCategory(project.id, { zhName: row.zh, enName: row.en, enabled: true }));
        created += 1;
        continue;
      }
      if (row.zh === row.wasZh && row.en === row.wasEn) continue;
      // en_name 传空串 = 按当前中文名重新翻译
      await call(() => sdk.updateCategory(project.id, row.id, { zh_name: row.zh, en_name: row.en, enabled: true }));
      updated += 1;
    }
    for (const item of removed) {
      await call(() => sdk.deleteCategory(project.id, item.id));
    }

    await call(() => sdk.updateAutoConfig(project.id, {
      auto_annotate_enabled: true,
      translate_enabled: true,
      box_threshold: boxThreshold,
    }));
    setBoxThreshold(boxThreshold);

    const fresh = await call(() => sdk.listCategories(project.id, { includeDisabled: true }));
    const names = fresh.filter((item) => item.enabled).map((item) => item.zh_name).filter(Boolean);
    renderTargetRows(fresh.filter((item) => item.enabled));
    analysisTargets.value = names.join("、");
    analysisTargetsDirty = false;
    renderProjectTargets(names);

    const bits = [`已保存 ${names.length} 项`];
    if (created) bits.push(`新增 ${created}`);
    if (updated) bits.push(`修改 ${updated}`);
    if (removed.length) bits.push(`删除 ${removed.length}`);
    projectTargetsStatus.textContent = `${bits.join("，")}，置信度 ${formatThreshold(boxThreshold)}，下一次上传会自动使用`;
  } catch (error) {
    projectTargetsStatus.classList.add("is-error");
    projectTargetsStatus.textContent = error.message === "Failed to fetch" ? "暂时无法连接后端，请稍后重试" : error.message;
  } finally {
    saveProjectTargets.disabled = false;
    saveProjectTargets.textContent = "保存识别方案";
  }
});

/* ---------------- 图片详情 ---------------- */

let detailCloseTimer = 0;

async function openImageDetail(image, options = {}) {
  clearTimeout(detailCloseTimer);
  resetInlineDelete();
  currentImage = image;
  currentImageContext = {
    source: options.source || "personal",
    analysisImageId: Number(options.analysisImageId || image.id),
    canDelete: options.canDelete !== false,
  };
  detailTitle.textContent = image.filename;
  detailDimensions.textContent = `${image.width || "?"} × ${image.height || "?"}`;
  detailSize.textContent = formatSize(image.size_bytes);
  const hasAnalysis = analysisByImage.has(currentImageContext.analysisImageId);
  const source = currentImageContext.source;
  detailAnalysis.textContent = (source === "team" || source === "enterprise") && !hasAnalysis
    ? `${source === "team" ? "小组" : "企业"}图片 · 尚未分析`
    : analysisText(currentImageContext.analysisImageId);
  sendToGroup.hidden = source !== "personal";
  // 小组图与企业图没有本地标注数据，导出标注图没有意义
  downloadAnnotated.hidden = source !== "personal";
  deleteImageButton.hidden = !currentImageContext.canDelete;
  // 从哪个栏目点进来，"返回"就回哪里
  backToImages.textContent = source === "team" ? "← 返回空间"
    : source === "enterprise" ? "← 返回空间"
      : "← 返回项目";
  detailImage.removeAttribute("src");
  annotationOverlay.replaceChildren();
  // 「在标注台打开」只在项目里、且这张图确实落在某个可改的任务上时才出现
  if (detailAnnotate) {
    detailAnnotate.hidden = true;
    detailAnnotate.onclick = null;
  }
  if (currentView === "project" && activeProject?.id) {
    sdk
      .listImageTasks(activeProject.id, image.id)
      .then((rows) => {
        if (currentImage !== image) return;
        const editable = (rows || []).find((row) =>
          row.task_status === "rejected_fix"
          || row.task_status === "rejected_reannotate"
          || (row.task_status === "queued" && row.task_mode === "manual")
        );
        if (!editable || !detailAnnotate) return;
        detailAnnotate.hidden = false;
        detailAnnotate.onclick = () => {
          const projectId = activeProject?.id;
          closeImageDetail(true);
          window.VSAnnotate?.open({
            projectId,
            taskId: editable.task_id,
            taskImageId: editable.task_image_id,
          });
        };
      })
      .catch(() => {});
  }
  content.hidden = true;
  detailView.hidden = false;
  requestAnimationFrame(() => detailView.classList.add("is-visible"));

  try {
    const { blob } = await call(() => sdk.imageContent(image.id));
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(blob);
    detailImage.src = previewUrl;
    await detailImage.decode().catch(() => {});
    renderAnnotationOverlay(currentImageContext.analysisImageId);
  } catch {
    detailTitle.textContent = "图片预览加载失败";
  }
}

// immediate = true 时不做淡出，直接换页（从详情切到别的栏目时用）
function closeImageDetail(immediate = false) {
  resetInlineDelete();
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null;
  detailImage.removeAttribute("src");
  annotationOverlay.replaceChildren();
  detailView.classList.remove("is-visible");
  currentImage = null;
  currentImageContext = { source: "personal", analysisImageId: null, canDelete: true };
  clearTimeout(detailCloseTimer);
  if (immediate) {
    detailView.hidden = true;
    content.hidden = false;
    return;
  }
  // 等淡出走完再撤掉这一层，否则会看到硬切
  detailCloseTimer = window.setTimeout(() => {
    detailView.hidden = true;
    content.hidden = false;
  }, 180);
}

// 不能直接把 closeImageDetail 交给 addEventListener：
// 那样会把 MouseEvent 当成 immediate 参数传进去，淡出就没了
backToImages.addEventListener("click", () => closeImageDetail());

/* ---------------- 上传与删除 ---------------- */

function renderSelection() {
  selectionPreviewUrls.forEach((url) => URL.revokeObjectURL(url));
  selectionPreviewUrls = [];
  selectionPanel.hidden = selectedFiles.length === 0;
  selectionTitle.textContent = `已选择 ${selectedFiles.length} 个文件`;
  uploadFeedback.textContent = selectedFiles.length ? "已加入待分析列表，确认后才上传" : "还未选择文件";
  const hasZip = selectedFiles.some((file) => file.name.toLowerCase().endsWith(".zip"));
  selectionSummary.textContent = hasZip
    ? "当前后端暂不支持 ZIP，请先解压后选择其中的图片"
    : `确认后才会上传并开始分析 · 单次最多 ${MAX_BATCH_FILES} 张`;
  selectedFileList.replaceChildren();
  selectedFiles.forEach((file, index) => {
    const row = document.createElement("div");
    row.className = "selected-file";
    const isZip = file.name.toLowerCase().endsWith(".zip");
    row.innerHTML = isZip
      ? "<span class=\"material-symbols-rounded selected-file-icon\" aria-hidden=\"true\">folder_zip</span><strong></strong><small></small><button type=\"button\" aria-label=\"移除此文件\">×</button>"
      : "<img class=\"selected-file-preview\" alt=\"待导入图片预览\" /><strong></strong><small></small><button type=\"button\" aria-label=\"移除此文件\">×</button>";
    if (!isZip) {
      const url = URL.createObjectURL(file);
      selectionPreviewUrls.push(url);
      row.querySelector(".selected-file-preview").src = url;
    }
    row.querySelector("strong").textContent = file.name;
    row.querySelector("small").textContent = formatSize(file.size);
    row.querySelector("button").addEventListener("click", () => {
      selectedFiles.splice(index, 1);
      renderSelection();
    });
    selectedFileList.append(row);
  });
}

// 选文件和拖入共用：先按 50 个上限预检，超出的直接不收，
// 而不是等用户点了"确认"再被后端退回。
function addFiles(incoming) {
  const files = Array.from(incoming || []).filter(Boolean);
  if (!files.length) return;

  const room = MAX_BATCH_FILES - selectedFiles.length;
  if (room <= 0) {
    uploadFeedback.textContent = `一次最多导入 ${MAX_BATCH_FILES} 个文件，请先确认或清除当前列表`;
    return;
  }

  const accepted = files.slice(0, room);
  selectedFiles.push(...accepted);
  renderSelection();

  if (accepted.length < files.length) {
    uploadFeedback.textContent =
      `一次最多导入 ${MAX_BATCH_FILES} 个文件，已忽略多出的 ${files.length - accepted.length} 个`;
  }
}

imageUpload.addEventListener("change", () => {
  addFiles(imageUpload.files);
  imageUpload.value = "";
});

clearSelection.addEventListener("click", () => {
  selectedFiles = [];
  renderSelection();
});

/* 轻量提示条：出现后 6 秒自己退场，不占着屏幕；点"查看图像数据"立即收起 */

let toastFadeTimer = 0;
let toastHideTimer = 0;

function hideUploadToast() {
  clearTimeout(toastFadeTimer);
  clearTimeout(toastHideTimer);
  uploadToast.classList.remove("is-visible");
  toastHideTimer = window.setTimeout(() => { uploadToast.hidden = true; }, 300);
}

function showUploadToast(title, detail) {
  clearTimeout(toastFadeTimer);
  clearTimeout(toastHideTimer);
  uploadToastTitle.textContent = title;
  uploadToastDetail.textContent = detail;
  uploadToast.hidden = false;
  requestAnimationFrame(() => uploadToast.classList.add("is-visible"));
  toastFadeTimer = window.setTimeout(hideUploadToast, 6000);
}

/* ---------------- 上传目标询问：同时在小组与企业里时才问 ---------------- */

const uploadTargetDialog = document.querySelector("#uploadTargetDialog");
const uploadTargetList = document.querySelector("#uploadTargetList");
let targetPickResolve = null;

function finishTargetPick(value) {
  const resolve = targetPickResolve;
  targetPickResolve = null;
  closeDialog(uploadTargetDialog);
  if (resolve) resolve(value);
}

document.querySelector("#closeUploadTarget").addEventListener("click", () => finishTargetPick(null));
uploadTargetDialog.addEventListener("click", (event) => {
  if (event.target === uploadTargetDialog) finishTargetPick(null);
});

function askUploadTarget(groups, enterprises) {
  uploadTargetList.replaceChildren();
  const team = groups[0];
  const ent = enterprises[0];
  const options = [
    { id: "personal", icon: "person", title: "个人空间", note: "上传后立即启动模型分析，结果就在同一页的图像列表里" },
    {
      id: "team", icon: "groups", title: team.name,
      note: groups.length > 1 ? `小组空间 · 共 ${groups.length} 个小组，可在小组页切换` : "小组空间 · 归档到小组成果",
    },
    {
      id: "enterprise", icon: "corporate_fare", title: ent.name,
      note: ent.my_role === "member" ? "企业空间 · 上传后需要管理员审核" : "企业空间 · 直接进入企业图库",
    },
  ];

  options.forEach((option) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "target-pick";
    button.dataset.target = option.id;
    const icon = document.createElement("span");
    icon.className = "material-symbols-rounded";
    icon.setAttribute("aria-hidden", "true");
    icon.textContent = option.icon;
    const copy = document.createElement("span");
    copy.className = "target-pick-copy";
    const strong = document.createElement("strong");
    strong.textContent = option.title;
    const small = document.createElement("small");
    small.textContent = option.note;
    copy.append(strong, small);
    const go = document.createElement("span");
    go.className = "material-symbols-rounded target-pick-go";
    go.setAttribute("aria-hidden", "true");
    go.textContent = "arrow_forward";
    button.append(icon, copy, go);
    button.addEventListener("click", () => finishTargetPick(option.id));
    uploadTargetList.append(button);
  });

  openDialog(uploadTargetDialog);
  return new Promise((resolve) => { targetPickResolve = resolve; });
}

// 直接写进小组 / 企业图库，不经过个人项目，也不建分析任务
async function sendFilesTo(files, destination, groups, enterprises) {
  const toTeam = destination === "team";
  const label = toTeam ? "小组" : "企业";
  confirmAnalysis.disabled = true;
  confirmAnalysis.textContent = "正在上传…";
  uploadFeedback.textContent = `正在导入 ${files.length} 个文件到${label}…`;
  let count = 0;
  try {
    for (let start = 0; start < files.length; start += API_BATCH_FILES) {
      const batch = files.slice(start, start + API_BATCH_FILES);
      const out = toTeam
        ? await call(() => sdk.uploadGroupImages(groups[0].id, batch))
        : await call(() => sdk.uploadEnterpriseImages(enterprises[0].id, batch));
      count += (out || []).length;
    }
    selectedFiles = [];
    renderSelection();
    uploadFeedback.textContent = `已导入 ${count} 张到${label}`;
    showUploadToast(
      `已上传 ${count} 张到${label}`,
      toTeam ? "在小组空间可以看到这批图" : "成员上传的图片会先进审核队列"
    );
    if (toTeam) window.VSTeam?.refreshResults();
    else window.VSTeamEnterprise?.refresh();
  } catch (error) {
    uploadFeedback.textContent = error.message;
  } finally {
    confirmAnalysis.disabled = false;
    confirmAnalysis.innerHTML = "确认并开始分析 <span>→</span>";
  }
}

confirmAnalysis.addEventListener("click", async () => {
  const files = [...selectedFiles];
  if (!files.length) return;
  const targets = selectedTargets();
  if (!targets.length) {
    uploadFeedback.textContent = "请先填写至少一个识别目标，例如：行人、汽车";
    analysisTargets.focus();
    return;
  }
  const hasZip = files.some((file) => file.name.toLowerCase().endsWith(".zip"));
  if (hasZip) {
    uploadFeedback.textContent = "当前后端暂未提供 ZIP 解压接口，请先解压后选择其中的图片";
    return;
  }

  // 同时在小组与企业里时，先问这批图归哪 —— 不同目标走完全不同的链路，
  // 猜错会让图片落到错误的空间，所以宁可多问一次。
  let groups = [];
  let enterprises = [];
  try {
    [groups, enterprises] = await Promise.all([
      sdk.listGroups().catch(() => []),
      sdk.listEnterprises().catch(() => []),
    ]);
  } catch {
    /* 探测失败就走默认的个人工作台，不打断主流程 */
  }
  if ((groups || []).length && (enterprises || []).length) {
    const destination = await askUploadTarget(groups, enterprises);
    if (!destination) return;
    if (destination !== "personal") {
      await sendFilesTo(files, destination, groups, enterprises);
      return;
    }
  }

  confirmAnalysis.disabled = true;
  confirmAnalysis.textContent = "正在上传…";
  uploadFeedback.textContent = `正在导入 ${files.length} 个已确认文件…`;

  try {
    const project = await ensureImageProject();
    let importedCount = 0;
    const importedImages = [];
    // 后端单次最多 20 张，这里按批切分，逐批提交
    for (let start = 0; start < files.length; start += API_BATCH_FILES) {
      const batch = files.slice(start, start + API_BATCH_FILES);
      const result = await call(() => sdk.uploadProjectImages(project.id, batch));
      const list = Array.isArray(result) ? result : [];
      importedCount += list.length;
      importedImages.push(...list);
    }
    confirmAnalysis.textContent = "正在提交模型分析…";
    uploadFeedback.textContent = "图片已导入，正在创建自动分析任务…";
    await submitAnalysisTask(project, importedImages, targets);
    renderProjectTargets(targets);
    analysisTargetsDirty = false;
    selectedFiles = [];
    renderSelection();
    uploadFeedback.textContent = `已导入 ${importedCount} 张图片，模型分析任务已提交`;
    showUploadToast(`已提交 ${importedCount} 张图片`, "模型正在分析，结果会自动更新到图像数据");
    // 列表在后台刷新；即使这一步失败也不该把"上传成功"改成报错
    loadImages({ force: true }).catch(() => {});
  } catch (error) {
    uploadFeedback.textContent = error.message === "Failed to fetch" ? "后端尚未启动，请稍后重试" : error.message;
  } finally {
    confirmAnalysis.disabled = false;
    confirmAnalysis.innerHTML = "确认并开始分析 <span>→</span>";
  }
});

goToData.addEventListener("click", () => {
  hideUploadToast();
  // 上传只能发生在项目页的「图像」tab，这里只做页内定位
  window.setTimeout(() => {
    document.querySelector("#personalDataTitle")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, 80);
});

["dragenter", "dragover"].forEach((eventName) => {
  uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.classList.add("is-dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  uploadZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    uploadZone.classList.remove("is-dragging");
  });
});

uploadZone.addEventListener("drop", (event) => {
  // 直接进待分析列表，和"点击选择"走同一条路径，上限检查也一致
  addFiles(event.dataTransfer.files);
});

// 后端只把 approved / cancelled 当成终态，模型跑完（auto_annotated）仍算活动任务。
// 先查清关联任务，再让用户一次确认；不再先撞一次 409、再弹第二个系统框。
async function findTasksHolding(projectId, imageId) {
  const tasks = await call(() => sdk.listProjectTasks(projectId));
  const holding = [];
  for (const row of tasks) {
    if (row.status === "approved" || row.status === "cancelled") continue;
    const detail = await call(() => sdk.getTask(projectId, row.id)).catch(() => null);
    if (!detail) continue;
    const covered = (detail.images || []).some((item) => Number(item.image_id) === Number(imageId));
    if (covered) holding.push(detail);
  }
  return holding;
}

async function cancelTasksHolding(projectId, imageId, knownTasks = null) {
  const holding = knownTasks || await findTasksHolding(projectId, imageId);
  for (const task of holding) {
    await call(() => sdk.cancelTask(projectId, task.id));
    analysisByImage.set(Number(imageId), { kind: "task", status: "cancelled", error: "" });
  }
  return holding.length;
}

async function removeImageNow(image) {
  await call(() => sdk.deleteImage(image.id));
  closeImageDetail();
  await loadImages({ force: true });
  // 如果这张图也在小组库里，成果区要跟着更新
  if (window.VSTeam) window.VSTeam.refreshResults();
}

deleteImageButton.addEventListener("click", async (event) => {
  event.stopPropagation();
  if (!currentImage) return;
  if (!deleteArmed) {
    armInlineDelete();
    return;
  }

  const image = currentImage;
  clearTimeout(deleteConfirmTimer);
  deleteConfirmTimer = 0;
  deleteArmed = false;
  deleteImageButton.disabled = true;
  deleteImageButton.textContent = "正在检查…";
  try {
    const project = await ensureImageProject();
    const holding = await findTasksHolding(project.id, image.id);
    if (holding.length) {
      deleteImageButton.textContent = "正在取消任务…";
      await cancelTasksHolding(project.id, image.id, holding);
    }
    deleteImageButton.textContent = "正在删除…";
    await removeImageNow(image);
    updateAnalysisLabels();
    showUploadToast("图片已删除", holding.length ? `同时取消了 ${holding.length} 个关联任务` : "图像数据已更新");
  } catch (error) {
    showUploadToast("删除没有完成", error.message || "请刷新后重试");
  } finally {
    deleteImageButton.disabled = false;
    resetInlineDelete();
  }
});

document.addEventListener("click", (event) => {
  if (deleteArmed && !deleteImageButton.contains(event.target)) resetInlineDelete();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && deleteArmed) resetInlineDelete();
});

// 个人图片 → 小组库：选小组的逻辑在 team.js，这里只负责触发
sendToGroup.addEventListener("click", () => {
  if (!currentImage) return;
  if (window.VSTeam) window.VSTeam.openPicker(currentImage);
  else showUploadToast("小组模块还没加载完", "请稍后重试");
});

// 小组成果通过 content_hash 找回个人图库中的原图 id，沿用真实分析状态与标注。
// 这是当前远端没有“服务端共享原图”接口时的前端桥接，不伪造任何状态。
window.VSApp = {
  openImageDetail,
  loadProject,
  async findPersonalImage(image) {
    if (!image) return null;
    if (!imagesLoaded) await loadImages({ force: false }).catch(() => []);
    return loadedImages.find((item) =>
      (image.content_hash && item.content_hash === image.content_hash)
      || Number(item.id) === Number(image.id)) || null;
  },
};

showView("personal");

// 有企业身份就点亮 Logo 旁的分隔栏与头像徽章。纯装饰，失败静默。
sdk
  .listEnterprises()
  .then((rows) => {
    if (rows && rows.length) setEnterpriseBrand(rows[0]);
  })
  .catch(() => {});
