# `.github`

Каталог CI/CD-метаданных репозитория.

## Состав

- `workflows/` — GitHub Actions workflow-файлы проекта.

## Контракт

- workflow-файлы должны отражать фактические проверки репозитория;
- изменения workflow синхронизируются с описанием в `workflows/README.md`;
- для новых workflow по возможности задаются ограничения по `paths`.

## Связанный документ

- `.github/workflows/README.md` — реестр и краткое назначение workflow.
