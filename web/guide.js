// 灵视标注平台 — 新手引导
// 每个账号只在第一次进入时走一遍：绿色描边框住要讲的元素，旁边跟一张说明卡。
// 不依赖任何框架，也不改页面本身的结构；引导结束只写一个 localStorage 标记。
(() => {
  const STORAGE_PREFIX = "vision-studio.guide.";

  const STEPS = [
    {
      target: ".utility-rail",
      title: "四个空间都在左边",
      text: "个人空间放你自己的图；小组和企业空间是多人共用的，权限由空间管理员分配。",
      placement: "right",
    },
    {
      target: "#railSettings",
      title: "设置里换外观",
      text: "主题、跟随系统深浅色、上传偏好、账号安全都在这里，随时能改。",
      placement: "right",
      pad: 6,
    },
    {
      target: ".personal-summary",
      title: "从「上传图片」开始",
      text: "选好图之后会让你填识别目标，模型按这些目标出框；也可以选人工标注自己画。",
      placement: "bottom",
    },
    {
      target: "#personalTasksSection",
      title: "任务和结果在这里",
      text: "模型分析完的任务会出现在「我的任务」，点开就能逐张复核、改框、通过或退回。",
      placement: "bottom",
      optional: true,
    },
  ];

  function key(accountId) {
    return `${STORAGE_PREFIX}${accountId || "anon"}`;
  }

  function done(accountId) {
    try { return localStorage.getItem(key(accountId)) === "1"; } catch { return true; }
  }

  function markDone(accountId) {
    try { localStorage.setItem(key(accountId), "1"); } catch { /* 无存储权限时每次都会提示，可接受 */ }
  }

  function build() {
    const layer = document.createElement("div");
    layer.className = "guide-layer";

    const ring = document.createElement("div");
    ring.className = "guide-ring";
    ring.setAttribute("aria-hidden", "true");

    const card = document.createElement("div");
    card.className = "guide-card";
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-live", "polite");

    const stepLabel = document.createElement("small");
    stepLabel.className = "guide-step";
    const title = document.createElement("strong");
    title.className = "guide-title";
    const text = document.createElement("p");
    text.className = "guide-text";
    const actions = document.createElement("div");
    actions.className = "guide-actions";
    const skip = document.createElement("button");
    skip.type = "button";
    skip.className = "text-button";
    skip.textContent = "跳过";
    const next = document.createElement("button");
    next.type = "button";
    next.className = "primary-button guide-next";
    next.textContent = "下一步";
    actions.append(skip, next);
    card.append(stepLabel, title, text, actions);

    layer.append(ring, card);
    document.body.append(layer);
    return { layer, ring, card, stepLabel, title, text, skip, next };
  }

  function place(node, target, placement, pad) {
    const rect = target.getBoundingClientRect();
    const gap = 14;
    // 框线要留在视口里：侧栏贴着左边、底部按钮贴着底边，直接用 rect ± pad 会有一半画到屏幕外
    const left = Math.max(4, rect.left - pad);
    const top = Math.max(4, rect.top - pad);
    const right = Math.min(window.innerWidth - 4, rect.right + pad);
    const bottom = Math.min(window.innerHeight - 4, rect.bottom + pad);
    const box = { left, top, width: right - left, height: bottom - top };
    node.ring.style.left = `${box.left}px`;
    node.ring.style.top = `${box.top}px`;
    node.ring.style.width = `${box.width}px`;
    node.ring.style.height = `${box.height}px`;

    // 先摆到视口里再量自身尺寸，避免读不到高度
    node.card.style.left = "0px";
    node.card.style.top = "0px";
    const size = node.card.getBoundingClientRect();
    let cardLeft = box.left;
    let cardTop = box.top + box.height + gap;
    if (placement === "right") {
      cardLeft = box.left + box.width + gap;
      cardTop = box.top;
    } else if (placement === "top") {
      cardTop = box.top - size.height - gap;
    }
    // 贴边时收进视口
    cardLeft = Math.max(16, Math.min(cardLeft, window.innerWidth - size.width - 16));
    cardTop = Math.max(16, Math.min(cardTop, window.innerHeight - size.height - 16));
    node.card.style.left = `${cardLeft}px`;
    node.card.style.top = `${cardTop}px`;
  }

  function start(accountId) {
    const visible = STEPS.map((step) => ({ ...step, node: document.querySelector(step.target) }))
      .filter((step) => step.node && step.node.getBoundingClientRect().width > 0);
    if (!visible.length) return;

    const ui = build();
    let index = 0;

    const finish = () => {
      ui.layer.remove();
      markDone(accountId);
      window.removeEventListener("resize", onResize);
    };

    const onResize = () => {
      const step = visible[index];
      if (step) place(ui, step.node, step.placement, step.pad ?? 10);
    };

    const show = () => {
      const step = visible[index];
      if (!step) return finish();
      ui.stepLabel.textContent = `第 ${index + 1} / ${visible.length} 步`;
      ui.title.textContent = step.title;
      ui.text.textContent = step.text;
      ui.next.textContent = index === visible.length - 1 ? "开始使用" : "下一步";
      step.node.scrollIntoView({ block: "nearest", behavior: "smooth" });
      window.setTimeout(() => place(ui, step.node, step.placement, step.pad ?? 10), 260);
    };

    ui.next.addEventListener("click", () => {
      index += 1;
      if (index >= visible.length) finish();
      else show();
    });
    ui.skip.addEventListener("click", finish);
    window.addEventListener("resize", onResize);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") finish();
    }, { once: true });

    show();
  }

  /** app.js 拿到当前账号后调用；同一个账号只会出现一次。 */
  function maybeStart(account) {
    const id = account && account.id;
    if (!id || done(id)) return;
    // 等首屏把统计和列表画完再引，否则框会框在空位置上
    window.setTimeout(() => {
      if (document.body.classList.contains("is-leaving")) return;
      start(id);
    }, 1400);
  }

  window.VSGuide = { start, maybeStart, reset: (id) => { try { localStorage.removeItem(key(id)); } catch { /* ignore */ } } };
})();
