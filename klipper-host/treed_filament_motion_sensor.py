"""Счётчик импульсов SFS V2.0 поверх штатного датчика движения Klipper.

Контур: required для подтверждения подачи при автоматической прочистке.
"""

from . import filament_motion_sensor


class TreedEncoderSensor(filament_motion_sensor.EncoderSensor):
    # Блок 1: Сохраняем штатный runout-контракт и публикуем число полных импульсов.
    def __init__(self, config):
        self.pulse_count = 0
        super().__init__(config)
        self.get_status = self._get_status
        config.get_printer().add_object(
            "filament_motion_sensor " + config.get_name().split()[-1], self)

    def encoder_event(self, eventtime, state):
        # Один фронт на цикл даёт консервативную оценку и не удваивает короткий импульс.
        if state:
            self.pulse_count += 1
        super().encoder_event(eventtime, state)

    def _get_status(self, eventtime):
        status = self.runout_helper.get_status(eventtime)
        status["pulse_count"] = self.pulse_count
        return status


def load_config_prefix(config):
    return TreedEncoderSensor(config)
