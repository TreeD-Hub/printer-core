# Первый старт Rock Pi для TreeD V2

Документ описывает базовый путь подготовки Rock Pi под V2-контур.

## 1. Базовая ОС

- Рекомендуемая база: **Armbian Debian 12**.
- Пользователь для runtime-путей проекта: `pi`.
- SSH должен быть включен.

## 2. Запуск installer

```bash
curl -fsSL https://raw.githubusercontent.com/TreeD-Hub/treed-mainshellOS/treed-v2/bootstrap-pi.sh | bash
```

`bootstrap-pi.sh` клонирует свежий installer checkout в `/home/pi/treed/treed-mainshellOS`, запускает loader в `TREED_DEPLOY_MODE=auto` и перезагружает систему только при состоянии `fresh`.

## 3. V2 UUID

Для текущей платы значения зафиксированы в `loader/bootstrap.sh`:
- Octopus Pro: `d372e54bf965`;
- EBB42: `efaf957ab20f`;
- Eddy: `95485b93332a`;
- Eddy включен по умолчанию (`TREED_EDDY_ENABLED=1`).

## 4. Что проверить после запуска

- `systemctl is-active klipper moonraker treed-can-setup`
- `ip -details link show can0`
- `cat /run/treed-loader/state.env`
