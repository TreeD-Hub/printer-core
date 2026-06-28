# `runtime-scripts/treed-update`

Root-side updater для TreeD UI и `treed-mainshellOS`.

- `treed-update-apply treed-shell ui-main-<run>-<attempt>` — проверяет release archive/manifest, атомарно публикует UI с `ui.previous` и перезапускает `treed-shell.service` с rollback при ошибке.
- `treed-update-apply treed-mainshellos vX.Y.Z` — переключает локальный checkout на release tag и запускает `install.sh` в `TREED_DEPLOY_MODE=auto`.
- Скрипт устанавливается loader-ом в `/usr/local/sbin/treed-update-apply`.
- Moonraker вызывает его только через ограниченное sudoers-правило.
