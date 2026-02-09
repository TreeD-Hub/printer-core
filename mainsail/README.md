# Mainsail Layer

Папка содержит репозиторные артефакты интерфейса Mainsail для TreeD.

Текущая зона ответственности:
- `mainsail/.theme/*` — файлы кастомной темы Mainsail.

Деплой:
- шаг: `loader/steps/klipper-mainsail-theme.sh`
- источник: `mainsail/.theme`
- runtime-путь: `/home/pi/printer_data/config/.theme`

Важно:
- это слой UI, не конфиг Klipper/Moonraker;
- изменения темы проверяются в веб-интерфейсе Mainsail после деплоя.

