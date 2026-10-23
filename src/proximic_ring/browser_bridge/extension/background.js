/* Native messaging is the only transport; web pages cannot access the broker. */
if (typeof importScripts === "function") importScripts("config.js", "core.js");
const api = globalThis.browser || chrome;
const safari = globalThis.MYTHLINK_BROWSER === "safari";
const browserName = safari ? "safari" : "chrome";
const nativeName = "com.proximic.voice.browser";
const session = crypto.randomUUID();
let token = "", port = null, nativePending = null, timer = null, busy = false;
let commandWait = false;
let status = {connected: false, authorized: false, reason: "desktop_unavailable"};
function nativeCall(data) {
  if (safari) return api.runtime.sendNativeMessage(nativeName, data);
  if (!port) {
    port = api.runtime.connectNative(nativeName);
    port.onMessage.addListener(value => { const pending = nativePending; nativePending = null; pending?.resolve(value); });
    port.onDisconnect.addListener(() => {
      void api.runtime.lastError;
      port = null; token = "";
      const pending = nativePending; nativePending = null;
      pending?.reject(new Error("desktop_unavailable"));
    });
  }
  return new Promise((resolve, reject) => {
    const deadline = setTimeout(() => { if (nativePending) { nativePending = null; port?.disconnect(); port = null; reject(new Error("native_timeout")); } }, 2000);
    nativePending = {resolve: v => { clearTimeout(deadline); resolve(v); }, reject: e => { clearTimeout(deadline); reject(e); }};
    port.postMessage(data);
  });
}
async function active() {
  const window = await api.windows.getLastFocused();
  const [tab] = await api.tabs.query({active: true, windowId: window.id});
  return {window, tab};
}
async function collect() {
  let window, tab;
  try { ({window, tab} = await active()); }
  catch (_) { return unavailableSnapshot(); }
  const base = {...unavailableSnapshot(), window: window.id ?? -1, tab: tab?.id ?? -1,
    focused: !!window.focused};
  return collectPage(base, tab);
}
function unavailableSnapshot() {
  return {session, window: -1, tab: -1, frame: -1,
    page: "", document: "", media: "", source: "", scene: "", playing: false,
    focused: false, authorized: false, input_context: "unknown", reason: "browser_page_unavailable"};
}
async function collectPage(base, tab) {
  if (!tab || !/^https?:/.test(tab.url || "")) return base;
  // WebExtension match patterns match hosts across ports. Safari rejects a
  // URL origin with an explicit port (Chrome happens to accept it).
  const url = new URL(tab.url);
  base.authorized = await api.permissions.contains({origins: [url.protocol + "//" + url.hostname + "/*"]});
  if (!base.authorized) return {...base, reason: "browser_permission_required"};
  let frames;
  try { frames = await api.webNavigation.getAllFrames({tabId: tab.id}); } catch (_) { return base; }
  const results = await Promise.all((frames || []).map(async frame => {
    try {
      const data = await api.tabs.sendMessage(tab.id, {type: "probe"}, {frameId: frame.frameId});
      return data && {...data, frame: frame.frameId};
    } catch (_) { return null; }
  }));
  const top = results.find(r => r?.frame === 0);
  if (!top) return {...base, reason: tab.status === "loading" ? "page_loading" : "browser_page_unavailable"};
  base.page = top.document + ":" + top.page;
  const focused = results.filter(r => r?.focused && r.input_context !== "unknown");
  base.input_context = focused.some(r => r.input_context === "text") ? "text" : focused.length ? "nontext" : "unknown";
  // Include each frame's route identity too: an iframe can navigate with
  // history.pushState without changing the top document or media element.
  const candidates = results.filter(Boolean).flatMap(r => r.media.map(m => ({...m, frame: r.frame, document: r.document + ":" + r.page, framePage: r.page})));
  const chosen = MythlinkMedia.select(candidates);
  if (!chosen.player) return {...base, reason: results.some(r => !r || r.partial) ? "browser_page_unavailable" : chosen.reason};
  const m = chosen.player;
  return {...base, frame: m.frame, document: m.document, media: m.media, source: m.source,
    scene: m.kind === "video" ? "video" : "music", playing: m.playing, reason: "recognized", framePage: m.framePage};
}
async function execute(command) {
  if (Date.now() >= command.expires) return {id: command.id, ok: false, reason: "browser_command_expired"};
  const snapshot = await collect();
  if (command.operation === "snapshot") return {id: command.id, snapshot};
  if (!snapshot.focused) return {id: command.id, ok: false, reason: "browser_window_unfocused"};
  if (snapshot.input_context !== "nontext") return {id: command.id, ok: false, reason: "text_focus"};
  if (!MythlinkMedia.same(snapshot, command.target)) return {id: command.id, ok: false, reason: "page_changed"};
  // Recheck the active tab after probing all frames, then validate the element
  // and source again inside that frame immediately before the DOM operation.
  const {window, tab} = await active();
  if (!window.focused || window.id !== snapshot.window || tab?.id !== snapshot.tab) return {id: command.id, ok: false, reason: "page_changed"};
  const result = await api.tabs.sendMessage(snapshot.tab, {type: "action", target: snapshot,
    framePage: snapshot.framePage, action: command.action, expires: command.expires}, {frameId: snapshot.frame});
  return {id: command.id, ...result};
}
async function tick() {
  if (busy) return;
  busy = true;
  let delay = 300;
  try {
    if (!token) {
      const response = await nativeCall({path: "/connect", version: 1, browser: browserName, session});
      if (!response?.token) throw new Error("desktop_unavailable");
      token = response.token;
      commandWait = response.command_wait === true;
    }
    // Page API failure does not break native connectivity. Keep an empty,
    // unauthorized snapshot so no stale target can receive a media action.
    let snapshot;
    try { snapshot = await collect(); } catch (_) { snapshot = unavailableSnapshot(); }
    const wait_ms = commandWait ? (snapshot.focused ? 300 : 1000) : 0;
    let response = await nativeCall({path: "/exchange", version: 1, token, snapshot, wait_ms});
    if (response?.error) throw new Error(response.error);
    status = {connected: true, authorized: snapshot.authorized, reason: snapshot.reason, scene: snapshot.scene};
    // The native broker holds this exchange until a command arrives or the
    // heartbeat expires. There is no uninterruptible client-side poll delay.
    delay = commandWait ? 0 : (snapshot.focused ? 300 : 1000);
    // Commands are acknowledged immediately, not at the next heartbeat.
    while (response?.commands?.length) {
      const replies = [];
      for (const command of response.commands) {
        try { replies.push(await execute(command)); }
        catch (_) { replies.push({id: command.id, ok: false, reason: "browser_page_unavailable"}); }
      }
      response = await nativeCall({path: "/exchange", version: 1, token, replies, wait_ms: commandWait ? 300 : 0});
    }
  } catch (_) { token = ""; status = {...status, connected: false, reason: "desktop_unavailable"}; delay = 1500; }
  finally { busy = false; clearTimeout(timer); timer = setTimeout(tick, delay); }
}
async function injectOpenTabs() {
  for (const tab of await api.tabs.query({})) {
    if (!/^https?:/.test(tab.url || "")) continue;
    try {
      if (api.scripting) await api.scripting.executeScript({target: {tabId: tab.id, allFrames: true}, files: ["content.js"]});
      else await api.tabs.executeScript(tab.id, {file: "content.js", allFrames: true, runAt: "document_start"});
    } catch (_) { /* The popup explains missing site access and protected pages. */ }
  }
}
api.runtime.onInstalled.addListener(() => { injectOpenTabs(); tick(); });
api.permissions.onAdded.addListener(() => { injectOpenTabs(); tick(); });
api.tabs.onActivated.addListener(() => tick());
api.windows.onFocusChanged.addListener(() => tick());
api.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id === api.runtime.id && message?.type === "status") { reply(status); return false; }
});
tick();
