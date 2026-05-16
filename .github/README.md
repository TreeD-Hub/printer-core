# `.github`

GitHub-специфичная инфраструктура репозитория: workflow, шаблоны и release-конфигурация.

## Состав

- `workflows/` — GitHub Actions для релизов и вспомогательной автоматизации.
- `labels.json` — source of truth для release-label'ов `major` / `minor` / `patch`.
- `pull_request_template.md` — обязательный шаблон PR для релизного потока `treed-v2_main`.
- `release.yml` — конфигурация auto-generated release notes GitHub.

## Контракт

- релизы публикуются только из ветки `treed-v2_main`;
- release-label'ы поддерживаются как код и синхронизируются workflow;
- описание релиза берется из секции `## Описание релиза` в PR body.
