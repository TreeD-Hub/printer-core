# Первый старт Rock Pi для TreeD V2

Документ описывает базовый путь подготовки Rock Pi под V2-контур.

## 1. Базовая ОС

- Рекомендуемая база: **Armbian Debian 12**.
- Пользователь для runtime-путей проекта: `pi`.
- SSH должен быть включен.

## 2. Подготовка репозитория

```bash
cd /home/pi
mkdir -p treed
cd treed
git clone --branch treed-v2 https://github.com/TreeD-Hub/treed-mainshellOS.git
cd treed-mainshellOS
```

## 3. Минимальные переменные для V2

```bash
export TREED_EDDY_ENABLED=0
```

Опционально:

```bash
export TREED_MAIN_MCU_SERIAL_BY_ID="/dev/serial/by-id/usb-..."
export TREED_EBB_CANBUS_UUID="<hex_uuid>"
export TREED_EDDY_CANBUS_UUID="<hex_uuid>"
export TREED_CAN_IFACE="can0"
export TREED_CAN_BITRATE="1000000"
export TREED_CAN_TXQUEUE="1024"
export TREED_Z_ENDSTOP_PIN="PG10"
export TREED_Z_POSITION_ENDSTOP="0.5"
```

## 4. Запуск loader

```bash
sudo bash loader/loader.sh
```

## 5. Что проверить после запуска

- `systemctl is-active klipper moonraker treed-can-setup`
- `ip -details link show can0`
- `ls -l /dev/serial/by-id/`

При `TREED_EDDY_ENABLED=1` без `TREED_EDDY_CANBUS_UUID` на CAN-шине должен остаться ровно один неизвестный UUID после резолва EBB.
