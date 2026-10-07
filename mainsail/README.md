# Mainsail

Папка содержит репозиторные артефакты интерфейса Mainsail для TreeD.

## Состав

- [`.theme/`](.theme/README.md) — тема Mainsail.
- [`web/`](web/README.md) — архив веб-интерфейса для установки без доступа к GitHub Releases.

## Установка и runtime-пути

- шаг: `loader/steps/klipper-mainsail-theme.sh`
- источник: `mainsail/.theme`
- runtime-путь: `${PI_HOME}/printer_data/config/.theme`
- web-слой устанавливается шагом `loader/steps/mainsail-web.sh` из `mainsail/web/mainsail.zip` по умолчанию.

## Контракт

- это слой UI, не конфиг Klipper/Moonraker;
- изменения темы проверяются в веб-интерфейсе Mainsail после деплоя.

Версию и SHA-256 архива задаёт [`runtime-versions.env`](../runtime-versions.env).
Установка и reverse proxy описаны в [шаге loader](../loader/steps/README.md).
