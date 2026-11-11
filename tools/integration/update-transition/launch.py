#!/usr/bin/env python3
"""Назначение: запустить проверку перехода в одноразовой QEMU VM.
Контур: отдельный рабочий каталог и временный непривилегированный контейнер Docker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.request
import zipfile


# Блок 1: Закрепляем гостевой образ и старые реальные release assets.
IMAGE_URL = ('https://cloud.debian.org/images/cloud/bookworm/20261026-2643/'
             'debian-12-genericcloud-amd64-20261026-2643.qcow2')
IMAGE_SHA256 = '24036a8b79cbb11d809752c60f2f885b1a3b02b1d663faa0b648eb87bd7a256e'
OLD_ASSETS = {
    'old-runtime.zip': ('treed-core-runtime.zip', '8545afc339cb52627fdefdc106a0f8af0706ec7d9b032d5a21fad8e2358e3a33'),
    'old-source.zip': ('treed-mainshellos-source.zip', '424d0e0101114cc4dc0a37b7fa487da3f2a5e0dc88f47ce6d19602a9f69e9ce7'),
}
DOCKER_IMAGE = 'python@sha256:b7a9e5f6a93d6d4a31f129bbd4bdee69efccb6ded0fb230d78aafaf37af98377'


def download(url, destination, expected):
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={'User-Agent': 'treed-integration'})
    with urllib.request.urlopen(request, timeout=120) as source, destination.open('xb') as target:
        while block := source.read(1024 * 1024):
            digest.update(block)
            target.write(block)
    if digest.hexdigest() != expected:
        raise ValueError(f'SHA-256 не совпадает: {destination.name}')


def package_tag(path):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        tag = manifest.get('tag')
        updater = manifest.get('updater') or {}
        if not isinstance(tag, str) or re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag) is None:
            raise ValueError(f'Неверный тег: {path}')
        if updater.get('protocol') != 1 or hashlib.sha256(archive.read(updater['entrypoint'])).hexdigest() != updater.get('sha256'):
            raise ValueError(f'Неверный updater envelope: {path}')
    return tag


# Блок 2: В контейнере запускается QEMU TCG, а все системные изменения остаются в госте.
CONTAINER_SCRIPT = r'''#!/bin/bash
set -euo pipefail
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends qemu-system-x86 qemu-utils genisoimage openssh-client >/work/container-install.log 2>&1
ssh-keygen -q -t ed25519 -N '' -f /work/guest-key
pubkey=$(cat /work/guest-key.pub)
mkdir /work/seed
printf '%s\n' '#cloud-config' 'disable_root: false' 'ssh_pwauth: false' 'users:' '  - default' '  - name: root' '    ssh_authorized_keys:' "      - $pubkey" >/work/seed/user-data
printf '%s\n' 'instance-id: treed-integration' 'local-hostname: treed-test' >/work/seed/meta-data
genisoimage -quiet -output /work/seed.iso -volid cidata -joliet -rock /work/seed
qemu-img create -f qcow2 -F qcow2 -b /work/debian12.qcow2 /work/guest.qcow2 24G
qemu-system-x86_64 -accel tcg,thread=multi -cpu max -smp 2 -m 4096 -nographic -drive file=/work/guest.qcow2,format=qcow2 -cdrom /work/seed.iso -nic user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:2222-:22 >/work/guest-console.log 2>&1 &
qemu_pid=$!
cp /work/guest-key /tmp/treed-guest-key
chmod 600 /tmp/treed-guest-key
ssh_args=(-i /tmp/treed-guest-key -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=3 -p 2222)
scp_args=(-i /tmp/treed-guest-key -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -P 2222)
cleanup() {
  timeout 30 ssh "${ssh_args[@]}" root@127.0.0.1 'journalctl -u treed-update.service --no-pager > /root/treed-update-journal.log; dpkg --audit > /root/treed-dpkg-audit.log; tar -cf /root/treed-evidence.tar /root/treed-update-journal.log /root/treed-dpkg-audit.log /root/treed-fault-evidence.json /var/log/treed-fixture-transport.log /var/lib/treed-update /var/log/treed-update 2>/dev/null || true' 2>/dev/null || true
  timeout 60 scp "${scp_args[@]}" root@127.0.0.1:/root/treed-evidence.tar /work/evidence.tar 2>/dev/null || true
  timeout 10 ssh "${ssh_args[@]}" root@127.0.0.1 'systemctl poweroff' 2>/dev/null || true
  for attempt in $(seq 1 30); do kill -0 "$qemu_pid" 2>/dev/null || break; sleep 1; done
  kill "$qemu_pid" 2>/dev/null || true
  wait "$qemu_pid" 2>/dev/null || true
}
trap cleanup EXIT
ready=0
for attempt in $(seq 1 120); do
  if boot_id=$(timeout 5 ssh "${ssh_args[@]}" root@127.0.0.1 'cloud-init status --wait >/dev/null && cat /proc/sys/kernel/random/boot_id' 2>/dev/null) && [[ -n "$boot_id" ]]; then ready=1; break; fi
  kill -0 "$qemu_pid" || break
  sleep 2
done
[[ "$ready" = 1 ]] || { echo 'Гостевая VM не запустилась' >&2; exit 1; }
ssh "${ssh_args[@]}" root@127.0.0.1 'printf "%s\n" "GRUB_CMDLINE_LINUX_DEFAULT=\"quiet splash consoleblank=0\"" > /etc/default/grub.d/99-treed-fixture.cfg && update-grub >/root/treed-grub.log 2>&1'
ssh "${ssh_args[@]}" root@127.0.0.1 'systemctl reboot' || true
ready=0
for attempt in $(seq 1 240); do
  current=$(ssh "${ssh_args[@]}" root@127.0.0.1 'cat /proc/sys/kernel/random/boot_id' 2>/dev/null || true)
  if [[ -n "$current" && "$current" != "$boot_id" ]]; then ready=1; break; fi
  kill -0 "$qemu_pid" || break
  sleep 2
done
[[ "$ready" = 1 ]] || { echo 'Гостевая VM не перезагрузилась' >&2; exit 1; }
cmdline=$(ssh "${ssh_args[@]}" root@127.0.0.1 'cat /proc/cmdline')
for token in quiet splash consoleblank=0; do
  [[ " $cmdline " == *" $token "* ]] || { echo "Нет boot-параметра: $token" >&2; exit 1; }
done
scp "${scp_args[@]}" -r /work/input root@127.0.0.1:/root/treed-input
ssh "${ssh_args[@]}" root@127.0.0.1 'env TREED_INTEGRATION_DISPOSABLE=1 bash /root/treed-input/guest-bootstrap.sh' 2>&1 | tee /work/guest-bootstrap.log
ssh "${ssh_args[@]}" root@127.0.0.1 "env TREED_INTEGRATION_DISPOSABLE=1 python3 -B /root/treed-input/run.py v0.49.2 $FIRST_TAG $NEXT_TAG" 2>&1 | tee /work/transition.log
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', required=True, type=Path, help='новый выделенный рабочий каталог')
    for name in ('first-runtime', 'first-source', 'next-runtime', 'next-source'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    first, next_tag = package_tag(args.first_runtime), package_tag(args.next_runtime)
    version = lambda tag: tuple(int(part) for part in tag[1:].split('.'))
    if not version('v0.49.2') < version(first) < version(next_tag):
        raise ValueError('Требуются последовательно возрастающие версии')
    work = args.work_dir.resolve()
    if ',' in str(work):
        raise ValueError('Рабочий путь не должен содержать запятую: Docker разбирает её как разделитель')
    work.mkdir(parents=True, exist_ok=False)
    inputs = work / 'input'
    inputs.mkdir()
    local = Path(__file__).resolve().parent
    for name in ('guest-bootstrap.sh', 'run.py', 'klippy-fixture.py', 'sitecustomize.py'):
        shutil.copyfile(local / name, inputs / name)
    for label in ('first', 'next'):
        for kind in ('runtime', 'source'):
            shutil.copyfile(getattr(args, label + '_' + kind), inputs / f'{label}-{kind}.zip')
    download(IMAGE_URL, work / 'debian12.qcow2', IMAGE_SHA256)
    for name, (asset, sha256) in OLD_ASSETS.items():
        download(f'https://github.com/TreeD-Hub/printer-core/releases/download/v0.49.2/{asset}', inputs / name, sha256)
    (work / 'container.sh').write_text(CONTAINER_SCRIPT, encoding='utf-8', newline='\n')
    print('Рабочий каталог:', work, flush=True)
    subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/amd64',
                    '--mount', f'type=bind,source={work},target=/work',
                    '-e', f'FIRST_TAG={first}', '-e', f'NEXT_TAG={next_tag}',
                    DOCKER_IMAGE, 'bash', '/work/container.sh'], check=True)


if __name__ == '__main__':
    main()
