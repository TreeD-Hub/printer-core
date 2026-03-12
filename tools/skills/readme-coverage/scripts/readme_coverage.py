#!/usr/bin/env python3
"""Check README.md coverage and optionally create placeholders via YAML profile."""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "language": "ru",
    "readme_coverage": {
        "skip_dirs": [".git", ".github", "__pycache__", ".venv", "venv", "node_modules", ".codex"],
        "required_sections": [
            "Состав",
            "Контракт и инварианты",
            "Runtime / Deploy",
            "Запуск и проверка",
            "Смежная документация",
        ],
        "orchestrator_rules": {
            "require_step_registry": True,
            "step_types": ["required", "optional"],
            "orchestrator_dirs": [],
        },
        "create_mode_default": "check",
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(root: pathlib.Path, config_arg: str) -> dict[str, Any]:
    config_path = pathlib.Path(config_arg)
    if not config_path.is_absolute():
        config_path = root / config_path

    config: dict[str, Any] = DEFAULT_CONFIG
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid config format in {config_path}")
        config = deep_merge(DEFAULT_CONFIG, raw)
    return config


def is_skipped(path: pathlib.Path, skip_dirs: set[str]) -> bool:
    return any(part in skip_dirs for part in path.parts)


def has_meaningful_content(path: pathlib.Path, skip_dirs: set[str]) -> bool:
    for child in path.iterdir():
        if child.name.startswith("."):
            continue
        rel = pathlib.Path(child.name)
        if is_skipped(rel, skip_dirs):
            continue
        return True
    return False


def section_text(name: str) -> str:
    if name == "Состав":
        return "- `...` — назначение основных файлов/подкаталогов."
    if name == "Контракт и инварианты":
        return "- Опишите гарантии и ограничения этого каталога."
    if name == "Runtime / Deploy":
        return "- Укажите runtime/deploy-пути (если применимо)."
    if name == "Запуск и проверка":
        return "```bash\n# команды запуска/проверки (если применимо)\n```"
    if name == "Смежная документация":
        return "- `../README.md` — связь с соседним слоем."
    return "- Заполните раздел фактическими данными."


def build_template(
    rel_path: str,
    required_sections: list[str],
    is_orchestrator: bool,
    step_types: list[str],
) -> str:
    title = rel_path if rel_path != "." else "root"
    parts = [
        f"# `{title}`",
        "",
        "Кратко опишите назначение каталога и его роль в репозитории.",
        "",
    ]

    for name in required_sections:
        parts.append(f"## {name}")
        parts.append("")
        parts.append(section_text(name))
        parts.append("")

    if is_orchestrator:
        steps = " | ".join(step_types) if step_types else "required | optional"
        parts.extend(
            [
                "## Реестр сценариев",
                "",
                f"Добавьте явный список сценариев/шагов с типом ({steps}) и назначением.",
                "",
                "| # | Сценарий | Тип | Назначение |",
                "|---|---|---|---|",
                "| 1 | ... | required | ... |",
                "",
            ]
        )

    return "\n".join(parts)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check README coverage by directories.")
    parser.add_argument("--root", default=".", help="Target repository root")
    parser.add_argument("--config", default=".codex/repo-standards.yaml", help="Path to config file")
    parser.add_argument("--create", action="store_true", help="Create README.md placeholders")
    parser.add_argument(
        "--fail-on-missing",
        action="store_true",
        help="Fail when README is missing (default in check mode)",
    )
    parser.add_argument(
        "--no-fail-on-missing",
        action="store_true",
        help="Do not fail when README is missing",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    cfg = load_config(root, args.config)
    readme_cfg = cfg.get("readme_coverage", {})

    skip_dirs = set(readme_cfg.get("skip_dirs", []))
    if not skip_dirs:
        skip_dirs = set(DEFAULT_CONFIG["readme_coverage"]["skip_dirs"])

    required_sections = list(readme_cfg.get("required_sections", []))
    if not required_sections:
        required_sections = list(DEFAULT_CONFIG["readme_coverage"]["required_sections"])

    orchestrator = readme_cfg.get("orchestrator_rules", {})
    require_step_registry = bool(orchestrator.get("require_step_registry", True))
    orchestrator_dirs = set(orchestrator.get("orchestrator_dirs", []))
    step_types = list(orchestrator.get("step_types", ["required", "optional"]))

    mode_default = str(readme_cfg.get("create_mode_default", "check")).strip().lower()
    create_enabled = args.create or mode_default == "create"

    if args.fail_on_missing and args.no_fail_on_missing:
        print("ERROR: use only one of --fail-on-missing / --no-fail-on-missing", file=sys.stderr)
        return 2

    if args.fail_on_missing:
        fail_on_missing = True
    elif args.no_fail_on_missing:
        fail_on_missing = False
    else:
        fail_on_missing = not create_enabled

    missing: list[pathlib.Path] = []
    for current, dirs, _files in os.walk(root):
        current_path = pathlib.Path(current)

        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]

        rel_current = current_path.relative_to(root) if current_path != root else pathlib.Path(".")
        if is_skipped(rel_current, skip_dirs):
            continue

        if not has_meaningful_content(current_path, skip_dirs):
            continue

        readme_path = current_path / "README.md"
        if readme_path.exists():
            continue

        missing.append(current_path)

    if not missing:
        print("OK: all scanned directories contain README.md")
        return 0

    print(f"MISSING README: {len(missing)} directory(ies)")
    for directory in missing:
        rel = directory.relative_to(root).as_posix() if directory != root else "."
        print(rel)

        if create_enabled:
            is_orchestrator = require_step_registry and rel in orchestrator_dirs
            template = build_template(rel, required_sections, is_orchestrator, step_types)
            (directory / "README.md").write_text(template, encoding="utf-8")

    if create_enabled:
        print("PLACEHOLDERS_CREATED")
        return 0

    return 1 if fail_on_missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

