/* Optional wide view for pseudocode. Without dialog support, the inline code remains scrollable. */
(() => {
  if (typeof HTMLDialogElement === "undefined" || !HTMLDialogElement.prototype.showModal) return;

  const active = new Set();
  const waiting = new Map();
  const selector = "details.kernel-drill";
  let dialog;
  let title;
  let enlarged;
  let opener;

  function update(pre) {
    const heading = pre.closest(".card-program").querySelector(":scope > dt");
    if (!heading) return;
    const button = heading.querySelector(".kernel-code-zoom");
    if (pre.clientWidth && pre.scrollWidth > pre.clientWidth + 1) {
      if (!button) {
        const zoom = document.createElement("button");
        zoom.type = "button";
        zoom.className = "kernel-code-zoom";
        zoom.textContent = "放大代码";
        zoom.setAttribute("aria-haspopup", "dialog");
        heading.append(zoom);
      }
    } else if (button && button !== opener) {
      const summary = button.closest(selector)?.querySelector(":scope > summary");
      const focused = document.activeElement === button;
      button.remove();
      if (focused) summary?.focus({preventScroll: true});
    }
  }

  const resizer = window.ResizeObserver && new ResizeObserver(entries => {
    for (const {target} of entries) if (active.has(target)) update(target);
  });

  function stopWaiting(call) {
    waiting.get(call)?.disconnect();
    waiting.delete(call);
  }

  function track(call) {
    if (call.closest("details:not([open])")) return;
    const pre = call.querySelector(":scope > .kernel-card > .card-program pre.kernel-pseudocode");
    if (pre) {
      stopWaiting(call);
      if (!active.has(pre)) {
        active.add(pre);
        resizer?.observe(pre);
      }
      update(pre);
    } else if (!waiting.has(call) && window.MutationObserver) {
      // The compact article replaces a direct placeholder with the downloaded card.
      const observer = new MutationObserver(() => track(call));
      observer.observe(call, {childList: true});
      waiting.set(call, observer);
    }
  }

  document.addEventListener("toggle", event => {
    const call = event.target;
    if (!(call instanceof HTMLDetailsElement)) return;
    if (call.open) {
      if (call.matches(selector)) track(call);
      call.querySelectorAll(selector + "[open]").forEach(track);
    } else {
      for (const pre of active) {
        if (call.contains(pre)) {
          resizer?.unobserve(pre);
          active.delete(pre);
        }
      }
      for (const pending of waiting.keys()) if (call.contains(pending)) stopWaiting(pending);
    }
  }, true);

  function createDialog() {
    dialog = document.createElement("dialog");
    dialog.className = "kernel-code-reader";
    title = document.createElement("h2");
    title.id = "kernel-code-reader-title";
    dialog.setAttribute("aria-labelledby", title.id);

    const header = document.createElement("div");
    header.className = "kernel-code-reader-header";
    const close = document.createElement("button");
    close.type = "button";
    close.className = "kernel-code-close";
    close.textContent = "关闭";
    close.autofocus = true;
    header.append(title, close);

    const pre = document.createElement("pre");
    pre.tabIndex = 0;
    pre.setAttribute("aria-label", "伪代码");
    enlarged = document.createElement("code");
    pre.append(enlarged);
    dialog.append(header, pre);
    document.body.append(dialog);
    dialog.addEventListener("close", () => {
      const previous = opener;
      opener = null;
      if (previous?.isConnected) {
        previous.focus({preventScroll: true});
        const pre = previous.closest(".card-program").querySelector("pre.kernel-pseudocode");
        if (pre) update(pre); // The viewport may have changed while reading.
      }
    });
  }

  document.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest("button");
    if (!button) return;
    if (button.matches(".kernel-code-close") && dialog?.contains(button)) {
      dialog.close();
      return;
    }
    if (!button.matches(".kernel-code-zoom")) return;
    const code = button.closest(".card-program")?.querySelector("pre.kernel-pseudocode > code");
    const name = button.closest(selector)?.querySelector(":scope > summary code");
    if (!code || !name) return;
    if (!dialog) createDialog();
    title.textContent = name.textContent.trim() + " · 伪代码";
    enlarged.textContent = code.textContent;
    opener = button;
    dialog.showModal();
    enlarged.parentElement.scrollTo(0, 0);
  });

  if (!resizer) window.addEventListener("resize", () => active.forEach(update));
  function start() {
    document.querySelectorAll(selector + "[open]").forEach(track);
    if (document.fonts) document.fonts.ready.then(() => active.forEach(update));
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, {once: true});
  else start();
})();
