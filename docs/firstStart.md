# Первый запуск TreeD MainshellOS на чистой плате

Документ описывает **полный путь** от свежей microSD с MainsailOS до полностью настроенной платы TreeD:

- единые логин/пароль;
- единое имя хоста (`treedos`);
- локаль и часовой пояс (Volgоград);
- подготовленный каталог `/home/pi/treed` и `.staging` для Git;
- клонирование репозитория `treed-mainshellOS` и запуск `loader.sh`;
- применение кастомного загрузчика (Plymouth) и темы Mainsail;
- готовность к дальнейшей работе через Git (без ручного копипаста конфигов).

Документ рассчитан на технаря/оператора, который **умеет пользоваться SSH**, но не обязан помнить все команды — все команды ниже копируются как есть.

---

## Термины по ходу

Чтобы не путаться:

- **hostname** — имя устройства в сети, то, что видно в приглашении терминала (`pi@treedos:~ $`) и в списке устройств роутера.
- **locale** — локаль, набор настроек языка + формат времени/дат/чисел.
- **timezone** — часовой пояс (например, `Europe/Volgograd`).
- **репозиторий (repo)** — проект на GitHub; здесь это `treed-mainshellOS`.
- **loader.sh** — скрипт внутри репозитория, который применяет тему загрузчика, тему Mainsail и выкладывает конфиги Klipper.
- **.staging** — служебная папка, где лежат клоны репозиториев; оттуда уже производится «деплой» в систему.

---

## 0. Подготовка microSD

1. Скачиваем актуальный образ **MainsailOS** для Raspberry Pi (под свою модель).
2. Записываем образ на microSD (Raspberry Pi Imager / balenaEtcher и т.п.).
3. Вставляем microSD в Raspberry Pi.
4. Подключаем:
   - экран (HDMI);
   - клавиатуру;
   - сеть (Ethernet или Wi-Fi, если будем сразу настраивать);
   - питание.

После включения Raspberry Pi загрузится в консоль.

---

## 1. Первый вход и настройка Wi-Fi

### 1.1. Вход на локальной консоли

На экране появится приглашение:

```text
raspberrypi login:
```

или похожее (`mainsailos login:`).

Вводим:

- **Login**: `pi`
- **Password**: `raspberry`  
  (дефолтный пароль MainsailOS; дальше мы его сменим скриптом).

Попадаем в консоль:

```text
pi@mainsailos:~ $
```

или что-то очень похожее.

### 1.2. Настройка Wi-Fi через `raspi-config`

Команда:

```bash
sudo raspi-config
```

Откроется текстовое меню:

1. **System Options** → **Wireless LAN**.
2. Вводим:
   - SSID — имя вашей Wi-Fi сети;
   - пароль — пароль от Wi-Fi.
3. Выходим через **Finish**. Если предлагается **перезагрузиться** — соглашаемся.

После перезагрузки **ещё раз** входим локально (`pi` / `raspberry`).

---

## 2. Узнать IP-адрес платы

Нужно узнать IP, чтобы подключаться по SSH с рабочего компьютера.

На Pi:

```bash
hostname -I
```

Пример вывода:

```text
192.168.0.195
```

Это IP платы в локальной сети. Запоминаем.

---

## 3. Подключение по SSH с рабочего компьютера

Дальнейшая настройка удобнее через SSH (удалённая консоль).

### 3.1. Подключение из Windows PowerShell

На ПК открываем PowerShell:

```powershell
ssh pi@192.168.0.195
```

Подставляем **свой** IP вместо `192.168.0.195`.

При первом подключении:

- появится вопрос про доверие к ключу хоста — пишем `yes`, Enter;
- запрашивается пароль — пока **старый**: `raspberry`.

Если уже раньше подключались к этому IP и видим:

```text
REMOTE HOST IDENTIFICATION HAS CHANGED!
```

то:

```powershell
ssh-keygen -R 192.168.0.195
ssh pi@192.168.0.195
```

Снова `yes`, `raspberry`.

Успешный вход:

```text
pi@mainsailos:~ $
```

---

## 4. Стартовый скрипт TreeD (базовая настройка платы)

Этот скрипт:

- обновляет систему и ставит базовые пакеты;
- настраивает локаль и часовой пояс;
- задаёт hostname `treedos`;
- меняет пароль пользователя `pi` на `treed`;
- включает SSH, SPI, I²C, UART, расширяет файловую систему;
- настраивает параметры HDMI под экран 960×544;
- ставит и включает KlipperScreen;
- готовит `/home/pi/treed` и `/home/pi/treed/.staging`;
- клонирует `treed-mainshellOS` в `.staging`;
- запускает `loader/loader.sh`, который развернёт Plymouth-тему, тему Mainsail и базовый минимальный профиль Klipper под RN12 (с релиза `v1.2.0`).

> Важно: скрипт нужно запускать **одним куском**; ничего не вырезаем.

В уже открытом SSH-сеансе:

```bash
sudo -s <<'EOF'
set -e

apt-get update
apt-get -y full-upgrade

timedatectl set-timezone Europe/Volgograd

sed -i 's/^# *ru_RU.UTF-8 UTF-8/ru_RU.UTF-8 UTF-8/' /etc/locale.gen
sed -i 's/^# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen
locale-gen
update-locale LANG=ru_RU.UTF-8

hostnamectl set-hostname treedos
if grep -q '^127\.0\.1\.1' /etc/hosts; then
  sed -i 's/^127\.0\.1\.1.*/127.0.1.1 treedos/' /etc/hosts
else
  printf '
127.0.1.1 treedos
' >> /etc/hosts
fi

echo 'pi:treed' | chpasswd

CFG=$( [ -e /boot/firmware/config.txt ] && echo /boot/firmware/config.txt || echo /boot/config.txt )
grep -q 'hdmi_cvt=960 544 60' "$CFG" || cat >>"$CFG" <<EOC
hdmi_group=2
hdmi_mode=87
hdmi_cvt=960 544 60 6 0 0 0
hdmi_drive=2
disable_overscan=1
dtparam=i2c_arm=on
dtparam=spi=on
EOC

apt-get -y install git unzip dfu-util screen   python3-gi python3-gi-cairo libgtk-3-0   xserver-xorg x11-xserver-utils xinit openbox   python3-numpy python3-scipy python3-matplotlib   i2c-tools python3-venv rsync

for grp in dialout tty video input render plugdev gpio i2c spi; do
  getent group "$grp" >/dev/null 2>&1 && usermod -aG "$grp" pi || true
done

cd /home/pi
[ -d KlipperScreen ] && rm -rf KlipperScreen
git clone https://github.com/jordanruthe/KlipperScreen.git
cd KlipperScreen
./scripts/KlipperScreen-install.sh
systemctl enable KlipperScreen.service

mkdir -p /home/pi/treed
mkdir -p /home/pi/treed/.staging
chown -R pi:pi /home/pi/treed

if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_ssh 0
  raspi-config nonint do_spi 0
  raspi-config nonint do_i2c 0
  raspi-config nonint do_serial 2
  raspi-config nonint do_expand_rootfs
fi

STAGING_DIR="/home/pi/treed/.staging"
REPO_DIR="$STAGING_DIR/treed-mainshellOS"

mkdir -p "$STAGING_DIR"
rm -rf "$REPO_DIR"
git clone https://github.com/Yawllen/treed-mainshellOS.git "$REPO_DIR"

cd "$REPO_DIR"
git checkout v1.2.0 || true

chmod +x loader/loader.sh
./loader/loader.sh

EOF

sudo reboot
```

Кратко по блокам:

- `set -e` — любая ошибка роняет скрипт, не едем дальше в полурабочем состоянии.
- Обновления + `Europe/Volgograd` + `ru_RU.UTF-8` по умолчанию.
- `hostname` → `treedos`, правим `/etc/hosts`, чтобы не было `sudo: unable to resolve host`.
- Пароль `pi` → `treed`.
- Настройка HDMI (`960x544@60`) + включение I²C и SPI.
- Установка пакетов (git, Xorg, GTK, matplotlib и др.).
- Добавление пользователя `pi` в нужные группы (access к UART, I²C, SPI, видео, ввод и т.п.).
- Чистый `KlipperScreen`, свежая установка и включение сервиса.
- Создание `/home/pi/treed` и `.staging`, выдача прав `pi`.
- Включение SSH/SPI/I²C/UART, расширение rootfs.
- Клон репозитория `treed-mainshellOS` в `.staging`, checkout на `v1.2.0`, запуск `loader/loader.sh`.

`loader.sh` внутри себя:

- ставит и настраивает Plymouth-тему `treed`;
- поднимает тему Mainsail (`mainsail/.theme` → `/home/pi/printer_data/config/.theme`);
- копирует `klipper/` → `/home/pi/treed/klipper`;
- прописывает `/home/pi/printer_data/config/printer.cfg` на `[include profiles/rn12_corexy_v1/mcu_rn12.cfg]` (если `klipper/printer.cfg` есть);

---

## 5. Состояние системы после перезагрузки

После выполнения скрипта и ребута:

- Имя хоста: `treedos`.
- Логин по SSH:

  ```bash
  ssh pi@IP_ПЛАТЫ
  ```

  пароль — `treed`.

- KlipperScreen установлен и стартует как сервис.
- Mainsail использует кастомную тему `.theme` из репозитория.
- Репозиторий `treed-mainshellOS`:

  ```text
  /home/pi/treed/.staging/treed-mainshellOS
  ```

- Конфиги Klipper:

  - исходники профилей лежат в `/home/pi/treed/klipper`;
  - рантайм-конфиг в `/home/pi/printer_data/config/printer.cfg` содержит:

    ```ini
    [include profiles/rn12_corexy_v1/mcu_rn12.cfg]
    ```

  - актуальный include-chain описан в `klipper/printer.cfg` (модули профиля `profiles/rn12_corexy_v1/*.cfg`).
  - для релиза `v1.2.0` в репо есть минимальный профиль `rn12_corexy_v1` под MKS Robin Nano 1.2.

---

## 6. Обновление после изменений в Git

После того как ты меняешь репозиторий `treed-mainshellOS` (локально → `git push`), на плате достаточно:

```bash
ssh pi@IP_ПЛАТЫ   # пароль treed

cd /home/pi/treed/.staging/treed-mainshellOS
git pull
./loader/loader.sh
```

`loader.sh`:

- подтянет и переустановит Plymouth-тему;
- обновит тему Mainsail;
- синхронизирует `klipper/` в `/home/pi/treed/klipper`;
- корректно обновит `printer.cfg` (если это требуется новой версией).

Если нужно поменять `serial` вручную, редактируй
`/home/pi/printer_data/config/profiles/rn12_corexy_v1/mcu_rn12.cfg`
и перезапусти `klipper`.

> Карта слоев и ownership: `README.md`
> Актуальная модель конфигов и ownership: `docs/config-ownership.md`.
