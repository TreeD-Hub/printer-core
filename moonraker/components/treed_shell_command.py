"""
MOONRAKER COMPONENT: TREED SHELL COMMAND
========================================
Назначение:
- Мост между секциями `[shell_command <name>]` в Moonraker-конфиге
  и remote method `machine.shell_command` для вызовов из Klipper-макросов.
Контур:
- компонент не падает на ошибках выполнения shell-команды;
- ошибки логируются, основной API-контур продолжает работать.
"""

from __future__ import annotations

# Блок 1: Импорты и базовые типы.
import logging
import shlex
from dataclasses import dataclass
from typing import Dict, Optional, TYPE_CHECKING, Iterable
from ..utils import ServerError

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..confighelper import ConfigHelper
    from .shell_command import ShellCommandFactory


# Блок 2: Внутренняя модель зарегистрированной shell-команды.
@dataclass
class ManagedCommand:
    command: str
    timeout: float
    verbose: bool
    cwd: Optional[str]


class TreeDShellCommand:
    def __init__(self, config: ConfigHelper) -> None:
        # Блок 3: Инициализация компонента и загрузка shell_command factory.
        self.server = config.get_server()
        self.event_loop = self.server.get_event_loop()
        self.shell_cmd: ShellCommandFactory = self.server.load_component(
            config, "shell_command"
        )
        self.commands: Dict[str, ManagedCommand] = {}

        # Блок 4: Чтение и валидация секций [shell_command <name>].
        for section in config.get_prefix_sections("shell_command "):
            cmd_cfg = config.getsection(section)
            parts = section.split(maxsplit=1)
            if len(parts) != 2 or not parts[1].strip():
                raise config.error(f"Invalid section name: [{section}]")
            name = parts[1].strip()
            self.commands[name] = ManagedCommand(
                command=cmd_cfg.get("command"),
                timeout=cmd_cfg.getfloat("timeout", 10.0, above=0.0),
                verbose=cmd_cfg.getboolean("verbose", False),
                cwd=cmd_cfg.get("cwd", None),
            )

        # Пустой набор команд не блокирует запуск, но фиксируется предупреждением.
        if not self.commands:
            self.server.add_warning(
                "[treed_shell_command]: no [shell_command <name>] sections found."
            )

        # Блок 5: Регистрация remote method machine.shell_command.
        try:
            self.server.register_remote_method("machine.shell_command", self._queue_run)
        except ServerError:
            LOGGER.info(
                "treed_shell_command: machine.shell_command already registered; "
                "skipping override"
            )

    def _queue_run(
        self, cmd: str = "", parameters: str = "", **_kwargs: object
    ) -> None:
        # Блок 6: Планирование асинхронного запуска команды в event loop.
        self.event_loop.register_callback(self._run, cmd, parameters)

    async def _run(self, cmd: str, parameters: object = "") -> None:
        # Блок 7: Разрешение команды по имени.
        entry = self.commands.get(cmd)
        if entry is None:
            LOGGER.info(
                "treed_shell_command: command '%s' not configured, request ignored",
                cmd,
            )
            return

        # Блок 8: Безопасная сборка командной строки с quoting параметров.
        full_cmd = entry.command
        if isinstance(parameters, Iterable) and not isinstance(parameters, (str, bytes)):
            args = [shlex.quote(str(p)) for p in parameters if str(p).strip()]
            if args:
                full_cmd = f"{full_cmd} " + " ".join(args)
        else:
            param_text = str(parameters or "").strip()
            if param_text:
                full_cmd = f"{full_cmd} {shlex.quote(param_text)}"

        # Блок 9: Выполнение shell-команды с контролем timeout/cwd/verbose.
        try:
            await self.shell_cmd.run_cmd_async(
                full_cmd,
                timeout=entry.timeout,
                verbose=entry.verbose,
                log_complete=False,
                cwd=entry.cwd,
            )
        except self.shell_cmd.error as err:
            LOGGER.info(
                "treed_shell_command: '%s' failed rc=%s",
                cmd,
                err.return_code,
            )
        except Exception:
            LOGGER.exception(
                "treed_shell_command: unexpected error running '%s'", cmd
            )


# Блок 10: Entry-point загрузки компонента Moonraker.
def load_component(config: ConfigHelper) -> TreeDShellCommand:
    return TreeDShellCommand(config)
