"""Режим TMC5160 XYZ; required, runtime API Klipper ce7002bed.

Профиль выбирается до создания шаговых осей; смена требует FIRMWARE_RESTART.
Токи, удержание и файлы пользовательских настроек остаются исходными.
"""
import logging


QUIET_STEPPER = dict(microsteps=128, step_pulse_duration=.000000100)
QUIET_TMC = dict(
    interpolate=True, stealthchop_threshold=999999,
    driver_MULTISTEP_FILT=True, driver_TOFF=3, driver_HSTRT=5,
    driver_HEND=2, driver_FD3=0, driver_DISFDCC=0, driver_CHM=0,
    driver_TBL=2, driver_TPFD=4, driver_VHIGHFS=0, driver_VHIGHCHM=0,
    driver_PWM_OFS=30, driver_PWM_GRAD=0, driver_PWM_FREQ=0,
    driver_PWM_AUTOSCALE=True, driver_PWM_AUTOGRAD=True,
    driver_FREEWHEEL=0, driver_PWM_REG=4, driver_PWM_LIM=12)
QUIET_LIMITS = dict(max_velocity=200., max_accel=50000.)


class TreedDriverMode:
    # Блок 1: Ранний выбор полного профиля до TMC и MCU_stepper.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.saved = self.printer.load_object(config, 'save_variables')
        self.loaded_mode = self.saved.allVariables.get('driver_mode', 'normal')
        if self.loaded_mode not in ('quiet', 'normal'):
            logging.warning('Режим XYZ: неверное сохранённое значение')
            self.loaded_mode = 'normal'
        if (self.printer.lookup_object('toolhead', None) is not None
                or any(self.printer.lookup_object('tmc5160 stepper_' + axis, None)
                       is not None for axis in ('x', 'y', 'z'))):
            raise config.error('driver_mode.cfg должен подключаться до TMC и toolhead')
        if self.loaded_mode == 'quiet':
            # Это разобранный конфиг в памяти, исходные файлы не записываются.
            for axis in ('x', 'y', 'z'):
                for prefix, values in (('stepper_', QUIET_STEPPER),
                                       ('tmc5160 stepper_', QUIET_TMC)):
                    for name, value in values.items():
                        config.fileconfig.set(prefix + axis, name, str(value))
            for name, value in QUIET_LIMITS.items():
                config.fileconfig.set('printer', name, str(value))
        self.microsteps = {}
        for axis in ('x', 'y', 'z'):
            count = config.getsection('stepper_' + axis).getint('microsteps')
            if count not in (1, 2, 4, 8, 16, 32, 64, 128, 256):
                raise config.error('Недопустимое число microsteps для ' + axis)
            self.microsteps[axis] = count
        self.drivers = []
        self.mode = None
        self.state = 'applying'
        self.message = None
        self.needs_restart = False
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
        try:
            with self.gcode.get_mutex():
                if not self.printer.is_shutdown():
                    self.apply_loaded_mode()
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
                    service_velocity_limit=50. if self.mode == 'quiet' else 350.,
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
            previous = dict(self.saved.allVariables)
            if mode == self.loaded_mode:
                if previous.get('driver_mode') != mode:
                    try:
                        self.save_mode(mode)
                    except Exception:
                        self.restore_selection(previous)
                        raise
                return
            try:
                self.save_mode(mode)
                self.require_idle(gcmd)
            except Exception:
                self.restore_selection(previous)
                raise
            self.mode, self.state, self.needs_restart = mode, 'applying', True
            self.message = 'Смена микрошагов: FIRMWARE_RESTART, затем требуется homing'
            try:
                self.gcode.run_script_from_command('FIRMWARE_RESTART')
            except Exception:
                self.restore_selection(previous)
                self.mode, self.state, self.needs_restart = self.loaded_mode, 'ready', False
                self.message = None
                raise
        except Exception as exc:
            raise gcmd.error('Режим XYZ: ' + str(exc)) from exc

    # Блок 3: Сохранение выбора и восстановление при отказе записи/перезапуска.
    def save_mode(self, mode):
        self.gcode.run_script_from_command(
            'SAVE_VARIABLE VARIABLE=driver_mode VALUE="' + repr(mode) + '"')

    def restore_selection(self, previous):
        self.saved.allVariables = dict(previous)
        mode = previous.get('driver_mode', self.loaded_mode)
        if mode not in ('quiet', 'normal'):
            mode = self.loaded_mode
        try:
            self.save_mode(mode)
        except Exception:
            self.state, self.needs_restart = 'fault', True
            self.message = 'Не подтверждено восстановление сохранённого режима'
            self.printer.invoke_shutdown('Режим XYZ: ' + self.message)
            raise

    # Блок 4: Потолок quiet для M204, travel и сервисных лимитов.
    def motion_limits(self):
        return (self.toolhead.max_velocity, self.toolhead.max_accel,
                self.toolhead.square_corner_velocity, self.toolhead.min_cruise_ratio)

    def limit_velocities(self, velocity, accel, scv, cruise_ratio):
        if self.loaded_mode == 'quiet':
            # None сохраняет текущий лимит; более низкие значения не повышаем.
            velocity = min(self.toolhead.max_velocity if velocity is None else velocity,
                           QUIET_LIMITS['max_velocity'])
            accel = min(self.toolhead.max_accel if accel is None else accel,
                        QUIET_LIMITS['max_accel'])
        return self.set_max_velocities(velocity, accel, scv, cruise_ratio)

    # Блок 5: Стартовая групповая запись без смены MRES в работающей кинематике.
    def write_fields(self, values):
        for driver, fields in zip(self.drivers, values):
            for field, value in fields.items():
                register = driver.fields.lookup_register(field)
                bits = driver.fields.set_field(field, value)
                driver.mcu_tmc.set_register(register, bits)
        for axis, driver, fields in zip(('x', 'y', 'z'), self.drivers, values):
            actual = driver.mcu_tmc.get_register('GCONF')
            if driver.fields.get_field('en_pwm_mode', actual) != fields['en_pwm_mode']:
                raise RuntimeError('GCONF драйвера не подтверждён')
            chop = driver.mcu_tmc.get_register('CHOPCONF')
            mres = driver.fields.get_field('mres', chop)
            if 256 >> mres != self.microsteps[axis]:
                raise RuntimeError('MRES драйвера не соответствует шагу Klipper: ' + axis)

    def apply_loaded_mode(self):
        mode = self.loaded_mode
        previous_mode = self.mode
        previous_limits = self.motion_limits()
        previous = [{field: d.fields.get_field(field)
                     for field in ('en_pwm_mode', 'tpwmthrs')} for d in self.drivers]
        self.state, self.message = 'applying', None
        try:
            if mode == 'quiet':
                # Сначала ограничиваем движение, затем включаем stealthChop.
                self.limit_velocities(None, None, None, None)
            self.write_fields([dict(en_pwm_mode=int(mode == 'quiet'), tpwmthrs=0)
                               for _ in self.drivers])
        except Exception as exc:
            self.message = str(exc)
            try:
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
