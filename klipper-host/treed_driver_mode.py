"""Режим TMC5160 XYZ; required, runtime API Klipper ce7002bed.

Переключение допускается только вне печати и сервисных операций.
Токи, microsteps и удержание моторов остаются исходными.
"""
import logging


class TreedDriverMode:
    # Блок 1: Единственный владелец рабочего режима XYZ и его состояния.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.saved = self.printer.load_object(config, 'save_variables')
        self.drivers = []
        self.mode = None
        self.state = 'applying'
        self.message = None
        self.needs_restart = False
        self.normal_limits = None
        self.gcode.register_command('TREED_DRIVER_MODE_SET', self.cmd_set)
        self.printer.register_event_handler('klippy:connect', self.handle_connect)
        self.printer.register_event_handler('klippy:ready', self.handle_ready)

    def handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.set_max_velocities = self.toolhead.set_max_velocities
        self.toolhead.set_max_velocities = self.limit_velocities
        self.drivers = [self.printer.lookup_object('tmc5160 stepper_' + axis)
                        for axis in ('x', 'y', 'z')]

    def handle_ready(self):
        # ready запрещает ожидание SPI; отдельный callback допускает ответ MCU.
        self.state = 'applying'
        self.printer.get_reactor().register_callback(self.apply_saved_mode)

    def apply_saved_mode(self, eventtime):
        if self.printer.is_shutdown():
            return
        mode = self.saved.allVariables.get('driver_mode', 'normal')
        if mode not in ('quiet', 'normal'):
            logging.warning('Режим XYZ: неверное сохранённое значение')
            mode = 'normal'
        try:
            with self.gcode.get_mutex():
                if not self.printer.is_shutdown():
                    self.apply(mode, persist=False)
        except Exception:
            logging.exception('Режим XYZ: ошибка стартового применения')
            self.state, self.needs_restart = 'fault', True
            self.printer.invoke_shutdown('Режим XYZ: стартовое применение не подтверждено')

    def get_status(self, eventtime):
        effective = {}
        for axis, driver in zip(('X', 'Y', 'Z'), self.drivers):
            pwm = driver.fields.get_field('en_pwm_mode')
            threshold = driver.fields.get_field('tpwmthrs')
            effective[axis] = ('normal' if not pwm or threshold == 0xfffff
                               else 'quiet' if threshold == 0 else None)
        return dict(contract_version='1.0', mode=self.mode, state=self.state,
                    available_modes=['normal', 'quiet'], effective_modes=effective,
                    needs_restart=self.needs_restart, message=self.message)

    # Блок 2: Повторный допуск после ожидания очереди, включая ручной сервис.
    def require_idle(self, gcmd):
        now = self.printer.get_reactor().monotonic()
        phase = self.printer.lookup_object('gcode_macro _TREED_OPERATION_STATE').variables['phase']
        stats = self.printer.lookup_object('print_stats').get_status(now)['state']
        pause = self.printer.lookup_object('pause_resume')
        manual = self.printer.lookup_object('manual_probe', None)
        recovery = self.printer.lookup_object('treed_z_recovery', None)
        sgt = self.printer.lookup_object('treed_sgt_executor', None)
        if (self.state != 'ready' or self.needs_restart or self.printer.is_shutdown()
                or self.printer.get_state_message()[1] != 'ready'
                or phase != 'idle' or stats in ('printing', 'paused', 'error')
                or pause.get_status(now)['is_paused'] or pause.pause_command_sent
                or self.printer.lookup_object('virtual_sdcard').is_active()
                or (manual is not None and manual.get_status(now)['is_active'])
                or (recovery is not None and (recovery.running or recovery.eddy_homing))
                or (sgt is not None and sgt.running)):
            raise gcmd.error('Режим XYZ: нужны готовый Klipper и завершённая печать/калибровка')

    def cmd_set(self, gcmd):
        params = gcmd.get_command_parameters()
        mode = gcmd.get('MODE')
        if set(params) != {'MODE'} or mode not in ('quiet', 'normal'):
            raise gcmd.error('Режим XYZ: нужен только MODE=quiet|normal')
        self.require_idle(gcmd)
        self.toolhead.wait_moves()
        self.require_idle(gcmd)
        try:
            self.apply(mode)
        except Exception as exc:
            raise gcmd.error('Режим XYZ: ' + str(exc)) from exc

    # Блок 3: Общий потолок XYZ, включая M204, travel и сервисные лимиты.
    def motion_limits(self):
        return (self.toolhead.max_velocity, self.toolhead.max_accel,
                self.toolhead.square_corner_velocity, self.toolhead.min_cruise_ratio)

    def limit_velocities(self, velocity, accel, scv, cruise_ratio):
        if self.normal_limits is not None:
            # None сохраняет текущий лимит; более низкие значения не повышаем.
            velocity = min(self.toolhead.max_velocity if velocity is None else velocity, 350.)
            accel = min(self.toolhead.max_accel if accel is None else accel, 15000.)
            scv = min(self.toolhead.square_corner_velocity if scv is None else scv, 350.)
        return self.set_max_velocities(velocity, accel, scv, cruise_ratio)

    # Блок 4: Групповая запись с откатом драйверов и лимитов движения.
    def write_fields(self, values):
        for driver, fields in zip(self.drivers, values):
            for field, value in fields.items():
                register = driver.fields.lookup_register(field)
                bits = driver.fields.set_field(field, value)
                driver.mcu_tmc.set_register(register, bits)
        for driver, fields in zip(self.drivers, values):
            actual = driver.mcu_tmc.get_register('GCONF')
            if driver.fields.get_field('en_pwm_mode', actual) != fields['en_pwm_mode']:
                raise RuntimeError('GCONF драйвера не подтверждён')

    def apply(self, mode, persist=True):
        previous_mode = self.mode
        previous_limits = self.motion_limits()
        previous_normal_limits = self.normal_limits
        previous = [{field: d.fields.get_field(field)
                     for field in ('en_pwm_mode', 'tpwmthrs')} for d in self.drivers]
        self.state, self.message = 'applying', None
        try:
            if mode == 'quiet':
                if self.normal_limits is None:
                    self.normal_limits = previous_limits
                # Сначала ограничиваем движение, затем включаем stealthChop.
                self.limit_velocities(None, None, None, None)
            self.write_fields([dict(en_pwm_mode=int(mode == 'quiet'), tpwmthrs=0)
                               for _ in self.drivers])
            if mode == 'normal' and self.normal_limits is not None:
                limits = self.normal_limits
                self.normal_limits = None
                self.set_max_velocities(*limits)
            if persist:
                self.gcode.run_script_from_command(
                    'SAVE_VARIABLE VARIABLE=driver_mode VALUE="' + repr(mode) + '"')
        except Exception as exc:
            self.message = str(exc)
            try:
                # При возврате в quiet потолок действует до восстановления TMC.
                self.normal_limits = previous_normal_limits
                self.set_max_velocities(*previous_limits)
                self.write_fields(previous)
                self.mode, self.state = previous_mode, 'ready'
            except Exception:
                self.mode, self.state, self.needs_restart = None, 'fault', True
                self.printer.invoke_shutdown('Режим XYZ: восстановление драйверов и лимитов не подтверждено')
            raise
        self.mode, self.state = mode, 'ready'


def load_config(config):
    return TreedDriverMode(config)
