"""Адресная проверка счётчика SFS V2.0 и порога автоматической прочистки."""

import importlib.util
from pathlib import Path
import re
import sys
import types


ROOT = Path(__file__).resolve().parents[2]
CFG = (ROOT / "klipper/profiles/treed_v2_corexy_v1/filament_sensor.cfg").read_text(encoding="utf-8")


class FakePrinter:
    def __init__(self):
        self.objects = {}

    def add_object(self, name, obj):
        assert name not in self.objects
        self.objects[name] = obj


class FakeConfig:
    def __init__(self):
        self.printer = FakePrinter()

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "treed_filament_motion_sensor filament_motion"


class FakeRunoutHelper:
    def __init__(self):
        self.detected = False

    def get_status(self, _eventtime):
        return {"filament_detected": self.detected, "enabled": True}


class FakeEncoderSensor:
    def __init__(self, _config):
        self.runout_helper = FakeRunoutHelper()
        self.get_status = self.runout_helper.get_status
        self.button_callback = self.encoder_event

    def encoder_event(self, _eventtime, _state):
        self.runout_helper.detected = True


extras = types.ModuleType("extras")
extras.__path__ = []
motion = types.ModuleType("extras.filament_motion_sensor")
motion.EncoderSensor = FakeEncoderSensor
sys.modules["extras"] = extras
sys.modules[motion.__name__] = motion
spec = importlib.util.spec_from_file_location(
    "extras.treed_filament_motion_sensor", ROOT / "klipper-host/treed_filament_motion_sensor.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

config = FakeConfig()
sensor = module.load_config_prefix(config)
assert config.printer.objects["filament_motion_sensor filament_motion"] is sensor

mm_per_pulse = float(re.search(r"(?m)^variable_mm_per_pulse: ([0-9.]+)$", CFG).group(1))
min_advance = float(re.search(r"(?m)^variable_min_advance_mm: ([0-9.]+)$", CFG).group(1))
assert min_advance > mm_per_pulse

for attempt in range(1, 6):
    before = sensor.get_status(0)["pulse_count"]
    sensor.button_callback(attempt, 1)
    sensor.button_callback(attempt + 0.1, 0)
    after = sensor.get_status(0)
    assert after["filament_detected"] is True
    assert (after["pulse_count"] - before) * mm_per_pulse < min_advance

before = sensor.get_status(0)["pulse_count"]
for pulse in range(4):
    sensor.button_callback(pulse, 1)
    sensor.button_callback(pulse + 0.1, 0)
assert (sensor.get_status(0)["pulse_count"] - before) * mm_per_pulse >= min_advance

check = CFG.split("[delayed_gcode _TREED_CLOG_RECOVERY_CHECK]", 1)[1].split(
    "[gcode_macro _TREED_CLOG_RECOVERY_FINISH]", 1
)[0]
assert "motion.filament_detected" not in check
assert "motion.pulse_count|int - recovery.start_pulses|int" in check
assert "_TREED_CLOG_RECOVERY_FINISH SUCCESS=1" in check
assert "recovery.attempt|int >= recovery.max_attempts|int" in check
assert check.index("_TREED_CLOG_RECOVERY_FINISH SUCCESS=1") < check.index(
    "recovery.attempt|int >= recovery.max_attempts|int"
)
assert "_TREED_CLOG_RECOVERY_ATTEMPT" in check
assert "RESUME" not in check
finish = CFG.split("[gcode_macro _TREED_CLOG_RECOVERY_FINISH]", 1)[1].split(
    "[gcode_macro _TREED_CLOG_RECOVERY_ABORT]", 1
)[0]
assert finish.index("if success == 1") < finish.index("RESUME") < finish.index("{% else %}")
print("Clog recovery pulse test: PASS")
