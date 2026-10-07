# Архив веб-интерфейса Mainsail

Архив Mainsail для установки без доступа к GitHub Releases.
Шаг loader сверяет его версию и SHA-256 с `runtime-versions.env`.

## Состав

- `mainsail.zip` — статический web-root Mainsail, ожидает `release_info.json` и `index.html` в корне архива.

## Контракт

- `loader/steps/mainsail-web.sh` всегда сначала проверяет этот архив по version/SHA-256 из `runtime-versions.env`.
- GitHub Releases используется только как fallback/recovery, если bundled artifact отсутствует или повреждён.
- Существующий runtime web-root принимается как fallback только при точном совпадении версии с manifest.
- После установки Moonraker update_manager может обновлять Mainsail в runtime web-root.

## Runtime

- целевой путь: `/var/www/mainsail` по умолчанию;
- владелец деплоя: `loader/steps/mainsail-web.sh`.

Смежная документация: [Mainsail и тема](../README.md),
[закреплённые версии](../../runtime-versions.env),
[переменные loader](../../loader/steps/README.md).
