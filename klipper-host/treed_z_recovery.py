"""Доступный ход Z между DIAG и Eddy; required для неизвестной Z.

Eddy остаётся рабочим Z-endstop. Повторяемость StallGuard не доказывает
отсутствие препятствия: параметры и свободный ход проверяет оператор.
API закреплён в runtime-versions.env (Klipper ce7002bed).
"""
import logging
import math
import copy
from datetime import datetime, timezone

from . import homing


class TreedZRecovery:
    AUTO_REMOVE_DISTANCE = 25.
    AUTO_REMOVE_SPEED = 50.
    AUTO_REMOVE_CYCLES = 5

    # Блок 1: Отдельные параметры и DIAG, не заменяющий endstop рельса Z.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.running = False
        self.eddy_homing = False
        self.z_travel = {}
        self.last_run = {}
        self.last_eddy = {}
        self.capture_eddy = False
        self.mesh_active = False
        self.mesh_fault = False
        self.mesh_dispatch = None
        self.last_mesh = {}
        self.motor_epoch = 0
        self.sgt = config.getint('sgt', 3, minval=-64, maxval=63)
        self.speed = config.getfloat('speed', 5., above=0.)
        self.current = config.getfloat('current', 0.9, above=0.)
        self.accel = config.getfloat('accel', 100., above=0.)
        self.clearance = config.getfloat('bottom_clearance_mm', 5., above=0.)
        self.pause = config.getfloat('stallguard_pause', 2., minval=2.)
        zconfig = config.getsection('stepper_z')
        low = zconfig.getfloat('position_min', 0.)
        high = zconfig.getfloat('position_max')
        self.config_z_max = high
        self.bottom = config.getfloat('bottom_position', high)
        self.max_seek = config.getfloat('max_seek', high - low, above=0., maxval=210.)
        values = (low, high, self.bottom, self.max_seek, self.speed,
                  self.current, self.accel, self.clearance, self.pause)
        if (not all(math.isfinite(v) for v in values)
                or not low < self.bottom <= high
                or self.bottom - self.clearance <= low
                or self.clearance >= self.max_seek):
            raise config.error('Z recovery: неверные границы поиска или отхода')
        if (config.getsection('printer').get('kinematics') != 'corexy'
                or zconfig.get('endstop_pin') != 'probe:z_virtual_endstop'):
            raise config.error('Z recovery: требуется CoreXY с Eddy Z-endstop')
        self.driver = self.printer.load_object(config, 'tmc5160 stepper_z')
        # В закреплённом TMC5160 get_status экспортирован из TMCCommandHelper.
        self.current_helper = self.driver.get_status.__self__.current_helper
        if self.current > self.current_helper.get_current()[3]:
            raise config.error('Z recovery: current превышает предел TMC5160')
        self.endstop = self.printer.lookup_object('pins').setup_pin(
            'endstop', 'tmc5160_stepper_z:virtual_endstop')
        self.printer.register_event_handler('klippy:mcu_identify', self._connect)
        self.printer.lookup_object('gcode').register_command(
            'TREED_Z_HOME_BOTTOM', self.cmd_home)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('TREED_Z_PARK_BOTTOM_MANUAL',
                               self.cmd_park_bottom_manual)
        gcode.register_command('TREED_Z_PARK_BOTTOM', self.cmd_park_bottom)
        gcode.register_command('_TREED_Z_TRAVEL_BEGIN', self.cmd_travel_begin)
        gcode.register_command('_TREED_Z_TRAVEL_APPLY', self.cmd_travel_apply)
        gcode.register_command('TREED_Z_RECOVERY_TEST', self.cmd_test)
        gcode.register_command('TREED_EDDY_ACCEPTANCE_HOME', self.cmd_eddy_test)
        gcode.register_command('TREED_EDDY_ACCEPTANCE_MESH', self.cmd_mesh_test)
        gcode.register_command('_TREED_ACCEPTANCE_MESH_RUN', self.cmd_mesh_run)
        gcode.register_command('_TREED_ACCEPTANCE_STAGE', self.cmd_stage)

    def _connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kin = self.toolhead.get_kinematics()
        steppers = self.kin.rails[2].get_steppers()
        if len(steppers) != 1 or steppers[0].get_name() != 'stepper_z':
            raise self.printer.config_error('Z recovery: поддерживается один мотор Z')
        self.endstop.add_stepper(steppers[0])
        self.enable = self.printer.lookup_object('stepper_enable').lookup_enable('stepper_z')
        self.enable.register_state_callback(self._motor_state)
        self.printer.register_event_handler('gcode:command_error', self._command_error)
        self.printer.register_event_handler('klippy:shutdown', self._invalidate_travel)

    def _motor_state(self, print_time, enabled):
        if not enabled:
            self.motor_epoch += 1
            self._invalidate_travel()

    def _set_z_limit(self, limit):
        # CoreXY ce7002bed: rail нужен для следующих set_position/homing,
        # limits — для check_move, axes_max и UI-контракт — для потребителей.
        rail = self.kin.rails[2]
        rail.position_max = limit
        if self.kin.limits[2][0] <= self.kin.limits[2][1]:
            self.kin.limits[2] = rail.get_range()
        self.kin.axes_max = self.toolhead.Coord(
            [self.kin.axes_max.x, self.kin.axes_max.y, limit])
        contract = self.printer.lookup_object('gcode_macro _TREED_UI_CONTRACT', None)
        if contract is not None:
            contract.variables = dict(contract.variables, axis_z_max=limit)

    def _invalidate_travel(self):
        self.z_travel = {}
        self.eddy_homing = False
        self.kin.clear_homing_state('z')
        self._set_z_limit(self.config_z_max)

    def _command_error(self):
        if self.eddy_homing:
            self._invalidate_travel()

    def get_status(self, eventtime):
        return copy.deepcopy(dict(last_run=self.last_run, last_eddy=self.last_eddy,
                                  last_mesh=self.last_mesh, mesh_active=self.mesh_active,
                                  mesh_fault=self.mesh_fault,
                                  z_travel=self.z_travel,
                                  running=self.running, motor_epoch=self.motor_epoch))

    # Блок 2: Допуск до любых изменений координат, тока и движения.
    def _require_idle(self, gcmd, required_phase=None):
        now = self.printer.get_reactor().monotonic()
        if self.printer.is_shutdown() or self.printer.get_state_message()[1] != 'ready':
            raise gcmd.error('Z recovery: Klipper не готов')
        phase = self.printer.lookup_object('gcode_macro _TREED_OPERATION_STATE').variables['phase']
        stats = self.printer.lookup_object('print_stats').get_status(now)['state']
        pause_resume = self.printer.lookup_object('pause_resume')
        paused = pause_resume.get_status(now)['is_paused'] or pause_resume.pause_command_sent
        sd_active = self.printer.lookup_object('virtual_sdcard').is_active()
        manual = self.printer.lookup_object('manual_probe', None)
        sgt = self.printer.lookup_object('treed_sgt_executor', None)
        allowed_phases = (required_phase,) if required_phase else ('idle', 'preparing')
        # virtual_sd активен внутри START_PRINT и до завершения END_PRINT.
        if (self.running or self.eddy_homing or phase not in allowed_phases or paused
                or stats in ('paused', 'error')
                or ((stats == 'printing' or sd_active)
                    and phase not in ('preparing', 'auto_remove'))
                or (manual is not None and manual.get_status(now)['is_active'])
                or (sgt is not None and sgt.running)):
            raise gcmd.error('Z recovery: печать, пауза или калибровка активна')

    def _alive(self, check_pause=True):
        if self.printer.is_shutdown():
            raise self.printer.command_error('Z recovery: Klipper shutdown')
        if check_pause and self.running and self.motor_epoch != self.last_run['motor_epoch']:
            raise self.printer.command_error('Z recovery: мотор Z отключался во время поиска')
        pause_resume = self.printer.lookup_object('pause_resume')
        if check_pause and (pause_resume.is_paused or pause_resume.pause_command_sent):
            raise self.printer.command_error('Z recovery: запрошена пауза')

    def _set_z(self, z):
        pos = self.toolhead.get_position()
        pos[2] = z
        # Как stock homing: временная система координат только внутри исполнителя.
        self.toolhead.set_position(pos, homing_axes='z')

    # Блок 3: Ограниченная проба с MCU trigger position, затем обычный отход.
    def _seek(self):
        self._alive()
        self.toolhead.wait_moves()
        self.toolhead.dwell(self.pause)
        self.toolhead.wait_moves()
        self._alive()
        start = self.toolhead.get_position()[2]
        target = self.toolhead.get_position()
        target[2] = self.bottom
        move = homing.HomingMove(self.printer, [(self.endstop, 'z_bottom')])
        probe = dict(start_mm=start if math.isfinite(start) else None,
                     target_mm=target[2], trigger_mm=None, halt_mm=None,
                     travel_mm=None, overshoot_mm=None, no_movement=None,
                     failure_reason=None)
        self.last_run.setdefault('probes', []).append(probe)
        try:
            trigger = move.homing_move(target, self.speed, probe_pos=True)[2]
        except BaseException:
            probe['failure_reason'] = self.last_run['failure_reason'] = 'homing_error'
            # Исключение возможно до home_wait: прекращаем MCU-движение без отхода.
            self.printer.invoke_shutdown('Z recovery: сбой sensorless-поиска')
            raise
        halt = self.toolhead.get_position()[2]
        no_movement = move.check_no_movement() is not None
        finite = all(math.isfinite(v) for v in (start, trigger, halt))
        travel = trigger - start if finite else None
        overshoot = halt - trigger if finite else None
        probe.update(trigger_mm=trigger if math.isfinite(trigger) else None,
                     halt_mm=halt if math.isfinite(halt) else None,
                     travel_mm=travel if travel is not None and math.isfinite(travel) else None,
                     overshoot_mm=overshoot if overshoot is not None and math.isfinite(overshoot) else None,
                     no_movement=no_movement)
        if not math.isfinite(start):
            reason = 'non_finite_start'
        elif not math.isfinite(trigger):
            reason = 'non_finite_trigger'
        elif not math.isfinite(halt):
            reason = 'non_finite_halt'
        elif no_movement:
            reason = 'no_movement'
        elif trigger <= start:
            reason = 'trigger_before_start'
        elif trigger > self.bottom:
            reason = 'trigger_after_bottom'
        elif halt < trigger:
            reason = 'halt_before_trigger'
        elif halt > self.bottom:
            reason = 'halt_after_bottom'
        elif halt - trigger > self.clearance:
            reason = 'overshoot_exceeds_clearance'
        else:
            reason = None
        probe['failure_reason'] = reason
        if reason:
            self.last_run['failure_reason'] = reason
            raise self.printer.command_error('Z recovery: DIAG %s' % reason)
        try:
            self._alive()
        except BaseException:
            probe['failure_reason'] = self.last_run['failure_reason'] = 'state_lost'
            raise
        return trigger - start, halt - trigger

    def _retreat(self, distance):
        self._alive()
        pos = self.toolhead.get_position()
        pos[2] = self.bottom - distance
        try:
            self.toolhead.move(pos, self.speed)
            self.toolhead.wait_moves()
        except BaseException:
            self.printer.invoke_shutdown('Z recovery: сбой отхода')
            raise
        self._alive()

    # Блок 4: Возврат точных live-полей, включая ток, даже при частичном сбое.
    def _restore(self, saved, requested_hold):
        registers = {}
        for field, value in saved.items():
            reg = self.driver.fields.lookup_register(field)
            registers[reg] = self.driver.fields.set_field(field, value)
        self.current_helper.req_hold_current = requested_hold
        if self.printer.is_shutdown():
            return False  # Кэш возвращён, но MCU требует FIRMWARE_RESTART.
        for reg, bits in registers.items():
            self.driver.mcu_tmc.set_register(reg, bits, self.toolhead.get_last_move_time())
        self.toolhead.wait_moves()
        self._alive(check_pause=False)
        return True

    def cmd_home(self, gcmd):
        self._run_bottom(gcmd, auto_remove=False)

    def cmd_park_bottom_manual(self, gcmd):
        self._run_bottom(gcmd, auto_remove=False, force=True)

    def cmd_park_bottom(self, gcmd):
        self._run_bottom(gcmd, auto_remove=True)

    def _run_bottom(self, gcmd, auto_remove, force=False):
        self.last_run = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                             start_state=dict(position=self.toolhead.get_position(),
                                              homed_axes=self.kin.get_status(0)['homed_axes']),
                             probes=[], sgt=self.sgt, current=self.current,
                             motor_epoch=self.motor_epoch,
                             mode=('auto_remove' if auto_remove else
                                   'manual_park' if force else 'recovery'),
                             result='running')
        try:
            self._home(gcmd, auto_remove, force)
            if auto_remove:
                self._auto_remove(gcmd)
            self.last_run['result'] = 'passed' if self.last_run['probes'] else 'skipped'
        except BaseException as exc:
            self.last_run.update(result='failed', error=str(exc))
            raise
        finally:
            self.last_run['shutdown'] = self.printer.is_shutdown()
            self.last_run['klipper_state'] = self.printer.get_state_message()[1]
            self.last_run['end_position'] = [v if math.isfinite(v) else None
                                             for v in self.toolhead.get_position()]

    def _home(self, gcmd, auto_remove=False, force=False):
        if gcmd.get_command_parameters():
            command = ('TREED_Z_PARK_BOTTOM' if auto_remove else
                       'TREED_Z_PARK_BOTTOM_MANUAL' if force else
                       'TREED_Z_HOME_BOTTOM')
            raise gcmd.error('%s: параметры не поддерживаются' % command)
        required_phase = 'auto_remove' if auto_remove else 'idle' if force else None
        self._require_idle(gcmd, required_phase)
        now = self.printer.get_reactor().monotonic()
        if not auto_remove and not force and 'z' in self.kin.get_status(now)['homed_axes']:
            gcmd.respond_info('Z recovery: Z уже известна; нижняя опора пропущена')
            return
        if self.speed > min(self.toolhead.max_velocity, self.kin.max_z_velocity):
            raise gcmd.error('Z recovery: speed превышает текущий лимит Z')
        self.toolhead.wait_moves()
        self._invalidate_travel()
        fields = ('sgt', 'en_pwm_mode', 'diag0_stall', 'diag1_stall',
                  'tcoolthrs', 'thigh', 'globalscaler', 'irun', 'ihold')
        saved = {f: self.driver.fields.get_field(f) for f in fields}
        self.last_run['tmc_before'] = dict(saved)
        requested_hold = self.current_helper.get_current()[2]
        limits = (self.toolhead.max_velocity, self.toolhead.max_accel,
                  self.toolhead.square_corner_velocity, self.toolhead.min_cruise_ratio)
        self.running = True
        success = restored = False
        try:
            self.toolhead.set_max_velocities(None, min(self.accel, limits[1]), None, None)
            self.current_helper.set_current(self.current, self.current,
                                            self.toolhead.get_last_move_time())
            bits = self.driver.fields.set_field('sgt', self.sgt)
            self.driver.mcu_tmc.set_register(self.driver.fields.lookup_register('sgt'),
                                             bits, self.toolhead.get_last_move_time())
            # Ненулевой рабочий TCOOLTHRS stock helper не заменяет: для поиска
            # нужен полный диапазон StallGuard, независимо от рабочих настроек.
            bits = self.driver.fields.set_field('tcoolthrs', 0xfffff)
            self.driver.mcu_tmc.set_register(self.driver.fields.lookup_register('tcoolthrs'),
                                             bits, self.toolhead.get_last_move_time())
            self.enable.motor_enable(self.toolhead.get_last_move_time())
            # Временное начало ниже soft limit даёт запас хода без расширения position_max.
            self._set_z(self.bottom - self.max_seek)
            _, overshoot = self._seek()
            stepper = self.kin.rails[2].get_steppers()[0]
            step_mm = stepper.get_step_dist()
            contact_mcu_mm = stepper.get_mcu_position() * step_mm - overshoot
            if (not math.isfinite(contact_mcu_mm) or not math.isfinite(step_mm)
                    or step_mm <= 0. or self.motor_epoch != self.last_run['motor_epoch']):
                raise gcmd.error('Z recovery: потеря MCU-опоры DIAG')
            self._set_z(self.bottom + overshoot)
            self._retreat(self.clearance)
            # До Eddy Z0 это временная координата, не измеренная высота контакта.
            self._set_z(self.bottom - self.clearance)
            self._set_z_limit(self.bottom - self.clearance)
            self.z_travel = dict(state='pending_eddy', contact_mcu_mm=contact_mcu_mm,
                                 step_mm=step_mm, motor_epoch=self.motor_epoch)
            success = True
        finally:
            try:
                restored = self._restore(saved, requested_hold)
            except BaseException:
                logging.exception('Z recovery: ошибка восстановления TMC')
                self.printer.invoke_shutdown('Z recovery: TMC не восстановлен')
                raise
            finally:
                try:
                    self.toolhead.set_max_velocities(*limits)
                except BaseException:
                    success = False
                    self.printer.invoke_shutdown('Z recovery: лимиты не восстановлены')
                    raise
                finally:
                    self.running = False
                    self.last_run.update(tmc_restored=restored,
                                         tmc_after={f: self.driver.fields.get_field(f) for f in fields})
                    if not success or not restored or self.motor_epoch != self.last_run['motor_epoch']:
                        self._invalidate_travel()
            if not restored:
                raise gcmd.error('Z recovery: MCU не восстановлен; нужен FIRMWARE_RESTART')
        if self.motor_epoch != self.last_run['motor_epoch']:
            raise gcmd.error('Z recovery: мотор Z отключался во время поиска')
        gcmd.respond_info('Z recovery: DIAG найден, временная Z=%.6f; Z0 и ход определит Eddy'
                          % (self.bottom - self.clearance))

    def _auto_remove(self, gcmd):
        bottom_safe = self.bottom - self.clearance
        top = bottom_safe - self.AUTO_REMOVE_DISTANCE
        z_min, z_max = self.kin.rails[2].get_range()
        if (self.z_travel.get('state') != 'pending_eddy'
                or not self.last_run['probes']
                or self.last_run['probes'][-1].get('failure_reason')):
            raise gcmd.error('Z auto-remove: нижний DIAG не подтверждён')
        if top < z_min or bottom_safe > z_max:
            raise gcmd.error('Z auto-remove: недостаточно 25 мм допустимого хода вверх')
        if self.AUTO_REMOVE_SPEED > min(self.toolhead.max_velocity,
                                        self.kin.max_z_velocity):
            raise gcmd.error('Z auto-remove: скорость 50 мм/с превышает текущий лимит Z')
        if abs(self.toolhead.get_position()[2] - bottom_safe) > 1.e-6:
            raise gcmd.error('Z auto-remove: исходная нижняя позиция потеряна')

        self.last_run['auto_remove'] = dict(cycles=0, distance=self.AUTO_REMOVE_DISTANCE,
                                            speed=self.AUTO_REMOVE_SPEED,
                                            bottom_safe=bottom_safe)
        self.running = True
        try:
            for cycle in range(self.AUTO_REMOVE_CYCLES):
                for target in (top, bottom_safe):
                    self._alive()
                    pos = self.toolhead.get_position()
                    pos[2] = target
                    self.toolhead.move(pos, self.AUTO_REMOVE_SPEED)
                    self.toolhead.wait_moves()
                    self._alive()
                self.last_run['auto_remove']['cycles'] = cycle + 1
        except BaseException:
            self.printer.invoke_shutdown('Z auto-remove: сбой движения')
            raise
        finally:
            self.running = False
        if abs(self.toolhead.get_position()[2] - bottom_safe) > 1.e-6:
            raise gcmd.error('Z auto-remove: итоговая нижняя позиция потеряна')

    # Блок 5: Общая MCU-опора DIAG/Eddy и рабочий лимит до следующей потери Z.
    def cmd_travel_begin(self, gcmd):
        self._require_idle(gcmd)
        if gcmd.get_command_parameters() or not self.z_travel:
            self._invalidate_travel()
            raise gcmd.error('Z travel: нужна новая опора DIAG; выполните полный G28')
        self.eddy_homing = True

    def cmd_travel_apply(self, gcmd):
        try:
            if gcmd.get_command_parameters() or not self.eddy_homing or not self.z_travel:
                raise gcmd.error('Z travel: нет начатого измерения DIAG/Eddy')
            self.toolhead.wait_moves()
            self._alive()
            ref = self.z_travel
            stepper = self.kin.rails[2].get_steppers()[0]
            step_mm = stepper.get_step_dist()
            if (not ref or self.motor_epoch != ref['motor_epoch'] or step_mm != ref['step_mm']
                    or 'z' not in self.kin.get_status(0)['homed_axes']):
                raise gcmd.error('Z travel: общая опора DIAG/Eddy потеряна')
            z = self.toolhead.get_position()[2]
            # SET_KINEMATIC_POSITION и G28 меняют координаты, но сохраняют MCU-счётчик.
            z0_mcu_mm = stepper.get_mcu_position() * step_mm - z
            contact_z = ref['contact_mcu_mm'] - z0_mcu_mm
            limit = min(self.config_z_max, contact_z - self.clearance)
            if (not all(math.isfinite(v) for v in (z, z0_mcu_mm, contact_z, limit))
                    or limit <= 0. or not self.kin.rails[2].position_min <= z <= limit):
                raise gcmd.error('Z travel: неверный или недостаточный ход между DIAG и Eddy')
            self._set_z_limit(limit)
            self.z_travel.update(state='measured', z0_mcu_mm=z0_mcu_mm,
                                 contact_z=contact_z, z_max=limit)
            self.eddy_homing = False
            gcmd.respond_info('Z travel: DIAG Z=%.3f, запас=%.3f, доступно Z<=%.3f мм'
                              % (contact_z, self.clearance, limit))
        except BaseException:
            self._invalidate_travel()
            raise

    # Блок 6: Явные диагностические входы. Никаких записей конфигурации.
    def _require_test(self, gcmd):
        self._require_idle(gcmd)
        phase = self.printer.lookup_object('gcode_macro _TREED_OPERATION_STATE').variables['phase']
        if phase != 'idle' or self.capture_eddy or self.mesh_fault or gcmd.get_int('CONFIRM', 0) != 1:
            raise gcmd.error('Acceptance: нужны idle и явный CONFIRM=1')

    def cmd_test(self, gcmd):
        self._require_test(gcmd)
        if set(gcmd.get_command_parameters()) - {'CONFIRM', 'START_Z'}:
            raise gcmd.error('Acceptance: неизвестный параметр')
        start_z = gcmd.get_float('START_Z', None)
        if start_z is not None:
            if (not math.isfinite(start_z) or not 0. < start_z <= self.bottom - self.clearance
                    or 'z' not in self.kin.get_status(0)['homed_axes']):
                raise gcmd.error('Acceptance: START_Z требует известную Z в безопасных пределах')
            self.toolhead.wait_moves()
            pos = self.toolhead.get_position()
            pos[2] = start_z
            try:
                self.toolhead.move(pos, self.speed)
                self.toolhead.wait_moves()
            except BaseException:
                self._invalidate_travel()
                self.printer.invoke_shutdown('Acceptance: сбой установки стартовой Z')
                raise
        before = dict(position=self.toolhead.get_position(),
                      homed_axes=self.kin.get_status(0)['homed_axes'])
        self.kin.clear_homing_state('z')
        gcode = self.printer.lookup_object('gcode')
        try:
            self.cmd_home(gcode.create_gcode_command('TREED_Z_HOME_BOTTOM', '', {}))
        finally:
            self.last_run['before_forced_unknown'] = before

    def cmd_eddy_test(self, gcmd):
        self._require_test(gcmd)
        if set(gcmd.get_command_parameters()) != {'CONFIRM'}:
            raise gcmd.error('Acceptance: допустим только CONFIRM=1')
        if not set('xyz') <= set(self.kin.get_status(0)['homed_axes']):
            raise gcmd.error('Acceptance: перед Eddy нужны известные XYZ')
        self.last_eddy = dict(timestamp=datetime.now(timezone.utc).isoformat(),
                              stages={name: 'not_started' for name in
                                      ('eddy_coarse', 'eddy_probe', 'final_z0')},
                              motor_epoch=self.motor_epoch, result='running')
        self.capture_eddy = True
        try:
            self.printer.lookup_object('gcode').run_script_from_command(
                'BED_MESH_CLEAR\n_TREED_PRINT_OFFSET_DISABLE\n_TREED_UI_RESET_Z_OFFSET\n_TREED_EDDY_HOME_Z')
            if any(v != 'passed' for v in self.last_eddy['stages'].values()):
                raise gcmd.error('Acceptance: маркеры стадий Eddy отсутствуют')
            if self.motor_epoch != self.last_eddy['motor_epoch']:
                raise gcmd.error('Acceptance: во время измерения отключался мотор Z')
            self.last_eddy['result'] = 'passed'
        except BaseException as exc:
            self.last_eddy.update(result='failed', error=str(exc))
            for stage, state in self.last_eddy['stages'].items():
                if state == 'running':
                    self.last_eddy['stages'][stage] = 'failed'
            self._invalidate_travel()
            raise
        finally:
            self.capture_eddy = False

    def cmd_stage(self, gcmd):
        if not self.capture_eddy:
            return
        stage, state = gcmd.get('STAGE'), gcmd.get('STATE')
        if stage not in self.last_eddy['stages'] or state not in ('running', 'passed'):
            raise gcmd.error('Acceptance: неизвестная стадия')
        if stage == 'final_z0' and state == 'passed':
            self.toolhead.wait_moves()
            stepper = self.kin.rails[2].get_steppers()[0]
            counter, step_mm = stepper.get_mcu_position(), stepper.get_step_dist()
            # Координаты Klipper каждый раз обнуляются; MCU-счётчик сохраняет
            # общую систему отсчёта только внутри одной сессии с включённым Z.
            z = self.toolhead.get_position()[2]
            self.last_eddy.update(z0_mcu_mm=counter * step_mm - z,
                                  mcu_counter=counter, step_mm=step_mm, corrected_z=z)
        self.last_eddy['stages'][stage] = state

    def cmd_mesh_test(self, gcmd):
        self._require_test(gcmd)
        if set(gcmd.get_command_parameters()) != {'CONFIRM'}:
            raise gcmd.error('Acceptance: допустим только CONFIRM=1')
        if not set('xyz') <= set(self.kin.get_status(0)['homed_axes']):
            raise gcmd.error('Acceptance: перед mesh нужны известные XYZ')
        bedmesh = self.printer.lookup_object('bed_mesh')
        gcode = self.printer.lookup_object('gcode')
        configfile = self.printer.lookup_object('configfile')
        snapshot = lambda: copy.deepcopy(configfile.get_status(self.printer.get_reactor().monotonic()))
        before = snapshot()
        save_profile = bedmesh.save_profile
        handlers = {name: gcode.ready_gcode_handlers[name]
                    for name in ('BED_MESH_CALIBRATE', 'BED_MESH_CALIBRATE_BASE')}
        self.last_mesh = dict(timestamp=datetime.now(timezone.utc).isoformat(), result='running',
                              mesh_profile_persistence_suppressed=0, save_profile_restored=0,
                              save_config_sent=0, pending_config_changed=None, config_before=before)
        self.capture_eddy = True
        self.mesh_active = True
        deny = self._deny_mesh
        try:
            # В закреплённом Klipper calibrate всегда вызывает этот callback.
            # Запрещаем даже изменение autosave в памяти, не только запись файла.
            bedmesh.save_profile = lambda name: None
            self.last_mesh['mesh_profile_persistence_suppressed'] = 1
            for name in handlers:
                # register_command повторно оборачивает extended G-code;
                # сохраняем именно исходные dispatch callbacks и их identity.
                gcode.ready_gcode_handlers[name] = deny
            self.mesh_dispatch = handlers['BED_MESH_CALIBRATE_BASE']
            gcode.run_script_from_command(
                'TREED_BED_MESH_CALIBRATE_EDDY ACCEPTANCE=1 PROFILE=treed_acceptance METHOD=scan ADAPTIVE=0')
            self.toolhead.wait_moves()
            if self.mesh_dispatch is not None:
                raise gcmd.error('Acceptance: диагностический scan не был вызван')
            self.last_mesh['mesh'] = copy.deepcopy(bedmesh.get_status(self.printer.get_reactor().monotonic()))
            current_mesh = bedmesh.get_mesh()
            if current_mesh is None:
                raise gcmd.error('Acceptance: runtime mesh отсутствует')
            self.last_mesh['mesh']['mesh_params'] = copy.deepcopy(current_mesh.get_mesh_params())
            self.last_mesh['mesh']['z_range'] = current_mesh.get_z_range()
            self.last_mesh['result'] = 'passed'
        except BaseException as exc:
            self.last_mesh.update(result='failed', error=str(exc))
            raise
        finally:
            faults = []
            try:
                bedmesh.save_profile = save_profile
                if bedmesh.save_profile is not save_profile:
                    faults.append('save_profile identity mismatch')
                else:
                    self.last_mesh['save_profile_restored'] = 1
            except BaseException as exc:
                faults.append(str(exc))
            for name, handler in handlers.items():
                try:
                    gcode.ready_gcode_handlers[name] = handler
                    if gcode.ready_gcode_handlers[name] is not handler:
                        faults.append(name+' identity mismatch')
                except BaseException as exc:
                    faults.append(name+': '+str(exc))
            try:
                after = snapshot()
                self.last_mesh.update(config_after=after, pending_config_changed=int(before != after))
                if before != after:
                    faults.append('production/pending config changed')
            except BaseException as exc:
                faults.append('config snapshot: '+str(exc))
            self.mesh_dispatch = None
            self.mesh_active = False
            self.capture_eddy = False
            if faults:
                self.mesh_fault = True
                self.last_mesh.update(result='fault', restoration_errors=faults)
                self.printer.invoke_shutdown('Acceptance: mesh handler/config fault')
                raise gcmd.error('Acceptance: '+ '; '.join(faults))

    def _deny_mesh(self, gcmd):
        raise gcmd.error('Acceptance: обычный mesh запрещён во время diagnostic scan')

    def cmd_mesh_run(self, gcmd):
        if not self.mesh_active or self.mesh_dispatch is None:
            raise gcmd.error('Acceptance: диагностический mesh не ожидается или уже запущен')
        handler = self.mesh_dispatch
        self.mesh_dispatch = None  # Одноразовый вход закрывается до вызова probe.
        handler(gcmd)


def load_config(config):
    return TreedZRecovery(config)
