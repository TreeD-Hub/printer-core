#!/usr/bin/env python3
from __future__ import annotations

# ==========================================
# TOOL: VALIDATE KLIPPER CONFIGS
# ==========================================
# Назначение:
# - Выполняет статическую проверку include-цепочки Klipper-конфигов.
# - Находит проблемы include-файлов, include-циклов и дублей секций.
# Контур:
# - read-only анализ (без правок файлов).

# Блок 1: Импорты и базовые паттерны парсинга.
import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

INCLUDE_RE = re.compile(
    r"^\s*\[\s*include\s+([^\]]+?)\s*\]\s*(?:[#;].*)?$",
    re.IGNORECASE,
)
SECTION_RE = re.compile(
    r"^\s*\[\s*([A-Za-z0-9_]+)(?:\s+([^\]]+?))?\s*\]\s*(?:[#;].*)?$"
)
OPTIONAL_MISSING_INCLUDES = {"local_overrides.cfg"}

# Блок 2: Модель диагностического сообщения.
@dataclass
class Issue:
    code: str
    message: str
    path: Path | None = None
    line: int | None = None

    def render(self, repo_root: Path) -> str:
        location = ""
        if self.path is not None:
            try:
                rel = self.path.relative_to(repo_root).as_posix()
            except ValueError:
                rel = str(self.path)
            if self.line is not None:
                location = f"{rel}:{self.line}: "
            else:
                location = f"{rel}: "
        return f"{self.code}: {location}{self.message}"


# Блок 3: Вспомогательные функции чтения файла и извлечения include.
def load_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8-sig").splitlines()
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()


def parse_include_specs(path: Path) -> list[tuple[int, str]]:
    specs: list[tuple[int, str]] = []
    for lineno, line in enumerate(load_lines(path), start=1):
        match = INCLUDE_RE.match(line)
        if match:
            spec = match.group(1).strip().strip('"').strip("'")
            specs.append((lineno, spec))
    return specs


# Блок 4: Разрешение include-спецификаций в целевые файлы.
def resolve_include_targets(
    current_file: Path,
    include_spec: str,
    include_line: int,
    repo_root: Path,
    issues: list[Issue],
) -> list[Path]:
    include_spec = include_spec.strip()
    if not include_spec:
        issues.append(
            Issue(
                code="E_INCLUDE_EMPTY",
                message="Пустой include.",
                path=current_file,
                line=include_line,
            )
        )
        return []

    if include_spec in OPTIONAL_MISSING_INCLUDES:
        optional_target = (current_file.parent / include_spec).resolve()
        return [optional_target] if optional_target.is_file() else []

    is_glob = any(ch in include_spec for ch in "*?[")
    if is_glob:
        matches = sorted(
            p.resolve() for p in current_file.parent.glob(include_spec) if p.is_file()
        )
        if not matches:
            issues.append(
                Issue(
                    code="E_INCLUDE_GLOB_EMPTY",
                    message=f"Include-маска не дала файлов: {include_spec}",
                    path=current_file,
                    line=include_line,
                )
            )
        return matches

    target = Path(include_spec)
    if not target.is_absolute():
        target = current_file.parent / target
    target = target.resolve()

    if not target.exists():
        issues.append(
            Issue(
                code="E_INCLUDE_MISSING",
                message=f"Include-файл не найден: {include_spec}",
                path=current_file,
                line=include_line,
            )
        )
        return []
    if not target.is_file():
        issues.append(
            Issue(
                code="E_INCLUDE_NOT_FILE",
                message=f"Include не указывает на файл: {include_spec}",
                path=current_file,
                line=include_line,
            )
        )
        return []

    if repo_root not in target.parents and target != repo_root:
        issues.append(
            Issue(
                code="E_INCLUDE_OUTSIDE_REPO",
                message=f"Include выходит за границы репозитория: {target}",
                path=current_file,
                line=include_line,
            )
        )

    return [target]


# Блок 5: Обход include-графа с детектом циклов.
def walk_include_graph(
    entry_file: Path, repo_root: Path, issues: list[Issue]
) -> list[Path]:
    visited: set[Path] = set()
    ordered: list[Path] = []

    def walk(path: Path, stack: list[Path]) -> None:
        if path in stack:
            chain = " -> ".join(p.name for p in [*stack, path])
            issues.append(
                Issue(
                    code="E_INCLUDE_CYCLE",
                    message=f"Циклический include: {chain}",
                    path=path,
                )
            )
            return

        if path in visited:
            return
        visited.add(path)
        ordered.append(path)

        for line_no, include_spec in parse_include_specs(path):
            targets = resolve_include_targets(
                current_file=path,
                include_spec=include_spec,
                include_line=line_no,
                repo_root=repo_root,
                issues=issues,
            )
            for target in targets:
                walk(target, [*stack, path])

    walk(entry_file.resolve(), [])
    return ordered


# Блок 6: Сбор секций для проверки дублей объявлений.
def collect_sections(files: list[Path]) -> dict[tuple[str, str], list[tuple[Path, int]]]:
    sections: dict[tuple[str, str], list[tuple[Path, int]]] = {}
    for file_path in files:
        for line_no, line in enumerate(load_lines(file_path), start=1):
            match = SECTION_RE.match(line)
            if not match:
                continue

            section_type = (match.group(1) or "").strip().lower()
            section_name = (match.group(2) or "").strip().lower()
            if section_type == "include":
                continue

            key = (section_type, section_name)
            sections.setdefault(key, []).append((file_path, line_no))
    return sections


# Блок 7: Основная валидация entry-конфига и include-цепочки.
def validate(entry: Path, repo_root: Path) -> list[Issue]:
    issues: list[Issue] = []

    if not entry.exists():
        return [
            Issue(
                code="E_ENTRY_MISSING",
                message=f"Точка входа не найдена: {entry}",
                path=entry,
            )
        ]
    if not entry.is_file():
        return [
            Issue(
                code="E_ENTRY_NOT_FILE",
                message=f"Точка входа не является файлом: {entry}",
                path=entry,
            )
        ]

    files = walk_include_graph(entry_file=entry, repo_root=repo_root, issues=issues)
    sections = collect_sections(files)

    for (section_type, section_name), defs in sorted(sections.items()):
        if len(defs) < 2:
            continue
        section_repr = (
            f"[{section_type} {section_name}]"
            if section_name
            else f"[{section_type}]"
        )
        first_path, first_line = defs[0]
        first_ref = f"{first_path.relative_to(repo_root).as_posix()}:{first_line}"
        for path, line in defs[1:]:
            issues.append(
                Issue(
                    code="E_DUP_SECTION",
                    message=(
                        f"Дублируется секция {section_repr} "
                        f"(первое объявление: {first_ref})"
                    ),
                    path=path,
                    line=line,
                )
            )

    if not issues:
        print(
            "OK: include-цепочка и секции валидны "
            f"(entry={entry.relative_to(repo_root).as_posix()}, files={len(files)})."
        )

    return issues


# Блок 8: CLI-интерфейс утилиты.
def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка Klipper-конфигов в репозитории.")
    parser.add_argument(
        "--entry",
        default="klipper/printer.cfg",
        help="Путь до entry-конфига относительно корня репозитория.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    entry = Path(args.entry)
    if not entry.is_absolute():
        entry = (repo_root / entry).resolve()

    issues = validate(entry=entry, repo_root=repo_root)
    if not issues:
        return 0

    print("FAIL: найдены проблемы конфигов:")
    for issue in issues:
        print(f" - {issue.render(repo_root)}")
    return 1


# Блок 9: Точка входа скрипта.
if __name__ == "__main__":
    sys.exit(main())
