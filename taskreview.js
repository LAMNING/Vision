// 任务复核工作台：任务列表里「待复核」那一行的「复核」按钮打开这里。
//
// 为什么要有这一层：后端把复核拆成了两步 —— 先逐图 approve/reject，再对任务做
// 一次裁决（approve 要求全部通过、reject 要求至少一张被驳回）。图像 tab 只能逐张
// 点，没有地方能看到「这个任务还有几张没看」和「看完之后怎么结案」，所以复核人
// 会卡在半路。
//
// 版式直接复用标注台的 .annotate-*（左选图 / 中看图 / 右裁决），三个主题只在
// CSS 里补复核专用的几处颜色，脚本不判断主题。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { sdk, call, el, request } = VS;

  // 逐图复核状态 → 文案与 tag 配色（tag-* 是图像 tab 那套，主题里已经有覆盖）
  const REVIEW_TAG = {
    pending: ["待审核", "tag-wait"],
    approved: ["已通过", "tag-pass"],
    rejected: ["已退回", "tag-reject"],
  };
  const DECISION_LABEL = { approve: "通过", reject_fix: "退回修改", reject_reannotate: "退回重标" };

  const state = {
    projectId: null,
    task: null,
    rows: [],          // 任务里的每张图（TaskImageRead）
    index: -1,
    items: [],         // 当前图的 bbox
    urls: new Map(),   // "f123" / "t123" -> objectURL
    token: 0,          // 每次 open 自增，异步结果回来时用它判断是否已经切走
    busy: false,
    onDone: null,
  };

  /* ---------------- 小工具 ---------------- */

  function say(message, isError = false) {
    const line = $("#taskReviewStatus");
    line.textContent = message || "";
    line.classList.toggle("is-error", !!isError);
  }

  function currentRow() {
    return state.index >= 0 && state.index < state.rows.length ? state.rows[state.index] : null;
  }

  function imageOf(row) {
    return row && row.image ? row.image : null;
  }

  function fileName(row) {
    const image = imageOf(row);
    return (image && image.filename) || `#${row.image_id}`;
  }

  function counts() {
    const total = state.rows.length;
    const approved = state.rows.filter((row) => row.review_status === "approved").length;
    const rejected = state.rows.filter((row) => row.review_status === "rejected").length;
    return { total, approved, rejected, pending: total - approved - rejected };
  }

  function reviewable() {
    return !!state.task && state.task.status === "auto_annotated";
  }

  /* ---------------- 图层 ---------------- */

  function openLayer() {
    const view = $("#taskReviewView");
    $("#contentArea").hidden = true;
    document.body.classList.add("has-layer");
    view.hidden = false;
    requestAnimationFrame(() => view.classList.add("is-visible"));
  }

  function closeLayer(immediate = false) {
    const view = $("#taskReviewView");
    const content = $("#contentArea");
    view.classList.remove("is-visible");
    if (immediate) {
      view.hidden = true;
      content.hidden = false;
      document.body.classList.remove("has-layer");
      return;
    }
    window.setTimeout(() => {
      view.hidden = true;
      content.hidden = false;
      document.body.classList.remove("has-layer");
    }, 180);
  }

  function isOpen() {
    const view = $("#taskReviewView");
    return view && !view.hidden;
  }

  /* ---------------- 图片字节 ---------------- */

  async function fullUrl(imageId) {
    const key = `f${imageId}`;
    if (state.urls.has(key)) return state.urls.get(key);
    const { blob } = await call(() => sdk.imageContent(imageId));
    const url = URL.createObjectURL(blob);
    state.urls.set(key, url);
    return url;
  }

  async function thumbUrl(imageId) {
    const key = `t${imageId}`;
    if (state.urls.has(key)) return state.urls.get(key);
    const blob = await request("GET", `/images/${imageId}/content`, { params: { size: "thumb" }, raw: true });
    const url = URL.createObjectURL(blob);
    state.urls.set(key, url);
    return url;
  }

  function releaseUrls() {
    state.urls.forEach((url) => URL.revokeObjectURL(url));
    state.urls.clear();
  }

  /* ---------------- 左栏 ---------------- */

  function renderRail() {
    const box = $("#taskReviewRail");
    box.replaceChildren();
    if (!state.rows.length) {
      box.append(el("div", "annotate-empty", "这个任务里还没有图片"));
      return;
    }
    state.rows.forEach((row, index) => {
      const image = imageOf(row);
      const item = el("button", "annotate-rail-item");
      item.type = "button";
      item.dataset.state = row.annotation_status || "pending";
      item.dataset.review = row.review_status || "pending";
      if (index === state.index) item.classList.add("is-active");
      item.append(el("span", "annotate-rail-state"));
      const thumb = el("img", "annotate-rail-thumb");
      thumb.alt = "";
      item.append(thumb);
      item.append(el("span", "annotate-rail-name", fileName(row)));
      const [label, tone] = REVIEW_TAG[row.review_status] || REVIEW_TAG.pending;
      item.append(el("span", `tag ${tone} review-rail-tag`, label));
      item.title = `${fileName(row)}\n${row.annotation_status === "annotated" ? "已标注" : "未标注"}\n${label}`;
      item.addEventListener("click", () => selectImage(index));
      box.append(item);
      if (image) {
        thumbUrl(image.id)
          .then((url) => {
            if (thumb.isConnected) thumb.src = url;
          })
          .catch(() => { /* 缩略图取不到就留灰底，不影响复核 */ });
      }
    });
  }

  function renderProgress() {
    const { total, approved, rejected } = counts();
    const bits = [`已通过 ${approved} / ${total}`];
    if (rejected) bits.push(`已退回 ${rejected}`);
    $("#taskReviewProgress").textContent = bits.join(" · ");
  }

  /* ---------------- 中栏：图片 + 只读框 ---------------- */

  function paintOverlay() {
    const canvas = $("#taskReviewCanvas");
    const img = $("#taskReviewImage");
    let svg = canvas.querySelector("svg");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "review-overlay");
      canvas.append(svg);
    }
    svg.innerHTML = "";
    // 图还没解码完时 naturalWidth 是 0；退回用任务里带的原始宽高，先按原图坐标铺好 viewBox。
    const image = imageOf(currentRow());
    const width = img.naturalWidth || Number((image && image.width) || 0);
    const height = img.naturalHeight || Number((image && image.height) || 0);
    if (!width || !height) return;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    state.items.forEach((item) => {
      const box = item.payload && item.payload.box;
      if (!Array.isArray(box) || box.length !== 4) return;
      const [x1, y1, x2, y2] = box.map(Number);
      if (![x1, y1, x2, y2].every(Number.isFinite)) return;
      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(Math.min(x1, x2)));
      rect.setAttribute("y", String(Math.min(y1, y2)));
      rect.setAttribute("width", String(Math.abs(x2 - x1)));
      rect.setAttribute("height", String(Math.abs(y2 - y1)));
      svg.append(rect);
    });
  }

  function renderBoxes() {
    const box = $("#taskReviewBoxes");
    box.replaceChildren();
    if (!state.items.length) {
      box.append(el("div", "annotate-empty", "这张图还没有标注框。"));
      return;
    }
    state.items.forEach((item) => {
      const row = el("div", "review-box-row");
      const score = item.confidence == null ? "" : ` ${Math.round(Number(item.confidence) * 100)}%`;
      row.append(el("span", "review-box-label", item.label || "目标"), el("span", "review-box-score", score));
      box.append(row);
    });
  }

  function renderVerdict() {
    const box = $("#taskReviewVerdict");
    box.replaceChildren();
    const row = currentRow();
    if (!row) return;
    const [label, tone] = REVIEW_TAG[row.review_status] || REVIEW_TAG.pending;
    box.append(el("span", `tag ${tone}`, label));
    box.append(
      el("span", "review-verdict-note", row.annotation_status === "annotated" ? "已标注完成" : "还没有标注")
    );
    box.append(el("span", "review-verdict-name", fileName(row)));
    if (row.review_status === "rejected" && row.reject_reason) {
      box.append(el("p", "review-verdict-reason", `驳回原因：${row.reject_reason}`));
    }
  }

  /* ---------------- 右栏：这一张的裁决 ---------------- */

  function renderActions() {
    const row = currentRow();
    const editable = reviewable() && !!row;
    const canApprove = editable && row.annotation_status === "annotated" && row.review_status !== "approved";
    const canReject = editable && row.review_status !== "rejected";

    $("#taskReviewApprove").disabled = !canApprove;
    $("#taskReviewReject").disabled = !canReject;
    $("#taskReviewReason").disabled = !editable;

    const { total, approved, rejected, pending } = counts();
    $("#taskReviewPass").disabled = !editable || pending > 0 || rejected > 0 || approved === 0 || approved !== total;
    $("#taskReviewRejectFix").disabled = !editable || rejected === 0;
    $("#taskReviewReannotate").disabled = !editable || rejected === 0;
    $("#taskReviewComment").disabled = !editable;
  }

  /** 换图时把「驳回原因」收起来——上一次写的理由不该跟着带到另一张上。 */
  function resetReason() {
    $("#taskReviewReasonWrap").hidden = true;
    $("#taskReviewReason").value = "";
    $("#taskReviewReject").textContent = "驳回这张";
  }

  function selectImage(index) {
    if (index < 0 || index >= state.rows.length) return;
    state.index = index;
    resetReason();
    renderRail();
    renderVerdict();
    renderActions();
    loadImage();
  }

  async function loadImage() {
    const row = currentRow();
    const image = imageOf(row);
    if (!row || !image) return;
    const token = state.token;
    const img = $("#taskReviewImage");
    state.items = [];
    paintOverlay();
    renderBoxes();
    say("正在读取这一张的标注…");
    try {
      const [url, page] = await Promise.all([
        fullUrl(image.id),
        call(() => sdk.listAnnotations(state.projectId, {
          taskId: state.task.id,
          taskImageId: row.id,
          limit: 200,
        })),
      ]);
      if (token !== state.token) return;
      img.src = url;
      img.onload = () => {
        if (token === state.token) paintOverlay();
      };
      const items = Array.isArray(page) ? page : (page && page.items) || [];
      state.items = items.filter(
        (item) => item.annotation_type === "bbox" && Array.isArray(item.payload && item.payload.box)
      );
      paintOverlay();
      renderBoxes();
      const [label] = REVIEW_TAG[row.review_status] || REVIEW_TAG.pending;
      say(`这一张有 ${state.items.length} 个框 · ${label}`);
    } catch (error) {
      if (token !== state.token) return;
      say(error.message || "读取这一张的标注失败", true);
    }
  }

  /** 裁决完自动跳到下一张还没看的，复核人不用自己找。 */
  function advance() {
    const next = state.rows.findIndex((row) => row.review_status === "pending");
    if (next >= 0) {
      selectImage(next);
      return;
    }
    selectImage(state.index);
    const { total, approved, rejected, pending } = counts();
    if (!pending && !rejected) say(`全部 ${total} 张都通过了，可以「通过任务」。`);
    else say(`已经看过一遍：${approved} 张通过，${rejected} 张退回。`);
  }

  function setBusy(busy) {
    state.busy = busy;
    document.querySelectorAll("#taskReviewView button").forEach((button) => {
      button.disabled = busy;
    });
    if (!busy) renderActions();
  }

  async function reviewCurrent(approved) {
    if (state.busy) return;
    const row = currentRow();
    if (!row) return;
    const reason = $("#taskReviewReason").value.trim();
    if (!approved && !reason) {
      say("驳回要写明原因，标注员会照着改", true);
      $("#taskReviewReason").focus();
      return;
    }
    setBusy(true);
    say(approved ? "正在通过这一张…" : "正在退回这一张…");
    try {
      const updated = await call(() =>
        sdk.reviewTaskImage(state.projectId, state.task.id, row.id, {
          approved,
          reason: approved ? undefined : reason,
        })
      );
      row.review_status = updated.review_status;
      row.reject_reason = updated.reject_reason;
      row.reviewed_at = updated.reviewed_at;
      renderProgress();
      // 最后一张通过时后端已经把任务收口了（全部通过 → approved），这里不必再让人点
      // 一次「通过任务」，直接回到项目页并把结果交给调用方。
      if (approved) {
        const latest = await call(() => sdk.getTask(state.projectId, state.task.id)).catch(
          () => null
        );
        if (latest && latest.status === "approved") {
          // leave() 会清掉 onDone，所以先把回调取出来
          const done = state.onDone;
          leave(true);
          done?.("approve");
          return;
        }
      }
      advance();
    } catch (error) {
      say(error.message || "这一张没有复核成功", true);
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision) {
    if (state.busy) return;
    setBusy(true);
    say(`正在${DECISION_LABEL[decision] || "结案"}…`);
    try {
      await call(() =>
        sdk.submitTaskReview(state.projectId, state.task.id, {
          decision,
          comment: $("#taskReviewComment").value.trim() || undefined,
        })
      );
      const done = state.onDone;
      leave(true);
      if (done) done(decision);
    } catch (error) {
      say(error.message || "复核结论没有提交成功", true);
    } finally {
      setBusy(false);
    }
  }

  /* ---------------- 打开 / 关闭 ---------------- */

  async function open({ projectId, taskId, onDone }) {
    state.projectId = projectId;
    state.task = null;
    state.rows = [];
    state.index = -1;
    state.items = [];
    state.onDone = onDone || null;
    state.token += 1;
    const token = state.token;

    releaseUrls();
    openLayer();
    $("#taskReviewTitle").textContent = "读取任务…";
    $("#taskReviewProgress").textContent = "";
    $("#taskReviewRail").replaceChildren();
    $("#taskReviewBoxes").replaceChildren();
    $("#taskReviewVerdict").replaceChildren();
    $("#taskReviewImage").removeAttribute("src");
    $("#taskReviewComment").value = "";
    say("正在读取任务与图片…");

    try {
      const task = await call(() => sdk.getTask(projectId, taskId));
      if (token !== state.token) return;
      state.task = task;
      state.rows = (task.images || []).slice();
      $("#taskReviewTitle").textContent = task.name || `任务 #${task.id}`;
      if (!state.rows.length) {
        say("这个任务里还没有图片，没有可复核的内容。", true);
        renderRail();
        renderProgress();
        renderActions();
        return;
      }
      if (!reviewable()) {
        say(`任务当前是「${task.status}」，只有待复核的任务能在这里裁决。`, true);
      }
      renderRail();
      renderProgress();
      const first = state.rows.findIndex((row) => row.review_status === "pending");
      selectImage(first >= 0 ? first : 0);
    } catch (error) {
      if (token !== state.token) return;
      say(error.message || "读取任务失败", true);
    }
  }

  function leave(immediate = false) {
    if (!isOpen()) return;
    state.token += 1;
    state.onDone = null;
    releaseUrls();
    closeLayer(immediate);
  }

  /* ---------------- 绑定 ---------------- */

  function bind() {
    $("#taskReviewBack").addEventListener("click", () => leave());
    $("#taskReviewApprove").addEventListener("click", () => reviewCurrent(true));
    $("#taskReviewReject").addEventListener("click", () => {
      // 第一次点先把原因框亮出来，第二次（写了原因）才真的退回
      const wrap = $("#taskReviewReasonWrap");
      if (wrap.hidden) {
        wrap.hidden = false;
        $("#taskReviewReject").textContent = "确认驳回";
        say("写明要改的地方再点一次「确认驳回」");
        $("#taskReviewReason").focus();
        return;
      }
      reviewCurrent(false);
    });
    $("#taskReviewPass").addEventListener("click", () => decide("approve"));
    $("#taskReviewRejectFix").addEventListener("click", () => decide("reject_fix"));
    $("#taskReviewReannotate").addEventListener("click", () => decide("reject_reannotate"));
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !isOpen()) return;
      leave();
    });
  }

  bind();
  window.VSTaskReview = { open, close: leave };
})();
