# UI runtime delivery

Целевой production-контур для экранного UI принтера:

1. `treed-shell` публикует GitHub Release с asset `treed-shell-ui.zip`.
2. `loader/steps/treed-shell-install.sh` в этом репозитории скачивает asset, проверяет `treed-shell-ui-manifest.json`, распаковывает bundle в runtime-каталог и пишет `treed-shell.service`.
3. `treed-shell.service` запускает локальный HTTP server для распакованного bundle и открывает его через OS-owned kiosk/browser runtime.
4. `KlipperScreen` остается fallback UI и переключается через `treed-ui`.

## Границы ответственности

- `treed-shell`: React UI, live build, release artifact `treed-shell-ui.zip`.
- `packages/printer-logic`: shared domain contract, уже включается в UI bundle при сборке.
- `printer-core`: установка artifact, browser/kiosk runtime, systemd, fallback, Moonraker/host-side contracts.

## Loader variables

- `TREED_SHELL_RELEASE_API_URL` — GitHub Releases API URL, default `https://api.github.com/repos/TreeD-Hub/treed-shell/releases`.
- `TREED_SHELL_RELEASE_TAG_PREFIX` — release tag prefix, default `ui-main-`.
- `TREED_SHELL_UI_ASSET_NAME` — release asset name, default `treed-shell-ui.zip`.
- `TREED_SHELL_UI_ARCHIVE_URL` — optional direct archive URL; when set, loader skips GitHub API lookup.
- `TREED_SHELL_RUNTIME_DIR` — runtime root, default `${PI_HOME}/treed/treed-shell-runtime`.
- `TREED_SHELL_WEB_DIR` — unpacked UI root, default `${TREED_SHELL_RUNTIME_DIR}/ui`; must stay inside `TREED_SHELL_RUNTIME_DIR`.
- `TREED_SHELL_HTTP_PORT` — local static server port, default `8787`.
- `TREED_SHELL_BROWSER_BIN` — optional browser binary override.

## Не production path

- `git clone treed-shell` inside loader.
- `npm ci` on the printer for TreeD Shell.
- Rust/Tauri build on the printer.
- `apps/web-ui` release as printer UI.
