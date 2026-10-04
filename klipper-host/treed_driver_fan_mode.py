"""Обдув драйверов по нагрузке; required, адаптер controller_fan ce7002bed.

Штатный controller_fan владеет пином и таймером; extra задаёт критерий нагрузки.
Мощность регулируется от порога включения вентилятора до обычной скорости.
"""
import math


class TreedDriverFanMode:
    # Блок 1: Пресеты скоростей и защитный предел времени без охлаждения.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.saved = self.printer.load_object(config, 'save_variables')
        self.controller = self.printer.load_object(config, 'controller_fan driver_fan')
        self.normal = (self.controller.fan_speed, self.controller.idle_speed)
        self.load_delay = config.getfloat('load_delay', 30., minval=1., maxval=60.)
        if not math.isfinite(self.load_delay):
            raise config.error('Обдув драйверов: load_delay должен быть конечным числом')
        self.motor_since = self.last_load = None
        self.load_reason = 'idle'
        fan_config = config.getsection('controller_fan driver_fan')
        minimum = fan_config.getfloat('off_below', 0.)
        self.min_power = max(.01, minimum)
        active = config.getfloat('quiet_active_speed', min(.8, self.normal[0]))
        self.quiet = (active, config.getfloat('quiet_idle_speed', min(.4, self.normal[1], self.normal[0])))
        if (any(v is None or not math.isfinite(v) for v in self.quiet)
                or not 0 < self.quiet[1] <= self.quiet[0] <= self.normal[0]
                or self.quiet[1] > self.normal[1] or min(self.quiet) < minimum):
            raise config.error('Тихий обдув: скорости должны быть не ниже off_below и не выше обычных')
        self.mode, self.state, self.message = 'normal', 'ready', None
        # Подмена до klippy:ready: штатный handle_ready зарегистрирует этот callback.
        self.controller.callback = self.callback
        self.gcode.register_command('TREED_DRIVER_FAN_MODE_SET', self.cmd_set)
        self.printer.register_event_handler('klippy:ready', self.handle_ready)

    def handle_ready(self):
        settings = self.saved.allVariables.get('driver_fan_mode', 'normal')
        mode = settings.get('mode') if isinstance(settings, dict) else settings
        power = settings.get('power') if isinstance(settings, dict) else None
        if mode not in self.available_modes() or not self.valid_power(power):
            self.message = 'Сохранённый профиль недоступен; включён обычный обдув'
            mode, power = 'normal', None
        self.set_speeds(mode, power)
        self.mode = mode

    def available_modes(self):
        return ['normal', 'quiet']

    def get_status(self, eventtime):
        return dict(contract_version='1.0', mode=self.mode, state=self.state,
                    available_modes=self.available_modes(), needs_restart=False,
                    speed=self.controller.get_status(eventtime).get('speed'),
                    active_speed=self.controller.fan_speed,
                    idle_speed=self.controller.idle_speed,
                    power_control=True, min_power=self.minimum_power(), max_power=self.normal[0],
                    load_reason=self.load_reason, load_delay=self.load_delay,
                    message=self.message)

    # Блок 2: Нагрузка определяется операцией и временем удержания, а не нагревом.
    def callback(self, eventtime):
        enabled = any(self.controller.stepper_enable.lookup_enable(name).is_motor_enabled()
                      for name in self.controller.stepper_names)
        if not enabled:
            self.motor_since = None
            reason = 'idle'
        else:
            if self.motor_since is None:
                self.motor_since = eventtime
            operation = self.printer.lookup_object('gcode_macro _TREED_OPERATION_STATE')
            phase = operation.variables.get('phase', 'unknown')
            stats = self.printer.lookup_object('print_stats').get_status(eventtime)['state']
            sgt = self.printer.lookup_object('treed_sgt_executor', None)
            if phase not in ('idle', 'preparing', 'printing', 'paused', 'calibrating', 'auto_remove'):
                reason = 'unknown_state'
            elif sgt is not None and sgt.running:
                reason = 'calibrating'
            elif phase != 'idle':
                reason = phase
            elif stats in ('printing', 'paused'):
                reason = stats
            elif self.printer.lookup_object('virtual_sdcard').is_active():
                reason = 'printing'
            elif eventtime - self.motor_since >= self.load_delay:
                reason = 'motor_timeout'
            else:
                reason = 'manual_delay'
        active = reason not in ('idle', 'manual_delay')
        speed = 0.
        if active:
            self.last_load = eventtime
            speed = self.normal[0] if reason == 'unknown_state' else self.controller.fan_speed
        elif self.last_load is not None and eventtime - self.last_load < self.controller.idle_timeout:
            reason = 'cooldown'
            speed = self.controller.idle_speed
        self.load_reason = reason
        if speed != self.controller.last_speed:
            self.controller.last_speed = speed
            self.controller.fan.set_speed(speed)
        return eventtime + 1.

    # Блок 3: Мощность меняется в рабочем диапазоне, таймер нагрузки не сбрасывается.
    def minimum_power(self):
        return self.min_power

    def valid_power(self, power):
        return power is None or (type(power) in (int, float) and math.isfinite(power)
                                and self.minimum_power() <= power <= self.normal[0])

    def set_speeds(self, mode, power=None):
        active, idle = self.quiet if mode == 'quiet' else self.normal
        self.controller.fan_speed = active if power is None else power
        self.controller.idle_speed = min(idle, self.controller.fan_speed)

    def cmd_set(self, gcmd):
        mode = gcmd.get('MODE')
        params = set(gcmd.get_command_parameters())
        if 'MODE' not in params or params - {'MODE', 'POWER'} or mode not in self.available_modes():
            raise gcmd.error('Обдув драйверов: MODE=quiet|normal [POWER=проценты]')
        power = gcmd.get_float('POWER') / 100. if 'POWER' in params else None
        if not self.valid_power(power):
            raise gcmd.error('Обдув драйверов: POWER вне диапазона вентилятора')
        if self.printer.is_shutdown() or self.printer.get_state_message()[1] != 'ready':
            raise gcmd.error('Обдув драйверов: Klipper не готов')
        previous_speeds = (self.controller.fan_speed, self.controller.idle_speed)
        self.state, self.message = 'applying', None
        try:
            self.set_speeds(mode, power)
            settings = mode if power is None else dict(mode=mode, power=power)
            self.gcode.run_script_from_command(
                'SAVE_VARIABLE VARIABLE=driver_fan_mode VALUE="' + repr(settings) + '"')
        except Exception as exc:
            self.controller.fan_speed, self.controller.idle_speed = previous_speeds
            self.state, self.message = 'ready', str(exc)
            raise gcmd.error('Обдув драйверов: профиль не сохранён') from exc
        self.mode, self.state = mode, 'ready'


def load_config(config):
    return TreedDriverFanMode(config)
