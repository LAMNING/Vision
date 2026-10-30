// Vision Studio — 逐图复核
// 入口是项目「图像」tab 里每一行的「审核」按钮：弹窗显示这张图的标注框，
// 通过 / 驳回（驳回必须写原因，标注员会看到）。复核结果只落在 task_image 上，
// 任务级裁决仍在任务列表里做（后端要求每张图先复核完才能裁决）。
(function () {
  const $ = (selector) => document.querySelector(selector);
  const { sdk, call } = VS;

  const dialog = $("#imageReviewDialog");
  const title = $("#imageReviewTitle");
  const summary = $("#imageReviewSummary");
  const stage = $("#imageReviewStage");
  const image = $("#imageReviewImage");
  const boxes = $("#imageReviewBoxes");
  const reasonWrap = $("#imageReviewReasonWrap");
  const reason = $("#imageReviewReason");
  const statusLine = $("#imageReviewStatus");
  const approveButton = $("#imageReviewApprove");
  const rejectButton = $("#imageReviewReject");

  const state = {
    projectId: null,
    taskId: null,
    taskImageId: null,
    image: null,
    items: [],
    mode: "approve",
    busy: false,
    onDone: null,
    url: null,
  };

  function say(message, isError = false) {
    statusLine.textContent = message || "";
    statusLine.classList.toggle("is-error", !!isError);
  }

  function releaseUrl() {
    if (state.url) URL.revokeObjectURL(state.url);
    state.url = null;
    image.removeAttribute("src");
  }

  function reset() {
    releaseUrl();
    boxes.replaceChildren();
    reason.value = "";
    reasonWrap.hidden = true;
    approveButton.textContent = "通过";
    rejectButton.textContent = "驳回";
    say("");
  }

  function close() {
    if (dialog.hidden) return;
    dialog.classList.remove("is-visible");
    window.setTimeout(() => {
      dialog.hidden = true;
      reset();
    }, 140);
    state.onDone = null;
  }

  function drawBoxes() {
    boxes.replaceChildren();
    if (!state.items.length) {
      boxes.append(VS.el("li", "review-box-empty", "这张图还没有标注框。"));
      return;
    }
    state.items.forEach((item) => {
      const row = VS.el("li", "review-box-row");
      const label = VS.el("span", "review-box-label", item.label || "目标");
      const score =
        item.confidence == null
          ? ""
          : ` ${Math.round(Number(item.confidence) * 100)}%`;
      row.append(label, VS.el("span", "review-box-score", score));
      boxes.append(row);
    });
  }

  function paintOverlay() {
    if (!stage) return;
    // viewBox 用原图尺寸，rect 直接写原图像素坐标，缩放交给浏览器
    const natural = {
      width: image.naturalWidth || Number(state.image?.width) || 0,
      height: image.naturalHeight || Number(state.image?.height) || 0,
    };
    let svg = stage.querySelector("svg");
    if (!svg) {
      svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "review-overlay");
      stage.append(svg);
    }
    svg.innerHTML = "";
    if (!natural.width || !natural.height) return;
    svg.setAttribute("viewBox", `0 0 ${natural.width} ${natural.height}`);
    svg.setAttribute("preserveAspectRatio", "xMidYMid meet");

    state.items.forEach((item) => {
      const box = item.payload?.box;
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

  async function open({ projectId, taskId, taskImageId, image: imageRow, onDone }) {
    if (dialog.hidden) {
      dialog.hidden = false;
      window.requestAnimationFrame(() => dialog.classList.add("is-visible"));
    }
    reset();
    state.projectId = projectId;
    state.taskId = taskId;
    state.taskImageId = taskImageId;
    state.image = imageRow || null;
    state.items = [];
    state.mode = "approve";
    state.onDone = onDone || null;

    title.textContent = "审核标注";
    summary.textContent = imageRow?.filename
      ? `${imageRow.filename} · 逐图复核，通过或写明要改的地方`
      : "逐图复核，通过或写明要改的地方";
    say("正在读取这一张的标注…");

    try {
      const [page, content] = await Promise.all([
        call(() =>
          sdk.listAnnotations(projectId, {
            taskId,
            taskImageId,
            limit: 200,
          })
        ),
        call(() => sdk.imageContent(imageRow.id)),
      ]);
      const items = Array.isArray(page) ? page : page?.items || [];
      state.items = items.filter(
        (item) => item.annotation_type === "bbox" && Array.isArray(item.payload?.box)
      );
      state.url = URL.createObjectURL(content.blob);
      image.src = state.url;
      image.onload = () => paintOverlay();
      drawBoxes();
      say(state.items.length ? `这一张有 ${state.items.length} 个框` : "这一张没有标注框，确认后可通过");
    } catch (error) {
      say(error.message || "读取标注失败", true);
    }
  }

  function setMode(mode) {
    state.mode = mode;
    const rejecting = mode === "reject";
    reasonWrap.hidden = !rejecting;
    approveButton.textContent = rejecting ? "确认驳回" : "通过";
    rejectButton.textContent = rejecting ? "取消驳回" : "驳回";
    if (rejecting) reason.focus();
  }

  async function submit() {
    if (state.busy) return;
    const rejecting = state.mode === "reject";
    const text = reason.value.trim();
    if (rejecting && !text) {
      say("驳回要写明原因，标注员会照着改", true);
      reason.focus();
      return;
    }

    state.busy = true;
    approveButton.disabled = true;
    rejectButton.disabled = true;
    say(rejecting ? "正在驳回…" : "正在通过…");
    try {
      await call(() =>
        sdk.reviewTaskImage(state.projectId, state.taskId, state.taskImageId, {
          approved: !rejecting,
          reason: rejecting ? text : undefined,
        })
      );
      const done = state.onDone;
      state.onDone = null;
      close();
      if (done) done(rejecting ? "rejected" : "approved");
    } catch (error) {
      say(error.message || "复核没有提交成功", true);
    } finally {
      state.busy = false;
      approveButton.disabled = false;
      rejectButton.disabled = false;
    }
  }

  approveButton.addEventListener("click", () => submit());

  rejectButton.addEventListener("click", () => {
    if (state.mode === "reject") {
      setMode("approve");
      say("");
      return;
    }
    setMode("reject");
    say("写明要改的地方再确认驳回");
  });

  $("#closeImageReview").addEventListener("click", close);
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dialog.hidden) close();
  });

  window.VSReview = { open, close };
})();
