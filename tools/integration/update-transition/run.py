#!/usr/bin/env python3
"""Назначение: проверить переход Core с реальным systemd и прерыванием dpkg.
Контур: только одноразовая VM; изменяет пакеты, службы и данные гостя.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import time
import urllib.request
import uuid
import zipfile


# Блок 1: Проверяем подготовленный fixture и фиксируем пользовательские данные.
HOME = Path('/home/radxa/printer_data')
STATE = Path('/var/lib/treed-update/state.json')
RELEASES = Path('/opt/treed-fixture/releases')
MARKER = b'#*# <---------------------- SAVE_CONFIG ---------------------->'
TRANSPORT_LOG = Path('/var/log/treed-fixture-transport.log')


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def data_snapshot():
    paths = [HOME / 'config/local_overrides.cfg', HOME / 'config/treed_variables.cfg',
             HOME / 'system/treed-autoprinter-credentials.json']
    store = HOME / 'system/treed-autoprinter'
    paths.extend(sorted(path for path in store.rglob('*') if path.is_file()))
    if not store.is_dir() or len(paths) == 3:
        raise RuntimeError('Fixture не содержит данных аккаунта')
    result = {}
    for path in [store, *paths]:
        info = path.stat()
        result[str(path)] = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
        if path.is_file():
            result[str(path)]['sha256'] = digest(path)
    printer = (HOME / 'config/printer.cfg').read_bytes()
    if MARKER not in printer:
        raise RuntimeError('Fixture не содержит SAVE_CONFIG')
    result['SAVE_CONFIG'] = hashlib.sha256(printer.split(MARKER, 1)[1]).hexdigest()
    return result


def submit(tag):
    request_id = str(uuid.uuid4())
    body = json.dumps({'requestId': request_id, 'targetId': 'printer-core', 'targetTag': tag}).encode()
    request = urllib.request.Request('http://127.0.0.1:7125/server/treed/update/apply', data=body,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.load(response)
    if result.get('accepted') is not True or result.get('requestId') != request_id:
        raise RuntimeError('Moonraker не принял новую операцию')
    print('Принята операция', result['operationId'], tag, flush=True)
    return result['operationId']


# Блок 2: Прерываем только настоящий dpkg после начала распаковки в cgroup worker.
def interrupt_unpack(operation):
    log = Path('/var/lib/treed-update/core') / operation / 'loader.log'
    if not log.exists() or 'Unpacking ' not in log.read_text(errors='replace'):
        return None
    unit = command('systemctl', 'show', '--property=ControlGroup', '--value', 'treed-update.service')
    processes = Path('/sys/fs/cgroup') / unit.lstrip('/') / 'cgroup.procs'
    if not unit or not processes.is_file():
        return None
    for pid in processes.read_text().split():
        try:
            name = Path('/proc', pid, 'comm').read_text().strip()
            args = Path('/proc', pid, 'cmdline').read_bytes().split(b'\0')
        except OSError:
            continue
        if name == 'dpkg' and b'--unpack' in args:
            try:
                os.kill(int(pid), signal.SIGSTOP)
                status = Path('/proc', pid, 'status').read_text()
                journals = sorted(path for path in Path('/var/lib/dpkg/updates').iterdir()
                                  if re.fullmatch(r'[0-9]+', path.name) and path.is_file())
                if not re.search(r'^State:\s+T', status, re.MULTILINE) or not journals:
                    os.kill(int(pid), signal.SIGCONT)
                    continue
                before = log.read_text(errors='replace').count('packages-core: resuming interrupted dpkg configuration')
                transport_offset = TRANSPORT_LOG.stat().st_size
                evidence = {'operationId': operation, 'dpkgPid': int(pid),
                            'transportOffset': transport_offset,
                            'journals': {path.name: digest(path) for path in journals}}
                Path('/root/treed-fault-evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
                command('systemctl', 'kill', '--kill-whom=all', '--signal=SIGKILL', 'treed-update.service')
            except (OSError, subprocess.SubprocessError):
                try:
                    os.kill(int(pid), signal.SIGCONT)
                except ProcessLookupError:
                    pass
                raise
            print('Worker прерван: dpkg остановлен и числовой журнал сохранён', flush=True)
            return before, transport_offset
    return None


def wait_applied(operation, *, fault, timeout):
    deadline = time.monotonic() + timeout
    fault_markers = None
    previous = None
    while time.monotonic() < deadline:
        try:
            state = json.loads(STATE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            time.sleep(0.5)
            continue
        if state.get('operationId') != operation:
            raise RuntimeError('Операция была заменена')
        current = (state.get('status'), state.get('phase'), state.get('resultCode'))
        if current != previous:
            print('Состояние:', *current, flush=True)
            previous = current
        if fault and fault_markers is None:
            fault_markers = interrupt_unpack(operation)
        if state.get('status') == 'applied' and state.get('busy') is False:
            if fault:
                if fault_markers is None:
                    raise RuntimeError('Установка завершилась без прерывания dpkg')
                log = Path('/var/lib/treed-update/core') / operation / 'loader.log'
                before, transport_offset = fault_markers
                if log.read_text(errors='replace').count('packages-core: resuming interrupted dpkg configuration') <= before:
                    raise RuntimeError('Loader не подтвердил восстановление прерванного dpkg')
                with TRANSPORT_LOG.open('rb') as transport:
                    transport.seek(transport_offset)
                    after = transport.read().decode('utf-8')
                if f"/{state['targetTag']}/" in after or f"/tags/{state['targetTag']}" in after:
                    raise RuntimeError('Восстановление повторно запросило выпуск по сети')
            return
        retry = state.get('retry') or {}
        if state.get('status') in {'rejected', 'rolled_back'} or retry.get('exhausted') is True:
            raise RuntimeError('Обновление или автоматическое восстановление завершилось ошибкой')
        time.sleep(0.5)
    raise TimeoutError('Обновление или автоматическое восстановление не завершилось')


# Блок 3: Проверяем закреплённые байты пакета и готовность после каждого выпуска.
def verify(operation, tag, expected_data):
    archive = RELEASES / tag / 'treed-core-runtime.zip'
    executor = Path('/var/lib/treed-update/executors') / operation / 'printer-core' / tag
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
    if manifest['tag'] != tag or digest(executor / 'package.zip') != digest(archive):
        raise RuntimeError('Сохранённый пакет не совпадает с тестовым release')
    if digest(executor / 'updater.py') != manifest['updater']['sha256']:
        raise RuntimeError('Сохранённый updater не совпадает с envelope')
    pin = json.loads((executor / 'executor.json').read_text(encoding='utf-8'))
    expected_pin = {'operationId': operation, 'targetId': 'printer-core', 'targetTag': tag,
                    'protocol': 1, 'packageSha256': digest(archive),
                    'updaterSha256': manifest['updater']['sha256']}
    if any(pin.get(key) != value for key, value in expected_pin.items()):
        raise RuntimeError('Сохранённая привязка updater не совпадает с операцией')
    verified = json.loads(command('/usr/local/sbin/treed-core-update', 'verify'))
    if verified.get('tag') != tag or data_snapshot() != expected_data:
        raise RuntimeError('Версия или пользовательские данные не совпадают')
    if command('dpkg', '--audit'):
        raise RuntimeError('После обновления остались повреждённые пакеты')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_tag')
    parser.add_argument('first_tag')
    parser.add_argument('next_tag')
    parser.add_argument('--timeout', type=int, default=2400)
    args = parser.parse_args()
    if any(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', tag) is None
           for tag in (args.old_tag, args.first_tag, args.next_tag)):
        raise ValueError('Теги должны иметь вид vX.Y.Z')
    if os.geteuid() != 0 or os.environ.get('TREED_INTEGRATION_DISPOSABLE') != '1':
        raise RuntimeError('Требуется root и явная отметка одноразовой VM')
    command('systemd-detect-virt', '--vm', '--quiet')
    for unit in ('klipper.service', 'moonraker.service', 'treed-update.path'):
        command('systemctl', 'is-active', '--quiet', unit)
    baseline = json.loads(command('/usr/local/sbin/treed-core-update', 'verify'))
    if baseline.get('tag') != args.old_tag:
        raise RuntimeError('Установленный baseline не совпадает с указанным выпуском')
    before = data_snapshot()
    first = submit(args.first_tag)
    wait_applied(first, fault=True, timeout=args.timeout)
    verify(first, args.first_tag, before)
    second = submit(args.next_tag)
    wait_applied(second, fault=False, timeout=args.timeout)
    verify(second, args.next_tag, before)
    print('Переход, автоматическое восстановление и следующее обновление проверены', flush=True)


if __name__ == '__main__':
    main()
