# Third-party notices

Mythlink integrates the following third-party components. Most downloaded ASR/LLM weights remain outside Git; the provider-supplied touchpad asset below is bundled for offline SDK use.

## llama.cpp

- Source: https://github.com/ggml-org/llama.cpp
- Installed location: `.runtime/local-llm/runtimes/` (downloaded during Windows setup)
- License: MIT
- The pinned release URL and SHA-256 are recorded in `local_llm_catalog.json`.

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
- The 32-point handwriting templates are derived from the prototype's local P01 mouse
  recordings. They are a baseline for Ring testing, not a trained Ring-specific model.

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
