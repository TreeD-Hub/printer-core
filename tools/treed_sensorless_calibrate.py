#!/usr/bin/env python3
# Сбор и анализ StallGuard2 для supervised калибровки X/Y.
# Контур: read-only для конфигов; движения только через TREED_SENSORLESS_PROBE.

import argparse
import json
import select
import socket
import statistics
import sys
import time
from pathlib import Path


SOCKET = Path.home() / 'printer_data/comms/klippy.sock'
LOGS = Path.home() / 'printer_data/logs/treed-sensorless'
SPEEDS = (64, 80, 96)
MAX_ATTEMPTS = 40
MAX_SECONDS = 2700
MAX_MISSES = 4


class KlipperClient:
    # Блок 1: Klipper JSON/ETX API и асинхронные пакеты телеметрии.
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.sock.settimeout(1.)
        self.buffer = b''
        self.next_id = 0
        self.samples = {'x': [], 'y': []}
        self.capture = False
        self.state = 'unknown'
        self.cancelled = False

    def close(self):
        self.sock.close()

    def _read(self):
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return []
        if not chunk:
            raise RuntimeError('Klipper API socket closed')
        self.buffer += chunk
        parts = self.buffer.split(b'\x03')
        self.buffer = parts.pop()
        messages = []
        for part in parts:
            if not part:
                continue
            msg = json.loads(part)
            if msg.get('stream') == 'sg' and self.capture:
                self.samples[msg['motor']].extend(
                    msg.get('params', {}).get('data', []))
            if msg.get('stream') == 'state':
                status = msg.get('params', {}).get('status', {})
                self.state = status.get('webhooks', {}).get('state', self.state)
                if (status.get('print_stats', {}).get('state') in
                        ('printing', 'paused', 'cancelled') or
                        status.get('pause_resume', {}).get('is_paused')):
                    self.cancelled = True
            messages.append(msg)
        return messages

    def call(self, method, params=None, timeout=30):
        self.next_id += 1
        ident = self.next_id
        req = {'id': ident, 'method': method, 'params': params or {}}
        self.sock.sendall(json.dumps(req).encode() + b'\x03')
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for msg in self._read():
                if msg.get('id') == ident:
                    if 'error' in msg:
                        raise RuntimeError(msg['error'].get('message', str(msg['error'])))
                    return msg['result']
            if self.state not in ('unknown', 'ready'):
                raise RuntimeError('Klipper left ready state: ' + self.state)
            if self.cancelled:
                raise RuntimeError('print/pause/cancel interrupted calibration')
        raise TimeoutError(method + ' timed out')

    def drain(self, seconds=.65):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._read()

    def prompt(self, message):
        print(message, end='', flush=True)
        while True:
            readable, _, _ = select.select([sys.stdin, self.sock], [], [], 1.)
            if self.sock in readable:
                self._read()
                if self.state not in ('unknown', 'ready') or self.cancelled:
                    raise RuntimeError('printer interrupted calibration')
            if sys.stdin in readable:
                return sys.stdin.readline().strip()

    def status(self):
        return self.call('objects/query', {'objects': {
            'webhooks': None, 'toolhead': ['homed_axes', 'position'],
            'gcode_move': ['absolute_coordinates'],
            'print_stats': ['state'], 'pause_resume': ['is_paused'],
            'virtual_sdcard': ['is_active'], 'treed_motor_sensorless': None,
        }})['status']

    def script(self, script, timeout=90):
        return self.call('gcode/script', {'script': script}, timeout=timeout)


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        raise ValueError('empty telemetry')
    return ordered[int((len(ordered) - 1) * fraction)]


# Блок 2: Отдельный результат DIAG/геометрии и качество SG-сигнала.
def telemetry_stats(samples, start, end):
    valid = [(float(t), int(sg), int(cs)) for t, sg, cs in samples
             if start <= t <= end and 0 <= sg <= 1023 and 0 <= cs <= 31]
    if len(valid) < 20:
        return {'valid': False, 'samples': len(valid), 'reason': 'too_few_samples'}
    cut = max(1, int(len(valid) * .8))
    free = [row[1] for row in valid[:cut]]
    near = [row[1] for row in valid[cut:]]
    median = statistics.median(free)
    mad = statistics.median(abs(value - median) for value in free)
    free_p10 = percentile(free, .1)
    free_p5 = percentile(free, .05)
    near_p10 = percentile(near, .1)
    margin = free_p10 - near_p10
    return {'valid': free_p10 >= 100 and near_p10 <= 100 and margin >= 100,
            'samples': len(valid), 'free_median': median,
            'free_min': min(free), 'free_max': max(free),
            'free_p5': free_p5, 'free_p10': free_p10, 'free_mad': mad,
            'near_p10': near_p10, 'margin': margin,
            'cs_actual_median': statistics.median(row[2] for row in valid)}


def choose_candidate(trials):
    # Центр подтверждённого окна между false и missed boundary.
    cold = [t for t in trials if t['phase'] == 'cold' and t['stage'] == 'sweep']
    good = {(t['speed'], t['sgt']) for t in cold
            if t['outcome'] == 'pass' and t['confirmed'] == 'pass'
            and t['telemetry']['valid']}
    sgts = sorted(sgt for speed, sgt in good if speed == 80)
    windows = []
    for sgt in sgts:
        if not windows or sgt != windows[-1][-1] + 2:
            windows.append([sgt])
        else:
            windows[-1].append(sgt)
    ranked = []
    for window in windows:
        if len(window) < 3:
            continue
        false_edge = any(t['speed'] == 80 and t['sgt'] < window[0]
                         and t['outcome'] == 'false_trigger'
                         and t['confirmed'] == 'false_trigger' for t in cold)
        missed_edge = any(t['speed'] == 80 and t['sgt'] > window[-1]
                          and t['outcome'] == 'missed_stall'
                          and t['confirmed'] == 'missed_stall' for t in cold)
        if not false_edge or not missed_edge:
            continue
        centers = {window[(len(window) - 1) // 2], window[len(window) // 2]}
        for sgt in centers:
            if all((speed, sgt) in good for speed in (64, 96)):
                margin = next(t['telemetry']['margin'] for t in cold
                              if t['speed'] == 80 and t['sgt'] == sgt)
                ranked.append((len(window), margin, sgt))
    return max(ranked)[2] if ranked else None


def fine_verified(trials, sgt):
    return all(any(t['stage'] == 'fine' and t['speed'] == 80
                   and t['sgt'] == sgt + delta and t['outcome'] == 'pass'
                   and t['confirmed'] == 'pass' and t['telemetry']['valid']
                   for t in trials) for delta in (-1, 1))


def recommendation(trials, sgt):
    if sgt is None or not fine_verified(trials, sgt):
        return None
    verified = [t for t in trials if t['stage'] == 'validation'
                and t['speed'] == 80 and t['sgt'] == sgt]
    if any(sum(t['phase'] == phase for t in verified) < 10
           for phase in ('cold', 'warm')):
        return None
    if any(t['outcome'] != 'pass' or t['confirmed'] != 'pass'
           or not t['telemetry']['valid'] for t in verified):
        return None
    return {'homing_speed': 80, 'driver_SGT': sgt,
            'cold_passes': sum(t['phase'] == 'cold' for t in verified),
            'warm_passes': sum(t['phase'] == 'warm' for t in verified),
            'confidence': 'supervised; physical validation required'}


# Блок 3: Ограниченный сбор без записи production-конфигов.
def ensure_ready(status):
    if status['webhooks']['state'] != 'ready':
        raise RuntimeError('Klipper is not ready')
    if status['print_stats']['state'] in ('printing', 'paused'):
        raise RuntimeError('printer is printing or paused')
    if status['pause_resume']['is_paused'] or status['virtual_sdcard']['is_active']:
        raise RuntimeError('printer is busy')
    if status['treed_motor_sensorless']['busy']:
        raise RuntimeError('sensorless probe is busy')
    if not all(axis in status['toolhead']['homed_axes'] for axis in 'xyz'):
        raise RuntimeError('XYZ must be homed and physically verified')


def run_trial(client, axis, speed, sgt, phase, stage, attempts, started):
    if attempts >= MAX_ATTEMPTS or time.monotonic() - started > MAX_SECONDS:
        raise RuntimeError('attempt or duration limit reached')
    status = client.status()
    ensure_ready(status)
    if status['toolhead']['position'][2] < 10.:
        client.script('G90\nG1 Z10 F600\nM400')
    client.script('G90\nG1 X122.5 Y122.5 F3000\nM400')
    for rows in client.samples.values():
        rows.clear()
    client.capture = True
    try:
        client.script('TREED_SENSORLESS_PROBE AXIS=%s SPEED=%s SGT=%s' %
                      (axis, speed, sgt), timeout=40)
        result = client.status()['treed_motor_sensorless']['last_result']
        if result is None:
            raise RuntimeError('probe returned no result')
        client.drain()
    except (RuntimeError, TimeoutError, OSError) as exc:
        raise TrialStop({
            'phase': phase, 'stage': stage, 'axis': axis, 'speed': speed,
            'sgt': sgt, 'outcome': 'error', 'confirmed': 'unknown',
            'error': str(exc), 'telemetry': {'valid': False},
            'samples': list(client.samples[axis.lower()]),
            'peer_samples': list(client.samples['y' if axis == 'X' else 'x']),
            'at': time.time(),
        })
    finally:
        client.capture = False
    samples = [row for row in client.samples[axis.lower()]
               if result['move_start'] <= row[0] <= result['move_end']]
    peer = ('y' if axis == 'X' else 'x')
    peer_samples = [row for row in client.samples[peer]
                    if result['move_start'] <= row[0] <= result['move_end']]
    stats = telemetry_stats(samples, result['move_start'], result['move_end'])
    print('%s %s mm/s SGT=%s: %s, SG samples=%s, margin=%s' % (
        axis, speed, sgt, result['outcome'], stats['samples'],
        stats.get('margin', 'unknown')))
    answer = client.prompt('Физически: [p] упор, [f] остановка в воздухе, '
                           '[m] пропуск stall, [h] давит в упор, '
                           '[c] остановить: ').lower()
    labels = {'p': 'pass', 'f': 'false_trigger',
              'm': 'missed_stall', 'h': 'hard_contact'}
    if answer not in labels:
        raise KeyboardInterrupt('оператор остановил калибровку')
    trial = {'phase': phase, 'stage': stage, 'axis': axis, 'speed': speed,
             'sgt': sgt, 'outcome': result['outcome'],
             'confirmed': labels[answer], 'telemetry': stats,
             'probe': result, 'samples': samples,
             'peer_samples': peer_samples, 'peer_telemetry': telemetry_stats(
                 peer_samples, result['move_start'], result['move_end']),
             'at': time.time()}
    if trial['outcome'] != trial['confirmed'] or trial['outcome'] in (
            'abort', 'error'):
        raise TrialStop(trial)
    if trial['outcome'] == 'missed_stall':
        print('Пропуск DIAG: заново выполните штатный XYZ-home через интерфейс '
              'принтера и физически подтвердите упоры. Движение пока остановлено.')
        if client.prompt('Введите REHOMED после проверки: ') != 'REHOMED':
            raise TrialStop(trial)
        ensure_ready(client.status())
    return trial


class TrialStop(Exception):
    def __init__(self, trial):
        self.trial = trial


def report(axis, trials, run_current=None):
    sgt = choose_candidate(trials)
    rec = recommendation(trials, sgt)
    lines = ['TreeD Sensorless Calibration', 'Axis: ' + axis,
             'Driver: TMC5160', 'Configured run current: %s A' %
             (run_current if run_current is not None else 'unknown'),
             'Trials: %s' % len(trials), 'Candidate SGT: %s' % sgt,
             'Tested speeds: ' + ', '.join(str(speed) for speed in
                                           sorted({t['speed'] for t in trials})),
             'False triggers: %s' % sum(t['confirmed'] == 'false_trigger'
                                        for t in trials),
             'Missed stalls: %s' % sum(t['confirmed'] == 'missed_stall'
                                      for t in trials),
             'Hard contacts: %s' % sum(t['confirmed'] == 'hard_contact'
                                      for t in trials),
             'Recommendation: ' + (json.dumps(rec, ensure_ascii=False)
                                    if rec else 'none; more verified data needed'),
             '', 'Cold sweep matrix: P=pass, F=false, M=missed, H=hard, ?=invalid']
    selected = [t['telemetry'] for t in trials
                if t['stage'] == 'validation' and t['sgt'] == sgt
                and t['telemetry']['valid']]
    if selected:
        lines.extend(('Free SG median: %s' % statistics.median(
                          t['free_median'] for t in selected),
                      'Free SG p10: %s' % statistics.median(
                          t['free_p10'] for t in selected),
                      'Near-stop SG p10: %s' % statistics.median(
                          t['near_p10'] for t in selected),
                      'SG margin median: %s' % statistics.median(
                          t['margin'] for t in selected)))
    sweep = {(t['speed'], t['sgt']): t for t in trials
             if t['phase'] == 'cold' and t['stage'] == 'sweep'}
    sgts = sorted({sgt for _, sgt in sweep})
    lines.append('speed\\SGT ' + ' '.join(str(sgt) for sgt in sgts))
    for speed in SPEEDS:
        marks = []
        for sgt in sgts:
            t = sweep.get((speed, sgt))
            marks.append({'pass': 'P', 'false_trigger': 'F',
                          'missed_stall': 'M', 'hard_contact': 'H'}.get(
                              t['confirmed'], '?')
                         if t else '?')
        lines.append('%s %s' % (speed, ' '.join(marks)))
    lines.extend(('', 'speed / SGT / phase / machine / operator / margin'))
    for t in trials:
        lines.append('%s / %s / %s / %s / %s / %s' % (
            t['speed'], t['sgt'], t['phase'], t['outcome'], t['confirmed'],
            t['telemetry'].get('margin', 'unknown')))
    return '\n'.join(lines) + '\n'


def save_raw(path, trials):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(trials, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description='Supervised TMC5160 X/Y calibration')
    parser.add_argument('--axis', required=True, choices=('X', 'Y'))
    parser.add_argument('--speed', type=int)
    parser.add_argument('--sgt', type=int)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--phase', choices=('cold', 'warm'), default='cold')
    parser.add_argument('--socket', type=Path, default=SOCKET)
    parser.add_argument('--output-dir', type=Path, default=LOGS)
    args = parser.parse_args()
    if (args.speed is None) != (args.sgt is None):
        parser.error('--speed and --sgt must be used together')
    if args.speed is not None and not 40 <= args.speed <= 100:
        parser.error('--speed must be 40..100')
    if args.sgt is not None and not -64 <= args.sgt <= 63:
        parser.error('--sgt must be -64..63')
    if not 1 <= args.repeat <= 10 or (args.repeat != 1 and args.speed is None):
        parser.error('--repeat must be 1..10 and requires --speed/--sgt')
    axis = args.axis
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    raw = args.output_dir / ('%s-%s.json' % (stamp, axis.lower()))
    txt = raw.with_suffix('.txt')
    trials = []
    run_current = None
    started = time.monotonic()
    client = KlipperClient(args.socket)
    was_absolute = True
    try:
        initial = client.status()
        ensure_ready(initial)
        was_absolute = initial['gcode_move']['absolute_coordinates']
        print('Подтвердите, что XYZ реально привязаны, каретка внутри области, '
              'упоры X-min/Y-max свободны, Z можно поднять до 10 мм.')
        if client.prompt('Введите YES для начала: ') != 'YES':
            return 1
        for motor in ('x', 'y'):
            header = client.call('tmc/stallguard_dump', {
                'name': 'stepper_' + motor,
                'response_template': {'stream': 'sg', 'motor': motor}})
            if tuple(header['header']) != ('time', 'sg_result', 'cs_actual'):
                raise RuntimeError('unexpected StallGuard format')
        client.call('objects/subscribe', {
            'objects': {'webhooks': ['state'], 'print_stats': ['state'],
                        'pause_resume': ['is_paused']},
            'response_template': {'stream': 'state'}})
        config = client.call('objects/query', {'objects': {
            'configfile': ['settings']}})['status']['configfile']['settings']
        driver_config = config['tmc5160 stepper_' + axis.lower()]
        base_sgt = int(client.status()['treed_motor_sensorless'][
            'sgt_' + axis.lower()])
        run_current = driver_config['run_current']
        if args.speed is not None:
            for _ in range(args.repeat):
                trials.append(run_trial(client, axis, args.speed, args.sgt,
                                        args.phase, 'validation',
                                        len(trials), started))
                save_raw(raw, trials)
                if (trials[-1]['outcome'] != 'pass' or
                        trials[-1]['confirmed'] != 'pass' or
                        not trials[-1]['telemetry']['valid']):
                    return 2
            return 0
        sgts = range(max(-64, base_sgt - 4), min(63, base_sgt + 4) + 1, 2)
        print('Sweep:', SPEEDS, list(sgts), 'current SGT:', base_sgt)
        for speed in SPEEDS:
            for sgt in sgts:
                trials.append(run_trial(client, axis, speed, sgt, 'cold',
                                        'sweep', len(trials), started))
                save_raw(raw, trials)
                if sum(t['outcome'] == 'missed_stall' for t in trials) >= MAX_MISSES:
                    print('Достигнут лимит пропусков DIAG; серия остановлена.')
                    return 2
        selected = choose_candidate(trials)
        if selected is None:
            print('Устойчивое окно не найдено; рекомендация не выдаётся.')
            return 2
        for offset in (-1, 1):
            trials.append(run_trial(client, axis, 80, selected + offset,
                                    'cold', 'fine', len(trials), started))
            save_raw(raw, trials)
        if not fine_verified(trials, selected):
            print('Соседние SGT не прошли точную проверку.')
            return 2
        for _ in range(10):
            trials.append(run_trial(client, axis, 80, selected, 'cold',
                                    'validation', len(trials), started))
            save_raw(raw, trials)
        print('Проведите безопасный прогрев XY штатными движениями и проверьте '
              'температуру моторов. Ток автоматически не меняется.')
        if client.prompt('Введите WARM после прогрева: ') != 'WARM':
            return 2
        for _ in range(10):
            trials.append(run_trial(client, axis, 80, selected, 'warm',
                                    'validation', len(trials), started))
            save_raw(raw, trials)
        return 0 if recommendation(trials, selected) else 2
    except TrialStop as stop:
        trials.append(stop.trial)
        print('Калибровка остановлена: проба требует проверки оператора.')
        return 2
    except (KeyboardInterrupt, RuntimeError, TimeoutError, OSError) as exc:
        print('Калибровка остановлена:', exc, file=sys.stderr)
        return 2
    finally:
        if not was_absolute and client.state in ('unknown', 'ready'):
            try:
                client.script('G91', timeout=5)
            except (RuntimeError, TimeoutError, OSError):
                pass
        save_raw(raw, trials)
        txt.write_text(report(axis, trials, run_current), encoding='utf-8')
        print('Raw:', raw, 'Report:', txt)
        client.close()


if __name__ == '__main__':
    sys.exit(main())
