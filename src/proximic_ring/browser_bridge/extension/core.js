/* Shared, deterministic player selection. No site names or button labels. */
(() => {
  const keys = ["session", "window", "tab", "page", "frame", "document", "media", "source"];
  function same(a, b) { return !!a && !!b && keys.every(k => a[k] === b[k]); }
  function select(candidates) {
    const eligible = candidates.filter(m => m.loaded && (m.visible || m.playing || m.kind === "audio"));
    if (!eligible.length) return {reason: "no_web_player"};
    const rank = m => [m.pip || m.fullscreen ? 1 : 0, m.interacted || 0, m.playing ? 1 : 0, m.area || 0];
    eligible.sort((a, b) => {
      const x = rank(a), y = rank(b);
      for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return y[i] - x[i];
      return 0;
    });
    const [first, second] = eligible;
    // Several equally plausible players: require a click on the intended one.
    if (second && rank(first).every((v, i) => v === rank(second)[i])) return {reason: "multiple_players"};
    return {reason: "recognized", player: first};
  }
  globalThis.MythlinkMedia = {same, select};
})();
