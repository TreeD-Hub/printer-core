#!/bin/bash
set -euo pipefail
# Назначение: подготовить исторический fixture обновления в одноразовой VM.
# Контур: только гостевая система QEMU; устанавливает пакеты и systemd-службы.

# Блок 1: Проверка границ и подлинных исходных архивов.
[[ "${TREED_INTEGRATION_DISPOSABLE:-}" = 1 && "$(id -u)" = 0 ]]
systemd-detect-virt --vm --quiet
INPUT=/root/treed-input
OLD=/opt/treed-old-source
FIRST_SOURCE=/opt/treed-first-source
printf '%s\n' \
  '8545afc339cb52627fdefdc106a0f8af0706ec7d9b032d5a21fad8e2358e3a33  old-runtime.zip' \
  '424d0e0101114cc4dc0a37b7fa487da3f2a5e0dc88f47ce6d19602a9f69e9ce7  old-source.zip' | (cd "$INPUT" && sha256sum -c -)
DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y git sudo nginx rsync python3-venv python3-dev build-essential libffi-dev libssl-dev
id radxa >/dev/null 2>&1 || useradd -m -s /bin/bash radxa
python3 -m zipfile -e "$INPUT/old-source.zip" "$OLD"
python3 -m zipfile -e "$INPUT/first-source.zip" "$FIRST_SOURCE"
git init -q /opt/treed-old-git
git -C /opt/treed-old-git remote add origin https://github.com/TreeD-Hub/printer-core.git
git -C /opt/treed-old-git fetch -q --depth=1 origin refs/tags/v0.49.2:refs/tags/v0.49.2
git -C /opt/treed-old-git checkout -q --detach v0.49.2
[[ "$(git -C /opt/treed-old-git rev-parse HEAD)" = f8ee9c04e219d5db8e74b17ca3696f0668f7ff9e ]]
for spec in 'klipper https://github.com/Klipper3d/klipper.git ce7002bedf37e938bb483572949f3703ac6476cb' 'moonraker https://github.com/Arksine/moonraker.git 1cfb0c41e468645951a371621f06d32777b6107c' 'crowsnest https://github.com/mainsail-crew/crowsnest.git 436042452f564f3d737e3b980a213f849a8a0562'; do
  read -r name origin revision <<< "$spec"
  sudo -u radxa git init -q "/home/radxa/$name"
  sudo -u radxa git -C "/home/radxa/$name" remote add origin "$origin"
  sudo -u radxa git -C "/home/radxa/$name" fetch -q --depth=1 origin "$revision"
  sudo -u radxa git -C "/home/radxa/$name" checkout -q --detach FETCH_HEAD
  [[ "$(sudo -u radxa git -C "/home/radxa/$name" rev-parse HEAD)" = "$revision" ]]
done
sudo -u radxa python3 -m venv /home/radxa/crowsnest-env
sudo -u radxa /home/radxa/crowsnest-env/bin/python -m pip install -r /home/radxa/crowsnest/requirements.txt

# Блок 2: Настоящие закреплённые окружения Moonraker и vision без весов моделей.
source "$FIRST_SOURCE/loader/lib/python-runtime-lock.sh"
python_runtime_lock moonraker >/root/moonraker-lock.txt
python_runtime_lock vision >/root/vision-lock.txt
grep -E '^(pip|setuptools|wheel)==' /root/moonraker-lock.txt >/root/moonraker-bootstrap.txt
python3 -m venv /home/radxa/moonraker-env
moon=/home/radxa/moonraker-env/bin/python
"$moon" -m pip install --upgrade -c /root/moonraker-lock.txt -r /root/moonraker-bootstrap.txt
"$moon" -m pip install --no-build-isolation --upgrade -r /root/moonraker-lock.txt
"$moon" -m pip install --no-build-isolation -c /root/moonraker-lock.txt -r /home/radxa/moonraker/scripts/moonraker-requirements.txt
"$moon" -m pip check
chown -R radxa:radxa /home/radxa/moonraker-env
python3 -m venv /opt/treed-vision
vision=/opt/treed-vision/bin/python
grep -E '^(pip|setuptools|wheel)==' /root/vision-lock.txt >/root/vision-bootstrap.txt
"$vision" -m pip install --upgrade -c /root/vision-lock.txt -r /root/vision-bootstrap.txt
"$vision" -m pip install --index-url https://download.pytorch.org/whl/cpu -c /root/vision-lock.txt 'torch==2.11.0' 'torchvision==0.26.0'
"$vision" -m pip install --no-build-isolation -r /root/vision-lock.txt
"$vision" -m pip check
"$vision" -c 'import torch, torchvision, rfdetr'
printf '%s\n' 'VISION_PYTHON=/opt/treed-vision/bin/python' >/etc/default/treed-vision

# Блок 3: Разворачиваем старый payload с проверкой пакета и upstream SHA.
python3 -B - <<'PY'
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
import pwd
core = SourceFileLoader('old_core', '/opt/treed-old-source/runtime-scripts/treed-update/treed-core-update').load_module()
env = {'TREED_UPDATE_PI_HOME': '/home/radxa', 'TREED_UPDATE_PI_USER': 'radxa'}
manifest, payload = core.load_package(Path('/root/treed-input/old-runtime.zip'), 'v0.49.2')
locations = core.roots(env)
account = pwd.getpwnam('radxa')
rows = []
for key, original in payload.items():
    destination = core.destination(key, locations)
    destination.parent.mkdir(parents=True, exist_ok=True)
    data = core.render(key, original, None, env)
    destination.write_bytes(data)
    if key.startswith('sbin/'):
        os.chmod(destination, 0o755)
    else:
        os.chown(destination, account.pw_uid, account.pw_gid)
    rows.append({'path': key, 'runtimeSha256': core.content_hash(key, data)})
manifest['files'] = rows
core.check_compatibility(manifest, env, manifest)
core.write_json(core.manifest_path(env), manifest)
PY

# Блок 4: Имитируется только оборудование; службы и проверки остаются настоящими.
install -d -m 0755 /opt/treed-fixture /etc/systemd/system/klipper.service.d /home/radxa/printer_data/{comms,gcodes,logs,system}
install -m 0644 "$INPUT/klippy-fixture.py" /opt/treed-fixture/klippy-fixture.py
chown -R radxa:radxa /home/radxa/printer_data
groupadd -f render
printf '%s\n' 'verbosity=1' 'bootlogo=true' 'extraargs=quiet splash plymouth.ignore-serial-consoles logo.nologo vt.global_cursor_default=0 consoleblank=0 loglevel=3 vt.handoff=7 usbcore.autosuspend=-1' >/boot/armbianEnv.txt
systemctl mask getty@tty1.service
timedatectl set-timezone Europe/Moscow
timedatectl set-ntp true
cat >/etc/systemd/system/treed-can-setup.service <<'UNIT'
[Unit]
Description=Integration simulated CAN hardware
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/true
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/klipper.service <<'UNIT'
[Unit]
Description=Klipper hardware fixture
After=treed-can-setup.service
Requires=treed-can-setup.service
[Service]
User=radxa
Group=radxa
WorkingDirectory=/home/radxa/klipper
ExecStart=/usr/bin/python3 /opt/treed-fixture/klippy-fixture.py
Restart=always
[Install]
WantedBy=multi-user.target
UNIT
cat >/etc/systemd/system/klipper.service.d/integration.conf <<'UNIT'
[Service]
ExecStartPre=
ExecStart=
ExecStart=/usr/bin/python3 /opt/treed-fixture/klippy-fixture.py
UNIT
cat >/etc/systemd/system/moonraker.service <<'UNIT'
[Unit]
Description=Moonraker API Server
After=network.target klipper.service
Wants=klipper.service
[Service]
Type=simple
User=radxa
Group=radxa
SupplementaryGroups=dialout tty video render
WorkingDirectory=/home/radxa/moonraker
ExecStart=/home/radxa/moonraker-env/bin/python /home/radxa/moonraker/moonraker/moonraker.py -d /home/radxa/printer_data
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
cat >/usr/local/sbin/ip <<'SCRIPT'
#!/bin/sh
case "$*" in
  'link show can0'|'-details link show can0')
    printf '%s\n' '3: can0: <NOARP,UP,LOWER_UP> mtu 16 qdisc pfifo_fast state UP mode DEFAULT group default qlen 128' '    can state ERROR-ACTIVE restart-ms 100 bitrate 1000000' ;;
  *) exec /usr/sbin/ip "$@" ;;
esac
SCRIPT
chmod 0755 /usr/local/sbin/ip
systemctl daemon-reload
systemctl enable --now treed-can-setup.service klipper.service

# Блок 5: Реальные старые шаги конфигурации Mainsail и Moonraker.
env REPO_DIR="$OLD" PI_HOME=/home/radxa PI_USER=radxa bash "$OLD/loader/steps/mainsail-web.sh"
env REPO_DIR="$OLD" PI_HOME=/home/radxa PI_USER=radxa TREED_DEPLOY_MODE_EFFECTIVE=preserve TREED_UPDATE_MANAGED_LOADER=0 bash "$OLD/loader/steps/moonraker-config.sh"
chown -R radxa:radxa /home/radxa/printer_data
systemctl enable --now moonraker.service
systemctl restart moonraker.service
ready=0
for attempt in $(seq 1 120); do
  if curl -fsS --max-time 4 http://127.0.0.1:7125/server/info >/tmp/treed-old-info.json 2>/dev/null && \
     python3 -c 'import json; r=json.load(open("/tmp/treed-old-info.json"))["result"]; assert r["klippy_state"] == "ready" and not r["failed_components"]'; then
    ready=1
    break
  fi
  sleep 1
done
[[ "$ready" = 1 ]] || { echo 'Старый Moonraker не прошёл readiness после restart' >&2; exit 1; }
/usr/local/sbin/treed-core-update baseline /opt/treed-old-git

# Блок 6: Контрольные данные и transport fixture с неизменёнными URL и хешами.
install -m 0644 "$INPUT/sitecustomize.py" /opt/treed-fixture/sitecustomize.py
install -d /etc/systemd/system/treed-update.service.d
printf '%s\n' '[Service]' 'Environment=PYTHONPATH=/opt/treed-fixture' >/etc/systemd/system/treed-update.service.d/integration.conf
python3 -B - <<'PY'
import hashlib, json, re, shutil, zipfile
from pathlib import Path
root = Path('/root/treed-input')
for name in ('first', 'next'):
    runtime, source = (root / (name + '-' + suffix + '.zip') for suffix in ('runtime', 'source'))
    with zipfile.ZipFile(runtime) as archive:
        manifest = json.loads(archive.read('manifest.json'))
    tag = manifest['tag']
    if re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag) is None:
        raise ValueError('Некорректный тег пакета')
    target = Path('/opt/treed-fixture/releases') / tag
    target.mkdir(parents=True, exist_ok=False)
    assets = []
    for asset, path in (('treed-core-runtime.zip', runtime), ('treed-mainshellos-source.zip', source)):
        shutil.copyfile(path, target / asset)
        assets.append({'name': asset, 'digest': 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest(),
                       'browser_download_url': f'https://github.com/TreeD-Hub/printer-core/releases/download/{tag}/{asset}'})
    (target / 'release.json').write_text(json.dumps({'tag_name': tag, 'draft': False, 'prerelease': False, 'assets': assets}))
    (root / (name + '-tag')).write_text(tag)
PY
cfg=/home/radxa/printer_data/config
printf '%s\n' '# integration-local-override' >"$cfg/local_overrides.cfg"
printf '%s\n' '[Variables]' 'fixture_counter = 314159' >"$cfg/treed_variables.cfg"
cat >>"$cfg/printer.cfg" <<'CFG'

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
#*# [input_shaper]
#*# shaper_freq_x = 73.25
CFG
printf '%s\n' '{"printer_id":"integration-fixture","secret":"0000000000000000000000000000000000000000000000000000000000000000"}' >/home/radxa/printer_data/system/treed-autoprinter-credentials.json
install -d /home/radxa/printer_data/system/treed-autoprinter
printf '%s\n' 'integration-account-store-fixture' >/home/radxa/printer_data/system/treed-autoprinter/fixture.txt
chown -R radxa:radxa /home/radxa/printer_data
chmod 0600 /home/radxa/printer_data/system/treed-autoprinter-credentials.json
systemctl daemon-reload
/usr/local/sbin/treed-core-update verify
