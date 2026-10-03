"""Профили обдува драйверов; required, адаптер controller_fan ce7002bed.

Штатный controller_fan остаётся единственным владельцем пина и автоматики.
Тихие скорости доступны только после явной аппаратной приёмки.
"""
import math


class TreedDriverFanMode:
    # Блок 1: Проверяемые скорости профиля без произвольного PWM из UI.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.saved = self.printer.load_object(config, 'save_variables')
        self.controller = self.printer.load_object(config, 'controller_fan driver_fan')
        self.normal = (self.controller.fan_speed, self.controller.idle_speed)
        fan_config = config.getsection('controller_fan driver_fan')
        minimum = fan_config.getfloat('off_below', 0.)
        self.quiet = (config.getfloat('quiet_active_speed', None),
                      config.getfloat('quiet_idle_speed', None))
        self.validated = config.getboolean('quiet_validated', False)
        if self.validated and (any(v is None or not math.isfinite(v) for v in self.quiet)
                or not 0 < self.quiet[1] <= self.quiet[0] <= self.normal[0]
                or self.quiet[1] > self.normal[1] or min(self.quiet) < minimum):
            raise config.error('Тихий обдув: нужны проверенные скорости выше off_below, не выше обычных')
        self.mode, self.state, self.message = 'normal', 'ready', None
        self.gcode.register_command('TREED_DRIVER_FAN_MODE_SET', self.cmd_set)
        self.printer.register_event_handler('klippy:ready', self.handle_ready)

    def handle_ready(self):
        mode = self.saved.allVariables.get('driver_fan_mode', 'normal')
        if mode not in self.available_modes():
            self.message = 'Сохранённый тихий профиль недоступен; включён обычный обдув'
            mode = 'normal'
        self.set_speeds(mode)
        self.mode = mode

    def available_modes(self):
        return ['normal', 'quiet'] if self.validated else ['normal']

    def get_status(self, eventtime):
        return dict(contract_version='1.0', mode=self.mode, state=self.state,
                    available_modes=self.available_modes(), needs_restart=False,
                    speed=self.controller.get_status(eventtime).get('speed'),
                    active_speed=self.controller.fan_speed,
                    idle_speed=self.controller.idle_speed,
                    message=self.message if self.validated else
                    'Тихий обдув требует настройки скоростей и quiet_validated=True')

    # Блок 2: Меняем только профиль, сохраняя last_on и таймер охлаждения.
    def set_speeds(self, mode):
        self.controller.fan_speed, self.controller.idle_speed = (
            self.quiet if mode == 'quiet' else self.normal)

    def cmd_set(self, gcmd):
        mode = gcmd.get('MODE')
        if set(gcmd.get_command_parameters()) != {'MODE'} or mode not in self.available_modes():
            raise gcmd.error('Обдув драйверов: MODE=quiet|normal; тихий профиль должен быть проверен')
        if self.printer.is_shutdown() or self.printer.get_state_message()[1] != 'ready':
            raise gcmd.error('Обдув драйверов: Klipper не готов')
        previous = self.mode
        self.state, self.message = 'applying', None
        try:
            self.set_speeds(mode)
            self.gcode.run_script_from_command(
                'SAVE_VARIABLE VARIABLE=driver_fan_mode VALUE="' + repr(mode) + '"')
        except Exception as exc:
            self.set_speeds(previous)
            self.state, self.message = 'ready', str(exc)
            raise gcmd.error('Обдув драйверов: профиль не сохранён') from exc
        self.mode, self.state = mode, 'ready'


def load_config(config):
    return TreedDriverFanMode(config)
