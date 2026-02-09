# Plymouth Assets

Папка хранит исходники boot splash темы TreeD для Plymouth.

Цепочка применения:
- копирование темы: `loader/steps/plymouth-theme-install.sh`
- включение темы и пересборка initramfs: `loader/steps/plymouth-initramfs.sh`

Целевой путь на устройстве:
- `/usr/share/plymouth/themes/treed`

Важно:
- содержимое этой папки относится к boot UX;
- изменения должны проверяться на реальном старте системы.

