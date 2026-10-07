# Загрузочная тема Plymouth

Исходники темы TreeD для раннего экрана загрузки, выключения и обновления.

## Структура

- [`theme/`](theme/README.md) — правила для каталога тем.
- [`theme/treed/`](theme/treed/README.md) — активная тема TreeD.

## Цепочка применения через loader

- `loader/steps/plymouth-theme-install.sh` — копирует тему в системный каталог и выставляет default theme.
- `loader/steps/plymouth-initramfs.sh` — пересобирает initramfs для текущего ядра.
- `loader/steps/plymouth-initramfs-config.sh` — RPi: прописывает `initramfs ... followkernel` в `config.txt`; Armbian и Extlinux: проверяет соответствующий контур initrd.
- `loader/steps/plymouth-cmdline.sh` — нормализует kernel cmdline для splash.
- `loader/steps/plymouth-systemd.sh` — настраивает связанный systemd-контур (`plymouth-quit*`, `getty@tty1`).

## Runtime-пути

- тема: `/usr/share/plymouth/themes/treed`
- initrd: `${BOOT_DIR}/initrd.img-$(uname -r)` (после шага `plymouth-initramfs.sh`)

## Важные замечания

- файлы в этой папке влияют на ранний boot-контур;
- любое изменение темы нужно проверять на реальной загрузке устройства;
- имена обязательных файлов темы синхронизированы с `loader/steps/plymouth-theme-install.sh`.

Порядок шагов и выбор boot backend: [README loader](../loader/README.md).
