"""Отдельный исполнитель проб SGT для CoreXY/TMC5160.

Контур: opt-in; движение только по TREED_SGT_CALIBRATE, без G28/SAVE_CONFIG.
Счётчики шагов не являются энкодером и не измеряют силу контакта.
"""
import logging
import math

from . import homing
from .treed_sgt_calibration import (
    CalibrationConfig, EarlyTrigger, ProbeAborted, ProbePair, calibrate_sgt,
)


class TreedSgtExecutor:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.running = False
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('TREED_SGT_CALIBRATE', self.cmd_calibrate)

    # Блок 1: Только известный профиль, свободный принтер и ограниченный коридор.
    def _prepare(self, gcmd):
        allowed = {'AXIS', 'SGT_MIN', 'SGT_MAX', 'TOLERANCE', 'Z_MIN',
                   'PAIRS', 'ERROR_BUDGET'}
        if set(gcmd.get_command_parameters()) - allowed:
            raise gcmd.error('Неизвестный параметр TREED_SGT_CALIBRATE')
        axis = gcmd.get('AXIS').lower()
        if axis not in ('x', 'y'):
            raise gcmd.error('SGT: допустимы только X и Y')
        now = self.printer.get_reactor().monotonic()
        stats = self.printer.lookup_object('print_stats').get_status(now)
        paused = self.printer.lookup_object('pause_resume').get_status(now)
        if (self.running or stats['state'] in ('printing', 'paused')
                or paused['is_paused']
                or self.printer.lookup_object('virtual_sdcard').is_active()):
            raise gcmd.error('SGT: принтер занят или печать приостановлена')
        settings = self.printer.lookup_object('configfile').get_status(now)['settings']
        if settings['printer']['kinematics'] != 'corexy':
            raise gcmd.error('SGT: требуется CoreXY')
        stepper = 'stepper_' + axis
        if settings[stepper]['endstop_pin'] != 'tmc5160_' + stepper + ':virtual_endstop':
            raise gcmd.error('SGT: требуется virtual_endstop TMC5160 выбранной оси')
        toolhead = self.printer.lookup_object('toolhead')
        kin = toolhead.get_kinematics()
        if not set('xyz') <= set(kin.get_status(now)['homed_axes']):
            raise gcmd.error('SGT: сначала нужна достоверная привязка XYZ')
        index = 'xy'.index(axis)
        rail = kin.rails[index]
        hi = rail.get_homing_info()
        endstops = rail.get_endstops()
        if (len(endstops) != 1 or
                {s.get_name() for s in endstops[0][0].get_steppers()}
                != {'stepper_x', 'stepper_y'}):
            raise gcmd.error('SGT: DIAG должен останавливать оба двигателя CoreXY')
        variables = self.printer.lookup_object('gcode_macro G28').variables
        backoff = float(variables['xy_backoff_mm'])
        pause = float(variables['stallguard_pause_ms']) / 1000.
        z_min = gcmd.get_float('Z_MIN', above=0.)
        cfg = CalibrationConfig(
            sgt_min=gcmd.get_int('SGT_MIN'), sgt_max=gcmd.get_int('SGT_MAX'),
            reference_mm=hi.position_endstop,
            position_tolerance_mm=gcmd.get_float('TOLERANCE', above=0.),
            home_direction=1 if hi.positive_dir else -1,
            samples_per_sgt=gcmd.get_int('PAIRS', 4),
            error_budget_mm=gcmd.get_float('ERROR_BUDGET', 0.6, above=0.))
        if not all(math.isfinite(v) and v > 0 for v in
                   (backoff, pause, z_min, hi.speed, hi.retract_speed)):
            raise gcmd.error('SGT: параметры движения должны быть конечными и положительными')
        pause = max(2., pause)
        if cfg.position_tolerance_mm >= backoff / 2:
            raise gcmd.error('SGT: допуск должен быть меньше половины отхода')
        if hi.speed > toolhead.get_max_velocity()[0]:
            raise gcmd.error('SGT: текущий max_velocity ограничивает homing_speed')
        start = cfg.reference_mm - cfg.home_direction * backoff
        low, high = rail.get_range()
        if not low <= start <= high or not low <= cfg.reference_mm <= high:
            raise gcmd.error('SGT: коридор проб выходит за границы оси')
        # Не перемещаем каретку через всё поле и не создаём Z-привязку за оператора.
        toolhead.wait_moves()
        pos = toolhead.get_position()
        if (not all(math.isfinite(v) for v in pos[:3]) or pos[2] < z_min
                or any(not rail.get_range()[0] <= pos[i] <= rail.get_range()[1]
                       for i, rail in enumerate(kin.rails))
                or not min(start, cfg.reference_mm) <= pos[index]
                <= max(start, cfg.reference_mm)):
            raise gcmd.error('SGT: установите Z не ниже Z_MIN и ось в коридор отхода от упора')
        driver = self.printer.lookup_object('tmc5160 ' + stepper)
        return toolhead, kin, index, hi, endstops, driver, cfg, start, pause

    # Блок 2: Реальные DIAG и trigger position, без home_rails/G28 и смены опоры.
    def _check_alive(self):
        if self.printer.is_shutdown():
            raise ProbeAborted('Klipper shutdown; требуется восстановление и повторный homing')

    def _set_sgt(self, driver, toolhead, value):
        self._check_alive()
        reg = driver.fields.lookup_register('sgt')
        bits = driver.fields.set_field('sgt', value)
        driver.mcu_tmc.set_register(reg, bits, toolhead.get_last_move_time())

    def _hit(self, toolhead, index, hi, endstops, cfg, start, pause):
        self._check_alive()
        self._retreat(toolhead, index, start, hi.retract_speed)
        toolhead.dwell(pause)
        toolhead.wait_moves()
        self._check_alive()
        target = toolhead.get_position()
        # Никакого обхода soft limits: отсутствие DIAG до этой точки — отказ.
        target[index] = cfg.reference_mm
        hmove = homing.HomingMove(self.printer, endstops)
        try:
            trigger = hmove.homing_move(target, hi.speed, probe_pos=True)
        except Exception:
            # Ошибка возможна и до штатного home_wait/end event; MCU должен
            # прекратить движение, даже если host не смог завершить очистку.
            self.printer.invoke_shutdown('SGT: сбой измерительного движения')
            raise
        self._check_alive()
        if hmove.check_no_movement() is not None:
            raise ProbeAborted('DIAG сработал без движения')
        if not all(math.isfinite(v) for v in trigger[:3]):
            raise ProbeAborted('Некорректная точка DIAG')
        value = trigger[index]
        if (value - cfg.reference_mm) * cfg.home_direction > cfg.position_tolerance_mm:
            raise ProbeAborted('Поздний DIAG или неверная опора')
        halt = toolhead.get_position()
        if not min(start, cfg.reference_mm) <= halt[index] <= max(start, cfg.reference_mm):
            raise ProbeAborted('Остановка вне коридора пробы')
        return value

    def _retreat(self, toolhead, index, start, speed):
        self._check_alive()
        pos = toolhead.get_position()
        pos[index] = start
        try:
            toolhead.manual_move(pos, speed)
            toolhead.wait_moves()
        except Exception:
            self.printer.invoke_shutdown('SGT: сбой отхода')
            raise

    # Блок 3: Возвращаем live SGT и поля, временно меняемые TMCVirtualPinHelper.
    def _restore(self, driver, saved, toolhead):
        registers = {}
        for field, value in saved.items():
            reg = driver.fields.lookup_register(field)
            registers[reg] = driver.fields.set_field(field, value)
        if self.printer.is_shutdown():
            return False  # Кэш восстановлен; состояние отключённого MCU неизвестно.
        try:
            for reg, bits in registers.items():
                driver.mcu_tmc.set_register(reg, bits, toolhead.get_last_move_time())
            toolhead.wait_moves()
            self._check_alive()
        except Exception:
            logging.exception('SGT: не удалось восстановить драйвер')
            self.printer.invoke_shutdown('SGT: восстановление драйвера не подтверждено')
            return False
        return True

    def cmd_calibrate(self, gcmd):
        try:
            prepared = self._prepare(gcmd)
        except ValueError as exc:
            raise gcmd.error(str(exc)) from exc
        toolhead, kin, index, hi, endstops, driver, cfg, start, pause = prepared
        fields = ('sgt', 'en_pwm_mode', 'diag0_stall', 'diag1_stall',
                  'tcoolthrs', 'thigh')
        saved = {f: driver.fields.get_field(f) for f in fields
                 if driver.fields.lookup_register(f) is not None}
        self.running = True
        result = None
        restored = False

        def probe_pair(sgt):
            self._set_sgt(driver, toolhead, sgt)
            values = []
            for _ in range(2):
                value = self._hit(toolhead, index, hi, endstops, cfg, start, pause)
                values.append(value)
                # Ранний первый DIAG отклоняет кандидат без выдуманной пары.
                if (value - cfg.reference_mm) * cfg.home_direction < -cfg.position_tolerance_mm:
                    gcmd.respond_info('SGT=%d: ранний DIAG %.6f мм' % (sgt, value))
                    raise EarlyTrigger('DIAG вне ожидаемой области')
            gcmd.respond_info('SGT=%d: DIAG %.6f / %.6f мм' % (sgt, *values))
            return ProbePair(*values)

        try:
            gcmd.respond_info('SGT: ось=%s, опора=%.6f, допуск=%.6f, скорость=%g, ускорение=%g' %
                              ('XY'[index], cfg.reference_mm, cfg.position_tolerance_mm,
                               hi.speed, toolhead.get_max_velocity()[1]))
            result = calibrate_sgt(cfg, probe_pair, self.printer.is_shutdown)
            if result.status == 'aborted':
                raise gcmd.error('SGT: поиск прерван: ' + result.reason)
            # Отход только после корректно завершённого поиска, до возврата SGT.
            self._check_alive()
            self._retreat(toolhead, index, start, hi.retract_speed)
            self._check_alive()
        finally:
            try:
                restored = self._restore(driver, saved, toolhead)
            finally:
                self.running = False
                # После контактов счётчики не доказывают физическую привязку.
                kin.clear_homing_state('xy')
            if not restored:
                raise gcmd.error('SGT: рекомендация отменена; драйвер не восстановлен на MCU, нужен FIRMWARE_RESTART')
        for item in result.candidates:
            gcmd.respond_info('SGT=%d: %s, пар=%d, средняя ошибка=%s' %
                              (item.sgt, item.status, len(item.pairs), item.mean_abs_error_mm))
        if result.recommended_sgt is None:
            gcmd.respond_info('SGT: надёжный кандидат не найден; исходный SGT восстановлен')
        else:
            gcmd.respond_info('SGT: рекомендован %d для ограниченных проб, участок %s; для G28 не проверен и не применён. Исходный SGT восстановлен' %
                              (result.recommended_sgt, result.best_interval))
        gcmd.respond_info('SGT: перед дальнейшим движением требуется повторный homing X/Y')


def load_config(config):
    return TreedSgtExecutor(config)
