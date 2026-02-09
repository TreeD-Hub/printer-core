# Прошивка MKS Robin Nano V1.2 (RN12) под Klipper

Инструкция описывает актуальный цикл прошивки RN12 и привязки к текущему TreeD-пайплайну.

## 1. Предусловия

- Raspberry Pi с Klipper/MainsailOS.
- Плата MKS Robin Nano V1.2.
- USB-A <-> USB-B кабель (Pi <-> RN12) для режима `usb`
  или UART-подключение (TX/RX/GND, 3.3V TTL) для режима `uart`.
- microSD (FAT32) для загрузчика платы.

Репозиторные артефакты:
- эталонный бинарник: `firmware/rn12/ROBIN_NANO.bin`
- активный профиль Klipper: `klipper/profiles/rn12_hbot_v1/*`

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
- Communication interface: `Serial (on USART3 PB11/PB10)`
- Baud rate: `250000`

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

## 6. Как попадает serial в профиль TreeD

Актуальная модель:
- файл профиля: `klipper/profiles/rn12_hbot_v1/mcu_rn12.cfg`
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

Для режима `uart`:

```bash
cd /home/pi/treed/treed-mainshellOS
sudo TREED_MCU_TRANSPORT=uart TREED_MCU_UART_DEV=/dev/serial0 bash loader/loader.sh
```

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

## 8. Актуальные пути в runtime

- `printer.cfg`: `/home/pi/printer_data/config/printer.cfg`
- профиль: `/home/pi/printer_data/config/profiles/rn12_hbot_v1/`
- mcu-файл: `/home/pi/printer_data/config/profiles/rn12_hbot_v1/mcu_rn12.cfg`

---

См. также:
- `README.md`
- `docs/config-ownership.md`
- `klipper/profiles/rn12_hbot_v1/README.md`
