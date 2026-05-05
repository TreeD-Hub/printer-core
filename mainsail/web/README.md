# Mainsail Web Bundle

Каталог содержит bundled web-архив Mainsail для установки без доступа к GitHub Releases.

## Состав

- `mainsail.zip` — статический web-root Mainsail, ожидает `release_info.json` и `index.html` в корне архива.

## Контракт

- `loader/steps/mainsail-web.sh` по умолчанию предпочитает этот архив (`TREED_MAINSAIL_PREFER_LOCAL_ZIP=1`).
- Если нужен live-download из GitHub Releases, установите `TREED_MAINSAIL_PREFER_LOCAL_ZIP=0`.
- После установки Moonraker update_manager может обновлять Mainsail в runtime web-root.

## Runtime

- целевой путь: `/var/www/mainsail` по умолчанию;
- владелец деплоя: `loader/steps/mainsail-web.sh`.
