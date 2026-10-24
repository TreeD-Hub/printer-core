# ==========================================
# MOTION GUARD — временные лимиты движения
# ==========================================
# Назначение:
# - Выполняет только разрешённые сервисные макросы с временными лимитами.
# - Применяет общие номиналы сервисного G1 без изменения координатного контракта Klipper.
# Контур:
# - required: восстановление лимитов и режима G90/G91 при ошибке без движения.

import logging
import math
import re
from decimal import Decimal


OPERATIONS = {
    'START_WIPE': ('_TREED_START_NOZZLE_WIPE_PREP', set()),
    'HOME_XY': ('_TREED_HOME_XY_RUN', {'X', 'Y', 'ACCEL'}),
    'SHAPER': ('_TREED_SHAPER_CALIBRATE_RUN', {
        'MODE', 'SAVE', 'ACCEL', 'FULL_FREQ_START', 'FULL_FREQ_END',
        'FULL_HZ_PER_SEC', 'LIGHT_WINDOW', 'LIGHT_FREQ_MIN',
        'LIGHT_FREQ_MAX', 'LIGHT_HZ_PER_SEC', 'ACCEL_PER_HZ'}),
    'XY_TEST': ('_TREED_XY_MOTION_TEST_RUN', {
        'SPEED', 'ACCEL', 'ITER', 'MARGIN', 'Z', 'END_Z', 'Z_SPEED',
        'SCV', 'ZIGZAG_STEPS', 'CIRCLE_RADIUS', 'SMALL_STEP',
        'SMALL_REPEATS'}),
}
SERVICE_MOTION = {
    'PARK': 'park_velocity',
    'RESUME_WIPE': 'resume_wipe_velocity',
    'START_WIPE': 'start_wipe_velocity',
    'TRAVEL': 'travel_velocity',
    'Z_TRAVEL': 'z_travel_velocity',
    'CANCEL_Z': 'cancel_z_velocity',
}


class TreedMotorGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('TREED_MOTION_GUARD', self.cmd_run)
        self.gcode.register_command('TREED_SERVICE_MOVE', self.cmd_service_move)
        self.gcode.register_command('TREED_SERVICE_RESUME', self.cmd_service_resume)

    # Блок 1а: Проверка профиля и перевод физической скорости в штатный G-code F.
    def service_move(self, gcmd, kind, params):
        driver_mode = self.printer.lookup_object('treed_driver_mode')
        if (driver_mode.state != 'ready' or driver_mode.mode not in ('quiet', 'normal')
                or driver_mode.needs_restart or self.printer.is_shutdown()):
            raise gcmd.error('TREED_SERVICE_MOVE: профиль XYZ не готов')
        toolhead = self.printer.lookup_object('toolhead')
        gcode_move = self.printer.lookup_object('gcode_move')
        factor = gcode_move.speed_factor
        ceiling = toolhead.max_velocity
        try:
            speed = (float(params['SPEED']) if 'SPEED' in params else
                     driver_mode.motion[SERVICE_MOTION[kind]])
            coordinates = {axis: float(params[axis]) for axis in ('X', 'Y', 'Z')
                           if axis in params}
        except (TypeError, ValueError):
            raise gcmd.error('TREED_SERVICE_MOVE: неверное число')
        if any(not math.isfinite(value) for value in coordinates.values()):
            raise gcmd.error('TREED_SERVICE_MOVE: координаты должны быть конечными')
        if (not coordinates or not math.isfinite(speed) or speed <= 0.
                or not math.isfinite(factor) or factor <= 0.
                or not math.isfinite(ceiling) or ceiling <= 0.):
            raise gcmd.error('TREED_SERVICE_MOVE: нужны оси и конечные положительные лимиты')
        # В Python Klipper factor равен multiplier/60; G-code F снова задаётся в мм/мин.
        physical_speed = min(speed * factor * 60., ceiling)
        feed = physical_speed / factor
        if not math.isfinite(feed) or feed <= 0.:
            raise gcmd.error('TREED_SERVICE_MOVE: подача вне конечного положительного диапазона')
        # Экспонента E в обычном G1 распознаётся как ось экструдера.
        script = 'G1 ' + ' '.join('%s%s' % (axis, format(Decimal(str(value)), 'f'))
                                  for axis, value in coordinates.items())
        script += ' F%s' % format(Decimal(format(feed, '.12g')), 'f')
        self.gcode.run_script_from_command(
            'SAVE_GCODE_STATE NAME=TREED_SERVICE_MOVE_STATE')
        try:
            self.gcode.run_script_from_command(script)
        finally:
            try:
                self._run_state_cleanup(
                    'RESTORE_GCODE_STATE NAME=TREED_SERVICE_MOVE_STATE MOVE=0')
            except BaseException:
                self.printer.invoke_shutdown(
                    'TREED_SERVICE_MOVE: не подтверждено восстановление G-code state')
                raise

    def cmd_service_move(self, gcmd):
        try:
            params = gcmd.get_command_parameters()
            if (set(params) - {'KIND', 'X', 'Y', 'Z', 'SPEED'}
                    or 'KIND' not in params):
                raise gcmd.error('TREED_SERVICE_MOVE: неверные параметры')
            kind = params['KIND'].upper()
            if kind not in SERVICE_MOTION:
                raise gcmd.error('TREED_SERVICE_MOVE: неизвестный KIND')
            self.service_move(gcmd, kind, params)
        except BaseException:
            self._restore_failed_start_wipe()
            raise

    def _run_state_cleanup(self, script):
        cancel = self.printer.lookup_object('treed_print_cancel', None)
        if cancel is not None:
            return cancel.run_state_cleanup(script)
        return self.gcode.run_script_from_command(script)

    def _restore_failed_start_wipe(self):
        state = self.printer.lookup_object('gcode_macro _TREED_START_PURGE_CFG', None)
        if state is not None and state.variables['state_saved'] == 1:
            try:
                self._run_state_cleanup('_TREED_PURGE_RESTORE_STATE')
            except BaseException:
                logging.exception('TREED_SERVICE_MOVE: очистка стартового wipe не выполнена')
                self.printer.invoke_shutdown(
                    'TREED_SERVICE_MOVE: не подтверждено восстановление состояния wipe')

    def cmd_service_resume(self, gcmd):
        params = gcmd.get_command_parameters()
        if set(params) - {'VELOCITY'}:
            raise gcmd.error('TREED_SERVICE_RESUME: неверные параметры')
        driver_mode = self.printer.lookup_object('treed_driver_mode')
        if (driver_mode.state != 'ready' or driver_mode.mode not in ('quiet', 'normal')
                or driver_mode.needs_restart or self.printer.is_shutdown()):
            raise gcmd.error('TREED_SERVICE_RESUME: профиль XYZ не готов')
        ceiling = self.printer.lookup_object('toolhead').max_velocity
        try:
            velocity = (float(params['VELOCITY']) if 'VELOCITY' in params else
                        driver_mode.motion['resume_velocity'])
        except (TypeError, ValueError):
            raise gcmd.error('TREED_SERVICE_RESUME: неверное число')
        if (not math.isfinite(velocity) or velocity <= 0.
                or not math.isfinite(ceiling) or ceiling <= 0.):
            raise gcmd.error('TREED_SERVICE_RESUME: скорость должна быть конечной и положительной')
        self.gcode.run_script_from_command(
            'RESUME_BASE VELOCITY=%s' % min(velocity, ceiling))

    # Блок 1: Проверка команды и единственная граница временных лимитов.
    def cmd_run(self, gcmd):
        action = gcmd.get('ACTION').upper()
        if action not in OPERATIONS:
            raise gcmd.error('TREED_MOTION_GUARD: неизвестная операция')
        macro, allowed = OPERATIONS[action]
        params = gcmd.get_command_parameters()
        if any(key not in allowed | {'ACTION'} or
               not re.fullmatch(r'[A-Za-z0-9_.+-]+', value)
               for key, value in params.items()):
            raise gcmd.error('TREED_MOTION_GUARD: неверный параметр')

        if action == 'START_WIPE':
            state = self.printer.lookup_object('gcode_macro _TREED_START_PURGE_CFG')
            if state.variables['state_saved'] != 0:
                raise gcmd.error('NOZZLE_WIPE: подготовка прервана; сначала выполните CANCEL_PRINT')

        accel = (None if action == 'START_WIPE' else
                 gcmd.get_float('ACCEL', above=0.))
        velocity = gcmd.get_float('SPEED', above=0.) if action == 'XY_TEST' else None
        scv = gcmd.get_float('SCV', minval=0.) if action == 'XY_TEST' else None
        if not all(math.isfinite(value) for value in (accel, velocity, scv)
                   if value is not None):
            raise gcmd.error('TREED_MOTION_GUARD: лимиты должны быть конечными')

        toolhead = self.printer.lookup_object('toolhead')
        gcode_move = self.printer.lookup_object('gcode_move')
        old_limits = (toolhead.max_velocity, toolhead.max_accel,
                      toolhead.square_corner_velocity,
                      toolhead.min_cruise_ratio)
        old_absolute = gcode_move.absolute_coord
        script = macro + ' ' + ' '.join(
            '%s=%s' % (key, value) for key, value in params.items()
            if key != 'ACTION')
        try:
            toolhead.set_max_velocities(velocity, accel, scv, None)
            try:
                self.gcode.run_script_from_command(script)
            except BaseException:
                # Координаты после ошибки остаются фактическими; возвращаем лишь режим G90/G91.
                gcode_move.absolute_coord = old_absolute
                if action == 'START_WIPE':
                    self._restore_failed_start_wipe()
                if action == 'SHAPER':
                    state = self.printer.lookup_object(
                        'gcode_macro _TREED_OPERATION_STATE')
                    if state.variables['phase'] == 'calibrating':
                        state.variables = dict(state.variables, phase='idle')
                raise
        finally:
            try:
                toolhead.set_max_velocities(*old_limits)
            except BaseException:
                self.printer.invoke_shutdown(
                    'TREED_MOTION_GUARD: не подтверждено восстановление лимитов')
                raise


def load_config(config):
    return TreedMotorGuard(config)
