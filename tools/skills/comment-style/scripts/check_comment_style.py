#!/usr/bin/env python3
"""Check structured comment style coverage using per-repo YAML config."""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Any

import yaml

DEFAULT_CONFIG: dict[str, Any] = {
    "language": "ru",
    "comment_style": {
        "target_paths": ["."],
        "extensions": [".sh", ".bash", ".py", ".cfg", ".conf", ".service", ".ini"],
        "skip_dirs": [".git", ".github", "__pycache__", ".venv", "venv", "node_modules", ".codex"],
        "header_line": "# ==========================================",
        "header_labels": {
            "purpose": "Назначение",
            "contour": "Контур",
        },
        "block_regex": r"^\s*#\s*Блок\s+\d+\s*:",
        "require_block_markers": True,
        "max_header_lines": 40,
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


def normalize_extensions(items: list[str]) -> set[str]:
    result = set()
    for item in items:
        ext = item.strip()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        result.add(ext.lower())
    return result


def should_skip(path: pathlib.Path, skip_dirs: set[str]) -> bool:
    return any(part in skip_dirs for part in path.parts)


def iter_files(root: pathlib.Path, target_paths: list[str], extensions: set[str], skip_dirs: set[str]) -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for raw in target_paths:
        target = pathlib.Path(raw)
        if not target.is_absolute():
            target = root / target
        if not target.exists():
            continue
        if target.is_file():
            if target.suffix.lower() in extensions:
                files.append(target)
            continue

        for item in target.rglob("*"):
            if not item.is_file():
                continue
            rel = item.relative_to(root)
            if should_skip(rel, skip_dirs):
                continue
            if item.suffix.lower() not in extensions:
                continue
            files.append(item)
    return files


def check_file(
    path: pathlib.Path,
    root: pathlib.Path,
    header_line: str,
    purpose_label: str,
    contour_label: str,
    block_re: re.Pattern[str],
    require_block_markers: bool,
    max_header_lines: int,
) -> list[str]:
    issues: list[str] = []

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")

    lines = text.splitlines()
    header_scope = lines[:max_header_lines]

    purpose_re = re.compile(rf"^\s*#\s*{re.escape(purpose_label)}\s*:")
    contour_re = re.compile(rf"^\s*#\s*{re.escape(contour_label)}\s*:")

    if not any(header_line in line for line in header_scope):
        issues.append("MISSING_HEADER")
    if not any(purpose_re.search(line) for line in header_scope):
        issues.append("MISSING_PURPOSE")
    if not any(contour_re.search(line) for line in header_scope):
        issues.append("MISSING_CONTOUR")
    if require_block_markers and not any(block_re.search(line) for line in lines):
        issues.append("MISSING_BLOCK_MARKER")

    rel = path.relative_to(root).as_posix()
    return [f"{code} {rel}" for code in issues]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check structured comment style coverage.")
    parser.add_argument("--root", default=".", help="Target repository root")
    parser.add_argument("--paths", nargs="+", help="Relative paths inside --root")
    parser.add_argument("--config", default=".codex/repo-standards.yaml", help="Path to config file")
    parser.add_argument("--max-header-lines", type=int, help="Override header search window")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    root = pathlib.Path(args.root).resolve()

    cfg = load_config(root, args.config)
    style = cfg.get("comment_style", {})

    target_paths = args.paths or style.get("target_paths", ["."])
    extensions = normalize_extensions(style.get("extensions", []))
    if not extensions:
        extensions = normalize_extensions(DEFAULT_CONFIG["comment_style"]["extensions"])

    skip_dirs = set(style.get("skip_dirs", []))
    if not skip_dirs:
        skip_dirs = set(DEFAULT_CONFIG["comment_style"]["skip_dirs"])

    header_labels = style.get("header_labels", {})
    purpose_label = str(header_labels.get("purpose", "Назначение"))
    contour_label = str(header_labels.get("contour", "Контур"))

    header_line = str(style.get("header_line", DEFAULT_CONFIG["comment_style"]["header_line"]))
    block_regex = str(style.get("block_regex", DEFAULT_CONFIG["comment_style"]["block_regex"]))
    block_re = re.compile(block_regex)
    require_block_markers = bool(style.get("require_block_markers", True))

    max_header_lines = args.max_header_lines or int(style.get("max_header_lines", 40))

    files = iter_files(root, target_paths, extensions, skip_dirs)
    if not files:
        print("No files matched.")
        return 0

    findings: list[str] = []
    for path in files:
        findings.extend(
            check_file(
                path=path,
                root=root,
                header_line=header_line,
                purpose_label=purpose_label,
                contour_label=contour_label,
                block_re=block_re,
                require_block_markers=require_block_markers,
                max_header_lines=max_header_lines,
            )
        )

    if findings:
        for line in findings:
            print(line)
        print(f"FAIL: {len(findings)} issue(s) found.")
        return 1

    print(f"OK: checked {len(files)} files, no style gaps found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

