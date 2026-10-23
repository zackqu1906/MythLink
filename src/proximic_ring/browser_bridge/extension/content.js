/* Runs in the extension's isolated world, including permitted child frames. */
(() => {
  if (globalThis.__mythlinkMedia) return;
  globalThis.__mythlinkMedia = true;
  const api = globalThis.browser || chrome;
  const documentID = crypto.randomUUID();
  let route = location.href, page = crypto.randomUUID();
  const ids = new WeakMap(), tracked = new Set(), roots = new Set([document]);
  let lastDiscover = 0, partial = false;
  function remember(media) {
    if (!ids.has(media)) {
      ids.set(media, {id: crypto.randomUUID(), url: null, source: "", interacted: 0});
      media.addEventListener("emptied", () => { ids.get(media).source = crypto.randomUUID(); });
    }
    tracked.add(media);
  }
  function discover() {
    // Open shadow roots may be attached after the host was inserted.
    if (Date.now() - lastDiscover < 2000) return;
    lastDiscover = Date.now();
    partial = false;
    let count = 0;
    for (const root of roots) {
      if (root !== document && !root.host.isConnected) { roots.delete(root); continue; }
      root.querySelectorAll("video,audio").forEach(remember);
      for (const el of root.querySelectorAll("*")) {
        if (++count > 20000) { partial = true; break; }
        if (el.shadowRoot) roots.add(el.shadowRoot);
      }
      if (partial) break;
    }
  }
  new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node.nodeType !== 1) continue;
      if (node.matches("video,audio")) remember(node);
      node.querySelectorAll("video,audio").forEach(remember);
    }
    for (const media of tracked) if (!media.isConnected) tracked.delete(media);
  }).observe(document, {childList: true, subtree: true});
  document.addEventListener("pointerdown", event => {
    const path = event.composedPath();
    let media = path.find(el => el instanceof HTMLMediaElement);
    if (!media) {
      // A player's own controls may be siblings of its media element.
      for (const el of path.slice(0, 3)) {
        if (!el.querySelectorAll || el === document.body) break;
        const found = el.querySelectorAll("video,audio");
        if (found.length === 1) { media = found[0]; break; }
      }
    }
    if (media) { remember(media); ids.get(media).interacted = Date.now(); }
  }, true);
  function context() {
    let el = document.activeElement;
    while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement;
    const editable = el?.isContentEditable || el?.matches("textarea,input:not([type=button]):not([type=submit]):not([type=range]):not([type=checkbox]):not([type=radio]),[role=textbox],[role=searchbox],[role=combobox]");
    if (!editable && el?.localName?.includes("-") && !el.shadowRoot) return "unknown";
    return editable ? "text" : document.hasFocus() && !el?.matches("iframe,frame") ? "nontext" : "unknown";
  }
  function probe() {
    if (route !== location.href) { route = location.href; page = crypto.randomUUID(); }
    discover();
    const media = [];
    for (const el of tracked) {
      if (!el.isConnected) { tracked.delete(el); continue; }
      const meta = ids.get(el), url = el.currentSrc || el.src || "";
      // Object identity matters for srcObject streams; URLs never leave this script.
      const src = el.srcObject || url;
      if (meta.url !== src) { meta.url = src; meta.source = crypto.randomUUID(); }
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      const visible = style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0 && rect.width > 8 && rect.height > 8;
      media.push({media: meta.id, source: meta.source, kind: el.localName,
        loaded: !!src && el.readyState >= 1, playing: !el.paused && !el.ended,
        visible, area: visible ? Math.round(rect.width * rect.height) : 0,
        interacted: meta.interacted, pip: document.pictureInPictureElement === el,
        fullscreen: !!document.fullscreenElement && (document.fullscreenElement === el || document.fullscreenElement.contains(el))});
    }
    return {document: documentID, page, input_context: context(), focused: document.hasFocus(), media, partial};
  }
  async function action(message) {
    const current = probe(), t = message.target;
    if (Date.now() >= message.expires) return {ok: false, reason: "browser_command_expired"};
    if (current.document + ":" + current.page !== t.document || current.page !== message.framePage) return {ok: false, reason: "page_changed"};
    if (context() === "text") return {ok: false, reason: "text_focus"};
    const el = [...tracked].find(e => ids.get(e).id === t.media);
    if (!el || ids.get(el).source !== t.source || !el.isConnected) return {ok: false, reason: "page_changed"};
    try {
      switch (message.action) {
        case "play": if (el.paused || el.ended) await el.play(); else el.pause(); break;
        case "backward": case "forward": {
          if (!el.seekable.length) throw new Error("not_seekable");
          const value = el.currentTime + (message.action === "forward" ? 10 : -10);
          const ranges = Array.from({length: el.seekable.length}, (_, i) => [el.seekable.start(i), el.seekable.end(i)]);
          const range = ranges.find(([a, b]) => el.currentTime >= a && el.currentTime <= b) || ranges[ranges.length - 1];
          el.currentTime = Math.max(range[0], Math.min(range[1], value));
          break;
        }
        case "volume-up": el.volume = Math.min(1, el.volume + .1); el.muted = false; break;
        case "volume-down": el.volume = Math.max(0, el.volume - .1); break;
        default: throw new Error("unsupported_action");
      }
      return {ok: true};
    } catch (error) {
      return {ok: false, reason: message.action === "play" && error?.name === "NotAllowedError"
        ? "browser_playback_permission_required" : "browser_action_failed"};
    }
  }
  api.runtime.onMessage.addListener((message, sender, reply) => {
    if (sender.id !== api.runtime.id) return;
    if (message?.type === "probe") { reply(probe()); return false; }
    if (message?.type === "action") { action(message).then(reply, () => reply({ok: false, reason: "browser_action_failed"})); return true; }
  });
})();
