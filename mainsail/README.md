# Mainsail Layer

Папка содержит репозиторные артефакты интерфейса Mainsail для TreeD.

Текущая зона ответственности:
- `mainsail/.theme/*` — файлы кастомной темы Mainsail.
- `mainsail/web/mainsail.zip` — bundled web-root Mainsail для установки без доступа к GitHub Releases.

Деплой:
- шаг: `loader/steps/klipper-mainsail-theme.sh`
- источник: `mainsail/.theme`
- runtime-путь: `${PI_HOME}/printer_data/config/.theme`
- web-слой устанавливается шагом `loader/steps/mainsail-web.sh` из `mainsail/web/mainsail.zip` по умолчанию.

Важно:
- это слой UI, не конфиг Klipper/Moonraker;
- изменения темы проверяются в веб-интерфейсе Mainsail после деплоя.
