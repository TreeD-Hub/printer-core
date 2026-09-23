# Измерение вибраций двигателей TreeD поверх закреплённого Klipper.
# Copyright (C) 2026 TreeD contributors
# SPDX-License-Identifier: GPL-3.0-only
import hashlib
import datetime
import json
import math
import os
import tempfile

from . import treed_motor_math as motor_math
from . import treed_motor_wave as motor_wave
from . import homing as klipper_homing


ALGORITHM = 'mslut-xy-phase-h4-v3'
PHASE_MODEL = 'delta=s4*sin(4t)+c4*cos(4t);radians'
XY_MOTORS = ('stepper_x', 'stepper_y')
MAX_TABLE_VECTOR_DELTA = 16
HOMING_MOVE_ORIGINAL = klipper_homing.HomingMove.homing_move


class TreedMotorCalibration:
    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.xy_speeds = config.getfloatlist('xy_speeds', (100., 150., 200.))
        self.z_speeds = config.getfloatlist('z_speeds', (2., 3.))
        self.xy_accel = config.getfloat('xy_accel', 15000., above=0.)
        self.z_accel = config.getfloat('z_accel', 30., above=0.)
        self.xy_margin = config.getfloat('xy_margin', 25., minval=10.)
        self.z_low = config.getfloat('z_low', 25., minval=15.)
        self.z_high = config.getfloat('z_high', 50., above=self.z_low)
        self.min_cruise = config.getfloat('min_cruise_seconds', 0.2, minval=0.2)
        self.xy_settle = config.getfloat('xy_settle_seconds', 0.15,
                                         minval=0.05)
        limits = (self.xy_speeds + self.z_speeds +
                  (self.xy_accel, self.z_accel, self.xy_margin,
                   self.z_low, self.z_high, self.min_cruise, self.xy_settle))
        if (not self.xy_speeds or not self.z_speeds or
                not all(math.isfinite(value) and value > 0. for value in limits)):
            raise config.error('motor calibration needs finite positive limits')
        self.state = 'idle'
        self.stage = 'idle'
        self.motor = None
        self.progress = 0.
        self.error = None
        self.results = []
        self.characterization = {}
        self.last_verdict = None
        self.cancel_requested = False
        self.timer = None
        self.old_accel = None
        self.toolhead = None
        self.chip = None
        self.kin_steppers = {}
        self.config_fingerprint = None
        self.build_id = None
        self.mcu_builds = {}
        self.drivers = {}
        self.driver_mutated = False
        self.phase_reason = 'required_hardware_or_config_missing'
        self.base_tables = {}
        self.current_tables = {}
        self.profile = None
        self.profile_state = 'missing'
        self.candidate = None
        self.verifying_saved_profile = False
        self.phase_enabled = False
        self.phase_transition = False
        self.flow = None
        self.flow_result = None
        self.mode = None
        self.passes_done = 0
        self._internal_dispatch = False
        self.report_path = os.path.join(
            os.path.dirname(os.path.dirname(
                self.printer.get_start_args()['config_file'])),
            'treed_motor_calibration', 'last_measurement.json')
        self.profile_path = os.path.join(os.path.dirname(self.report_path),
                                         'active_profile.json')
        self.previous_path = os.path.join(os.path.dirname(self.report_path),
                                          'previous_profile.json')
        self.circle_report_path = os.path.join(os.path.dirname(self.report_path),
                                               'last_circle_compare.json')
        self.circle_report = None
        self.circle_profile = None
        self.circle_old_velocity = None
        for name, callback in (
                ('TREED_MOTOR_CALIBRATE', self.cmd_calibrate),
                ('TREED_MOTOR_CALIBRATION_CANCEL', self.cmd_cancel),
                ('TREED_MOTOR_CALIBRATION_STATUS', self.cmd_status),
                ('TREED_MOTOR_PHASE', self.cmd_phase),
                ('TREED_MOTOR_CALIBRATION_SAVE', self.cmd_save),
                ('TREED_MOTOR_CIRCLE_COMPARE', self.cmd_circle_compare)):
            self.gcode.register_command(name, callback)
        self._install_gcode_gate()
        self.printer.register_event_handler('klippy:shutdown', self._shutdown)
        self.printer.register_event_handler('klippy:disconnect', self._shutdown)
        self.printer.register_event_handler('klippy:connect', self._connect)
        self.printer.register_event_handler('stepper_enable:motor_off',
                                            self._motor_off)
        self._install_homing_guard()

    def _connect(self):
        self.chip = self.printer.lookup_object('adxl345', None)
        settings = self.printer.lookup_object('configfile').get_status(
            self.reactor.monotonic()).get('settings', {})
        names = ('printer', 'stepper_x', 'stepper_y', 'stepper_z',
                 'tmc5160 stepper_x', 'tmc5160 stepper_y',
                 'tmc5160 stepper_z', 'adxl345', 'input_shaper',
                 'treed_motor_calibration')
        if all(name in settings for name in names):
            source = json.dumps({name: settings[name] for name in names},
                                sort_keys=True, separators=(',', ':'))
            self.config_fingerprint = hashlib.sha256(
                source.encode('utf-8')).hexdigest()
        sources = [__file__, motor_math.__file__, motor_wave.__file__]
        digest = hashlib.sha256()
        for source in sources:
            with open(source, 'rb') as input_file:
                digest.update(input_file.read())
        digest.update(str(self.printer.get_start_args().get(
            'software_version')).encode('utf-8'))
        self.mcu_builds = {
            name: {key: obj.get_status(self.reactor.monotonic()).get(key)
                   for key in ('mcu_version', 'mcu_build_versions')}
            for name, obj in self.printer.lookup_objects('mcu')}
        digest.update(json.dumps(self.mcu_builds, sort_keys=True).encode('utf-8'))
        self.build_id = digest.hexdigest()
        for motor in XY_MOTORS:
            driver = self.printer.lookup_object('tmc5160 ' + motor, None)
            if driver is None:
                continue
            self.drivers[motor] = driver.mcu_tmc
            fields = driver.mcu_tmc.get_fields()
            if fields.get_field('direct_mode') or fields.get_field('mres') != 4:
                self.phase_reason = 'tmc_not_step_dir_16_microsteps'
            self.base_tables[motor] = {
                reg: fields.registers[reg] for reg in motor_wave.REGISTER_NAMES}
            try:
                motor_wave.decode_table(self.base_tables[motor])
            except ValueError:
                self.phase_reason = 'tmc_base_wave_incompatible'
        self.current_tables = dict(self.base_tables)
        if (len(self.drivers) == 2 and self.phase_reason ==
                'required_hardware_or_config_missing'):
            self.phase_reason = ''
        self._load_profile()
        toolhead = self.printer.lookup_object('toolhead')
        self._install_limit_guard(toolhead)
        for method in ('move', 'drip_move'):
            original = getattr(toolhead, method)

            def guarded_move(newpos, speed, *args, _original=original):
                if self.phase_enabled and not self.phase_transition:
                    limits = (self.circle_profile if getattr(self, 'mode', None) == 'circle' and
                              self.state == 'running' else self.profile)['limits']
                    max_velocity, max_accel = toolhead.get_max_velocity()
                    if (max_velocity > limits['max_velocity'] or
                            max_accel > limits['max_accel']):
                        raise self.printer.command_error(
                            'TreeD motor phase motion exceeds verified limits')
                return _original(newpos, speed, *args)

            setattr(toolhead, method, guarded_move)

    def _install_limit_guard(self, toolhead):
        original = toolhead.set_max_velocities

        def guarded(max_velocity, max_accel, square_corner_velocity,
                    min_cruise_ratio):
            if self.phase_enabled:
                limits = (self.circle_profile if getattr(self, 'mode', None) == 'circle' and
                          self.state == 'running' else self.profile)['limits']
                velocity = (toolhead.max_velocity if max_velocity is None
                            else max_velocity)
                accel = toolhead.max_accel if max_accel is None else max_accel
                if (not math.isfinite(velocity) or not math.isfinite(accel) or
                        velocity > limits['max_velocity'] or
                        accel > limits['max_accel']):
                    raise self.printer.command_error(
                        'TreeD тихий режим: VELOCITY=%.1f ACCEL=%.0f '
                        'выше проверенных лимитов %.1f мм/с и %.0f мм/с²; '
                        'для штатных лимитов сначала выполните '
                        'TREED_MOTOR_PHASE ENABLE=0' % (
                            velocity, accel, limits['max_velocity'],
                            limits['max_accel']))
            return original(max_velocity, max_accel, square_corner_velocity,
                            min_cruise_ratio)

        toolhead.set_max_velocities = guarded

    def _install_homing_guard(self):
        def guarded(hmove, *args, **kwargs):
            if self.phase_enabled:
                raise self.printer.command_error(
                    'disable TreeD motor phase before homing or probing')
            return HOMING_MOVE_ORIGINAL(hmove, *args, **kwargs)

        klipper_homing.HomingMove.homing_move = guarded

    def _install_gcode_gate(self):
        # Общая граница G-code: сервисные команды не вмешиваются между проходами.
        original = self.gcode._process_commands
        allowed = {'M112', 'M105', 'STATUS', 'TREED_MOTOR_CALIBRATION_STATUS',
                   'TREED_MOTOR_CALIBRATION_CANCEL'}

        def command_name(line):
            parts = line.split(';', 1)[0].strip().upper().split()
            if parts and parts[0].startswith('N') and parts[0][1:].isdigit():
                return parts[1] if len(parts) > 1 else ''
            return parts[0] if parts else ''

        def guarded(commands, need_ack=True):
            if self._internal_dispatch:
                return original(commands, need_ack)
            if self.phase_enabled and self.state != 'running':
                for line in commands:
                    if command_name(line) in ('SET_TMC_FIELD', 'SET_TMC_CURRENT',
                                             'INIT_TMC', 'M18', 'M84',
                                             'SET_STEPPER_ENABLE'):
                        raise self.printer.command_error(
                            'disable TreeD motor phase before changing TMC')
                return original(commands, need_ack)
            if self.state != 'running':
                if any(command_name(line) in ('SET_TMC_FIELD',
                                              'SET_TMC_CURRENT')
                       for line in commands):
                    self.driver_mutated = True
                return original(commands, need_ack)
            for line in commands:
                name = command_name(line)
                if name in allowed or not name:
                    original([line], need_ack)
                else:
                    self.gcode.respond_raw('!! TreeD motor calibration busy')
                    original([''], need_ack)

        self.gcode._process_commands = guarded

    def get_status(self, eventtime):
        supported = (self.chip is not None and self.config_fingerprint is not None
                     and len(self.drivers) == 2 and not self.phase_reason
                     and not self.driver_mutated)
        return {'available': self.chip is not None, 'phase_supported': supported,
                'phase_reason': '' if supported else (
                    'tmc_changed_since_startup' if self.driver_mutated else
                    self.phase_reason or 'sensor_or_runtime_config_missing'),
                'phase_backend': 'tmc5160_mslut_step_dir',
                'phase_backend_capabilities': {
                    'backend': motor_wave.BACKEND_CAPABILITIES['backend'],
                    'phase_harmonics': list(
                        motor_wave.BACKEND_CAPABILITIES['phase_harmonics']),
                    'measurable_harmonics': list(
                        motor_wave.BACKEND_CAPABILITIES['measurable_harmonics']),
                    'direction_specific':
                        motor_wave.BACKEND_CAPABILITIES['direction_specific']},
                'direct_mode_supported': False,
                'state': self.state, 'stage': self.stage,
                'motor': self.motor or '', 'progress': self.progress,
                'error': self.error or '', 'profile_state': self.profile_state,
                'profile_reason': (self.profile or {}).get(
                    'verification_failure', ''),
                'phase_enabled': self.phase_enabled,
                 'runtime_driver_dirty': self.driver_mutated,
                 'candidate_ready': self.candidate is not None,
                 'results': self.results, 'report_path': self.report_path,
                 'mode': self.mode or '',
                 'circle_report_path': self.circle_report_path,
                'config_fingerprint': self.config_fingerprint or ''}

    def _shutdown(self, *args):
        self.cancel_requested = True
        self.phase_enabled = False
        if self.profile_state == 'enabled':
            self.profile_state = 'saved'
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
        if self.state == 'running':
            self.state = 'failed'
            self.error = 'klipper_shutdown'
            try:
                if self.mode == 'circle':
                    self.phase_reason = 'tmc_state_unknown'
                    self.circle_report.update(state='failed', error=self.error)
                    self._write_circle_report()
                else:
                    self._write_report()
            except OSError:
                pass
        # После shutdown не отправляем новые команды движения или SPI.

    def _motor_off(self):
        if not self.phase_enabled:
            return
        # На следующем enable штатный TMC helper запишет исходную таблицу.
        for motor in XY_MOTORS:
            self.drivers[motor].get_fields().registers.update(
                self.base_tables[motor])
        self.current_tables = dict(self.base_tables)
        self.phase_enabled = False
        if not (self.mode == 'circle' and self.state == 'running'):
            self.profile_state = 'saved'

    def _atomic_json(self, path, data):
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.motor-', dir=directory)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _validate_profile(self, profile):
        if (not isinstance(profile, dict) or
                not isinstance(profile.get('tables'), dict) or
                not isinstance(profile.get('coefficients'), dict) or
                not isinstance(profile.get('projection'), dict) or
                not isinstance(profile.get('verified'), dict) or
                not isinstance(profile.get('limits'), dict)):
            raise ValueError('profile_structure_invalid')
        if (profile.get('schema') != 3 or profile.get('algorithm') != ALGORITHM
                or profile.get('phase_model') != PHASE_MODEL
                or profile.get('build_id') != self.build_id
                or profile.get('config_fingerprint') != self.config_fingerprint
                or set(profile.get('tables', {})) != set(XY_MOTORS)
                or set(profile.get('projection', {})) != set(XY_MOTORS)
                or profile.get('verified', {}).get('accepted') is not True):
            raise ValueError('profile_stale_or_incomplete')
        if ('verification_failure' in profile and
                (not isinstance(profile['verification_failure'], str) or
                 not profile['verification_failure'])):
            raise ValueError('profile_verification_state_invalid')
        if set(profile.get('coefficients', {})) != set(XY_MOTORS):
            raise ValueError('profile_coefficients_missing')
        for motor, table in profile['tables'].items():
            motor_wave.decode_table(table)
            coefficients = profile['coefficients'][motor]
            expected, metrics = motor_wave.phase_table(
                coefficients, self.base_tables[motor])
            if table != expected or profile['projection'][motor] != metrics:
                raise ValueError('profile_table_mismatch')
        limits = profile.get('limits', {})
        if (not all(isinstance(limits.get(k), (int, float)) and
                    math.isfinite(limits[k]) and limits[k] > 0
                    for k in ('max_velocity', 'max_accel'))
                or limits['max_velocity'] > max(self.xy_speeds)
                or limits['max_accel'] > self.xy_accel):
            raise ValueError('profile_limits_invalid')

    def _load_profile(self):
        self.profile = None
        try:
            with open(self.profile_path, encoding='utf-8') as input_file:
                profile = json.load(input_file)
        except FileNotFoundError:
            self.profile_state = 'missing'
            return
        except (OSError, ValueError):
            self.profile_state = 'invalid'
            return
        try:
            self._validate_profile(profile)
        except (ValueError, TypeError, KeyError):
            self.profile_state = 'stale'
            return
        self.profile = profile
        self.profile_state = ('rejected' if 'verification_failure' in profile
                              else 'saved')

    def _align_motor(self, motor, table, allow_jog=True):
        self.toolhead.wait_moves()
        driver = self.drivers[motor]
        def phase_now():
            raw = driver.get_register_raw('MSCNT')
            if raw['spi_status'] & 0x3:
                raise motor_math.MeasurementError('tmc_fault_before_table_switch')
            return raw['data'] & 1023

        scores = motor_wave.transition_scores(self.current_tables[motor], table)
        phase = phase_now()
        targets = [p for p in range(phase % 16, 1024, 16)
                   if p % 256 and scores[p % 256] <= MAX_TABLE_VECTOR_DELTA]
        if not targets:
            raise motor_math.MeasurementError('tmc_table_transition_too_large')
        if phase % 256 and scores[phase % 256] <= MAX_TABLE_VECTOR_DELTA:
            return phase
        if not allow_jog:
            raise motor_math.MeasurementError('tmc_phase_alignment_requires_motion')
        status = self._ready()
        center = tuple((status['axis_minimum'][i] +
                        status['axis_maximum'][i]) / 2. for i in (0, 1))
        self.toolhead.manual_move((center[0], center[1], None), 50.)
        self.toolhead.wait_moves()
        phase = phase_now()
        if phase % 256 and scores[phase % 256] <= MAX_TABLE_VECTOR_DELTA:
            return phase
        self._phase_jog(motor, 1)
        observed = phase_now()
        delta = (observed - phase) & 1023
        if delta not in (16, 1008):
            raise motor_math.MeasurementError('tmc_phase_direction_unknown')
        direction = 1 if delta == 16 else -1
        steps, target = min((
            (((target - observed) * direction // 16 + 32) % 64 - 32,
             target) for target in targets),
            key=lambda item: (abs(item[0]), scores[item[1] % 256]))
        if steps:
            self._phase_jog(motor, steps)
        if phase_now() != target:
            raise motor_math.MeasurementError('table_alignment_failed')
        return target

    def _phase_jog(self, motor, steps):
        stepper = self.kin_steppers[motor]
        sign = -1 if stepper.get_dir_inverted()[0] else 1
        dx = sign * steps * stepper.get_step_dist() / 2.
        slope = 1 if motor == 'stepper_x' else -1
        pos = self.toolhead.get_position()
        dest = (pos[0] + dx, pos[1] + slope * dx)
        status = self._ready()
        if any(not status['axis_minimum'][i] + self.xy_margin <= dest[i]
               <= status['axis_maximum'][i] - self.xy_margin
               for i in (0, 1)):
            raise motor_math.MeasurementError('table_alignment_out_of_bounds')
        self.toolhead.manual_move((dest[0], dest[1], None), 5.)
        self.toolhead.wait_moves()

    def _switch_table(self, motor, table, allow_jog=True):
        if self.current_tables[motor] == table:
            return
        motor_wave.decode_table(table)
        aligned_phase = self._align_motor(motor, table, allow_jog)
        driver = self.drivers[motor]
        current_before = driver.get_register('MSCURACT')
        def vector(value):
            fields = (value & 511, (value >> 16) & 511)
            return tuple(v - 512 if v & 256 else v for v in fields)
        baseline = vector(current_before)
        try:
            for reg in motor_wave.REGISTER_NAMES:
                driver.set_register(reg, table[reg])
                if any(abs(a - b) > MAX_TABLE_VECTOR_DELTA
                       for a, b in zip(vector(driver.get_register('MSCURACT')),
                                       baseline)):
                    raise motor_math.MeasurementError('tmc_current_vector_changed')
            after = driver.get_register_raw('MSCNT')
            if (after['spi_status'] & 0x3 or
                    after['data'] & 1023 != aligned_phase):
                raise motor_math.MeasurementError('tmc_current_vector_changed')
        except Exception:
            self.printer.invoke_shutdown('TreeD motor table switch failed')
            raise
        driver.get_fields().registers.update(table)
        self.current_tables[motor] = dict(table)

    def _switch_tables(self, tables, allow_jog=True):
        changes = [motor for motor in XY_MOTORS
                   if self.current_tables[motor] != tables[motor]]
        for motor in changes:
            self._switch_table(motor, tables[motor], allow_jog)

    def _check_base_tables(self):
        if self.driver_mutated:
            raise motor_math.MeasurementError('tmc_changed_since_startup')
        for motor in XY_MOTORS:
            fields = self.drivers[motor].get_fields().registers
            if any(fields[reg] != self.base_tables[motor][reg]
                   for reg in motor_wave.REGISTER_NAMES):
                raise motor_math.MeasurementError('tmc_wave_changed_since_startup')

    def _ready(self):
        status = self.toolhead.get_status(self.reactor.monotonic())
        if not all(axis in status['homed_axes'] for axis in 'xyz'):
            raise motor_math.MeasurementError('not_homed')
        pause = self.printer.lookup_object('pause_resume', None)
        if pause is not None and pause.get_status(self.reactor.monotonic())['is_paused']:
            raise motor_math.MeasurementError('printer_paused')
        stats = self.printer.lookup_object('print_stats', None)
        if stats is not None and stats.get_status(self.reactor.monotonic())['state'] in ('printing', 'paused'):
            raise motor_math.MeasurementError('printer_busy')
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if sd is not None and sd.get_status(self.reactor.monotonic())['is_active']:
            raise motor_math.MeasurementError('printer_busy')
        return status

    def _geometry(self, motors):
        status = self._ready()
        lo, hi = status['axis_minimum'], status['axis_maximum']
        xlo, xhi = lo[0] + self.xy_margin, hi[0] - self.xy_margin
        ylo, yhi = lo[1] + self.xy_margin, hi[1] - self.xy_margin
        zlo, zhi = max(lo[2] + 5., self.z_low), min(hi[2] - 5., self.z_high)
        if xlo >= xhi or ylo >= yhi or zlo >= zhi:
            raise motor_math.MeasurementError('unsafe_bounded_area')
        center = ((xlo + xhi) * .5, (ylo + yhi) * .5, zlo)
        half_span = min((xhi - xlo) * .5, (yhi - ylo) * .5)
        jobs = []
        for motor in motors:
            speeds = self.z_speeds if motor == 'stepper_z' else self.xy_speeds
            speed_limit = (self.toolhead.get_kinematics().max_z_velocity
                           if motor == 'stepper_z' else
                           self.toolhead.get_max_velocity()[0])
            accel = (min(self.z_accel, self.old_accel,
                         self.toolhead.get_kinematics().max_z_accel)
                     if motor == 'stepper_z' else
                     min(self.xy_accel, self.old_accel))
            for speed in speeds:
                if speed > speed_limit:
                    raise motor_math.MeasurementError('speed_exceeds_runtime_limit')
                if motor == 'stepper_z':
                    start = (center[0], center[1], zlo)
                    end = (center[0], center[1], zhi)
                    length = zhi - zlo
                else:
                    start, end = motor_math.diagonal(motor, center, half_span, 1)
                    length = math.dist(start, end)
                motor_math.cruise_window(
                    length, speed, accel, self.min_cruise,
                    self.xy_settle if motor in XY_MOTORS else .30)
                jobs.extend(((motor, speed, accel, start, end),
                             (motor, speed, accel, end, start)))
        return jobs

    def _stepper_frequency(self, motor, speed, start, end):
        stepper = self.kin_steppers[motor]
        rotation_distance, steps_per_rotation = stepper.get_rotation_distance()
        section = self.config.getsection(motor)
        full_steps = section.getint('full_steps_per_rotation', 200)
        microsteps = section.getint('microsteps')
        gearing = steps_per_rotation / (full_steps * microsteps)
        motor_speed = motor_math.path_motor_speed(motor, speed, start, end)
        return motor_math.electrical_frequency(
            motor_speed, rotation_distance, full_steps, gearing)

    def _run_pass(self, job):
        motor, speed, accel, start, end = job
        self.motor = motor
        self._ready()
        old_pos = self.toolhead.get_position()
        # XY-переход выполняется на безопасной высоте; Z меняется отдельно.
        if old_pos[2] < start[2]:
            self.toolhead.manual_move((None, None, start[2]), min(3., self.z_speeds[0]))
            self.toolhead.wait_moves()
        self.toolhead.manual_move((start[0], start[1], None), min(30., speed))
        self.toolhead.wait_moves()
        self.toolhead.manual_move((None, None, start[2]), min(3., self.z_speeds[0]))
        self.toolhead.wait_moves()
        self.toolhead.set_max_velocities(None, accel, None, None)
        idle_client = self.chip.start_internal_client()
        try:
            self.toolhead.dwell(1.5)
        finally:
            idle_client.finish_measurements()
        idle = idle_client.get_samples()
        if any(m.get('errors', 0) or m.get('overflows', 0) for m in idle_client.msgs):
            raise motor_math.MeasurementError('sensor_data_loss')
        stepper = self.kin_steppers[motor]
        command_start_steps = stepper.get_mcu_position()
        client = self.chip.start_internal_client()
        try:
            move_start = self.toolhead.get_last_move_time()
            self.toolhead.manual_move(end, speed)
            move_end = self.toolhead.get_last_move_time()
        finally:
            client.finish_measurements()
        samples = client.get_samples()
        if any(m.get('errors', 0) or m.get('overflows', 0) for m in client.msgs):
            raise motor_math.MeasurementError('sensor_data_loss')
        length = math.dist(start, end)
        enter, leave = motor_math.cruise_window(
            length, speed, accel, self.min_cruise,
            self.xy_settle if motor in XY_MOTORS else .30)
        frequency = self._stepper_frequency(motor, speed, start, end)
        inverted = stepper.get_dir_inverted()[0]
        sign = ((1 if end[2] > start[2] else -1) * (-1 if inverted else 1)
                if motor == 'stepper_z' else math.copysign(
                    1, motor_math.motor_component(motor, end[0] - start[0],
                                                  end[1] - start[1], inverted)))
        # Фаза относится к командам микрошагов, а не к положению ротора.
        microsteps = self.config.getsection(motor).getint('microsteps')
        phase_scale = (-1 if inverted else 1) * 2. * math.pi / (4. * microsteps)
        samples = [tuple(sample) + (
            phase_scale * stepper.get_past_mcu_position(sample[0]),)
                   for sample in samples]
        harmonics = motor_math.measure_harmonics(
            samples, idle, move_start + enter, move_start + leave,
            frequency)
        quality = ('valid' if any(h['quality'] == 'valid' for h in harmonics.values())
                   else 'unmeasurable' if all(
                       h['quality'] == 'unmeasurable' for h in harmonics.values())
                   else 'insufficient_signal')
        return {'motor': motor, 'direction': 'positive' if sign > 0 else 'negative',
                'trajectory': ('vertical_z' if motor == 'stepper_z' else
                               'joint_x' if start[1] == end[1] else
                               'isolated_diagonal'),
                'quality': quality,
                'speed_mm_s': speed, 'accel_mm_s2': accel,
                'move_start_print_time': move_start,
                'move_end_print_time': move_end,
                'command_start_steps': command_start_steps,
                'harmonics': harmonics}

    def _run_pass_with_retry(self, job):
        for retry in range(3):
            try:
                result = self._run_pass(job)
                result['sample_gap_retries'] = retry
                return result
            except motor_math.MeasurementError as exc:
                if not str(exc).startswith('dropped_samples:') or retry == 2:
                    raise
                self.gcode.respond_info('TreeD motor sample gap; repeat pass %d/2'
                                        % (retry + 1))

    def _write_report(self):
        self._atomic_json(self.report_path, {
            'schema': 3, 'algorithm': ALGORITHM,
            'phase_model': PHASE_MODEL,
            'state': self.state, 'stage': self.stage,
            'motor': self.motor, 'progress': self.progress, 'error': self.error,
            'config_fingerprint': self.config_fingerprint,
            'build_id': self.build_id, 'results': self.results,
            'speed_characterization': self.characterization,
            'verification': self.last_verdict})

    # Блок 1: отдельный read-only отчёт кругового сравнения.
    def _write_circle_report(self):
        if self.circle_report is not None:
            self._atomic_json(self.circle_report_path, self.circle_report)

    def _verify_circle_tables(self, expected):
        for motor in XY_MOTORS:
            driver = self.drivers[motor]
            for register in motor_wave.REGISTER_NAMES:
                raw = driver.get_register_raw(register)
                if raw['spi_status'] & 0x3 or raw['data'] != expected[motor][register]:
                    raise motor_math.MeasurementError('tmc_table_state_unknown')

    def _circle_geometry(self, requested_radius):
        status = self._ready()
        lo, hi = status['axis_minimum'], status['axis_maximum']
        xlo, xhi = lo[0] + self.xy_margin, hi[0] - self.xy_margin
        ylo, yhi = lo[1] + self.xy_margin, hi[1] - self.xy_margin
        zlo = max(lo[2] + 5., self.z_low)
        if xlo >= xhi or ylo >= yhi or zlo > min(hi[2] - 5., self.z_high):
            raise motor_math.MeasurementError('unsafe_bounded_area')
        center = ((xlo + xhi) / 2., (ylo + yhi) / 2., zlo)
        maximum = min((xhi - xlo) / 2., (yhi - ylo) / 2.)
        radius = requested_radius if requested_radius is not None else .7 * maximum
        if not math.isfinite(radius) or radius < 10. or radius > maximum:
            raise motor_math.MeasurementError('circle_radius_out_of_bounds')
        for clockwise in (True, False):
            for point in motor_math.circle_points(center, radius, clockwise):
                if not xlo <= point[0] <= xhi or not ylo <= point[1] <= yhi:
                    raise motor_math.MeasurementError('circle_out_of_safe_area')
        return center, radius

    # Блок 2: захват ADXL и командной фазы двух двигателей на одном круге.
    def _run_circle_pass(self, speed, direction, repeat, enabled):
        self._ready()
        points = self.circle_paths[direction]
        start = points[0]
        position = self.toolhead.get_position()
        if position[2] < start[2]:
            self.toolhead.manual_move((None, None, start[2]),
                                      min(3., self.z_speeds[0]))
            self.toolhead.wait_moves()
        self.toolhead.manual_move((start[0], start[1], None), min(30., speed))
        self.toolhead.wait_moves()
        self.toolhead.manual_move((None, None, start[2]),
                                  min(3., self.z_speeds[0]))
        self.toolhead.wait_moves()
        expected = (self.circle_profile['tables'] if enabled else self.base_tables)
        self._verify_circle_tables(expected)
        self.toolhead.dwell(1.5)
        steppers = [self.kin_steppers[motor] for motor in XY_MOTORS]
        initial = [stepper.get_mcu_position() for stepper in steppers]
        # История stepcompress уже хранит командный знак; инверсия DIR
        # нужна для электрической фазы TMC, но не для координат CoreXY.
        distances = [stepper.get_step_dist() for stepper in steppers]
        phases = [(-1 if stepper.get_dir_inverted()[0] else 1) *
                  2. * math.pi / (4. * self.config.getsection(motor).getint('microsteps'))
                  for motor, stepper in zip(XY_MOTORS, steppers)]
        frequencies = {}
        for motor, stepper in zip(XY_MOTORS, steppers):
            rotation, steps_per_rotation = stepper.get_rotation_distance()
            section = self.config.getsection(motor)
            full_steps = section.getint('full_steps_per_rotation', 200)
            gearing = steps_per_rotation / (full_steps * section.getint('microsteps'))
            frequencies[motor] = motor_math.electrical_frequency(
                math.sqrt(2.) * speed, rotation, full_steps, gearing)
        client = self.chip.start_internal_client()
        try:
            move_start = self.toolhead.get_last_move_time()
            for point in points[1:]:
                if self.cancel_requested or self.printer.is_shutdown():
                    raise motor_math.MeasurementError('circle_interrupted')
                self.toolhead.manual_move(point, speed)
            move_end = self.toolhead.get_last_move_time()
        finally:
            client.finish_measurements()
        if move_end <= move_start:
            raise motor_math.MeasurementError('circle_print_time_invalid')
        if move_end - move_start >= 25.:
            raise motor_math.MeasurementError('circle_exceeds_step_history')
        if any(m.get('errors', 0) or m.get('overflows', 0)
               for m in client.msgs):
            raise motor_math.MeasurementError('sensor_data_loss')
        samples = []
        for sample in client.get_samples():
            if not move_start <= sample[0] <= move_end:
                continue
            steps = [stepper.get_past_mcu_position(sample[0])
                     for stepper in steppers]
            a, b = [(now - start_steps) * distance
                    for now, start_steps, distance in zip(steps, initial, distances)]
            samples.append(tuple(sample[:4]) +
                           (steps[0] * phases[0], steps[1] * phases[1],
                            start[0] + (a + b) / 2.,
                            start[1] + (a - b) / 2.))
        result = motor_math.circle_analysis(
            samples, self.circle_center, self.circle_radius, frequencies,
            direction == 'CW')
        result['total']['actual_speed_mm_s'] = sum(
            math.dist(left, right) for left, right in zip(points, points[1:])) / (
                move_end - move_start)
        result['total']['speed_source'] = 'commanded_path_over_print_time'
        for index, sector in enumerate(result['sectors']):
            angle = (index + .5) * 2. * math.pi / 16.
            sector['angle_degrees'] = (index + .5) * 22.5
            sector['commanded_motor_velocities_mm_s'] = (
                motor_math.circle_motor_velocities(
                    sector['actual_speed_mm_s'], angle, direction == 'CW'))
        return dict(result, timestamp=datetime.datetime.now(
            datetime.timezone.utc).isoformat(), speed_mm_s=speed,
            direction=direction, repeat=repeat,
            compensation='on' if enabled else 'off',
            move_start_print_time=move_start, move_end_print_time=move_end)

    def _run_circle_with_retry(self, speed, direction, repeat, enabled):
        for retry in range(3):
            try:
                result = self._run_circle_pass(speed, direction, repeat, enabled)
                result['retry_count'] = retry
                return result
            except motor_math.MeasurementError as exc:
                self.circle_report['invalid_passes'].append({
                    'speed_mm_s': speed, 'direction': direction,
                    'repeat': repeat, 'compensation': 'on' if enabled else 'off',
                    'attempt': retry + 1, 'reason': str(exc)})
                if not str(exc).startswith('dropped_samples:') or retry == 2:
                    raise
                self.gcode.respond_info('TreeD circle sample gap; repeat pass %d/2'
                                        % (retry + 1))

    # Блок 3: чередование OFF/ON и парная секторная статистика.
    def _circle_flow(self):
        comparisons = []
        for speed in self.circle_speeds:
            for direction in self.circle_directions:
                pairs = []
                for repeat in range(1, self.circle_repeats + 1):
                    rows = {}
                    for enabled in ((False, True) if repeat % 2 else (True, False)):
                        yield ('tables', self.circle_profile['tables']
                               if enabled else self.base_tables, enabled)
                        rows[enabled] = yield ('circle_pass', speed, direction,
                                               repeat, enabled)
                    pairs.append((rows[False], rows[True]))
                total = motor_math.circle_compare(
                    [(off['total'], on['total']) for off, on in pairs])
                sectors = [motor_math.circle_compare([
                    (off['sectors'][i], on['sectors'][i]) for off, on in pairs])
                    for i in range(16)]
                comparisons.append({'speed_mm_s': speed, 'direction': direction,
                                    'total': total, 'sectors': sectors})
        yield ('tables', self.base_tables, False)
        return comparisons

    def _joint_jobs(self, speeds):
        status = self._ready()
        lo, hi = status['axis_minimum'], status['axis_maximum']
        x = (lo[0] + hi[0]) / 2.
        y = (lo[1] + hi[1]) / 2.
        z = max(lo[2] + 5., self.z_low)
        span = (hi[0] - lo[0]) / 2. - self.xy_margin
        if span <= 0:
            raise motor_math.MeasurementError('unsafe_joint_area')
        a, b = (x - span, y, z), (x + span, y, z)
        accel = min(self.xy_accel, self.old_accel)
        for speed in speeds:
            motor_math.cruise_window(2. * span, speed, accel,
                                      self.min_cruise, self.xy_settle)
            for motor in XY_MOTORS:
                yield (motor, speed, accel, a, b)
                yield (motor, speed, accel, b, a)

    def _collect(self, jobs, label):
        rows = []
        for job in jobs:
            rows.append((yield ('pass', job, label)))
        return rows

    @staticmethod
    def _score(rows, harmonic):
        entries = [r['harmonics']['H%d' % harmonic] for r in rows]
        if any(e['quality'] != 'valid' for e in entries):
            return None
        return sum(e['amplitude_mm_s2'] for e in entries) / len(entries)

    def _measure_flow(self, motors):
        self.stage = 'measuring'
        jobs = self._geometry(motors)
        yield from self._collect(jobs, 'measure')
        return None

    def _search_phase_trials(self, motor, harmonic, jobs, reference, best_score,
                             best, trials, stage):
        self.stage = stage
        for magnitude, phase in trials:
            trial = dict(best)
            trial['s%d' % harmonic] = magnitude * math.cos(phase)
            trial['c%d' % harmonic] = magnitude * math.sin(phase)
            try:
                table, _ = motor_wave.phase_table(trial, self.base_tables[motor])
            except ValueError:
                continue
            yield ('table', motor, table)
            rows = yield from self._collect(jobs, '%s_H%d_%.4f_%.3f' % (
                stage, harmonic, magnitude, phase))
            score = self._score(rows, harmonic)
            if score is None or score >= .95 * best_score:
                continue
            if any(next(row['harmonics']['H%d' % harmonic]
                        ['amplitude_mm_s2'] for row in rows
                        if row['direction'] == direction) >
                   1.05 * stats['mean_mm_s2'] +
                   3. * stats['noise_floor_mm_s2']
                   for direction, stats in reference.items()):
                continue
            best_score, best = score, trial
        return best_score, best

    def _tune_flow(self):
        if len(set(self.xy_speeds)) < 3:
            raise motor_math.MeasurementError('tune_requires_three_xy_speeds')
        speeds = sorted(set(self.xy_speeds))
        holdout = (speeds[0], speeds[-1])
        jobs = self._geometry(XY_MOTORS)
        coeffs = {motor: {'s4': 0., 'c4': 0.}
                  for motor in XY_MOTORS}
        tables = dict(self.base_tables)
        self.stage = 'baseline'
        yield ('tables', tables)
        baseline = yield from self._collect(jobs * 3, 'speed_baseline')
        self.characterization = {}
        for motor in XY_MOTORS:
            for harmonic in motor_wave.BACKEND_CAPABILITIES['measurable_harmonics']:
                choices = []
                speed_profiles = {}
                for speed in speeds:
                    group = [row for row in baseline if row['motor'] == motor
                             and row['speed_mm_s'] == speed]
                    stats = motor_math.baseline_summary(group, harmonic)
                    speed_profiles[str(speed)] = (
                        {'quality': 'valid', 'directions': stats} if stats else
                        {'quality': 'unmeasurable_or_unstable'})
                    if stats is not None:
                        choices.append((speed, stats))
                if not choices:
                    self.characterization['%s:H%d' % (motor, harmonic)] = {
                        'quality': 'unmeasurable_or_unstable',
                        'speeds': speed_profiles}
                    continue
                target = speeds[len(speeds) // 2] if harmonic == 2 else speeds[0]
                speed, stats = min(choices, key=lambda item: abs(item[0] - target))
                self.characterization['%s:H%d' % (motor, harmonic)] = {
                    'quality': 'valid', 'training_speed_mm_s': speed,
                    'directions': stats, 'speeds': speed_profiles}
                if harmonic not in motor_wave.BACKEND_CAPABILITIES['phase_harmonics']:
                    continue
                train_jobs = [j for j in jobs if j[0] == motor and j[1] == speed]
                reference_rows = [row for row in baseline
                                  if row['motor'] == motor and
                                  row['speed_mm_s'] == speed]
                if tables[motor] != self.base_tables[motor]:
                    yield ('table', motor, tables[motor])
                    reference_rows = yield from self._collect(
                        train_jobs * 3, 'training_after_previous_harmonic')
                    stats = motor_math.baseline_summary(reference_rows, harmonic)
                    if stats is None:
                        continue
                best_score = self._score(reference_rows, harmonic)
                best = dict(coeffs[motor])
                best_score, best = yield from self._search_phase_trials(
                    motor, harmonic, train_jobs, stats, best_score, best,
                    ((magnitude, phase) for magnitude in (.01, .02, .04)
                     for phase in (0., math.pi)), 'approximate_magnitude')
                magnitude = math.hypot(best['s%d' % harmonic],
                                       best['c%d' % harmonic])
                if magnitude:
                    best_score, best = yield from self._search_phase_trials(
                        motor, harmonic, train_jobs, stats, best_score, best,
                        ((magnitude, phase) for phase in
                         (0., math.pi / 4., math.pi / 2., 3. * math.pi / 4.,
                          math.pi, 5. * math.pi / 4., 3. * math.pi / 2.,
                          7. * math.pi / 4.)), 'phase_search')
                    magnitude = math.hypot(best['s%d' % harmonic],
                                           best['c%d' % harmonic])
                    phase = math.atan2(best['c%d' % harmonic],
                                       best['s%d' % harmonic])
                    best_score, best = yield from self._search_phase_trials(
                        motor, harmonic, train_jobs, stats, best_score, best,
                        ((magnitude * factor, phase) for factor in (.75, 1.25)
                         if magnitude * factor <= .06), 'magnitude_refinement')
                coeffs[motor] = best
                tables[motor], _ = motor_wave.phase_table(
                    best, self.base_tables[motor])
                yield ('table', motor, tables[motor])
        if all(tables[motor] == self.base_tables[motor] for motor in XY_MOTORS):
            raise motor_math.MeasurementError(
                'no_representable_phase_correction_found')
        self.stage = 'validation'
        validation = []
        selected = None
        for attenuation in (1., .75, .5, .25):
            scaled = {motor: {key: value * attenuation
                              for key, value in coeffs[motor].items()}
                      for motor in XY_MOTORS}
            try:
                trial_tables = {motor: motor_wave.phase_table(
                    scaled[motor], self.base_tables[motor])[0]
                    for motor in XY_MOTORS}
            except ValueError:
                continue
            verdict = yield from self._verify_flow(trial_tables, holdout)
            validation.append({'attenuation': attenuation, 'verdict': verdict})
            if verdict['accepted']:
                selected = (attenuation, scaled, trial_tables)
                break
        if selected is None:
            raise motor_math.MeasurementError('validation_failed: ' +
                                              str(validation[-1] if validation else
                                                  'no_representable_candidate'))
        attenuation, coeffs, tables = selected
        self.stage = 'verifying'
        verdict = yield from self._verify_flow(tables, holdout)
        if not verdict['accepted']:
            raise motor_math.MeasurementError('final_verification_failed: ' +
                                              str(verdict))
        projection = {motor: motor_wave.phase_table(
            coeffs[motor], self.base_tables[motor])[1] for motor in XY_MOTORS}
        return {'schema': 3, 'algorithm': ALGORITHM,
                'phase_model': PHASE_MODEL,
                'build_id': self.build_id,
                'mcu_builds': self.mcu_builds,
                'config_fingerprint': self.config_fingerprint,
                'coefficients': coeffs, 'tables': tables,
                'projection': projection,
                'limits': {'max_velocity': max(holdout),
                           'max_accel': min(self.xy_accel, self.old_accel),
                           'tested_speeds': list(holdout)},
                'attenuation': attenuation,
                'validation': validation, 'verified': verdict}

    def _verify_flow(self, tables, speeds):
        jobs = [j for j in self._geometry(XY_MOTORS) if j[1] in speeds]
        jobs += list(self._joint_jobs(speeds))
        yield ('tables', self.base_tables)
        baseline = yield from self._collect(jobs * 3, 'verification_baseline')
        yield ('tables', tables)
        corrected = yield from self._collect(jobs * 3, 'verification_candidate')
        verdict = motor_math.compare_verification(baseline, corrected)
        self.last_verdict = verdict
        yield ('tables', self.base_tables)
        return verdict

    def _finish(self, state, error=None):
        if self.timer is not None:
            self.reactor.unregister_timer(self.timer)
            self.timer = None
        if self.printer.is_shutdown():
            state, error = 'failed', 'klipper_shutdown'
        if self.mode == 'circle' and not self.printer.is_shutdown():
            try:
                self.toolhead.wait_moves()
            except Exception as exc:
                self.printer.invoke_shutdown('TreeD circle motion stop unknown')
                state, error = 'failed', 'motion_stop_unverified: %s' % exc
        if (not self.printer.is_shutdown() and self.mode in ('tune', 'verify', 'circle')
                and self.current_tables != self.base_tables):
            try:
                self._switch_tables(self.base_tables)
            except Exception as exc:
                self.printer.invoke_shutdown('TreeD motor calibration recovery failed')
                state, error = 'failed', 'table_restore_failed: %s' % exc
        if self.mode == 'circle':
            if not self.printer.is_shutdown():
                try:
                    self._verify_circle_tables(self.base_tables)
                    self.phase_enabled = False
                except Exception as exc:
                    self.printer.invoke_shutdown('TreeD circle stock state unknown')
                    state, error = 'failed', 'table_restore_unverified: %s' % exc
            if self.printer.is_shutdown():
                self.phase_reason = 'tmc_state_unknown'
                state, error = 'failed', error or 'tmc_state_unknown'
        self.state = state
        self.stage = state
        self.error = error
        if (self.mode == 'verify' and self.verifying_saved_profile and
                state in ('verified', 'failed')):
            if self.candidate is self.profile:
                self.candidate = None
            profile = dict(self.profile)
            if state == 'verified':
                profile.pop('verification_failure', None)
            else:
                profile['verification_failure'] = str(error or 'verification_failed')
            try:
                self._atomic_json(self.profile_path, profile)
            except OSError as exc:
                if state == 'failed':
                    self.profile = profile
                    self.profile_state = 'rejected'
                    self.printer.invoke_shutdown(
                        'TreeD motor profile rejection could not be saved')
                self.state = self.stage = 'failed'
                self.error = '%s; profile_state_write_failed: %s' % (
                    self.error or '', exc)
            else:
                self.profile = profile
                self.profile_state = ('saved' if state == 'verified'
                                      else 'rejected')
        if self.mode == 'tune' and state != 'candidate':
            self.candidate = None
        if state in ('measured', 'candidate', 'verified', 'cancelled'):
            self.motor = None
        if self.old_accel is not None:
            if self.mode != 'circle' or not self.printer.is_shutdown():
                try:
                    self.toolhead.set_max_velocities(
                        self.circle_old_velocity if self.mode == 'circle' else None,
                        self.old_accel, None, None)
                except Exception as exc:
                    if self.mode != 'circle':
                        raise
                    self.printer.invoke_shutdown('TreeD circle limit restore failed')
                    self.state = self.stage = 'failed'
                    self.error = 'limit_restore_failed: %s' % exc
            self.old_accel = None
            if self.mode == 'circle':
                self.circle_old_velocity = None
        if self.mode == 'circle':
            self.circle_report.update(state=self.state, stage=self.stage,
                                      error=self.error,
                                      ended_at=datetime.datetime.now(
                                          datetime.timezone.utc).isoformat())
            try:
                self._write_circle_report()
            except OSError as exc:
                self.state = self.stage = 'failed'
                self.error = '%s; report_write_failed: %s' % (self.error or '', exc)
            self.gcode.respond_info('TreeD Motor Compensation Circle Compare: %s%s'
                                    % (self.state, ' (%s)' % self.error
                                       if self.error else ''))
            if self.state == 'compared':
                for row in self.circle_report['comparisons']:
                    metric = row['total'].get('rms_accel_mm_s2', {})
                    delta = metric.get('relative_delta_percent')
                    self.gcode.respond_info('%g mm/s %s: %s (%s)' % (
                        row['speed_mm_s'], row['direction'],
                        '%+.1f%%' % delta if delta is not None else 'n/a',
                        metric.get('verdict')))
                worst = max((
                    (sector['rms_accel_mm_s2']['relative_delta_percent'],
                     row['speed_mm_s'], row['direction'], index,
                     sector['rms_accel_mm_s2']['verdict'])
                    for row in self.circle_report['comparisons']
                    for index, sector in enumerate(row['sectors'])
                    if sector['rms_accel_mm_s2']['relative_delta_percent'] is not None),
                    default=None)
                if worst:
                    self.gcode.respond_info(
                        'Worst sector: %g mm/s / %s / sector %d: %+.1f%% (%s)'
                        % (worst[1], worst[2], worst[3], worst[0], worst[4]))
            return
        try:
            self._write_report()
        except OSError as exc:
            self.state = 'failed'
            self.stage = 'failed'
            self.error = '%s; report_write_failed: %s' % (self.error or '', exc)
            if self.mode == 'tune':
                self.candidate = None
        self.gcode.respond_info('TreeD motor calibration: %s%s' % (
            self.state, ' (%s)' % self.error if self.error else ''))

    def _next(self, eventtime):
        if self.cancel_requested:
            self._finish('cancelled')
            self.timer = None
            return self.reactor.NEVER
        try:
            if self.stage == 'homing':
                with self.gcode.get_mutex():
                    self._internal_dispatch = True
                    try:
                        self.gcode.run_script_from_command('G28')
                    finally:
                        self._internal_dispatch = False
                    if self.mode == 'measure':
                        self.flow = self._measure_flow(self.requested_motors)
                        self.stage = 'measuring'
                    elif self.mode == 'tune':
                        self.flow = self._tune_flow()
                        self.stage = 'baseline'
                    else:
                        self.stage = 'verifying'
                        profile = self.candidate or self.profile
                        self.flow = self._verify_flow(
                            profile['tables'], tuple(profile['limits']['tested_speeds']))
            else:
                action = self.flow.send(self.flow_result)
                self.flow_result = None
                if action[0] == 'pass':
                    result = self._run_pass_with_retry(action[1])
                    result['trial'] = action[2]
                    self.results.append(result)
                    self.flow_result = result
                    self.passes_done += 1
                    self.progress = min(.95, self.passes_done / 120.)
                elif action[0] == 'circle_pass':
                    result = self._run_circle_with_retry(*action[1:])
                    self.circle_report['passes'].append(result)
                    self.flow_result = result
                    self.passes_done += 1
                    self.progress = min(.95, self.passes_done /
                                        self.circle_total_passes)
                elif action[0] == 'table':
                    self._switch_table(action[1], action[2])
                elif action[0] == 'tables':
                    self._switch_tables(action[1],
                                        allow_jog=self.mode != 'circle')
                    if self.mode == 'circle':
                        self._verify_circle_tables(action[1])
                        self.phase_enabled = action[2]
        except StopIteration as done:
            self.progress = 1.
            if self.mode == 'circle':
                self.circle_report['comparisons'] = done.value
                state = 'compared'
            elif self.mode == 'measure':
                state = ('unmeasurable' if any(
                    r['quality'] == 'unmeasurable' for r in self.results)
                         else 'insufficient_signal' if any(
                             r['quality'] == 'insufficient_signal'
                             for r in self.results) else 'measured')
            elif self.mode == 'tune':
                self.candidate = done.value
                state = 'candidate'
            else:
                state = 'verified' if done.value['accepted'] else 'failed'
                if state == 'failed':
                    self.error = done.value['reason']
                    self.candidate = None
            self._finish(state, self.error)
            return self.reactor.NEVER
        except Exception as exc:
            if self.mode == 'verify':
                self.candidate = None
            self._finish('failed', str(exc))
            self.timer = None
            return self.reactor.NEVER
        return self.reactor.monotonic() + .05

    def cmd_calibrate(self, gcmd):
        mode = gcmd.get('MODE', 'measure').lower()
        motors = gcmd.get('MOTORS', 'XY').upper()
        if mode not in ('measure', 'tune', 'verify') or motors not in ('XY', 'Z', 'ALL'):
            raise gcmd.error('MODE=measure|tune|verify MOTORS=XY|Z|ALL')
        if self.state == 'running':
            raise gcmd.error('motor calibration already running')
        if mode in ('tune', 'verify') and motors != 'XY':
            raise gcmd.error('XY correction only; use MODE=measure for Z')
        if self.phase_enabled:
            raise gcmd.error('disable TreeD phase before calibration')
        if mode != 'measure' and not self.get_status(
                self.reactor.monotonic())['phase_supported']:
            raise gcmd.error(self.get_status(
                self.reactor.monotonic())['phase_reason'])
        if mode == 'verify' and self.candidate is None and self.profile is None:
            raise gcmd.error('no compatible candidate or saved profile')
        if mode != 'measure':
            self._check_base_tables()
        self.toolhead = self.printer.lookup_object('toolhead')
        self.chip = self.printer.lookup_object('adxl345', None)
        if self.chip is None or not hasattr(self.chip, 'start_internal_client'):
            raise gcmd.error('ADXL345 unavailable')
        self.kin_steppers = {s.get_name(): s for s in
                             self.toolhead.get_kinematics().get_steppers()}
        self.requested_motors = (['stepper_x', 'stepper_y'] if motors == 'XY' else
                                 ['stepper_z'] if motors == 'Z' else
                                 ['stepper_x', 'stepper_y', 'stepper_z'])
        if any(m not in self.kin_steppers for m in self.requested_motors):
            raise gcmd.error('requested motor missing from active kinematics')
        if any(m in XY_MOTORS for m in self.requested_motors):
            if self.toolhead.get_max_velocity()[1] < self.xy_accel:
                raise gcmd.error('set runtime acceleration to at least xy_accel first')
        if mode == 'tune':
            if len(set(self.xy_speeds)) < 3:
                raise gcmd.error('tune_requires_three_xy_speeds')
            for motor in XY_MOTORS:
                slope = 1 if motor == 'stepper_x' else -1
                frequency = self._stepper_frequency(
                    motor, max(self.xy_speeds), (0., 0., 0.),
                    (1., slope, 0.))
                if 8. * frequency >= self.chip.data_rate:
                    raise gcmd.error(
                        'ADXL345 sensor_bandwidth: cannot verify H2 at %.0f mm/s'
                        % max(self.xy_speeds))
                slow_frequency = self._stepper_frequency(
                    motor, min(self.xy_speeds), (0., 0., 0.),
                    (1., slope, 0.))
                if 16. * slow_frequency >= self.chip.data_rate:
                    raise gcmd.error(
                        'ADXL345 sensor_bandwidth: cannot characterize H4 at %.0f mm/s'
                        % min(self.xy_speeds))
        self._ready_before_home(gcmd)
        self.results = []
        self.characterization = {}
        self.last_verdict = None
        if mode == 'tune':
            self.candidate = None
        self.error = None
        self.progress = 0.
        self.cancel_requested = False
        self.mode = mode
        self.verifying_saved_profile = (mode == 'verify' and
                                        (self.candidate is None or
                                         self.candidate is self.profile))
        self.flow = None
        self.flow_result = None
        self.passes_done = 0
        self.old_accel = self.toolhead.get_max_velocity()[1]
        self.state = 'running'
        self.stage = 'homing'
        self.timer = self.reactor.register_timer(self._next, self.reactor.NOW)
        gcmd.respond_info('TreeD motor %s started; use STATUS or CANCEL' % mode)

    def cmd_circle_compare(self, gcmd):
        if self.state == 'running' or self.phase_enabled:
            raise gcmd.error('disable TreeD phase and finish calibration first')
        status = self.get_status(self.reactor.monotonic())
        if not status['phase_supported']:
            raise gcmd.error(status['phase_reason'])
        profile = self.candidate or self.profile
        if profile is None or profile is self.profile and self.profile_state != 'saved':
            raise gcmd.error('no compatible accepted XY profile')
        try:
            self._validate_profile(profile)
        except (ValueError, KeyError, TypeError) as exc:
            raise gcmd.error('profile stale or rejected: %s' % exc)
        if 'verification_failure' in profile:
            raise gcmd.error('profile verification failed')
        self.toolhead = self.printer.lookup_object('toolhead')
        self.chip = self.printer.lookup_object('adxl345', None)
        if self.chip is None or not hasattr(self.chip, 'start_internal_client'):
            raise gcmd.error('ADXL345 unavailable')
        self.kin_steppers = {s.get_name(): s for s in
                             self.toolhead.get_kinematics().get_steppers()}
        if any(motor not in self.kin_steppers for motor in XY_MOTORS):
            raise gcmd.error('XY motors missing from active kinematics')
        self._ready()
        self._check_base_tables()
        self._verify_circle_tables(self.base_tables)
        maximum = min(profile['limits']['max_velocity'],
                      self.toolhead.get_max_velocity()[0])
        supplied = gcmd.get('SPEEDS', None)
        try:
            speeds = ([float(value) for value in supplied.split(',')]
                      if supplied is not None else
                      [speed for speed in (50., 100., 150., 200.)
                       if speed <= maximum])
        except ValueError:
            raise gcmd.error('SPEEDS must be comma-separated positive numbers')
        if not speeds and supplied is None:
            speeds = [maximum]
        if (not speeds or len(speeds) > 12 or
                any(not math.isfinite(speed) or speed <= 0 or speed > maximum
                    for speed in speeds)):
            raise gcmd.error('circle speed exceeds verified profile or runtime limit')
        speeds = tuple(dict.fromkeys(speeds))
        direction = gcmd.get('DIRECTION', 'BOTH').upper()
        if direction not in ('CW', 'CCW', 'BOTH'):
            raise gcmd.error('DIRECTION=BOTH|CW|CCW')
        repeats = gcmd.get_int('REPEATS', 3, minval=1, maxval=10)
        radius = gcmd.get_float('RADIUS', None, above=0.)
        try:
            center, radius = self._circle_geometry(radius)
        except motor_math.MeasurementError as exc:
            raise gcmd.error(str(exc))
        paths = {name: motor_math.circle_points(center, radius, name == 'CW')
                 for name in ('CW', 'CCW')}
        self.circle_profile = profile
        self.circle_center, self.circle_radius = center, radius
        self.circle_paths = paths
        self.circle_speeds = speeds
        self.circle_directions = (('CW', 'CCW') if direction == 'BOTH'
                                  else (direction,))
        self.circle_repeats = repeats
        self.circle_total_passes = (len(speeds) * len(self.circle_directions) *
                                    repeats * 2)
        original_speed, original_accel = self.toolhead.get_max_velocity()
        accel = min(original_accel, profile['limits']['max_accel'], self.xy_accel)
        # ponytail: предел 20 с оставляет запас до 30-секундного step history;
        # длинные круги потребуют потокового чтения командных шагов.
        if any(2. * math.pi * radius / speed + 2. * speed / accel >= 20.
               for speed in speeds):
            raise gcmd.error('circle duration exceeds command step history')
        fingerprint = hashlib.sha256(json.dumps(
            profile, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        self.circle_report = {
            'schema': 1, 'state': 'running', 'error': None,
            'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'profile_id': fingerprint[:16], 'profile_fingerprint': fingerprint,
            'profile_source': 'candidate' if profile is self.candidate else 'saved',
            'klipper_version': self.printer.get_start_args().get('software_version'),
            'mcu_versions': self.mcu_builds,
            'config_fingerprint': self.config_fingerprint,
            'build_id': self.build_id, 'center_mm': center, 'radius_mm': radius,
            'segments': len(paths['CW']) - 1, 'sector_count': 16,
            'speeds_mm_s': speeds, 'directions': self.circle_directions,
            'repeats': repeats, 'accel_mm_s2': accel,
            'max_velocity_mm_s': maximum,
            'square_corner_velocity_mm_s': self.toolhead.square_corner_velocity,
            'passes': [], 'invalid_passes': [], 'comparisons': []}
        self.old_accel = original_accel
        self.circle_old_velocity = original_speed
        try:
            self.toolhead.set_max_velocities(maximum, accel, None, None)
        except Exception:
            try:
                self.toolhead.set_max_velocities(original_speed, original_accel,
                                                 None, None)
            except Exception:
                self.printer.invoke_shutdown('TreeD circle limit restore failed')
            self.old_accel = self.circle_old_velocity = None
            raise
        self.mode = 'circle'
        self.state = 'running'
        self.stage = 'comparing'
        self.error = None
        self.progress = 0.
        self.cancel_requested = False
        self.passes_done = 0
        self.flow = self._circle_flow()
        self.flow_result = None
        self.timer = self.reactor.register_timer(self._next, self.reactor.NOW)
        gcmd.respond_info('TreeD circle compare started; use STATUS or CANCEL')

    def _ready_before_home(self, gcmd):
        stats = self.printer.lookup_object('print_stats', None)
        if stats is not None and stats.get_status(self.reactor.monotonic())['state'] in ('printing', 'paused'):
            raise gcmd.error('printer_busy')
        pause = self.printer.lookup_object('pause_resume', None)
        if pause is not None and pause.get_status(self.reactor.monotonic())['is_paused']:
            raise gcmd.error('printer_paused')
        sd = self.printer.lookup_object('virtual_sdcard', None)
        if sd is not None and sd.get_status(self.reactor.monotonic())['is_active']:
            raise gcmd.error('printer_busy')

    def cmd_cancel(self, gcmd):
        if self.state != 'running':
            raise gcmd.error('no motor calibration running')
        self.cancel_requested = True
        gcmd.respond_info('TreeD motor calibration cancellation requested')

    def cmd_status(self, gcmd):
        gcmd.respond_info(json.dumps(self.get_status(self.reactor.monotonic()),
                                     ensure_ascii=False))

    def cmd_phase(self, gcmd):
        # TODO(N-04): пока это ручной экспериментальный режим вне печати.
        # START_PRINT выполняет homing, запрещённый при phase_enabled.
        # Интеграция с печатью требует отдельного порядка включения/выключения
        # после исправления homing, отзыва непрошедшего профиля, восстановления
        # состояния и стендовой проверки измерения и применения.
        enabled = gcmd.get_int('ENABLE', minval=0, maxval=1)
        if self.state == 'running':
            raise gcmd.error('motor calibration running')
        if (enabled and self.profile is not None and
                'verification_failure' in self.profile):
            raise gcmd.error('profile verification failed: %s' %
                             self.profile['verification_failure'])
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kin_steppers = {s.get_name(): s for s in
                             self.toolhead.get_kinematics().get_steppers()}
        if enabled == self.phase_enabled:
            gcmd.respond_info('TreeD motor phase already %s' %
                              ('enabled' if enabled else 'disabled'))
            return
        self._ready_before_home(gcmd)
        self._ready()
        if enabled:
            if not self.get_status(self.reactor.monotonic())['phase_supported']:
                raise gcmd.error(self.get_status(
                    self.reactor.monotonic())['phase_reason'])
            if self.profile is None:
                raise gcmd.error('no compatible verified profile')
            self._check_base_tables()
            limits = self.profile['limits']
            speed, accel = self.toolhead.get_max_velocity()
            if speed > limits['max_velocity'] or accel > limits['max_accel']:
                raise gcmd.error('set velocity and acceleration within profile limits first')
            self._switch_tables(self.profile['tables'])
            self.phase_enabled = True
            self.profile_state = 'enabled'
        else:
            self.phase_transition = True
            try:
                self._switch_tables(self.base_tables)
            finally:
                self.phase_transition = False
            self.phase_enabled = False
            self.profile_state = 'saved'
        gcmd.respond_info('TreeD motor phase %s' %
                          ('enabled' if enabled else 'disabled'))

    def cmd_save(self, gcmd):
        if self.state == 'running' or self.phase_enabled:
            raise gcmd.error('calibration or phase active')
        if self.candidate is None:
            raise gcmd.error('no verified candidate to save')
        self._validate_profile(self.candidate)
        if os.path.exists(self.profile_path):
            try:
                with open(self.profile_path, encoding='utf-8') as input_file:
                    previous = json.load(input_file)
            except (OSError, ValueError):
                previous = None
            if (isinstance(previous, dict) and
                    isinstance(previous.get('verified'), dict) and
                    previous['verified'].get('accepted') is True):
                self._atomic_json(self.previous_path, previous)
        self._atomic_json(self.profile_path, self.candidate)
        self.profile = self.candidate
        self.candidate = None
        self.profile_state = 'saved'
        gcmd.respond_info('TreeD motor profile saved; ENABLE=1 applies it')


def load_config(config):
    return TreedMotorCalibration(config)
