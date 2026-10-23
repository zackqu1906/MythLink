# Desktop owner adapter

Vendored from Emanuele-web04/remodex, commit
`f67b1499dbd8ace7a23902fad3242cc88f7e9a9e`,
`phodex-bridge/src/desktop-ipc-*.js` and `codex-runtime-settings.js`.
Apache-2.0; see LICENSE. Upstream file headers are retained.

Mythlink modifications:
- Preserve streamed items when a turn completion contains only an item summary.
- Accept request ID zero when retiring resolved requests.
- Mark completed items for the existing Mythlink follower event projection.

service.cjs is Mythlink's local execution service, shared by cards in one
application process. It uses the installed Codex CLI and Node runtime; it never
launches a desktop window. It stops owned tasks on application exit and runs
Computer Use end-of-turn cleanup before accepting another turn in that thread.
