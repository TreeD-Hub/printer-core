# Доставка экранного UI

Установка готового экранного UI из релиза `printer-ui`:

1. Репозиторий `printer-ui` публикует GitHub Release с артефактом `treed-shell-ui.zip`.
2. `loader/steps/treed-shell-install.sh` в этом репозитории скачивает asset, проверяет `treed-shell-ui-manifest.json`, распаковывает bundle в runtime-каталог и пишет `treed-shell.service`.
3. `treed-shell.service` запускает локальный HTTP server для распакованного bundle и открывает его через OS-owned kiosk/browser runtime.
4. `KlipperScreen` остается fallback UI и переключается через `treed-ui`.

## Границы ответственности

- `treed-shell`: React UI, live build, release artifact `treed-shell-ui.zip`.
- `packages/printer-logic`: shared domain contract, уже включается в UI bundle при сборке.
- `printer-core`: установка artifact, browser/kiosk runtime, systemd, fallback, Moonraker/host-side contracts.

## Переменные loader

- `TREED_SHELL_RELEASE_API_URL` — API релизов, по умолчанию `https://api.github.com/repos/TreeD-Hub/printer-ui/releases`.
- `TREED_SHELL_RELEASE_TAG_PREFIX` — префикс тега, по умолчанию `ui-main-`.
- `TREED_SHELL_UI_ASSET_NAME` — имя артефакта, по умолчанию `treed-shell-ui.zip`.
- `TREED_SHELL_UI_ARCHIVE_URL` — необязательный прямой URL архива; при его наличии поиск через GitHub API пропускается.
- `TREED_SHELL_RUNTIME_DIR` — корневой runtime-каталог, по умолчанию `${PI_HOME}/treed/treed-shell-runtime`.
- `TREED_SHELL_WEB_DIR` — распакованный UI, по умолчанию `${TREED_SHELL_RUNTIME_DIR}/ui`; должен находиться внутри `TREED_SHELL_RUNTIME_DIR`.
- `TREED_SHELL_HTTP_PORT` — порт локального HTTP-сервера, по умолчанию `8787`.
- `TREED_SHELL_BROWSER_BIN` — необязательный путь к браузеру.

## Ограничения доставки

- Loader устанавливает готовый bundle; клонирование и сборка исходников UI на принтере в эту процедуру не входят.
- `npm ci` и сборка Rust/Tauri на принтере не требуются.
- Релиз `apps/web-ui` не используется как экранный UI.

Смежная документация: [параметры и шаги установки](../../loader/steps/README.md),
[переключение TS/KS](../../runtime-scripts/treed-ui/README.md),
[обновление bundle и откат](../../runtime-scripts/treed-update/README.md).
