# ==========================================
# MOTION GUARD — временные лимиты движения
# ==========================================
# Назначение:
# - Выполняет только разрешённые сервисные макросы с временными лимитами.
# Контур:
# - required: восстановление лимитов и режима G90/G91 при ошибке без движения.

import math
import re


OPERATIONS = {
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


class TreedMotorGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('TREED_MOTION_GUARD', self.cmd_run)

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

        accel = gcmd.get_float('ACCEL', above=0.)
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
                if action == 'SHAPER':
                    state = self.printer.lookup_object(
                        'gcode_macro _TREED_OPERATION_STATE')
                    if state.variables['phase'] in ('calibrating',
                                                   'start_calibrating'):
                        state.variables = dict(state.variables, phase='idle')
                raise
        finally:
            toolhead.set_max_velocities(*old_limits)


def load_config(config):
    return TreedMotorGuard(config)
