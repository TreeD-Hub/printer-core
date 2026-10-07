# `tools/skills`

Локальные skills для комментариев и документации `printer-core`.

## Состав

- [`comment-style/SKILL.md`](comment-style/SKILL.md) — проверка формата комментариев.
- [`readme-coverage/SKILL.md`](readme-coverage/SKILL.md) — проверка покрытия каталогов файлами README.

## Контракт

- Инструкции локальной версии находятся в соответствующем `SKILL.md`.
- Профиль репозитория задаёт `.codex/repo-standards.yaml`.
- Проверка покрытия по умолчанию ничего не записывает; `--create` создаёт заготовки, которые нужно заполнить по фактическим файлам.

## Проверка

Проверка README из корня репозитория; требуется Python с PyYAML:

```bash
python tools/skills/readme-coverage/scripts/readme_coverage.py --root .
```

Проверка наличия README не проверяет достоверность текста и ссылок.
Общий обзор утилит: [README tools](../README.md).
