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

## Связанные каталоги

- `moonraker/base/README.md` — детали базовых конфиг-фрагментов.
- `moonraker/components/README.md` — детали кастомного компонента.
