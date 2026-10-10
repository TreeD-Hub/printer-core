# Назначение: отмена через штатный API Moonraker прерывает температурные
# ожидания и остаток макроса до выполнения штатного CANCEL_PRINT.
# После отмены применяет Z-поправку к работающей калибровке Eddy без перезапуска.
# Контур: required для профиля; движение внутри уже начатой команды не прерывается.
import math

import gcode


# Блок 1: Управляемое завершение текущего скрипта без shutdown Klipper.
class PrintCancelled(gcode.CommandError):
    pass


class TreedPrintCancel:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.config_error = config.error
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.cancel_pending = False
        self.cleanup_active = False
        self.printer.register_event_handler('klippy:connect', self._connect)

    # Блок 2: Подключение к закреплённому Klipper после загрузки всех конфигов.
    def _connect(self):
        self.heaters = self.printer.lookup_object('heaters')
        self.pause_resume = self.printer.lookup_object('pause_resume')
        self.virtual_sd = self.printer.lookup_object('virtual_sdcard')
        required = ((self.gcode, 'run_script'),
                    (self.gcode, 'run_script_from_command'),
                    (self.gcode, 'get_mutex'),
                    (self.heaters, '_wait_for_temperature'),
                    (self.heaters, '_get_temp'),
                    (self.heaters, 'turn_off_all_heaters'),
                    (self.virtual_sd, 'is_cmd_from_sd'))
        state = ((self.heaters, 'heaters'),
                 (self.heaters, 'available_sensors'),
                 (self.virtual_sd, 'current_file'),
                 (self.virtual_sd, 'must_pause_work'),
                 (self.pause_resume, 'sd_paused'))
        if (any(not callable(getattr(obj, name, None)) for obj, name in required)
                or any(not hasattr(obj, name) for obj, name in state)):
            raise self.config_error('TREED_PRINT_CANCEL: несовместимый Klipper')
        help_text = self.gcode.get_command_help().get('TEMPERATURE_WAIT')
        original_wait = self.gcode.register_command('TEMPERATURE_WAIT', None)
        if original_wait is None:
            raise self.config_error('TREED_PRINT_CANCEL: нет TEMPERATURE_WAIT')
        self.gcode.register_command('TEMPERATURE_WAIT', self._temperature_wait,
                                    desc=help_text)
        self.gcode.register_command('_TREED_EDDY_APPLY_LIVE_Z_OFFSET',
                                    self._apply_live_z_offset)

        self.command_script = self.gcode.run_script_from_command
        self.gcode.run_script = self._run_script
        self.gcode.run_script_from_command = self._run_script_from_command
        self.heaters._wait_for_temperature = self._wait_for_temperature

    # Блок 3: Сигнал отмены поступает до G-code mutex, cleanup — после его освобождения.
    def _run_script(self, script):
        lines = script.strip().splitlines()
        if len(lines) == 1 and lines[0].split(None, 1)[0].upper() == 'CANCEL_PRINT':
            return self._cancel(script)
        with self.gcode.get_mutex():
            self._check_cancel()
            try:
                self._run_script_from_command(script)
            except PrintCancelled:
                # virtual_sd завершает текущую строку без note_error; штатная
                # отмена ниже закрывает файл и переводит print_stats в cancelled.
                if not self.virtual_sd.is_cmd_from_sd():
                    raise

    def _cancel(self, script):
        if self.cancel_pending:
            return
        self.cancel_pending = True
        try:
            self.heaters.turn_off_all_heaters()
            if self.virtual_sd.current_file is not None:
                self.virtual_sd.must_pause_work = True
                # CANCEL_PRINT_BASE должен закрыть и уже остановленную SD-задачу.
                self.pause_resume.sd_paused = True
            with self.gcode.get_mutex():
                self.cleanup_active = True
                try:
                    self.command_script(script)
                finally:
                    self.cleanup_active = False
        finally:
            self.cancel_pending = False

    def _check_cancel(self):
        if self.cancel_pending and not self.cleanup_active:
            self.heaters.turn_off_all_heaters()
            raise PrintCancelled('TreeD: выполнение прервано отменой печати')

    def _run_script_from_command(self, script):
        for line in script.split('\n'):
            self._check_cancel()
            self.command_script(line)

    # Блок 4: Семантика штатных температурных ожиданий с проверкой отмены каждые 100 мс.
    def _wait_until(self, ready):
        self._check_cancel()
        if self.printer.get_start_args().get('debugoutput') is not None:
            return
        toolhead = self.printer.lookup_object('toolhead')
        eventtime = self.reactor.monotonic()
        next_report = eventtime
        while not self.printer.is_shutdown():
            self._check_cancel()
            if ready(eventtime):
                return
            if eventtime >= next_report:
                toolhead.get_last_move_time()
                self.gcode.respond_raw(self.heaters._get_temp(eventtime))
                next_report = eventtime + 1.
            eventtime = self.reactor.pause(eventtime + .1)

    def _wait_for_temperature(self, heater):
        self._wait_until(lambda eventtime: not heater.check_busy(eventtime))

    def _temperature_wait(self, gcmd):
        name = gcmd.get('SENSOR')
        minimum = gcmd.get_float('MINIMUM', float('-inf'))
        maximum = gcmd.get_float('MAXIMUM', float('inf'), above=minimum)
        if minimum == float('-inf') and maximum == float('inf'):
            raise gcmd.error('TEMPERATURE_WAIT: требуется MINIMUM или MAXIMUM')
        if name not in self.heaters.available_sensors:
            raise gcmd.error('TEMPERATURE_WAIT: неизвестный SENSOR ' + name)
        sensor = self.heaters.heaters.get(name)
        if sensor is None:
            sensor = self.printer.lookup_object(name)

        def ready(eventtime):
            temperature, _target = sensor.get_temp(eventtime)
            return minimum <= temperature <= maximum

        self._wait_until(ready)

    # Блок 5: После отмены следующий Z-home использует поправку без SAVE_CONFIG.
    def _apply_live_z_offset(self, gcmd):
        state = self.printer.lookup_object('print_stats').get_status(
            self.reactor.monotonic())['state']
        if state in ('printing', 'paused') or self.virtual_sd.current_file is not None:
            raise gcmd.error('Eddy: поправка допустима только после остановки печати')
        offset = gcmd.get_float('Z')
        if not math.isfinite(offset):
            raise gcmd.error('Eddy: Z-offset должен быть конечным числом')
        if offset == 0.:
            return
        probe = self.printer.lookup_object('probe_eddy_current btt_eddy', None)
        calibration = getattr(probe, 'calibration', None)
        if any(not callable(getattr(calibration, name, None)) for name in
               ('verify_calibrated', 'get_calibration', '_load_calibration')):
            raise gcmd.error('Eddy: несовместимая live-калибровка Klipper')
        calibration.verify_calibrated()
        frequencies, heights = calibration.get_calibration()
        shifted = [(height - offset, freq) for height, freq in zip(heights, frequencies)]
        self.printer.lookup_object('toolhead').wait_moves()
        self._check_cancel()
        # Штатная команда читает live offset; восстанавливаем его и при ошибке записи.
        original_offset = self.printer.lookup_object('gcode_move').get_status()['homing_origin'].z
        try:
            self.command_script('SET_GCODE_OFFSET Z=%s MOVE=0' % offset)
            self.command_script('Z_OFFSET_APPLY_PROBE')
        finally:
            self.command_script('SET_GCODE_OFFSET Z=%s MOVE=0' % original_offset)
        calibration._load_calibration(shifted)


def load_config(config):
    return TreedPrintCancel(config)
