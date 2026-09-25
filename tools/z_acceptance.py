"""Z/Eddy acceptance: явные аппаратные серии и офлайн-сравнение пакетов.

Контур: движения только через run --allow-motion; потеря Z дополнительно
требует --lose-z. Запускать на host принтера через collect_eddy_diagnostic.sh.
Никакой записи настроек, автоматического reboot или повтора после ошибки.
"""
import argparse
import itertools
import json
import math
import re
from pathlib import Path
import statistics
import sys
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.parse import quote

EDDY_Z0_RANGE_MM = 0.05
STAGES = ('bottom_reference', 'x_home', 'y_home', 'eddy_coarse', 'eddy_probe', 'final_z0')
OBJECTS = ('toolhead', 'print_stats', 'pause_resume', 'webhooks', 'configfile',
           'treed_z_recovery', 'gcode_macro _TREED_OPERATION_STATE', 'bed_mesh',
           'mcu', 'mcu EBBCan', 'mcu eddy', 'heater_bed', 'extruder',
           'temperature_probe btt_eddy')


# Блок 1: Расчёты используют сырые измерения, несовместимые сетки отклоняются.
def numbers(values):
    result = [float(v) for v in values]
    if not result or not all(math.isfinite(v) for v in result):
        raise ValueError('Нужны непустые конечные числовые измерения')
    return result


def stats(values):
    values = numbers(values)
    return dict(samples=len(values), median=statistics.median(values), min=min(values),
                max=max(values), range=max(values)-min(values),
                standard_deviation=statistics.stdev(values) if len(values) > 1 else None)


def mesh_stats(meshes):
    if len(meshes) < 2:
        return dict(runs=len(meshes), comparison='pending')
    first = meshes[0]
    matrix = first['probed_matrix']
    if not matrix or not matrix[0]:
        raise ValueError('Пустая mesh')
    shape = (len(matrix), len(matrix[0]))
    flattened = []
    for mesh in meshes:
        rows = mesh['probed_matrix']
        if (len(rows) != shape[0] or any(len(row) != shape[1] for row in rows)
                or any(mesh[key] != first[key] for key in ('mesh_min', 'mesh_max'))):
            raise ValueError('Несовместимые геометрия или размер mesh')
        flattened.append(numbers(v for row in rows for v in row))
    deltas = [a-b for left, right in itertools.combinations(flattened, 2)
              for a, b in zip(left, right)]
    absolute = sorted(abs(v) for v in deltas)
    return dict(runs=len(meshes), pairs=len(meshes)*(len(meshes)-1)//2,
                median_abs_delta_mm=statistics.median(absolute),
                p95_abs_delta_mm=absolute[math.ceil(.95*len(absolute))-1],
                max_abs_delta_mm=max(absolute),
                rms_delta_mm=math.sqrt(statistics.mean(v*v for v in deltas)))


def summarize(result):
    runs = result['z_bottom']['raw']
    valid = [r for r in runs if r.get('result') == 'passed' and r.get('acceptance_valid')]
    result['z_bottom'].update(runs=len(runs), successful_runs=len(valid))
    if valid:
        triggers = [r['trigger_position_mm'] for r in valid if r['trigger_position_mm'] is not None]
        overshoot = stats(r['probes'][0]['overshoot_mm'] for r in valid)
        result['z_bottom'].update(trigger_positions_mm=triggers,
            trigger_position=stats(triggers) if triggers else None,
            overshoot=overshoot, max_overshoot_mm=overshoot['max'])
    eddy = result['eddy_z0']['raw']
    values = [r['z0_mcu_mm'] for r in eddy if r.get('result') == 'passed']
    if values:
        if len({r['motor_epoch'] for r in eddy}) != 1:
            raise ValueError('Во время Z0 серии отключался мотор; общая опора потеряна')
        measured = stats(values)
        result['eddy_z0'].update(values_mm=values, **measured,
            range_mm=measured['range'], range_limit_mm=EDDY_Z0_RANGE_MM,
            repeatability_pass=(len(values) >= 2 and measured['range'] <= EDDY_Z0_RANGE_MM))
    result['mesh'].update(mesh_stats(result['mesh']['raw']))


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


# Блок 2: Синхронный Moonraker API без retry. Только loopback host принтера.
class Client:
    def request(self, path, script=None):
        data = None if script is None else json.dumps(dict(script=script)).encode()
        req = Request('http://127.0.0.1:7125'+path, data=data,
                      headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=1200 if script is not None else 15) as response:
            payload = json.load(response)
        if 'error' in payload:
            raise RuntimeError(str(payload['error']))
        return payload['result']

    def state(self):
        return self.request('/printer/objects/query?'+'&'.join(map(quote, OBJECTS)))['status']

    def command(self, script):
        return self.request('/printer/gcode/script', script)


class Run:
    def __init__(self, package, mode, client):
        self.package, self.client = Path(package), client
        self.path = self.package / 'result.json'
        self.bottom_offset_mm = None
        self.bottom_motor_epoch = None
        if self.path.exists():
            raise ValueError('Пакет уже содержит result.json; нужен независимый run_id')
        self.result = dict(schema_version=1, mode=mode, hardware_accepted=False,
                           started_at=datetime.now(timezone.utc).isoformat(),
                           result='running', evidence_status='pending', commands=[],
                           stages={s: 'not_started' for s in STAGES},
                           z_bottom=dict(raw=[]), eddy_z0=dict(raw=[]), mesh=dict(raw=[]),
                           mesh_diagnostics=dict(raw=[]))
        self.package.mkdir(parents=True, exist_ok=True)
        self.checkpoint()

    def checkpoint(self):
        save(self.path, self.result)

    def require_idle(self, state):
        if (state['webhooks']['state'] != 'ready'
                or state['print_stats']['state'] in ('printing', 'paused', 'error')
                or state['pause_resume']['is_paused']
                or state['gcode_macro _TREED_OPERATION_STATE']['phase'] != 'idle'):
            raise ValueError('Acceptance требует свободный принтер в ready/idle')

    def send(self, script, stage=None, telemetry=None):
        before = self.client.state()
        self.require_idle(before)
        if stage:
            self.result['stages'][stage] = 'running'
        record = dict(script=script, started_at=datetime.now(timezone.utc).isoformat(),
                      before=before, result='running')
        self.result['commands'].append(record)
        self.checkpoint()  # Команда сохраняется до отправки; timeout не вызывает retry.
        try:
            self.client.command(script)
            record['result'] = 'completed'
            if stage:
                self.result['stages'][stage] = 'passed'
        except BaseException as exc:
            record.update(result='failed', error=str(exc))
            if stage:
                self.result['stages'][stage] = 'failed'
            raise
        finally:
            try:
                after = self.client.state()
                record['after'] = after
                if telemetry:
                    key, section = telemetry
                    sample = after['treed_z_recovery'][key]
                    if sample and sample != before['treed_z_recovery'].get(key):
                        self.result[section]['raw'].append(sample)
                        if section == 'eddy_z0':
                            self.result['stages'].update(sample['stages'])
            except Exception as exc:
                record['state_error'] = str(exc)
            record['ended_at'] = datetime.now(timezone.utc).isoformat()
            self.checkpoint()
        try:
            if 'state_error' in record:
                raise RuntimeError('После команды недоступно состояние Klipper')
            if record['after']['webhooks']['state'] != 'ready':
                raise RuntimeError('Klipper перестал быть ready')
            if telemetry:
                sample = record['after']['treed_z_recovery'][telemetry[0]]
                if (sample == before['treed_z_recovery'].get(telemetry[0])
                        or sample.get('result') != 'passed'):
                    raise RuntimeError('Нет нового успешного измерения')
        except BaseException:
            if stage:
                self.result['stages'][stage] = 'failed'
            raise
        return record['after']

    def bottom(self, start=None):
        script = 'TREED_Z_RECOVERY_TEST CONFIRM=1'
        if start is not None:
            script += ' START_Z=%.6f' % start
        after = self.send(script, 'bottom_reference', ('last_run', 'z_bottom'))
        sample = self.result['z_bottom']['raw'][-1]
        sample['acceptance_valid'] = False
        probes = sample.get('probes', [])
        if (len(probes) != 1 or not sample.get('tmc_restored')
                or not isinstance(sample.get('tmc_before'), dict) or not sample['tmc_before']
                or sample.get('tmc_before') != sample.get('tmc_after')):
            self.result['stages']['bottom_reference'] = 'failed'
            raise ValueError('Z-bottom не подтвердил одну пробу и восстановление TMC')
        probe = probes[0]
        try:
            start_mm, trigger, halt = numbers(probe[k] for k in ('start_mm', 'trigger_mm', 'halt_mm'))
            travel, overshoot = numbers(probe[k] for k in ('travel_mm', 'overshoot_mm'))
            bottom = numbers([probe['target_mm']])[0]
        except (KeyError, TypeError, ValueError):
            self.result['stages']['bottom_reference'] = 'failed'
            raise ValueError('Z-bottom не вернул конечные координаты единственной пробы')
        if (probe.get('failure_reason') or probe.get('no_movement') is not False
                or not start_mm < trigger <= halt <= bottom
                or not math.isclose(travel, trigger - start_mm, abs_tol=1e-8)
                or not math.isclose(overshoot, halt - trigger, abs_tol=1e-8)):
            self.result['stages']['bottom_reference'] = 'failed'
            raise ValueError('Z-bottom вернул противоречивые данные первой пробы')
        sample['trigger_position_mm'] = None
        if self.bottom_offset_mm is not None:
            before = sample.get('before_forced_unknown', {})
            if ('z' not in before.get('homed_axes', '')
                    or sample.get('motor_epoch') != self.bottom_motor_epoch
                    or after['treed_z_recovery']['motor_epoch'] != self.bottom_motor_epoch):
                self.result['stages']['bottom_reference'] = 'failed'
                raise ValueError('Потеряна общая координатная опора bottom-серии')
            try:
                before_z = numbers([before['position'][2]])[0]
            except (KeyError, IndexError, TypeError, ValueError):
                self.result['stages']['bottom_reference'] = 'failed'
                raise ValueError('Недоступна исходная известная Z bottom-серии')
            position = before_z + travel + self.bottom_offset_mm
            if not math.isfinite(position):
                self.result['stages']['bottom_reference'] = 'failed'
                raise ValueError('Неконечная позиция первого trigger')
            sample['trigger_position_mm'] = position
            self.bottom_offset_mm = position - bottom
        sample['acceptance_valid'] = True
        self.checkpoint()

    def eddy(self):
        self.send('TREED_EDDY_ACCEPTANCE_HOME CONFIRM=1', telemetry=('last_eddy', 'eddy_z0'))

    def bootstrap(self):
        self.bottom()
        self.send('G28 X', 'x_home')
        self.send('G28 Y', 'y_home')
        self.eddy()

    def mesh(self):
        state = self.send('TREED_EDDY_ACCEPTANCE_MESH CONFIRM=1', telemetry=('last_mesh', 'mesh_diagnostics'))
        diagnostic = state['treed_z_recovery']['last_mesh']
        if (diagnostic['save_profile_restored'] != 1 or diagnostic['pending_config_changed'] != 0
                or diagnostic['mesh_profile_persistence_suppressed'] != 1):
            raise ValueError('Diagnostic mesh persistence fault')
        mesh = diagnostic['mesh']
        rows = mesh['probed_matrix']
        if not rows or not rows[0]:
            raise ValueError('Klipper вернул пустую mesh')
        numbers(v for row in rows for v in row)
        self.result['mesh']['raw'].append(mesh)
        self.checkpoint()

    def execute(self, count, starts):
        mode = self.result['mode']
        try:
            initial = self.client.state()
            self.require_idle(initial)
            self.result['initial_state'] = initial
            if mode == 'bottom' and 'z' in initial['toolhead']['homed_axes']:
                self.bottom_offset_mm = 0.
                self.bottom_motor_epoch = initial['treed_z_recovery']['motor_epoch']
            if mode == 'cold-start' and 'z' in initial['toolhead']['homed_axes']:
                raise ValueError('Cold-start требует неизвестную Z после ручной загрузки')
            if mode == 'bottom':
                for index in range(count):
                    self.bottom(starts[index % len(starts)] if starts else None)
            elif mode in ('bootstrap', 'cold-start'):
                self.bootstrap()
                if mode == 'cold-start':
                    self.mesh()
            else:
                self.send('G28')
                for _ in range(count):
                    self.eddy() if mode == 'z0' else self.mesh()
            summarize(self.result)
            if mode == 'z0' and not self.result['eddy_z0']['repeatability_pass']:
                raise ValueError('Eddy Z0 range превышает acceptance criterion')
            self.result['result'] = 'measured_pass'
        except BaseException as exc:
            self.result.update(result='failed', error=str(exc), error_kind='measurement_or_command_error')
            if any(s.get('result') == 'fault' for s in self.result['mesh_diagnostics']['raw']):
                self.result.update(result='fault', error_kind='mesh_persistence_fault')
            # Сохраняем и неполную серию; позднейшие стадии не запускаются.
            try:
                summarize(self.result)
            except Exception as summary_error:
                self.result['summary_error'] = str(summary_error)
        finally:
            self.result['ended_at'] = datetime.now(timezone.utc).isoformat()
            self.checkpoint()
        return self.result['result'] == 'measured_pass'


# Блок 3: Evidence всегда привязан к сессии. Отсутствие счётчиков не равно нулю ошибок.
def counter_delta(before, after):
    if not isinstance(before, int) or not isinstance(after, int) or after < before:
        return None
    return after - before


def finalize(package):
    package = Path(package)
    issues = []
    def load(relative):
        try:
            return read(package / relative)
        except (ValueError, OSError) as exc:
            issues.append(relative+': '+str(exc))
            return {}
    manifest = dict(line.split('=', 1) for line in (package/'manifest.env').read_text().splitlines() if '=' in line)
    states = [load('moonraker/acceptance-'+side+'.json').get('result', {}).get('status', {})
              for side in ('before', 'after')]
    if (package/'result.json').exists():
        result = read(package/'result.json')
    else:
        # Включая отказ до создания runner и сохранённый single-scan workflow.
        sample = states[1].get('treed_z_recovery', {}).get('last_mesh', {})
        result = dict(schema_version=1, mode=manifest['mode'], result='failed',
                      z_bottom=dict(raw=[]), eddy_z0=dict(raw=[]), mesh=dict(raw=[]),
                      mesh_diagnostics=dict(raw=[]))
        if manifest['mode'] == 'eddy-scan' and manifest['scan_result'] == 'completed' and sample.get('result') == 'passed':
            result['mesh']['raw'].append(sample['mesh'])
            result['mesh_diagnostics']['raw'].append(sample)
            result['result'] = 'measured_pass'
    version_path = package/'versions/printer-core.txt'
    version = version_path.read_text() if version_path.exists() else ''
    match = re.search(r'^commit=([0-9a-f]{40})$', version, re.M)
    result['commit'] = match[1] if match else None
    result['reference_commit'] = manifest.get('reference_commit')
    if not result['commit']:
        issues.append('actual commit unavailable')
    result['run_id'] = manifest.get('run_id')
    boot_path = package/'versions/boot.txt'
    boot = boot_path.read_text() if boot_path.exists() else ''
    result['boot_id'] = next((line.split('=', 1)[1] for line in boot.splitlines() if line.startswith('boot_id=')), None)
    if not result['boot_id']:
        issues.append('boot_id unavailable')
    configs = [s.get('configfile', {}) for s in states]
    result['production_config_unchanged'] = bool(configs[0]) and configs[0] == configs[1]
    if not result['production_config_unchanged']:
        issues.append('runtime config changed or unavailable')
    # process_id и MCU counters нельзя сравнивать через рестарт процесса.
    infos = [load('moonraker/printer-info-'+side+'.json').get('result', {}) for side in ('before', 'after')]
    session = [info.get('process_id') for info in infos]
    result['klipper_session'] = session
    same_session = session[0] is not None and session[0] == session[1]
    if not same_session:
        issues.append('Klipper session changed or unavailable')
    can = [load('can/counters.'+side+'.json') for side in ('before', 'after')]
    def can_count(snapshot, direction):
        if not isinstance(snapshot, list) or not snapshot:
            return None
        return snapshot[0].get('stats64', snapshot[0].get('stats', {})).get(direction, {}).get('errors')
    result['can'] = {direction+'_error_delta': counter_delta(can_count(can[0], direction), can_count(can[1], direction))
                     if same_session else None for direction in ('rx', 'tx')}
    communication_error = any(v is not None and v > 0 for v in result['can'].values())
    if any(v is None for v in result['can'].values()):
        issues.append('CAN counters unavailable or reset')
    result['mcu'] = {}
    for name in ('mcu', 'mcu EBBCan', 'mcu eddy'):
        snapshots = [s.get(name, {}) for s in states]
        result['mcu'][name] = dict(before=snapshots[0], after=snapshots[1])
        for counter in ('bytes_retransmit', 'bytes_invalid'):
            values = [s.get('last_stats', {}).get(counter) for s in snapshots]
            delta = counter_delta(*values) if same_session else None
            result['mcu'][name][counter+'_delta'] = delta
            if delta is None:
                issues.append(name+': '+counter+' unavailable/reset')
            elif delta > 0:
                communication_error = True
    for state in states:
        message = state.get('webhooks', {}).get('state_message', '').lower()
        if any(word in message for word in ('lost communication', 'timer too close', 'canbus', 'unable to connect')):
            communication_error = True
    for relative in ('kernel/messages.interval.txt', 'klipper/klippy.interval.log',
                     'klipper/session-boundaries.txt', 'versions/mcu-live.json'):
        path = package / relative
        if not path.is_file() or not path.stat().st_size:
            issues.append(relative+' unavailable')
        elif relative.endswith('.txt'):
            content = path.read_text(errors='replace')
            if 'unavailable' in content or re.search(r'# exit_code=(?!0\b)\d+', content):
                issues.append(relative+' capture failed')
    status_path = package/'status.txt'
    if status_path.exists():
        issues.extend(status_path.read_text().splitlines())
    diagnostic_meshes = result.get('mesh_diagnostics', {}).get('raw', [])
    mesh_fault = any(s.get('result') == 'fault' for s in diagnostic_meshes)
    flags = dict(save_config_sent=0)
    if diagnostic_meshes:
        flags.update(mesh_profile_persistence_suppressed=int(all(
            s.get('mesh_profile_persistence_suppressed') == 1 for s in diagnostic_meshes)),
            save_profile_restored=int(all(s.get('save_profile_restored') == 1 for s in diagnostic_meshes)),
            pending_config_changed=int(not result['production_config_unchanged'] or any(
                s.get('pending_config_changed') != 0 for s in diagnostic_meshes)))
        if (flags['mesh_profile_persistence_suppressed'] != 1
                or flags['save_profile_restored'] != 1 or flags['pending_config_changed'] != 0
                or any(s.get('result') != 'passed' for s in diagnostic_meshes)):
            issues.append('mesh persistence/restoration fault')
    elif result.get('mesh', {}).get('raw'):
        issues.append('mesh persistence evidence unavailable')
    result.update(flags)
    with (package/'manifest.env').open('a', encoding='utf-8') as handle:
        for key, value in flags.items():
            if key != 'save_config_sent':
                handle.write('%s=%s\n' % (key, value))
    result.update(evidence_issues=issues, evidence_status='incomplete' if issues else 'complete')
    if mesh_fault:
        result.update(result='fault', error_kind='mesh_persistence_fault')
    elif communication_error:
        result.update(result='failed', error_kind='communication_error')
    elif issues:
        result.update(result='inconclusive', error_kind='evidence_incomplete')
    result['hardware_accepted'] = False  # Только ручная приёмка может закрыть аппаратный gate.
    save(package/'result.json', result)
    return result['result'] == 'measured_pass'


def compare(packages):
    results = [read(Path(p)/'result.json') for p in packages]
    if len(results) < 2:
        raise ValueError('Нужны хотя бы два независимых пакета')
    if any(r.get('mode') != 'cold-start' or r.get('evidence_status') != 'complete'
           or r.get('result') != 'measured_pass' for r in results):
        raise ValueError('Сравнение требует полные успешные cold-start пакеты')
    boots = [r['boot_id'] for r in results]
    if len(set(boots)) != len(boots):
        raise ValueError('Пакеты относятся к одной загрузке')
    configs = [r['initial_state']['configfile'] for r in results]
    if any(c != configs[0] for c in configs[1:]) or len({r['commit'] for r in results}) != 1:
        raise ValueError('Изменились commit или runtime config')
    return dict(schema_version=1, hardware_accepted=False, boot_ids=boots,
                packages=[str(p) for p in packages],
                mesh=mesh_stats([r['mesh']['raw'][0] for r in results]),
                bottom_overshoot=stats(r['z_bottom']['raw'][0]['probes'][0]['overshoot_mm']
                                       for r in results),
                z0_note='Абсолютные MCU Z0 между загрузками несопоставимы')


# Блок 4: Явные разрешения движения и потери координаты только у run.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    run = commands.add_parser('run')
    run.add_argument('--mode', choices=('bottom', 'bootstrap', 'z0', 'mesh', 'cold-start'), required=True)
    run.add_argument('--package', type=Path, required=True)
    run.add_argument('--runs', type=int, default=10)
    run.add_argument('--starts', type=float, nargs='*', default=[])
    run.add_argument('--allow-motion', action='store_true')
    run.add_argument('--lose-z', action='store_true')
    finish = commands.add_parser('finalize')
    finish.add_argument('--package', type=Path, required=True)
    comparison = commands.add_parser('compare')
    comparison.add_argument('packages', nargs='+')
    comparison.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'finalize':
        return 0 if finalize(args.package) else 1
    if args.action == 'compare':
        if args.output.exists():
            parser.error('Файл сравнения уже существует')
        save(args.output, compare(args.packages))
        return 0
    if not args.allow_motion or (args.mode in ('bottom', 'bootstrap', 'cold-start') and not args.lose_z):
        parser.error('Нужны --allow-motion и для потери Z отдельный --lose-z')
    if not 2 <= args.runs <= 100 or any(not math.isfinite(v) or not 0 < v <= 250 for v in args.starts):
        parser.error('runs должен быть 2..100, starts — конечные Z в (0,250]')
    if args.starts and args.mode != 'bottom':
        parser.error('--starts применяется только к bottom')
    return 0 if Run(args.package, args.mode, Client()).execute(args.runs, args.starts) else 1


if __name__ == '__main__':
    sys.exit(main())
