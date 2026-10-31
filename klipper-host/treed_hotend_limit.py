# Назначение: ограничить цель хотэнда независимо от аварийного предела датчика.
# Контур: required; действует для G-code, API и штатной PID-калибровки.
import math
import shlex


class TreedHotendLimit:
    # Блок 1: Подключение после штатной регистрации PID и загрузки нагревателей.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.config_error = config.error
        self.max_target = config.getfloat('max_target', above=0.)
        # Новые версии Klipper регистрируют PID в собственном connect-обработчике.
        self.printer.load_object(config, 'pid_calibrate')
        self.printer.register_event_handler('klippy:connect', self._connect)

    def _connect(self):
        self.gcode = self.printer.lookup_object('gcode')
        heater = self.printer.lookup_object('heaters').lookup_heater('extruder')
        if (not math.isfinite(self.max_target)
                or not heater.min_temp < self.max_target <= heater.max_temp
                or not callable(getattr(heater, 'set_temp', None))
                or not callable(getattr(heater, 'alter_target', None))):
            raise self.config_error('TREED_HOTEND_LIMIT: несовместимый нагреватель или предел')
        help_text = self.gcode.get_command_help().get('PID_CALIBRATE')
        self.pid_calibrate = self.gcode.register_command('PID_CALIBRATE', None)
        if self.pid_calibrate is None:
            raise self.config_error('TREED_HOTEND_LIMIT: нет PID_CALIBRATE')
        self.gcode.register_command('PID_CALIBRATE', self._pid_calibrate,
                                    desc=help_text)
        self.set_temp = heater.set_temp
        self.alter_target = heater.alter_target
        heater.set_temp = self._set_temp
        heater.alter_target = self._alter_target

    # Блок 2: Общая граница целей; штатная проверка нижнего предела сохраняется.
    def _bounded_target(self, target):
        if not math.isfinite(target):
            raise self.printer.command_error('Температура хотэнда должна быть конечным числом')
        return min(target, self.max_target)

    def _set_temp(self, target):
        bounded = self._bounded_target(target)
        if bounded != target:
            self.gcode.respond_info('Температура хотэнда ограничена до %.1f °C' % bounded)
        return self.set_temp(bounded)

    def _alter_target(self, target):
        return self.alter_target(self._bounded_target(target))

    # Блок 3: PID получает ту же цель до создания ControlAutoTune.
    def _pid_calibrate(self, gcmd):
        if gcmd.get('HEATER') == 'extruder':
            target = gcmd.get_float('TARGET')
            bounded = self._bounded_target(target)
            if bounded != target:
                gcmd.respond_info('Цель PID хотэнда ограничена до %.1f °C' % bounded)
                params = gcmd.get_command_parameters()
                params['TARGET'] = str(bounded)
                # Снятый обработчик заново разбирает исходную строку команды.
                commandline = 'PID_CALIBRATE ' + ' '.join(
                    '%s=%s' % (key, shlex.quote(value)) for key, value in params.items())
                gcmd = self.gcode.create_gcode_command('PID_CALIBRATE', commandline, params)
        return self.pid_calibrate(gcmd)


def load_config(config):
    return TreedHotendLimit(config)
