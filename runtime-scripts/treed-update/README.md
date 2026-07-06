# `runtime-scripts/treed-update`

Root-side updater для TreeD UI и `printer-core`.

- `treed-update-apply printer-ui ui-main-<run>-<attempt>` — проверяет release archive/manifest, атомарно публикует UI с `ui.previous` и перезапускает `treed-shell.service` с rollback при ошибке.
- `treed-update-apply printer-core vX.Y.Z` — переключает локальный checkout на release tag и запускает `install.sh` в `TREED_DEPLOY_MODE=auto`.
- Legacy ids `treed-shell` и `treed-mainshellos` остаются входными aliases.
- Скрипт устанавливается loader-ом в `/usr/local/sbin/treed-update-apply`.
- Moonraker вызывает его только через ограниченное sudoers-правило.
