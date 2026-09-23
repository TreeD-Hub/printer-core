# Диагностический sensorless-проход X/Y с ограничением хода.
# Контур: required для сервисной калибровки; обычный G28 не изменяется.

class TreedMotorSensorless:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode.register_command('TREED_SENSORLESS_PROBE', self.cmd_probe)
        self.busy = False
        self.last_result = None

    def get_status(self, eventtime):
        return {
            'busy': self.busy, 'last_result': self.last_result,
            'sgt_x': self.printer.lookup_object(
                'tmc5160 stepper_x').mcu_tmc.get_fields().get_field('sgt'),
            'sgt_y': self.printer.lookup_object(
                'tmc5160 stepper_y').mcu_tmc.get_fields().get_field('sgt'),
        }

    # Блок 1: Проверка машинного состояния и подтверждённой оператором опоры.
    def _check_ready(self, gcmd, toolhead):
        if self.busy or self.printer.is_shutdown():
            raise gcmd.error('sensorless_probe_busy_or_shutdown')
        now = self.printer.get_reactor().monotonic()
        for name, key in (('print_stats', 'state'), ('virtual_sdcard', 'is_active'),
                          ('pause_resume', 'is_paused')):
            obj = self.printer.lookup_object(name, None)
            if obj is None:
                continue
            value = obj.get_status(now).get(key)
            if value in ('printing', 'paused') or value is True:
                raise gcmd.error('printer_busy_or_paused')
        motor = self.printer.lookup_object('treed_motor_calibration', None)
        if motor is not None and (motor.state == 'running' or motor.phase_enabled):
            raise gcmd.error('motor_calibration_active')
        status = toolhead.get_status(now)
        if not all(axis in status['homed_axes'] for axis in 'xyz'):
            raise gcmd.error('verified_xyz_home_required')
        x, y, z = toolhead.get_position()[:3]
        if not (90. <= x <= 155. and 90. <= y <= 155. and z >= 10.):
            raise gcmd.error('start_at_xy_center_and_z_at_least_10')

    # Блок 2: Проба штатным Klipper HomingMove и восстановление TMC/лимитов.
    def cmd_probe(self, gcmd):
        axis = gcmd.get('AXIS').upper()
        if axis not in ('X', 'Y'):
            raise gcmd.error('AXIS must be X or Y')
        speed = gcmd.get_float('SPEED', above=0.)
        if not 40. <= speed <= 100.:
            raise gcmd.error('SPEED must be 40..100 mm/s')
        sgt = gcmd.get_int('SGT', minval=-64, maxval=63)
        toolhead = self.printer.lookup_object('toolhead')
        self._check_ready(gcmd, toolhead)
        kin = toolhead.get_kinematics()
        if kin.__class__.__name__ != 'CoreXYKinematics':
            raise gcmd.error('CoreXY required')
        axis_index = 'XY'.index(axis)
        rail = kin.rails[axis_index]
        if ((axis == 'X' and rail.position_endstop != rail.position_min) or
                (axis == 'Y' and rail.position_endstop != rail.position_max)):
            raise gcmd.error('unexpected homing direction or boundary')
        driver = self.printer.lookup_object('tmc5160 stepper_' + axis.lower())
        fields = driver.mcu_tmc.get_fields()
        previous_sgt = fields.get_field('sgt')
        old_limits = (toolhead.max_velocity, toolhead.max_accel,
                      toolhead.square_corner_velocity, toolhead.min_cruise_ratio)
        self.busy = True
        self.last_result = None
        changed = False
        try:
            self.gcode.run_script_from_command('_TREED_SENSORLESS_PREPARE')
            toolhead.set_max_velocities(None, 700., None, None)
            reg = fields.lookup_register('sgt')
            changed = True
            driver.mcu_tmc.set_register(reg, fields.set_field('sgt', sgt),
                                        toolhead.get_last_move_time())
            toolhead.wait_moves()
            readback = driver.mcu_tmc.get_register_raw(reg)
            if fields.get_field('sgt', readback['data']) != sgt:
                raise gcmd.error('SGT readback mismatch')

            start = toolhead.get_position()
            target = list(start)
            boundary = rail.position_endstop
            target[axis_index] = boundary + (3. if axis == 'Y' else -3.)
            move_start = toolhead.get_last_move_time()
            outcome = 'error'
            detail = ''
            old_range = kin.limits[axis_index]
            try:
                kin.limits[axis_index] = (
                    (boundary - 3., old_range[1]) if axis == 'X'
                    else (old_range[0], boundary + 3.))
                try:
                    self.printer.lookup_object('homing').manual_home(
                        toolhead, rail.get_endstops(), target,
                        speed, True, True, True)
                    position = toolhead.get_position()[axis_index]
                    outcome = ('pass' if abs(position - boundary) <= 5.
                               else 'false_trigger')
                except self.printer.command_error as exc:
                    detail = str(exc)
                    if 'No trigger on ' in detail:
                        outcome = 'missed_stall'
            finally:
                kin.limits[axis_index] = old_range
            move_end = toolhead.get_last_move_time()
            trigger_position = toolhead.get_position()[axis_index]

            # Отход выполняется даже после несработавшего DIAG. Ход за упор
            # ограничен 3 мм; после ошибки координату надо проверить заново.
            away = list(toolhead.get_position())
            away[axis_index] += 10. if axis == 'X' else -10.
            try:
                toolhead.move(away, 20.)
                toolhead.wait_moves()
            except self.printer.command_error as exc:
                outcome, detail = 'abort', str(exc)
            self.last_result = {
                'axis': axis, 'speed': speed, 'sgt': sgt,
                'outcome': outcome, 'detail': detail,
                'start_position': start[axis_index],
                'trigger_position': trigger_position,
                'boundary': boundary, 'move_start': move_start,
                'move_end': move_end,
            }
            gcmd.respond_info('TREED_SENSORLESS_PROBE ' + outcome)
        finally:
            try:
                if changed and not self.printer.is_shutdown():
                    driver.mcu_tmc.set_register(
                        reg, fields.set_field('sgt', previous_sgt),
                        toolhead.get_last_move_time())
                    toolhead.wait_moves()
                    restored = driver.mcu_tmc.get_register_raw(reg)
                    if fields.get_field('sgt', restored['data']) != previous_sgt:
                        raise gcmd.error('SGT restore readback mismatch')
            finally:
                toolhead.set_max_velocities(*old_limits)
                self.busy = False


def load_config(config):
    return TreedMotorSensorless(config)
