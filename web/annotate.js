// 人工标注工作台：手动任务（mode=manual、status=queued）与退回返修（rejected_fix /
// rejected_reannotate）共用同一套界面，在图上拖框、改框、删框，整组覆盖保存。
//
// 三个主题共用同一份 DOM，脚本里不写任何主题判断：
//   · 澄白 pure-white  → styles.css 里的基础层（本节末的 .annotate-* 规则）
//   · 晴蓝 clear-sky   → themes.css 的 html[data-theme="clear-sky"] .annotate-* 覆盖
//   · 经典 classic      → 复用晴蓝组件层，再由 html[data-vision-theme="classic"] 收紧密度
// 所以这里只写结构、交互和 CSS 变量（--box-color / --box-fill），颜色一律交给 CSS。
(() => {
  const $ = (selector) => document.querySelector(selector);
  const { call, request, sdk, el, status } = VS;

  // 类目配色：按类目在列表里的位置取，同一个类目在任何主题下都是同一个颜色，
  // 深浅都够在白底上看清 —— 框线、色块、小圆点全用这一个值。
  const COLORS = [
    "#0b57d0", "#b45309", "#0f766e", "#7c3aed", "#be123c",
    "#15803d", "#4338ca", "#a21caf", "#0369a1", "#c2410c",
  ];
  const MIN_SIZE = 6;        // 原图像素：小于这个尺寸的框视为误触，松手即丢
  const MAX_HISTORY = 40;
  const HANDLES = ["nw", "ne", "sw", "se"];
  // 任务在这三个状态才允许编辑标注（与后端 tasks.py 的校验保持一致）
  const EDITABLE = new Set(["queued", "rejected_fix", "rejected_reannotate"]);

  const state = {
    projectId: null,
    task: null,
    rows: [],              // 任务里的每张图（TaskImageRead）
    index: -1,
    categories: [],
    activeCategoryId: null,
    boxes: [],             // [{ key, categoryId, label, box:[x0,y0,x1,y1] }]，坐标是原图像素
    selected: null,
    history: [],
    dirty: false,
    token: 0,              // 每次 open 自增：异步结果回来时用它判断是否已经切走
    urls: new Map(),       // "f123" / "t123" -> objectURL
    drag: null,
  };
  let keySeed = 0;

  /* ---------------- 小工具 ---------------- */

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  function colorOf(categoryId) {
    const index = state.categories.findIndex((item) => item.id === categoryId);
    return COLORS[(index < 0 ? 0 : index) % COLORS.length];
  }

  /** #0b57d0 + .12 → rgba(11,87,208,.12)，填充色跟着框线走，不依赖 color-mix。 */
  function tint(hex, alpha) {
    const value = hex.replace("#", "");
    const r = parseInt(value.slice(0, 2), 16);
    const g = parseInt(value.slice(2, 4), 16);
    const b = parseInt(value.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }

  function categoryOf(categoryId) {
    return state.categories.find((item) => item.id === categoryId) || null;
  }

  function currentRow() {
    return state.index >= 0 && state.index < state.rows.length ? state.rows[state.index] : null;
  }

  function imageOf(row) {
    return row && row.image ? row.image : null;
  }

  function natural() {
    const img = $("#annotateImage");
    return { w: img.naturalWidth || 0, h: img.naturalHeight || 0 };
  }

  function toPercent(value, total) {
    return `${total > 0 ? (value / total) * 100 : 0}%`;
  }

  /* ---------------- 图层（整层盖住内容区，和图片详情同一套做法） ---------------- */

  function openLayer() {
    const view = $("#annotateView");
    $("#contentArea").hidden = true;
    // 顶栏在这一层里没有意义（返回入口在标注台自己头上），收起来多给画布一点高度
    document.body.classList.add("has-layer");
    view.hidden = false;
    requestAnimationFrame(() => view.classList.add("is-visible"));
  }

  function closeLayer(immediate = false) {
    const view = $("#annotateView");
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
    const view = $("#annotateView");
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

  /* ---------------- 左栏：任务里的图片 ---------------- */

  const ROW_STATE = { pending: "待标注", annotated: "已标注", failed: "标注失败" };

  function renderRail() {
    const box = $("#annotateRails");
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
      if (index === state.index) item.classList.add("is-active");
      item.append(el("span", "annotate-rail-state"));
      const thumb = el("img", "annotate-rail-thumb");
      thumb.alt = "";
      item.append(thumb);
      item.append(el("span", "annotate-rail-name", (image && image.filename) || `#${row.image_id}`));
      item.title = `${(image && image.filename) || `#${row.image_id}`}\n${ROW_STATE[row.annotation_status] || "待标注"}`;
      item.addEventListener("click", () => selectImage(index));
      box.append(item);
      if (image) {
        thumbUrl(image.id)
          .then((url) => {
            if (thumb.isConnected) thumb.src = url;
          })
          .catch(() => { /* 缩略图取不到就留灰底，不影响标注 */ });
      }
    });
  }

  function renderProgress() {
    const total = state.rows.length;
    const done = state.rows.filter((row) => row.annotation_status === "annotated").length;
    $("#annotateProgress").textContent = `已标注 ${done} / ${total}`;
  }

  /* ---------------- 右栏：类目 ---------------- */

  function renderCategories() {
    const box = $("#annotateCategories");
    box.replaceChildren();
    if (!state.categories.length) {
      box.append(el("div", "annotate-empty", "项目还没有识别类目。先去「设置 → 识别类目」加一个，再回来标注。"));
      return;
    }
    state.categories.forEach((category) => {
      const color = colorOf(category.id);
      const item = el("button", "annotate-category");
      item.type = "button";
      item.style.setProperty("--box-color", color);
      if (category.id === state.activeCategoryId) item.classList.add("is-active");
      const dot = el("i", "");
      item.append(dot, el("span", "", category.zh_name || category.en_name || `类目 #${category.id}`));
      item.addEventListener("click", () => pickCategory(category.id));
      box.append(item);
    });
  }

  function pickCategory(categoryId) {
    state.activeCategoryId = categoryId;
    // 已经选中某个框时，点类目 = 改这个框的类目，和常见标注工具的手感一致
    const target = state.boxes.find((item) => item.key === state.selected);
    if (target) {
      const category = categoryOf(categoryId);
      target.categoryId = categoryId;
      target.label = (category && (category.zh_name || category.en_name)) || target.label;
      commitBoxes();
    }
    renderCategories();
    renderStage();
    renderBoxList();
    status("#annotateStatus", "");
  }

  /* ---------------- 中栏：画布与标注框 ---------------- */

  function paintBox(node, box) {
    const { w, h } = natural();
    node.style.left = toPercent(box[0], w);
    node.style.top = toPercent(box[1], h);
    node.style.width = toPercent(box[2] - box[0], w);
    node.style.height = toPercent(box[3] - box[1], h);
  }

  function buildBoxNode(item) {
    const color = colorOf(item.categoryId);
    const node = el("div", "annotate-box");
    node.dataset.key = item.key;
    node.style.setProperty("--box-color", color);
    node.style.setProperty("--box-fill", tint(color, 0.12));
    paintBox(node, item.box);
    if (item.key === state.selected) node.classList.add("is-selected");
    node.append(el("span", "annotate-box-label", item.label));
    if (item.key === state.selected) {
      HANDLES.forEach((dir) => {
        const handle = el("span", "annotate-handle");
        handle.dataset.dir = dir;
        node.append(handle);
      });
    }
    return node;
  }

  function renderStage() {
    const canvas = $("#annotateCanvas");
    canvas.querySelectorAll(".annotate-box").forEach((node) => node.remove());
    if (!state.rows.length || state.index < 0) return;
    state.boxes.forEach((item) => canvas.append(buildBoxNode(item)));
  }

  function nodeFor(key) {
    return $(`#annotateCanvas .annotate-box[data-key="${key}"]`);
  }

  function boxListRow(item) {
    const row = el("div", "annotate-boxrow");
    row.style.setProperty("--box-color", colorOf(item.categoryId));
    if (item.key === state.selected) row.classList.add("is-selected");
    const box = item.box;
    row.append(
      el("i", ""),
      el("span", "", item.label),
      el("small", "", `${Math.round(box[2] - box[0])}×${Math.round(box[3] - box[1])}`)
    );
    const remove = el("button", "annotate-boxrow-del", "×");
    remove.type = "button";
    remove.title = "删除这个框";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      dropBox(item.key);
    });
    row.append(remove);
    row.addEventListener("click", () => {
      state.selected = item.key;
      renderStage();
      renderBoxList();
    });
    return row;
  }

  function renderBoxList() {
    const box = $("#annotateBoxList");
    box.replaceChildren();
    if (!state.boxes.length) {
      box.append(el("div", "annotate-empty", "还没有框。在图上按住拖出一个矩形。"));
      return;
    }
    state.boxes.forEach((item) => box.append(boxListRow(item)));
  }

  /* ---------------- 撤销 / 脏标记 ---------------- */

  function snapshot() {
    state.history.push(state.boxes.map((item) => ({ ...item, box: item.box.slice() })));
    if (state.history.length > MAX_HISTORY) state.history.shift();
  }

  // 任何写操作走这里：先记快照，再标脏，最后同步按钮状态
  function commitBoxes() {
    state.dirty = true;
    updateActions();
  }

  function undo() {
    if (!state.history.length) {
      status("#annotateStatus", "没有可撤销的操作");
      return;
    }
    state.boxes = state.history.pop();
    if (!state.boxes.some((item) => item.key === state.selected)) state.selected = null;
    state.dirty = true;
    renderStage();
    renderBoxList();
    updateActions();
    status("#annotateStatus", "已撤销");
  }

  function dropBox(key) {
    snapshot();
    state.boxes = state.boxes.filter((item) => item.key !== key);
    if (state.selected === key) state.selected = null;
    commitBoxes();
    renderStage();
    renderBoxList();
  }

  /* ---------------- 指针交互 ---------------- */

  function pointerToImage(event) {
    const img = $("#annotateImage");
    const rect = img.getBoundingClientRect();
    const { w, h } = natural();
    if (!w || !h || !rect.width || !rect.height) return { x: 0, y: 0 };
    return {
      x: clamp(((event.clientX - rect.left) / rect.width) * w, 0, w),
      y: clamp(((event.clientY - rect.top) / rect.height) * h, 0, h),
    };
  }

  function onPointerDown(event) {
    if (event.button !== 0) return;
    if (!state.rows.length || state.index < 0) return;
    if (!state.categories.length) {
      status("#annotateStatus", "项目还没有识别类目，先在设置里加一个", true);
      return;
    }
    const canvas = $("#annotateCanvas");
    const boxNode = event.target.closest ? event.target.closest(".annotate-box") : null;
    const handle = event.target.closest ? event.target.closest(".annotate-handle") : null;

    if (boxNode) {
      event.preventDefault();
      const item = state.boxes.find((entry) => entry.key === boxNode.dataset.key);
      if (!item) return;
      const dir = handle ? handle.dataset.dir : null;
      // select() 会重建 DOM，方向要先读出来
      if (state.selected !== item.key) {
        state.selected = item.key;
        renderStage();
        renderBoxList();
      }
      canvas.setPointerCapture(event.pointerId);
      state.drag = {
        mode: dir ? "resize" : "move",
        dir,
        key: item.key,
        startBox: item.box.slice(),
        start: pointerToImage(event),
      };
      return;
    }

    // 空白处按下 = 拖一个新框
    if (state.activeCategoryId == null) {
      status("#annotateStatus", "先在右侧选一个类目，再在图上拖框", true);
      return;
    }
    event.preventDefault();
    const begin = pointerToImage(event);
    const item = {
      key: `box${++keySeed}`,
      categoryId: state.activeCategoryId,
      label: (categoryOf(state.activeCategoryId) || {}).zh_name || "未命名",
      box: [begin.x, begin.y, begin.x, begin.y],
    };
    snapshot();
    state.boxes.push(item);
    state.selected = item.key;
    renderStage();
    renderBoxList();
    canvas.setPointerCapture(event.pointerId);
    state.drag = { mode: "draw", key: item.key, start: begin };
    status("#annotateStatus", "");
  }

  function onPointerMove(event) {
    const drag = state.drag;
    if (!drag) return;
    event.preventDefault();
    const point = pointerToImage(event);
    const item = state.boxes.find((entry) => entry.key === drag.key);
    if (!item) return;
    const { w, h } = natural();

    if (drag.mode === "draw") {
      item.box = [
        Math.min(drag.start.x, point.x),
        Math.min(drag.start.y, point.y),
        Math.max(drag.start.x, point.x),
        Math.max(drag.start.y, point.y),
      ];
    } else if (drag.mode === "move") {
      const width = drag.startBox[2] - drag.startBox[0];
      const height = drag.startBox[3] - drag.startBox[1];
      const dx = point.x - drag.start.x;
      const dy = point.y - drag.start.y;
      const x0 = clamp(drag.startBox[0] + dx, 0, w - width);
      const y0 = clamp(drag.startBox[1] + dy, 0, h - height);
      item.box = [x0, y0, x0 + width, y0 + height];
    } else {
      // resize：nw / ne / sw / se 决定哪两条边跟着走
      const box = drag.startBox.slice();
      if (drag.dir.includes("w")) box[0] = clamp(point.x, 0, box[2] - MIN_SIZE);
      if (drag.dir.includes("e")) box[2] = clamp(point.x, box[0] + MIN_SIZE, w);
      if (drag.dir.includes("n")) box[1] = clamp(point.y, 0, box[3] - MIN_SIZE);
      if (drag.dir.includes("s")) box[3] = clamp(point.y, box[1] + MIN_SIZE, h);
      item.box = box;
    }

    const node = nodeFor(item.key);
    if (node) paintBox(node, item.box);
    state.dirty = true;
  }

  function onPointerUp(event) {
    const drag = state.drag;
    if (!drag) return;
    state.drag = null;
    const canvas = $("#annotateCanvas");
    if (canvas.hasPointerCapture && canvas.hasPointerCapture(event.pointerId)) {
      canvas.releasePointerCapture(event.pointerId);
    }
    const item = state.boxes.find((entry) => entry.key === drag.key);
    if (item && drag.mode === "draw") {
      const width = item.box[2] - item.box[0];
      const height = item.box[3] - item.box[1];
      if (width < MIN_SIZE || height < MIN_SIZE) {
        // 误触：撤销快照，直接把框丢掉
        if (state.history.length) state.history.pop();
        state.boxes = state.boxes.filter((entry) => entry.key !== item.key);
        state.selected = null;
      }
    }
    renderStage();
    renderBoxList();
    updateActions();
  }

  /* ---------------- 键盘 ---------------- */

  function onKeyDown(event) {
    if (!isOpen()) return;
    const tag = event.target && event.target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (event.key === "Escape") {
      event.preventDefault();
      leave();
      return;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      if (!state.selected) return;
      event.preventDefault();
      dropBox(state.selected);
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      undo();
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight" || event.key === "ArrowUp" || event.key === "ArrowDown") {
      if (!state.selected) return;
      event.preventDefault();
      const item = state.boxes.find((entry) => entry.key === state.selected);
      if (!item) return;
      const { w, h } = natural();
      const step = event.shiftKey ? 10 : 1;
      const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
      const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
      const width = item.box[2] - item.box[0];
      const height = item.box[3] - item.box[1];
      const x0 = clamp(item.box[0] + dx, 0, w - width);
      const y0 = clamp(item.box[1] + dy, 0, h - height);
      item.box = [x0, y0, x0 + width, y0 + height];
      commitBoxes();
      renderStage();
    }
  }

  /* ---------------- 选图 / 加载框 ---------------- */

  function normalizeBox(raw) {
    const { w, h } = natural();
    if (!Array.isArray(raw) || raw.length < 4) return [0, 0, 0, 0];
    let [x0, y0, x1, y1] = raw.slice(0, 4).map(Number);
    if (![x0, y0, x1, y1].every(Number.isFinite)) return [0, 0, 0, 0];
    if (x0 > x1) [x0, x1] = [x1, x0];
    if (y0 > y1) [y0, y1] = [y1, y0];
    return [clamp(x0, 0, w), clamp(y0, 0, h), clamp(x1, 0, w), clamp(y1, 0, h)];
  }

  async function loadBoxes(row) {
    const page = await call(() => sdk.listAnnotations(state.projectId, {
      taskId: state.task.id,
      taskImageId: row.id,
      limit: 200,
    }));
    const items = page && Array.isArray(page.items) ? page.items : [];
    state.boxes = items
      .filter((item) => item.annotation_type === "bbox" && item.payload && Array.isArray(item.payload.box))
      .map((item) => ({
        key: `box${++keySeed}`,
        categoryId: item.category_id,
        label: item.label || "未命名",
        box: normalizeBox(item.payload.box),
      }));
    state.selected = null;
    state.history = [];
    state.dirty = false;
    renderStage();
    renderBoxList();
    updateActions();
  }

  async function selectImage(index, { skipConfirm = false } = {}) {
    if (index < 0 || index >= state.rows.length || index === state.index) return;
    if (!skipConfirm && state.dirty && !(await confirmDiscard())) return;
    const token = state.token;
    const row = state.rows[index];
    const image = imageOf(row);
    state.index = index;
    state.selected = null;
    state.boxes = [];
    state.history = [];
    state.dirty = false;
    renderRail();
    renderBoxList();
    updateActions();
    status("#annotateStatus", "正在加载图片…");
    const img = $("#annotateImage");
    img.removeAttribute("src");
    renderStage();
    try {
      const url = await fullUrl(image.id);
      if (token !== state.token) return;
      img.src = url;
      await img.decode().catch(() => {});
      if (token !== state.token) return;
      await loadBoxes(row);
      if (token !== state.token) return;
      const editable = isEditable();
      status("#annotateStatus", editable
        ? (row.annotation_status === "annotated"
          ? `这张图已有 ${state.boxes.length} 个框，可以直接改`
          : "在图上按住拖出矩形；点类目给新框上色")
        : "当前任务状态不允许修改标注");
    } catch (error) {
      if (token === state.token) status("#annotateStatus", error.message, true);
    }
  }

  function confirmDiscard() {
    if (typeof window.confirmDanger !== "function") return Promise.resolve(true);
    return window.confirmDanger({
      kicker: "UNSAVED",
      title: "还有没保存的标注",
      message: "切换图片会丢掉这张图上未保存的框。",
      detail: "可以先点「保存标注」再切换。",
      confirmText: "丢弃并切换",
    });
  }

  /* ---------------- 保存 / 流转 ---------------- */

  function isEditable() {
    const task = state.task;
    if (!task) return false;
    if (!EDITABLE.has(task.status)) return false;
    return task.status !== "queued" || task.mode === "manual";
  }

  function buildItems() {
    return state.boxes.map((item) => {
      const category = categoryOf(item.categoryId);
      const label = (category && (category.zh_name || category.en_name)) || item.label || "未命名";
      const box = item.box.map((value) => Math.round(value * 100) / 100);
      return {
        label,
        category_id: category ? category.id : null,
        annotation_type: "bbox",
        payload: { box },
        confidence: null,
      };
    });
  }

  async function save({ silent = false } = {}) {
    const row = currentRow();
    if (!row || !isEditable()) return false;
    const items = buildItems();
    if (!silent) status("#annotateStatus", "正在保存…");
    try {
      await call(() => sdk.replaceTaskImageAnnotations(
        state.projectId, state.task.id, row.id, items, { source: "manual" }
      ));
      row.annotation_status = "annotated";
      state.dirty = false;
      renderRail();
      renderProgress();
      updateActions();
      if (!silent) {
        status("#annotateStatus", items.length ? `已保存 ${items.length} 个标注框` : "已清空这张图的标注");
      }
      return true;
    } catch (error) {
      status("#annotateStatus", error.message, true);
      return false;
    }
  }

  async function finish() {
    const task = state.task;
    if (!task) return;
    if (state.dirty && !(await save())) return;
    const button = $("#annotateFinish");
    button.disabled = true;
    try {
      if (task.status === "queued" && task.mode === "manual") {
        await call(() => sdk.completeTask(state.projectId, task.id));
      } else if (task.status === "rejected_fix") {
        await call(() => sdk.resubmitTask(state.projectId, task.id));
      } else if (task.status === "rejected_reannotate") {
        await call(() => sdk.requeueTask(state.projectId, task.id));
      } else {
        return;
      }
      window.VSProjects?.refreshTasks?.();
      leave(true);
    } catch (error) {
      status("#annotateStatus", error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  /* ---------------- 按钮状态 ---------------- */

  function updateActions() {
    const editable = isEditable();
    // 没有改动时保存没有意义，按钮保持灰掉
    $("#annotateSave").disabled = !editable || !state.dirty;
    $("#annotateUndo").disabled = !state.history.length;
    $("#annotateFinish").hidden = !editable;
    $("#annotateFinish").textContent = finishLabel();
  }

  function finishLabel() {
    const task = state.task;
    if (!task) return "完成标注";
    if (task.status === "rejected_fix") return "重新提交";
    if (task.status === "rejected_reannotate") return "重新排队";
    return "完成标注";
  }

  /* ---------------- 入口 ---------------- */

  async function open({ projectId, taskId, taskImageId }) {
    const token = ++state.token;
    releaseUrls();
    state.projectId = projectId;
    state.task = null;
    state.rows = [];
    state.index = -1;
    state.categories = [];
    state.activeCategoryId = null;
    state.boxes = [];
    state.selected = null;
    state.history = [];
    state.dirty = false;
    state.drag = null;

    openLayer();
    $("#annotateTitle").textContent = "人工标注";
    $("#annotateProgress").textContent = "";
    $("#annotateRails").replaceChildren();
    $("#annotateCategories").replaceChildren();
    $("#annotateBoxList").replaceChildren();
    $("#annotateImage").removeAttribute("src");
    $("#annotateCanvas").querySelectorAll(".annotate-box").forEach((node) => node.remove());
    $("#annotateSave").disabled = true;
    $("#annotateFinish").hidden = true;
    status("#annotateStatus", "正在加载任务…");

    try {
      const [task, categories] = await Promise.all([
        call(() => sdk.getTask(projectId, taskId)),
        call(() => sdk.listCategories(projectId)).catch(() => []),
      ]);
      if (token !== state.token) return;
      state.task = task;
      state.rows = Array.isArray(task.images) ? task.images.slice() : [];
      state.categories = (Array.isArray(categories) ? categories : []).filter((item) => item.enabled !== false);
      if (state.categories.length) state.activeCategoryId = state.categories[0].id;
      $("#annotateTitle").textContent = task.name || `任务 #${task.id}`;
      renderRail();
      renderProgress();
      renderCategories();
      renderBoxList();
      updateActions();
      if (!state.rows.length) {
        status("#annotateStatus", "这个任务没有图片，无法标注", true);
        return;
      }
      // 从看图页带着 taskImageId 进来就直接定位那一张；
      // 否则优先落在第一张还没标的图上，返修任务则从头开始过一遍
      const picked = taskImageId
        ? state.rows.findIndex((row) => row.id === Number(taskImageId))
        : -1;
      const first = picked >= 0 ? picked : state.rows.findIndex((row) => row.annotation_status !== "annotated");
      await selectImage(first < 0 ? 0 : first, { skipConfirm: true });
    } catch (error) {
      if (token === state.token) status("#annotateStatus", error.message, true);
    }
  }

  async function leave(immediate = false) {
    if (!immediate && state.dirty && !(await confirmDiscard())) return;
    state.token += 1;
    state.drag = null;
    closeLayer(immediate);
    releaseUrls();
    state.task = null;
    state.rows = [];
    state.index = -1;
    state.boxes = [];
    state.dirty = false;
  }

  /* ---------------- 绑定 ---------------- */

  function bind() {
    const canvas = $("#annotateCanvas");
    if (!canvas) return;
    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);
    document.addEventListener("keydown", onKeyDown);
    $("#annotateBack").addEventListener("click", () => leave());
    $("#annotateSave").addEventListener("click", () => save());
    $("#annotateUndo").addEventListener("click", undo);
    $("#annotateFinish").addEventListener("click", finish);
  }

  window.VSAnnotate = { open, close: leave, save };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bind, { once: true });
  else bind();
})();
