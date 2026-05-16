# Релизный поток `treed-v2_main`

Документ фиксирует фактический GitHub Actions-контур релизирования ветки `treed-v2_main`.

## Что считается релизом

- релиз создается только workflow `release-treed-v2-main.yml`;
- trigger: `push` в ветку `treed-v2_main`;
- результат: GitHub Release с semver-тегом формата `vX.Y.Z`, кратким описанием релиза и auto-generated changelog.

## Обязательный контракт PR

- изменения в `treed-v2_main` должны попадать через merge PR;
- у каждого merge PR должен быть ровно один release-label: `major`, `minor` или `patch`;
- в PR body должен быть непустой блок `## Описание релиза`;
- release workflow читает этот блок и публикует его в верхней части release body.

Если push не содержит PR-контекста или в диапазоне `before..after` есть direct push-коммит без связанного merged PR, релиз не публикуется и job завершается ошибкой.

## Правила версии

- формат тега: `vX.Y.Z`;
- если в одном push несколько PR, релиз один, а bump определяется по максимальному label:
  - `major` > `minor` > `patch`;
- первая версия при отсутствии tag history:
  - `patch` -> `v0.0.1`;
  - `minor` -> `v0.1.0`;
  - `major` -> `v1.0.0`.

## Changelog и release notes

- верхняя часть release body собирается из секций `## Описание релиза` всех PR, попавших в push;
- нижняя часть собирается через GitHub `generateReleaseNotes` с конфигом `.github/release.yml`;
- `CHANGELOG.md` в репозитории не ведется: source of truth по changelog живет в GitHub Release.

## Поведение при повторном запуске

- если semver-тег уже указывает на текущий commit и release уже существует, workflow завершает rerun идемпотентно без дубля;
- если tag с ожидаемым номером уже существует, но указывает на другой commit, workflow падает;
- если tag уже указывает на текущий commit, но release еще не создан, workflow использует существующий tag и публикует release.

## Labels as code

- файл `.github/labels.json` содержит канонический набор release-label'ов;
- workflow `sync-release-labels.yml` создает или обновляет labels в репозитории;
- синхронизацию можно запускать вручную через `workflow_dispatch` или автоматически при изменении label-конфига.
