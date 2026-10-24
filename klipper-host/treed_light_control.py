"""Подсветка через штатный output_pin; required, API Klipper ce7002bed.

Webhook обходит G-code mutex; пин, расписание MCU и status принадлежат Klipper.
"""


class TreedLightControl:
    # Блок 1: Один владелец пина и общая очередь для API и старых макросов.
    def __init__(self, config):
        self.printer = config.get_printer()
        self.pin = self.printer.load_object(config, 'output_pin chamber_light')
        self.printer.lookup_object('webhooks').register_endpoint(
            'treed/light/set', self.handle_set)
        self.printer.lookup_object('gcode').register_command(
            'TREED_LIGHT_SET', self.cmd_set)

    # Блок 2: Запрос принимается только при ready; очередь сохраняет MCU timing.
    def set_value(self, value):
        if self.printer.get_state_message()[1] != 'ready':
            raise self.printer.command_error('Klipper не готов к управлению подсветкой')
        self.pin.gcrq.send_async_request(float(value))

    def handle_set(self, request):
        value = request.get_int('value')
        if value not in (0, 1):
            raise request.error('value должен быть 0 или 1')
        self.set_value(value)
        request.send({'requested_value': value})

    def cmd_set(self, gcmd):
        self.set_value(gcmd.get_int('VALUE', minval=0, maxval=1))


def load_config(config):
    return TreedLightControl(config)
