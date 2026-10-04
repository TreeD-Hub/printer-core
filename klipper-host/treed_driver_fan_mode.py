"""Обдув драйверов по нагрузке; required, адаптер controller_fan ce7002bed.

Штатный controller_fan владеет пином и таймером; extra задаёт критерий нагрузки.
Подключённые вентиляторы работают только при полной мощности.
"""
import math


class TreedDriverFanMode:
    # Блок 1: Полная мощность и защитный предел времени без охлаждения.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.saved = self.printer.load_object(config, 'save_variables')
        self.controller = self.printer.load_object(config, 'controller_fan driver_fan')
        self.normal = (1., 1.)
        self.controller.fan_speed, self.controller.idle_speed = self.normal
        self.load_delay = config.getfloat('load_delay', 30., minval=1., maxval=60.)
        if not math.isfinite(self.load_delay):
            raise config.error('Обдув драйверов: load_delay должен быть конечным числом')
        self.motor_since = self.last_load = None
        self.load_reason = 'idle'
        # Старые параметры в local_overrides читаются, но не влияют на охлаждение.
        config.getfloat('quiet_active_speed', None)
        config.getfloat('quiet_idle_speed', None)
        self.mode, self.state, self.message = 'normal', 'ready', None
        # Подмена до klippy:ready: штатный handle_ready зарегистрирует этот callback.
        self.controller.callback = self.callback
        self.gcode.register_command('TREED_DRIVER_FAN_MODE_SET', self.cmd_set)
        self.printer.register_event_handler('klippy:ready', self.handle_ready)

    def handle_ready(self):
        settings = self.saved.allVariables.get('driver_fan_mode', 'normal')
        mode = settings.get('mode') if isinstance(settings, dict) else settings
        power = settings.get('power') if isinstance(settings, dict) else None
        self.message = None
        if mode != 'normal' or power is not None:
            self.message = 'Сохранённая регулировка недоступна; включён обдув 100%'
        self.set_speeds()
        self.mode = 'normal'

    def available_modes(self):
        return ['normal']

    def get_status(self, eventtime):
        return dict(contract_version='1.0', mode=self.mode, state=self.state,
                    available_modes=self.available_modes(), needs_restart=False,
                    speed=self.controller.get_status(eventtime).get('speed'),
                    active_speed=self.controller.fan_speed,
                    idle_speed=self.controller.idle_speed,
                    power_control=False, min_power=1., max_power=1.,
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

    # Блок 3: Старые пресеты не могут снизить питание; режим сохраняется без перезапуска.
    def set_speeds(self):
        self.controller.fan_speed, self.controller.idle_speed = self.normal

    def cmd_set(self, gcmd):
        mode = gcmd.get('MODE')
        params = set(gcmd.get_command_parameters())
        if params != {'MODE'} or mode != 'normal':
            raise gcmd.error('Обдув драйверов: доступен только MODE=normal на 100%')
        if self.printer.is_shutdown() or self.printer.get_state_message()[1] != 'ready':
            raise gcmd.error('Обдув драйверов: Klipper не готов')
        previous_speeds = (self.controller.fan_speed, self.controller.idle_speed)
        self.state, self.message = 'applying', None
        try:
            self.set_speeds()
            settings = mode
            self.gcode.run_script_from_command(
                'SAVE_VARIABLE VARIABLE=driver_fan_mode VALUE="' + repr(settings) + '"')
        except Exception as exc:
            self.controller.fan_speed, self.controller.idle_speed = previous_speeds
            self.state, self.message = 'ready', str(exc)
            raise gcmd.error('Обдув драйверов: профиль не сохранён') from exc
        self.mode, self.state = mode, 'ready'


def load_config(config):
    return TreedDriverFanMode(config)
