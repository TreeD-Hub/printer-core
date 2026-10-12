"""Локальные калибровки UI; required, API Klipper ce7002bed.

Меняет только принадлежащие процедуре числовые overrides. Сохранение
включаемых секций не поручается SAVE_CONFIG: он не записывает их значения.
"""
import hashlib
import logging
import math
import os
import re
import stat
import tempfile


# Блок 1: Правка конкретных опций без пересборки пользовательского конфига.
def update_overrides(text, section, values):
    if text.startswith('\ufeff'):
        return '\ufeff' + update_overrides(text[1:], section, values)
    lines = text.splitlines(keepends=True)
    newline = '\r\n' if '\r\n' in text else '\n'
    headers = [(i, m.group(1).strip().lower()) for i, line in enumerate(lines)
               if (m := re.match(r'^\s*\[([^\]]+)\]\s*(?:[#;].*)?$', line.rstrip()))]
    found = [i for i, name in headers if name == section.lower()]
    if len(found) > 1:
        raise ValueError('Повторная секция [' + section + '] в local_overrides.cfg')
    if not found:
        if text and not text.endswith(('\n', '\r')):
            text += newline
        return text + newline + '[' + section + ']' + newline + ''.join(
            key + ': ' + value + newline for key, value in values.items())
    start = found[0]
    end = next((i for i, _ in headers if i > start), len(lines))
    for key, value in values.items():
        hits = [(i, m) for i in range(start + 1, end)
                if (m := re.match(r'^(\s*' + re.escape(key)
                                  + r'\s*[:=]\s*)([^#;\r\n]*)(.*)$', lines[i], re.I))]
        if len(hits) > 1:
            raise ValueError('Повторная опция ' + key + ' в local_overrides.cfg')
        if hits:
            i, match = hits[0]
            if i + 1 < end and re.match(r'^\s+[^#;\s]', lines[i + 1]) and not re.match(
                    r'^\s*[\w]+\s*[:=]', lines[i + 1]):
                raise ValueError('Многострочная опция ' + key + ' требует ручной правки')
            suffix = match.group(3).rstrip('\r\n')
            if suffix:
                suffix = ' ' + suffix.lstrip()
            ending = '\r\n' if lines[i].endswith('\r\n') else '\n' if lines[i].endswith('\n') else ''
            lines[i] = match.group(1) + value + suffix + ending
        else:
            if end > 0 and not lines[end - 1].endswith(('\n', '\r')):
                lines[end - 1] += newline
            lines.insert(end, key + ': ' + value + newline)
            end += 1
    return ''.join(lines)


def calibration_hash(value):
    return hashlib.sha256(re.sub(r'\s+', '', str(value)).encode('utf-8')).hexdigest()


class TreedCalibration:
    # Блок 2: Одна точка допуска и публикация действующей настройки экструдера.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.saved = self.printer.load_object(config, 'save_variables')
        main = os.path.abspath(os.path.expanduser(self.printer.get_start_args()['config_file']))
        self.overrides_path = os.path.join(os.path.dirname(main), 'local_overrides.cfg')
        self.running = False
        self.cooling = None
        self.cooling_timer = None
        for name, method in (
                ('TREED_EDDY_SET_OFFSETS', self.cmd_offsets),
                ('TREED_EDDY_HEIGHT_SAVE_MARK', self.cmd_height_mark),
                ('TREED_EDDY_TEMPERATURE_SAVE_MARK', self.cmd_temperature_mark),
                ('TREED_EDDY_TEMPERATURE_PREPARE', self.cmd_temperature_prepare),
                ('TREED_EDDY_TEMPERATURE_COOL_ABORT', self.cmd_cooling_abort),
                ('TREED_EXTRUDER_CALIBRATION_EXTRUDE', self.cmd_extrude),
                ('TREED_EXTRUDER_ROTATION_DISTANCE', self.cmd_rotation)):
            self.gcode.register_command(name, method)
        self.printer.register_event_handler('klippy:ready', self.handle_ready)
        self.printer.register_event_handler('klippy:shutdown', self.cooling_shutdown)

    def status(self, name):
        obj = self.printer.lookup_object(name, None)
        return {} if obj is None else obj.get_status(self.printer.get_reactor().monotonic())

    def get_status(self, eventtime):
        extruder = self.printer.lookup_object('extruder', None)
        stepper = None if extruder is None else extruder.extruder_stepper
        distance = None if stepper is None else stepper.stepper.get_rotation_distance()[0]
        shaper = self.printer.lookup_object('input_shaper', None)
        shaping = {} if shaper is None else {
            key + '_' + axis.axis: value for axis in shaper.get_shapers()
            for key, value in axis.params.get_status().items() if axis.axis in ('x', 'y')}
        # Stock input_shaper/adxl345 не имеют get_status и не попадают в objects.list.
        return dict(running=self.running, rotation_distance=distance, input_shaper=shaping,
                    accelerometer_configured=self.printer.lookup_object('adxl345', None) is not None,
                    offsets_changed=bool(self.saved.allVariables.get('treed_eddy_offsets_changed', 0)))

    def require_idle(self, gcmd, pending=False, temperature_completion=False, cooling=False):
        pause = self.printer.lookup_object('pause_resume', None)
        sd = self.printer.lookup_object('virtual_sdcard', None)
        operation = self.printer.lookup_object('gcode_macro _TREED_OPERATION_STATE', None)
        recovery = self.printer.lookup_object('treed_z_recovery', None)
        sgt = self.printer.lookup_object('treed_sgt_executor', None)
        temperature = self.printer.lookup_object('gcode_macro _TREED_EDDY_TEMPERATURE_STATE', None)
        allowed_phase = 'calibrating' if cooling else 'idle'
        if (self.running or (self.cooling is not None and not cooling) or self.printer.is_shutdown()
                or self.printer.get_state_message()[1] != 'ready'
                or operation is None or operation.variables.get('phase') != allowed_phase
                or self.status('print_stats').get('state') in ('printing', 'paused', 'error')
                or (pause is not None and (pause.pause_command_sent or self.status('pause_resume').get('is_paused')))
                or (sd is not None and sd.is_active())
                or self.status('manual_probe').get('is_active')
                or self.status('temperature_probe btt_eddy').get('in_calibration')
                or (not temperature_completion and not cooling and temperature is not None and temperature.variables.get('active'))
                or (recovery is not None and (recovery.running or recovery.eddy_homing or recovery.mesh_active))
                or (sgt is not None and sgt.running)
                or (pending and self.status('configfile').get('save_config_pending'))):
            raise gcmd.error('Калибровка: нужен готовый принтер без печати, сервиса и несохранённых настроек')

    def preflight(self, gcmd, pending=False):
        self.require_idle(gcmd, pending)
        self.printer.lookup_object('toolhead').wait_moves()
        self.require_idle(gcmd, pending)

    def number(self, gcmd, name, minimum, maximum):
        value = gcmd.get_float(name)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise gcmd.error('%s: число от %g до %g' % (name, minimum, maximum))
        return value

    def parameters(self, gcmd, required, optional=()):
        keys = set(gcmd.get_command_parameters())
        if not set(required) <= keys or keys - set(required) - set(optional):
            raise gcmd.error('Калибровка: неверные параметры команды')

    def save_variable(self, key, value):
        self.gcode.run_script_from_command('SAVE_VARIABLE VARIABLE=' + key + ' VALUE="' + repr(value) + '"')

    # Блок 3: Ограниченное охлаждение до 40C без удержания очереди G-code.
    def temperatures(self, gcmd):
        values = tuple(self.status(name).get('temperature') for name in
                       ('temperature_probe btt_eddy', 'extruder', 'heater_bed'))
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0.
               for value in values):
            raise gcmd.error('Eddy: температуры датчика, сопла или стола неизвестны')
        return values

    def temperature_workflow(self, prompt, cooling):
        self.gcode.run_script_from_command(
            'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=active VALUE=1\n'
            'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=points VALUE=0\n'
            'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=cooling VALUE=%d' % cooling)
        for key, value in (('temperature_done', 0), ('temperature_hash', ''),
                           ('temperature_points', 0), ('active_step', 'temperature'),
                           ('operator_prompt', prompt)):
            self.save_variable('treed_eddy_' + key, value)

    def start_temperature_series(self, gcmd):
        timeout = self.status('idle_timeout')['idle_timeout']
        self.gcode.run_script_from_command(
            'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE '
            'VARIABLE=original_idle_timeout VALUE=%g' % timeout)
        try:
            self.temperature_workflow('temperature_points', 0)
            self.gcode.run_script_from_command(
                'G28\nSET_IDLE_TIMEOUT TIMEOUT=36000\n'
                'TEMPERATURE_PROBE_CALIBRATE PROBE=btt_eddy TARGET=56 STEP=4')
        except Exception:
            try:
                if self.status('temperature_probe btt_eddy').get('in_calibration'):
                    self.gcode.run_script_from_command('ABORT')
                self.gcode.run_script_from_command(
                    'SET_IDLE_TIMEOUT TIMEOUT=%g\n'
                    'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=active VALUE=0' % timeout)
                self.save_variable('treed_eddy_operator_prompt', 'none')
            except Exception:
                logging.exception('Eddy: восстановление после отказа запуска не подтверждено')
                self.printer.invoke_shutdown('Eddy: ошибка запуска температурной серии')
            raise

    def cmd_temperature_prepare(self, gcmd):
        self.parameters(gcmd, ())
        self.preflight(gcmd, pending=True)
        if (self.saved.allVariables.get('treed_eddy_primary_done') != 1
                or self.saved.allVariables.get('treed_eddy_offsets_changed', 0) == 1):
            raise gcmd.error('Eddy: сначала завершите первичную калибровку высоты')
        temperatures = self.temperatures(gcmd)
        if max(temperatures) <= 40.:
            self.gcode.run_script_from_command('M104 S0\nM140 S0')
            self.start_temperature_series(gcmd)
            return
        speed = self.status('fan').get('speed')
        timeout = self.status('idle_timeout').get('idle_timeout')
        if (not isinstance(speed, (int, float)) or not math.isfinite(speed) or not 0. <= speed <= 1.
                or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0.):
            raise gcmd.error('Eddy: состояние обдува или idle timeout неизвестно')
        reactor = self.printer.get_reactor()
        now = reactor.monotonic()
        self.cooling = dict(start=now, check=now + 60., temperatures=temperatures,
                            fan=speed, timeout=timeout, gcmd=gcmd)
        try:
            self.gcode.run_script_from_command('M104 S0\nM140 S0\nM106 S255\nSET_IDLE_TIMEOUT TIMEOUT=36000')
            self.temperature_workflow('cooling', 1)
            self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=_TREED_OPERATION_STATE VARIABLE=phase VALUE="\'calibrating\'"')
            if self.cooling_timer is None:
                self.cooling_timer = reactor.register_timer(self.cooling_tick, reactor.NEVER)
            reactor.update_timer(self.cooling_timer, now + 5.)
            gcmd.respond_info('Eddy: охлаждение датчика, сопла и стола до 40C; обдув включён')
        except Exception:
            self.finish_cooling()
            raise

    def finish_cooling(self):
        state, self.cooling = self.cooling, None
        if state is None:
            return
        reactor = self.printer.get_reactor()
        if self.cooling_timer is not None:
            reactor.update_timer(self.cooling_timer, reactor.NEVER)
        if self.printer.is_shutdown():
            return
        script = ('M106 S%g\nSET_IDLE_TIMEOUT TIMEOUT=%g\n'
                  'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=active VALUE=0\n'
                  'SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=cooling VALUE=0'
                  % (state['fan'] * 255., state['timeout']))
        operation = self.printer.lookup_object('gcode_macro _TREED_OPERATION_STATE')
        if operation.variables.get('phase') == 'calibrating':
            script += '\nSET_GCODE_VARIABLE MACRO=_TREED_OPERATION_STATE VARIABLE=phase VALUE="\'idle\'"'
        try:
            self.gcode.run_script_from_command(script)
            self.save_variable('treed_eddy_operator_prompt', 'none')
        except Exception:
            logging.exception('Eddy: восстановление обдува и idle timeout не подтверждено')
            self.printer.invoke_shutdown('Eddy: ошибка завершения охлаждения')
            raise

    def cooling_tick(self, eventtime):
        reactor = self.printer.get_reactor()
        with self.gcode.get_mutex():
            state = self.cooling
            if state is None:
                return reactor.NEVER
            try:
                gcmd = state['gcmd']
                self.require_idle(gcmd, pending=True, cooling=True)
                if (self.saved.allVariables.get('treed_eddy_primary_done') != 1
                        or self.saved.allVariables.get('treed_eddy_offsets_changed', 0) == 1):
                    raise gcmd.error('Eddy: первичная калибровка высоты изменилась во время охлаждения')
                temperatures = self.temperatures(gcmd)
                now = reactor.monotonic()
                if max(temperatures) <= 40.:
                    self.finish_cooling()
                    self.preflight(gcmd, pending=True)
                    self.start_temperature_series(gcmd)
                    return reactor.NEVER
                if now - state['start'] >= 600.:
                    raise gcmd.error('Eddy: охлаждение до 40C не завершилось за 10 минут')
                if now >= state['check']:
                    names = ('датчик', 'сопло', 'стол')
                    stalled = [name for name, previous, current in zip(names, state['temperatures'], temperatures)
                               if current > 40. and current > previous - .2]
                    if stalled:
                        raise gcmd.error('Eddy: за минуту температура не снизилась на 0.2C: ' + ', '.join(stalled))
                    state.update(check=now + 60., temperatures=temperatures)
                return now + 5.
            except Exception as exc:
                try:
                    self.finish_cooling()
                except Exception:
                    logging.exception('Eddy: восстановление после охлаждения не подтверждено')
                    self.printer.invoke_shutdown('Eddy: ошибка завершения охлаждения')
                self.gcode.respond_raw('!! ' + str(exc))
                return reactor.NEVER

    def cmd_cooling_abort(self, gcmd):
        self.parameters(gcmd, ())
        if self.cooling is None:
            raise gcmd.error('Eddy: нет активного охлаждения')
        self.finish_cooling()
        self.save_variable('treed_eddy_active_step', 'not_started')
        gcmd.respond_info('Eddy: охлаждение отменено')

    def cooling_shutdown(self):
        self.finish_cooling()

    # Блок 4: Атомарная запись существующего overrides; ошибка сохраняет старый файл.
    def write_overrides(self, gcmd, section, values):
        path = self.overrides_path
        temporary = None
        try:
            if os.path.islink(path):
                raise ValueError('local_overrides.cfg — ссылка; требуется ручная правка')
            old_stat = os.stat(path) if os.path.exists(path) else None
            if old_stat is not None and not stat.S_ISREG(old_stat.st_mode):
                raise ValueError('local_overrides.cfg должен быть обычным файлом')
            if old_stat is None:
                previous = ''
            else:
                with open(path, encoding='utf-8', newline='') as stream:
                    previous = stream.read()
            updated = update_overrides(previous, section, values)
            fd, temporary = tempfile.mkstemp(prefix='.treed-calibration-', dir=os.path.dirname(path))
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
                stream.write(updated)
                stream.flush()
                os.fsync(stream.fileno())
            if old_stat is not None:
                os.chmod(temporary, stat.S_IMODE(old_stat.st_mode))
                if hasattr(os, 'chown'):
                    os.chown(temporary, old_stat.st_uid, old_stat.st_gid)
                with open(path, encoding='utf-8', newline='') as stream:
                    current = stream.read()
                current_stat = os.stat(path)
                if (os.path.islink(path) or current != previous
                        or (current_stat.st_ino, current_stat.st_mtime_ns, current_stat.st_size)
                        != (old_stat.st_ino, old_stat.st_mtime_ns, old_stat.st_size)):
                    raise ValueError('local_overrides.cfg изменён другим редактором; повторите после проверки')
            elif os.path.exists(path):
                raise ValueError('local_overrides.cfg создан другим редактором; повторите после проверки')
            os.replace(temporary, path)
            temporary = None
        except (OSError, ValueError, UnicodeError) as exc:
            raise gcmd.error('Калибровка не записана: ' + str(exc)) from exc
        finally:
            if temporary is not None:
                os.unlink(temporary)

    # Блок 5: XY проверяется по реальным scan/screw координатам до инвалидирования.
    def validate_geometry(self, gcmd, x, y):
        th = self.status('toolhead')
        lower, upper = th['axis_minimum'], th['axis_maximum']
        mesh = self.printer.lookup_object('gcode_macro _TREED_EDDY_MESH_CFG').variables
        offsets = (x, y)
        for axis, index in (('x', 0), ('y', 1)):
            for bound in ('min', 'max'):
                point = float(mesh['scan_' + bound + '_' + axis])
                if not lower[index] <= point - offsets[index] <= upper[index]:
                    raise gcmd.error('Eddy offset: scan area требует выхода сопла за пределы ' + axis.upper())
        screws = self.status('configfile')['settings']['screws_tilt_adjust']
        for key, point in screws.items():
            if re.fullmatch(r'screw\d+', key):
                coords = point if isinstance(point, (list, tuple)) else [float(p) for p in point.split(',')]
                for i in (0, 1):
                    if not lower[i] <= float(coords[i]) + offsets[i] <= upper[i]:
                        raise gcmd.error('Eddy offset: датчик выходит за пластину у ' + key)

    def cmd_offsets(self, gcmd):
        self.parameters(gcmd, ('X', 'Y'))
        x, y = self.number(gcmd, 'X', -100., 100.), self.number(gcmd, 'Y', -100., 100.)
        self.preflight(gcmd, pending=True)
        self.validate_geometry(gcmd, x, y)
        self.save_variable('treed_eddy_offsets_changed', 1)
        self.save_variable('treed_eddy_height_hash', '')
        self.save_variable('treed_eddy_temperature_hash', '')
        for field in ('primary', 'temperature', 'z0', 'screws', 'mesh', 'required'):
            self.save_variable('treed_eddy_' + field + '_done', 0)
        self.write_overrides(gcmd, 'probe_eddy_current btt_eddy',
                             dict(x_offset=format(x, '.9g'), y_offset=format(y, '.9g')))
        gcmd.respond_info('Eddy XY записаны; требуется повторная калибровка высоты и температуры')
        self.gcode.run_script_from_command('RESTART')

    # Блок 6: Stamp сохраняется после ACCEPT, проверяется только при новом ready.
    def cmd_height_mark(self, gcmd):
        self.parameters(gcmd, ())
        self.require_idle(gcmd)
        config = self.status('configfile')
        section = 'probe_eddy_current btt_eddy'
        curve = (config.get('save_config_pending_items', {}).get(section) or {}).get('calibrate')
        if not curve or not str(curve).strip():
            raise gcmd.error('Eddy: новая кривая после ACCEPT отсутствует; сохранение не подтверждено')
        probe = config['settings'][section]
        self.save_variable('treed_eddy_offsets_changed', 1)
        self.save_variable('treed_eddy_height_hash', calibration_hash(curve))
        self.save_variable('treed_eddy_height_x', float(probe['x_offset']))
        self.save_variable('treed_eddy_height_y', float(probe['y_offset']))

    def handle_ready(self):
        self.printer.get_reactor().register_callback(self.verify_height)

    def cmd_temperature_mark(self, gcmd):
        self.parameters(gcmd, ())
        self.require_idle(gcmd, temperature_completion=True)
        pending = self.status('configfile').get('save_config_pending_items', {}).get('temperature_probe btt_eddy') or {}
        curve, minimum = pending.get('drift_calibration'), pending.get('drift_calibration_min_temp')
        if not curve or minimum is None or not math.isfinite(float(minimum)):
            state = self.printer.lookup_object('gcode_macro _TREED_EDDY_TEMPERATURE_STATE').variables
            self.gcode.run_script_from_command('SET_IDLE_TIMEOUT TIMEOUT=%g' % state['original_idle_timeout'])
            self.gcode.run_script_from_command('SET_GCODE_VARIABLE MACRO=_TREED_EDDY_TEMPERATURE_STATE VARIABLE=active VALUE=0')
            self.save_variable('treed_eddy_temperature_done', 0)
            raise gcmd.error('Eddy: свежий результат температурной серии отсутствует; fit/сохранение не подтверждены')
        self.save_variable('treed_eddy_temperature_done', 0)
        self.save_variable('treed_eddy_temperature_hash', calibration_hash(curve))
        self.save_variable('treed_eddy_temperature_min', float(minimum))

    def verify_height(self, eventtime):
        if self.printer.is_shutdown():
            return
        with self.gcode.get_mutex():
            values = self.saved.allVariables
            temperature = self.status('configfile').get('config', {}).get('temperature_probe btt_eddy', {})
            try:
                if (values.get('treed_eddy_temperature_hash')
                        and values.get('treed_eddy_temperature_done') == 0
                        and temperature.get('drift_calibration')
                        and calibration_hash(temperature['drift_calibration']) == values['treed_eddy_temperature_hash']
                        and float(temperature['drift_calibration_min_temp']) == values['treed_eddy_temperature_min']):
                    self.save_variable('treed_eddy_temperature_done', 1)
            except (KeyError, TypeError, ValueError):
                logging.warning('Eddy: сохранённая температурная серия не подтверждена')
            if values.get('treed_eddy_offsets_changed') != 1 or not values.get('treed_eddy_height_hash'):
                return
            section = 'probe_eddy_current btt_eddy'
            probe = self.status('configfile').get('config', {}).get(section, {})
            try:
                matches = (bool(probe.get('calibrate'))
                           and calibration_hash(probe['calibrate']) == values['treed_eddy_height_hash']
                           and float(probe['x_offset']) == values['treed_eddy_height_x']
                           and float(probe['y_offset']) == values['treed_eddy_height_y'])
                if matches:
                    self.save_variable('treed_eddy_offsets_changed', 0)
            except (KeyError, TypeError, ValueError):
                logging.warning('Eddy: сохранённая высота/XY не подтверждены')

    # Блок 7: Измерение подачи сохраняет E-режим и возвращает исходный нагрев.
    def cmd_extrude(self, gcmd):
        self.parameters(gcmd, ('LENGTH', 'SPEED', 'TEMP'))
        self.preflight(gcmd)
        settings = self.status('configfile')['settings']['extruder']
        length = self.number(gcmd, 'LENGTH', 1., min(120., float(settings['max_extrude_only_distance'])))
        speed = self.number(gcmd, 'SPEED', .1, 5.)
        target = self.number(gcmd, 'TEMP', float(settings['min_extrude_temp']), float(settings['max_temp']) - 1.)
        previous_target = self.status('extruder')['target']
        state_saved = False
        self.running = True
        try:
            self.gcode.run_script_from_command('M109 S%g' % target)
            if not self.printer.lookup_object('extruder').get_heater().can_extrude:
                raise gcmd.error('Экструдер: температура недостаточна для подачи')
            self.gcode.run_script_from_command('SAVE_GCODE_STATE NAME=_treed_extruder_calibration')
            state_saved = True
            self.gcode.run_script_from_command('M83\nM220 S100\nM221 S100\nG1 E%g F%g\nM400' % (length, speed * 60.))
            gcmd.respond_info('Подача выполнена: %g мм; измерьте оставшуюся длину метки' % length)
        finally:
            self.running = False
            if not self.printer.is_shutdown():
                try:
                    cancel = self.printer.lookup_object('treed_print_cancel', None)
                    cancelled = cancel is not None and cancel.cancel_pending
                    cleanup = cancel.command_script if cancelled else self.gcode.run_script_from_command
                    if state_saved:
                        cleanup('RESTORE_GCODE_STATE NAME=_treed_extruder_calibration MOVE=0')
                    cleanup('M104 S%g' % (0. if cancelled else previous_target))
                except Exception:
                    logging.exception('Экструдер: режим или исходный нагрев не восстановлен')
                    self.printer.invoke_shutdown('Калибровка экструдера: cleanup не подтверждён')

    def cmd_rotation(self, gcmd):
        self.parameters(gcmd, ('DISTANCE',), ('SAVE',))
        distance = self.number(gcmd, 'DISTANCE', .1, 1000.)
        save = gcmd.get_int('SAVE', 0, minval=0, maxval=1)
        self.preflight(gcmd, pending=bool(save))
        initial = float(self.status('configfile')['settings']['extruder']['rotation_distance'])
        if not .5 * initial <= distance <= 2. * initial:
            raise gcmd.error('Экструдер: поправка более чем вдвое требует проверки механики и ручной настройки')
        if save:
            self.write_overrides(gcmd, 'extruder', dict(rotation_distance=format(distance, '.9g')))
            self.gcode.run_script_from_command('RESTART')
        else:
            self.gcode.run_script_from_command('SET_EXTRUDER_ROTATION_DISTANCE EXTRUDER=extruder DISTANCE=%g' % distance)


def load_config(config):
    return TreedCalibration(config)
