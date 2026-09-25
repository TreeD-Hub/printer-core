"""Нижняя опора Z через TMC5160; required для неизвестной Z после допуска.

Eddy остаётся рабочим Z-endstop. Повторяемость StallGuard не доказывает
отсутствие препятствия: параметры и свободный ход проверяет оператор.
API закреплён в runtime-versions.env (Klipper ce7002bed).
"""
import logging
import math

from . import homing


class TreedZRecovery:
    # Блок 1: Отдельные параметры и DIAG, не заменяющий endstop рельса Z.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.enabled = config.getboolean('enabled', False)
        self.running = False
        self.sgt = config.getint('sgt', 3, minval=-64, maxval=63)
        self.speed = config.getfloat('speed', 5., above=0.)
        self.current = config.getfloat('current', 0.9, above=0.)
        self.accel = config.getfloat('accel', 100., above=0.)
        self.backoff = config.getfloat('verify_backoff_mm', 5., above=0.)
        self.tolerance = config.getfloat('tolerance', 0.5, above=0.)
        self.clearance = config.getfloat('bottom_clearance_mm', 5., above=0.)
        self.pause = config.getfloat('stallguard_pause', 2., minval=2.)
        zconfig = config.getsection('stepper_z')
        low = zconfig.getfloat('position_min', 0.)
        high = zconfig.getfloat('position_max')
        self.bottom = config.getfloat('bottom_position', high)
        self.max_seek = config.getfloat('max_seek', high - low, above=0.)
        values = (low, high, self.bottom, self.max_seek, self.speed,
                  self.current, self.accel, self.backoff, self.tolerance,
                  self.clearance, self.pause)
        if (not all(math.isfinite(v) for v in values)
                or not low < self.bottom <= high
                or self.max_seek > self.bottom - low
                or max(self.backoff + self.tolerance, self.clearance)
                >= self.max_seek or self.tolerance >= self.backoff / 2.):
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

    def _connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kin = self.toolhead.get_kinematics()
        steppers = self.kin.rails[2].get_steppers()
        if len(steppers) != 1 or steppers[0].get_name() != 'stepper_z':
            raise self.printer.config_error('Z recovery: поддерживается один мотор Z')
        self.endstop.add_stepper(steppers[0])
        self.enable = self.printer.lookup_object('stepper_enable').lookup_enable('stepper_z')

    # Блок 2: Допуск до любых изменений координат, тока и движения.
    def _require_idle(self, gcmd):
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
        # virtual_sd уже printing внутри START_PRINT; только preparing допускает этот случай.
        if (self.running or phase not in ('idle', 'preparing') or paused
                or stats in ('paused', 'error')
                or ((stats == 'printing' or sd_active) and phase != 'preparing')
                or (manual is not None and manual.get_status(now)['is_active'])
                or (sgt is not None and sgt.running)):
            raise gcmd.error('Z recovery: печать, пауза или калибровка активна')

    def _alive(self, check_pause=True):
        if self.printer.is_shutdown():
            raise self.printer.command_error('Z recovery: Klipper shutdown')
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
        try:
            trigger = move.homing_move(target, self.speed, probe_pos=True)[2]
        except BaseException:
            # Исключение возможно до home_wait: прекращаем MCU-движение без отхода.
            self.printer.invoke_shutdown('Z recovery: сбой sensorless-поиска')
            raise
        self._alive()
        halt = self.toolhead.get_position()[2]
        if (not all(math.isfinite(v) for v in (trigger, halt))
                or move.check_no_movement() is not None
                or not start < trigger <= self.bottom
                or halt < trigger or halt > self.bottom):
            raise self.printer.command_error('Z recovery: некорректный или неподвижный DIAG')
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
        if gcmd.get_command_parameters():
            raise gcmd.error('TREED_Z_HOME_BOTTOM: параметры задаются в [treed_z_recovery]')
        self._require_idle(gcmd)
        now = self.printer.get_reactor().monotonic()
        if 'z' in self.kin.get_status(now)['homed_axes']:
            gcmd.respond_info('Z recovery: Z уже известна; нижняя опора пропущена')
            return
        if not self.enabled:
            raise gcmd.error('Z recovery: требуется аппаратная настройка и enabled: True')
        if self.speed > min(self.toolhead.max_velocity, self.kin.max_z_velocity):
            raise gcmd.error('Z recovery: speed превышает текущий лимит Z')
        self.toolhead.wait_moves()
        fields = ('sgt', 'en_pwm_mode', 'diag0_stall', 'diag1_stall',
                  'tcoolthrs', 'thigh', 'globalscaler', 'irun', 'ihold')
        saved = {f: self.driver.fields.get_field(f) for f in fields}
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
            self._set_z(self.bottom - self.max_seek)
            _, overshoot = self._seek()
            self._set_z(self.bottom + overshoot)
            self._retreat(self.backoff)
            # Смещение временного нуля оставляет допуск пробы внутри soft limits.
            self._set_z(self.bottom - self.backoff - self.tolerance)
            travel, overshoot = self._seek()
            if abs(travel - self.backoff) > self.tolerance:
                raise gcmd.error('Z recovery: повторный DIAG вне окна: %.6f мм' % travel)
            self._set_z(self.bottom + overshoot)
            self._retreat(self.clearance)
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
                    if not success or not restored:
                        self.kin.clear_homing_state('z')
            if not restored:
                raise gcmd.error('Z recovery: MCU не восстановлен; нужен FIRMWARE_RESTART')
        gcmd.respond_info('Z recovery: нижняя опора подтверждена, Z=%.6f; рабочий Z0 — Eddy'
                          % (self.bottom - self.clearance))


def load_config(config):
    return TreedZRecovery(config)
