# Moonraker Config Layer

Папка содержит репозиторный слой конфигурации Moonraker для TreeD.

Назначение:
- `moonraker.conf` — точка входа Moonraker в runtime-конфиге.
- `base/*.conf` — базовые фрагменты, включаемые из `moonraker.conf`.
- `components/*.py` — кастомные компоненты Moonraker.

Деплой:
- шаг: `loader/steps/moonraker-config.sh`
- runtime-путь конфига: `/home/pi/printer_data/config/moonraker.conf`
- runtime-путь base: `/home/pi/printer_data/config/moonraker/base/*.conf`
- runtime-путь generated: `/home/pi/printer_data/config/moonraker/generated/*.conf`

Важно:
- `moonraker.conf` должен включать `moonraker/base/*.conf` и `moonraker/generated/*.conf`;
- generated-слой управляется лоадером и не является источником истины в репозитории.

