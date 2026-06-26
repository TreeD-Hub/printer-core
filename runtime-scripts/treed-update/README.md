# `runtime-scripts/treed-update`

Root-side updater для `treed-mainshellOS`.

- `treed-update-apply` — переключает локальный checkout на release tag `vX.Y.Z` и запускает `install.sh` в `TREED_DEPLOY_MODE=auto`.
- Скрипт устанавливается loader-ом в `/usr/local/sbin/treed-update-apply`.
- Moonraker вызывает его только через ограниченное sudoers-правило.
