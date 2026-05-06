# Moonraker Config Layer

Каталог `moonraker/` содержит репозиторный слой конфигурации и компонента Moonraker для TreeD.

## Структура

- `moonraker/moonraker.conf` — entrypoint include-цепочки Moonraker.
- `moonraker/base/*.conf` — статические repo-managed фрагменты.
- `moonraker/components/*.py` — кастомные компоненты Moonraker.

## Контракт include-цепочки

`moonraker/moonraker.conf` обязан включать два слоя:

- `[include moonraker/base/*.conf]` — стабильный слой из репозитория.
- `[include moonraker/generated/*.conf]` — runtime-слой, генерируемый loader.

## Деплой

Деплой выполняет шаг `loader/steps/moonraker-config.sh`:

- `moonraker/moonraker.conf` -> `${PI_HOME}/printer_data/config/moonraker.conf`
- `moonraker/base/*.conf` -> `${PI_HOME}/printer_data/config/moonraker/base/*.conf`
- `moonraker/components/treed_shell_command.py` -> каталог `moonraker/components` установленного Moonraker

`generated` слой:

- расположен в `${PI_HOME}/printer_data/config/moonraker/generated/*.conf`;
- не является источником истины в репозитории;
- очищается/переинициализируется loader шагами.

Пост-обработка `00-core.conf` в `moonraker-config.sh`:
- если найден валидный локальный путь Mainsail (по умолчанию `/var/www/mainsail`, с `index.html` и `release_info.json`), updater-секция Mainsail остается активной;
- если путь не найден, секция `[update_manager mainsail]` автоматически комментируется, чтобы исключить warning о невалидном `path`.
- если найден валидный git checkout Crowsnest (по умолчанию `${PI_HOME}/crowsnest`, с `.git` и `tools/pkglist.sh`), updater-секция Crowsnest остается активной;
- если Crowsnest checkout не найден, секция `[update_manager crowsnest]` автоматически комментируется.

Шаг `klipperscreen-install.sh` после managed-установки пишет generated-фрагмент:

- `${PI_HOME}/printer_data/config/moonraker/generated/60-klipperscreen-update-manager.conf`
- секция `[update_manager KlipperScreen]` создается только после появления валидного checkout KlipperScreen;
- generated-секция содержит `primary_branch: master`, а installer нормализует checkout из detached HEAD в локальную ветку `master` с remote `origin/master`.

## Связанные каталоги

- `moonraker/base/README.md` — детали базовых конфиг-фрагментов.
- `moonraker/components/README.md` — детали кастомного компонента.
