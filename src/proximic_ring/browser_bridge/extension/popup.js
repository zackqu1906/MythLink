const api = globalThis.browser || chrome;
async function refresh() {
  const state = await api.runtime.sendMessage({type: "status"});
  document.getElementById("status").textContent = !state?.connected ? "请打开 Mythlink，并在设置中完成浏览器连接。" :
    !state.authorized ? "已连接 Mythlink，请允许当前网站访问。" :
    state.reason === "recognized" ? `已识别${state.scene === "music" ? "音频" : "视频"} · 使用 Mythlink「场景与手势」中的对应配置。` :
    state.reason === "multiple_players" ? "已连接 · 请先点击要控制的播放器。" : "已连接 · 等待可访问的视频或音频。";
  document.getElementById("authorize").hidden = !!state?.authorized;
}
document.getElementById("authorize").onclick = async () => {
  try {
    const ok = await api.permissions.request({origins: ["http://*/*", "https://*/*"]});
    document.getElementById("help").textContent = ok ? "授权完成，请刷新要使用的页面。Safari 如仍未识别，请在扩展设置中将网站访问设为允许。" : "尚未授权。可在浏览器的扩展设置中允许网站访问。";
  } catch (_) { document.getElementById("help").textContent = "请在 Safari → 设置 → 扩展 → Mythlink 浏览器助手中允许网站访问。"; }
  refresh();
};
refresh();
