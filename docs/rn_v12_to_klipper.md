# Прошивка MKS Robin Nano V1.2 (RN12) под Klipper

Инструкция описывает актуальный цикл прошивки RN12 и привязки к текущему TreeD-пайплайну.

## 1. Предусловия

- Raspberry Pi с Klipper/MainsailOS.
- Плата MKS Robin Nano V1.2.
- USB-A <-> USB-B кабель (Pi <-> RN12) для режима `usb`
  или UART-подключение через WiFi-UART разъем RN12 (3.3V TTL) для режима `uart`.
  - RN12 `PA9 (TX)` -> Pi `GPIO15 / pin 10 (RX)`
  - RN12 `PA10 (RX)` -> Pi `GPIO14 / pin 8 (TX)`
  - `GND` -> `GND`
- microSD (FAT32) для загрузчика платы.

Репозиторные артефакты:
- эталонный бинарник: `firmware/rn12/ROBIN_NANO.bin`
- активный профиль Klipper: `klipper/profiles/rn12_corexy_v1/*`

## 2. Сборка прошивки Klipper

```bash
cd /home/pi/klipper
git pull
make clean
make menuconfig
```

Для RN12 выставить:
- MCU: `STM32F103`
- Bootloader offset: `28KiB bootloader`
- Clock: `8 MHz crystal`
- Communication interface: `Serial (on USART1 PA10/PA9)`
- Baud rate: `250000`
- UART-линия RN12 в этом контуре: WiFi-header `PA9/PA10` (USART1).
- USART3 `PB11/PB10` в этом контуре не используется.

Сборка:

```bash
cd /home/pi/klipper
make -j4
ls -l out/klipper.bin
```

## 3. Подготовка `ROBIN_NANO.bin`

```bash
cd /home/pi/klipper
./scripts/update_mks_robin.py out/klipper.bin out/ROBIN_NANO.bin
```

Опционально сохранить в staging:

```bash
mkdir -p /home/pi/treed/.staging/firmware_rn12
cp out/ROBIN_NANO.bin /home/pi/treed/.staging/firmware_rn12/
```

На карту microSD копировать только один `*.bin`:
- `ROBIN_NANO.bin`

## 4. Прошивка платы через microSD

1. Выключить питание RN12.
2. Вставить карту с `ROBIN_NANO.bin`.
3. Включить питание.
4. Подождать 10-20 секунд.
5. Выключить питание и извлечь карту.

Признак успешной прошивки:
- на карте файл переименован в `ROBIN_NANO.CUR`.

## 5. Проверка канала связи с Pi

Режим `usb`:

```bash
ls -l /dev/serial/by-id/
```

Ожидается путь вида:
- `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`

Режим `uart`:

```bash
ls -l /dev/serial0
```

Ожидается симлинк вида `/dev/serial0 -> ttyAMA0` или `/dev/serial0 -> ttyS0`.
Для RN12 используется именно WiFi-UART разъем (`PA9/PA10`), а не `PB10/PB11`.

## 6. Как попадает serial в профиль TreeD

Актуальная модель:
- файл профиля: `klipper/profiles/rn12_corexy_v1/mcu_rn12.cfg`
- в файле должен быть только блок `[mcu]` (без `[printer]`)

Serial обычно ставится автоматически шагом `klipper-profiles` при запуске loader.

Режим задается переменными:
- `TREED_MCU_TRANSPORT=usb|uart`
- `TREED_MCU_UART_DEV=/dev/serial0` (для `uart`)

Полный прогон:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo bash loader/loader.sh
```

Если устройств несколько и нужна явная привязка:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo MCU_SERIAL_BY_ID="/dev/serial/by-id/usb-..." bash loader/loader.sh
```

Для режима `uart` используйте только двухфазный переход (без смешения шагов):

### Фаза 1. Подготовка Pi под UART (без переключения MCU transport)

Цель фазы: включить UART-контур на Pi, убрать serial-console конфликт и перезагрузить систему.

```bash
cd /home/pi/treed/treed-mainshellOS
sudo REPO_DIR="$(pwd)" TREED_MCU_TRANSPORT=uart TREED_UART_DISABLE_BT=1 bash loader/steps/rpi-uart-config.sh
sudo REPO_DIR="$(pwd)" TREED_MCU_TRANSPORT=uart bash loader/steps/plymouth-cmdline.sh
sudo reboot
```

После перезагрузки проверьте, что UART-устройство есть:

```bash
ls -l /dev/serial0
```

На фазе 1 профиль Klipper и `serial:` в `mcu_rn12.cfg` не переключаются.

### Фаза 2. Переключение профиля на UART transport

Цель фазы: перевести runtime-конфиг MCU на UART-путь после того, как Pi уже подготовлена.

```bash
cd /home/pi/treed/treed-mainshellOS
sudo TREED_MCU_TRANSPORT=uart TREED_MCU_UART_DEV=/dev/serial0 TREED_UART_DISABLE_BT=1 bash loader/loader.sh
```

Важно:
- Не пропускайте reboot между фазами.
- Не запускайте фазу 2 до фактического подключения RN12 по PA9/PA10 (WiFi-UART header).

## 7. Проверка Klipper после привязки MCU

```bash
sudo systemctl restart klipper
sleep 5
tail -n 120 /home/pi/printer_data/logs/klippy.log
```

Ищем в логе:
- `mcu 'mcu': Starting serial connect`
- `Loaded MCU 'mcu' ...`
- `Configured MCU 'mcu' ...`

### Минимальный smoke-test после установки/миграции

```bash
set -euo pipefail
SINCE="$(date -d 'today 00:00' '+%Y-%m-%d %H:%M:%S')"

echo "=== services ==="
systemctl is-active klipper moonraker

echo "=== klipper journal (today) ==="
sudo journalctl -u klipper --since "${SINCE}" --no-pager \
  | grep -Ei "Lost communication with MCU|Timeout with MCU|MCU 'mcu' shutdown|mcu.error|Error configuring printer" \
  && echo "WARN: найдены ошибки MCU" || echo "OK: свежих MCU-ошибок нет"

echo "=== klippy tail ==="
grep -aEi "Lost communication with MCU|Timeout with MCU|MCU 'mcu' shutdown|mcu.error|Error configuring printer" \
  /home/pi/printer_data/logs/klippy.log | tail -n 40 || true
```

Ожидаемый результат:
- `klipper` и `moonraker` в состоянии `active`;
- за текущий день нет новых `Lost communication with MCU` / `Timeout with MCU`.

## 8. Актуальные пути в runtime

- `printer.cfg`: `/home/pi/printer_data/config/printer.cfg`
- профиль: `/home/pi/printer_data/config/profiles/rn12_corexy_v1/`
- mcu-файл: `/home/pi/printer_data/config/profiles/rn12_corexy_v1/mcu_rn12.cfg`

---

См. также:
- `README.md`
- `docs/config-ownership.md`
- `klipper/profiles/rn12_corexy_v1/README.md`
