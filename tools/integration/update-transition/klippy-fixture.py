"""Имитатор протокола Klippy для гостевой системы; Moonraker и systemd настоящие."""
import asyncio
import json
from pathlib import Path
import sys
import time

# Блок 1: Состояние имитируемого оборудования и admission gate.
home = Path('/home/radxa')
socket = home / 'printer_data/comms/klippy.sock'
objects = {
    'webhooks': {'state': 'ready', 'state_message': 'integration Klippy fixture'},
    'print_stats': {'state': 'standby', 'filename': '', 'total_duration': 0, 'print_duration': 0, 'filament_used': 0, 'message': ''},
    'virtual_sdcard': {'is_active': False, 'progress': 0, 'file_position': 0},
    'display_status': {'progress': 0, 'message': None},
    'pause_resume': {'is_paused': False},
    'configfile': {'config': {'virtual_sdcard': {'path': str(home / 'printer_data/gcodes')}}},
    'heaters': {'available_sensors': [], 'available_heaters': []},
    'extruder': {'target': 0}, 'heater_bed': {'target': 0},
    'mcu': {}, 'mcu EBBCan': {}, 'mcu eddy': {},
    'gcode_macro _TREED_CAM_STATE': {'enabled': 0, 'generation': 0},
    'gcode_macro _TREED_OPERATION_STATE': {'phase': 'idle'},
    'gcode_macro _TREED_UPDATE_ADMISSION_STATE': {'active': 0},
    'gcode_macro _TREED_SYSTEM_POWER': {'enabled': 1},
    'gcode_macro _TREED_SERVICE_COMMANDS': {'enabled': 1},
}

# Блок 2: Только Klippy Unix-протокол; системные службы остаются настоящими.
async def client(reader, writer):
    try:
        while True:
            req = json.loads((await reader.readuntil(b'\x03'))[:-1])
            method, params = req['method'], req.get('params', {})
            if method == 'info':
                result = {'state': 'ready', 'state_message': 'integration Klippy fixture', 'software_version': 'fixture', 'klipper_path': str(home / 'klipper'), 'python_path': sys.executable}
            elif method == 'list_endpoints':
                result = {'endpoints': ['info', 'objects/list', 'objects/query', 'objects/subscribe', 'gcode/script']}
            elif method == 'objects/list':
                result = {'objects': list(objects)}
            elif method in {'objects/query', 'objects/subscribe'}:
                result = {'eventtime': time.monotonic(), 'status': {name: objects.get(name, {}) for name in params.get('objects', {})}}
            elif method == 'gcode/script':
                script = params['script']
                if script == '_TREED_UPDATE_GATE ACTION=BEGIN':
                    objects['gcode_macro _TREED_UPDATE_ADMISSION_STATE']['active'] = 1
                elif script == '_TREED_UPDATE_GATE ACTION=END':
                    objects['gcode_macro _TREED_UPDATE_ADMISSION_STATE']['active'] = 0
                elif script != 'TURN_OFF_HEATERS':
                    raise ValueError('Unexpected G-code: ' + script)
                print('FIXTURE_GCODE', script, flush=True)
                result = {}
            elif method in {'gcode/subscribe_output', 'register_remote_method', 'gcode/help'}:
                result = {}
            else:
                raise ValueError('Unexpected Klippy method: ' + method)
            writer.write(json.dumps({'id': req['id'], 'result': result}).encode() + b'\x03')
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()
        await writer.wait_closed()

# Блок 3: Сокет доступен настоящему Moonraker в гостевой системе.
async def main():
    socket.parent.mkdir(parents=True, exist_ok=True)
    socket.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(client, path=str(socket))
    async with server:
        await server.serve_forever()

asyncio.run(main())
