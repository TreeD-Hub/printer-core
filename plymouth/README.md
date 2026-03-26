# Plymouth Assets

Каталог `plymouth/` содержит исходники splash-темы TreeD и связанные ресурсы boot UX.

## Структура

- `plymouth/theme/README.md` — правила для каталога тем.
- `plymouth/theme/treed/*` — активная тема TreeD.

## Цепочка применения через loader

- `loader/steps/plymouth-theme-install.sh` — копирует тему в системный каталог и выставляет default theme.
- `loader/steps/plymouth-initramfs.sh` — пересобирает initramfs для текущего ядра.
- `loader/steps/plymouth-initramfs-config.sh` — прописывает `initramfs ... followkernel` в `config.txt`.
- `loader/steps/plymouth-cmdline.sh` — нормализует kernel cmdline для splash.
- `loader/steps/plymouth-systemd.sh` — настраивает связанный systemd-контур (`plymouth-quit*`, `getty@tty1`).

## Runtime-пути

- тема: `/usr/share/plymouth/themes/treed`
- initrd: `${BOOT_DIR}/initrd.img-$(uname -r)` (после шага `plymouth-initramfs.sh`)

## Важные замечания

- файлы в этой папке влияют на ранний boot-контур;
- любое изменение темы нужно проверять на реальной загрузке устройства;
- имена обязательных файлов темы синхронизированы с `loader/steps/plymouth-theme-install.sh`.
