# Third-party notices

Mythlink integrates the following third-party components. Most downloaded ASR/LLM weights remain outside Git; the provider-supplied touchpad asset below is bundled for offline SDK use.

## Node.js

- Source: https://nodejs.org/ and https://github.com/nodejs/node
- macOS bundles include Node.js 22.23.4 for the Agent background service, targeting macOS 11 or later.
- The official archive URL and SHA-256 are pinned in `scripts/install-node-macos.sh`.
- License: MIT and bundled dependency notices; the full upstream `LICENSE` is shipped alongside `node/bin/node`.

## llama.cpp

- Source: https://github.com/ggml-org/llama.cpp
- Installed location: `.runtime/local-llm/runtimes/` (downloaded during Windows setup)
- License: MIT
- The pinned release URL and SHA-256 are recorded in `local_llm_catalog.json`.
- macOS bundles include a build of b10516 targeting macOS 13.0, with CPU and Metal inference.
  Its source URL/hash and build options are pinned in `scripts/build-llama-macos.sh`;
  the full MIT license is shipped in `llama-cpp-b10516-macos-arm64/LICENSE`.

## Qwen3-4B-Instruct-2507-GGUF

- Base model: https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507
- GGUF quantization: https://huggingface.co/bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF
- Installed location: `.runtime/local-llm/models/` (downloaded during Windows setup)
- Model license: Apache License 2.0
- Model weights are not committed to Git; the pinned download URL and SHA-256 are recorded in
  `local_llm_catalog.json`.

## streaming-sensevoice

- Source: https://github.com/pengzhendong/streaming-sensevoice
- Location: `third_party/streaming-sensevoice` (vendored source snapshot)
- License: Apache License 2.0 (see the submodule's `LICENSE` file)

## Fun-ASR

- Source: https://github.com/QwenAudio/Fun-ASR
- Location: `third_party/Fun-ASR` (vendored source snapshot, model weights excluded)
- Source-code license: Apache License 2.0 (see the submodule's `LICENSE` file)
- Model weights: distributed separately; review the license shown on the selected ModelScope or
  Hugging Face model card before redistribution.

## Ringo Python SDK snapshot

- Location: `src/ring_python_sdk`
- Origin: supplied with the Ringo device integration used by this project.
- Public gesture, ADPCM audio and quaternion wire protocol ported from the supplied
  `ringo-sdk-1.4.0-public-20260924` Android SDK. The application receives
  firmware-confirmed gestures; no host gesture model or weights are distributed.
- License: no standalone license file was found in the supplied snapshot. Redistribution permission
  must be confirmed with the SDK/device provider before this repository is made public.

## Ring macOS SDK 0.4.0

- Location: `src/ring_mac_sdk` (all 23 package files copied unchanged from the supplied wheel).
- Original wheel, hashes, examples and documentation: [0.4.0 delivery](third_party/ring-mac-sdk-0.4.0-macos-arm64/).
- The [0.3.0 delivery](third_party/ringo-touchpad-delivery-20261105/) retains the earlier firmware, calibration utility and original notices.
- The encrypted receiver asset and native library are supplied runtime components;
  no ring frontend source or standalone frontend weights are included.
- The native library includes Monocypher; its upstream notice is preserved in
  `src/ring_mac_sdk/licenses/monocypher.md`.
- See the provider's [third-party notices](third_party/ring-mac-sdk-0.4.0-macos-arm64/THIRD_PARTY_NOTICES.md) for
  third-party dependencies and the permission boundary for SDK/model redistribution.
- Apple Silicon Mac touchpad control uses this receiver through the existing
  `ring_python_sdk` BLE session; see `docs/RING_MAC_SDK.md`.

Third-party names and trademarks belong to their respective owners.

## Wails macOS window focus policy

- Source: https://github.com/wailsapp/wails/blob/5b8e87bd45c1b87d6f2cfb7c0e5c3176eda85a2a/v3/pkg/application/webview_window_darwin.go
- The `windowFocus` policy is adapted to PyObjC/Qt in
  `src/proximic_ring/platforms/macos/mac_window_focus.py`: nonactivating panels
  are ordered and keyed independently; explicit normal-window focus activates
  the application before making that window key.
- Copyright (c) 2018-Present Lea Anthony, MIT. The full license is retained in
  the adapted module and included in the packaged Python code.

## Agent message rendering

The Agent completion icon in `src/proximic_ring/ui/assets/agent-completed.svg`
uses [Lucide circle-check](https://lucide.dev/icons/circle-check), recolored for
the interface. It is licensed under ISC; the copyright and full license text
are embedded in the SVG.

Local static assets in `src/proximic_ring/agent_workflow/ui/rich_text/vendor`:

- Marked 18.1.1 — https://github.com/markedjs/marked — MIT (`marked-LICENSE`).
- DOMPurify 3.4.17 — https://github.com/cure53/DOMPurify — MPL-2.0 OR Apache-2.0 (`dompurify-LICENSE`).
- KaTeX 0.19.0 — https://github.com/KaTeX/KaTeX — MIT (`katex-LICENSE`); WOFF2 fonts distributed with that release.
- Mermaid 12.2.0 — https://github.com/mermaid-js/mermaid — MIT (`mermaid-LICENSE`).
- highlight.js CDN assets 11.12.0 — https://github.com/highlightjs/cdn-release — BSD-3-Clause (`cdn-assets-LICENSE`).

`vendor/manifest.json` records the official npm tarball URLs, versions,
integrity checks and the SHA-256 of each copied file. Library files are unmodified.
Rendering does not fetch CDN scripts at runtime. Qt WebEngine / Qt WebChannel
are supplied by the existing PySide6 distribution and retain its Qt/Chromium licenses.

## Source Han Sans SC

- Source: https://github.com/adobe-fonts/source-han-sans/tree/2.005R
- Files: `src/proximic_ring/ui/assets/fonts/SourceHanSansSC-Regular.otf` and `SourceHanSansSC-Medium.otf`
- Copyright 2014–2025 Adobe. Unmodified release 2.005R fonts, used by the Figma history design.
- License: SIL Open Font License 1.1, bundled as `src/proximic_ring/ui/assets/fonts/LICENSE.txt`.

## Stroke input data

- The offline stroke dictionary in `src/proximic_ring/assets/strokes.json.gz` is imported
  unchanged from ProximicVoice `yyf` commit `741517b`. That branch generated it with
  `tools/build_stroke_assets.py` from the local `ring-stroke-ime-demo` prototype. It combines
  Rime stroke codes (`rime/rime-stroke`, commit `1e8fff9`, LGPL-3.0) and Conway stroke codes
  (`stroke-input/stroke-input-data`, commit `d66ba5f`, CC BY 4.0). Conway's simplified
  character ranking is public domain. Attribution and source details are retained in
  the prototype's `THIRD_PARTY.md`.
- Runtime ordinary matching filters this original table to retain reference orders,
  permutations of their five-category strokes, and verified grass-component orders.
  `assets/stroke-order-components.json` records grass positions derived from the
  same cached Make Me a Hanzi decomposition data already used for left-dot annotations;
  its source URL and SHA-256 are embedded. Rebuild with
  `tools/build_stroke_order_components.py`. Original source records remain intact.
- The 32-point handwriting templates are derived from the prototype's local P01 mouse
  recordings. They are a baseline for Ring testing, not a trained Ring-specific model.

## Stroke glyph outlines and order

- `assets/stroke-glyphs.json.gz` contains 9,574 offline Kaishu glyphs from
  [hanzi-writer-data 2.0.1](https://github.com/chanind/hanzi-writer-data), the
  data used by Hanzi Writer (the renderer underlying cnchar-draw). Original
  outlines derive from Make Me a Hanzi and Arphic PL KaitiM GB / PL UKai.
  The outlines are unchanged; medians and radical metadata are omitted.
- Outline license: Arphic Public License, included verbatim in
  `assets/stroke-glyphs-arphic-license.txt`. This data has a separate license
  from the Hanzi Writer and cnchar application code.
- Five-category order labels are derived from cnchar-order 3.2.6 (MIT).
  Its license is included in `assets/stroke-left-dots-cnchar-license.txt`.
  Labels with a different stroke count are omitted. Source archive URLs and
  SHA-256 checksums are retained inside the asset.
- Rebuild with `tools/build_stroke_glyphs.py`; the application renders these
  local paths and never fetches glyphs at runtime. No cnchar or Hanzi Writer
  JavaScript runtime is embedded.

## Stroke fold orders

`assets/stroke-folds.json.gz` retains fine stroke order letters from cnchar-order
3.2.6 (MIT, https://github.com/theajack/cnchar), restricted to canonical orders
present in the active five-category dictionary. Shared letter groups are kept
intact. The source archive URL and SHA-256 are embedded in the asset. The license
is included in `assets/stroke-left-dots-cnchar-license.txt`. Rebuild offline with
`tools/build_fold_annotations.py`; no downloaded JavaScript is executed.

## Stroke context data

The local next-character model `assets/stroke-context.json.gz` reuses the
existing demo's Tatoeba Mandarin counts (CC BY 2.0 FR) and Chinese Wikipedia
counts (CC BY-SA 4.0). Contributor names, article links/revisions and source
checksums are retained in `assets/stroke-context-sources.json.gz`; packaging
does not change those counts. Original processing and attribution are described
in `ring-stroke-ime-demo/THIRD_PARTY.md`. The generator is retained on the source `yyf`
branch as `tools/build_stroke_context.py`; this integration includes the generated data.

## Ringo touchpad model and MNN

- SDK location: `src/ring_python_sdk/touchpad`; model: `assets/touchpad_model.mnn`.
- Original model from the supplied Android SDK 1.4.0 public AAR, unmodified. This
  host touchpad model predicts cursor motion; it is separate from firmware gesture recognition.
- Asset SHA-256 and provenance: `touchpad/assets/provenance.json`.
- MNN runtime: 3.6.1 (Apache-2.0); original bundled notice in `assets/LICENSE-MNN.txt`.
- The MNN license does not grant a license to the ring provider's model. No separate
  model redistribution license was found in the supplied SDK; the existing provider
  permission requirement above also applies to this asset.

## BGE-M3 (optional Workflow retrieval model)

BAAI/bge-m3, MIT license, https://huggingface.co/BAAI/bge-m3 . Model weights are downloaded only on explicit request, pinned to revision `5617a9f61b028005a4858fdac845db406aefb181`. The app bundles the download manifest, not the 2.3 GB weights. Dense encoding follows the model's CLS pooling and L2 normalization configuration.

## Remodex desktop IPC adapter

The desktop owner adapter in `src/proximic_ring/agent_workflow/agent/desktop_owner/`
is derived from [Emanuele-web04/remodex](https://github.com/Emanuele-web04/remodex),
commit `f67b1499dbd8ace7a23902fad3242cc88f7e9a9e`, licensed under Apache-2.0.
The license and a description of Mythlink modifications are included alongside the files.

## GLiNER2.5 multilingual Decide (optional local voice router)

The automatic voice mode uses Fastino's `GLiNER2.5-multi-Decide` (287M),
Apache-2.0, https://huggingface.co/fastino/GLiNER2.5-multi-Decide .
The setup script pins model revision `e173bca1f0c4217d7a55b3b0c705d5ece8a0218d`.
Weights are downloaded to a separate local runtime and are not embedded in the app.
The `gliner2` inference library is Apache-2.0:
https://github.com/fastino-ai/GLiNER2 . Its dependencies are installed in an
isolated Python environment; see `requirements-intent-router.txt`.
