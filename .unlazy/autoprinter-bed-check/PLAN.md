# Проверка свободного стола

Режим Orchestrated. База printer-core: dev, HEAD
8f1944f99906122de4045a909abd85620ea69511, рабочее дерево чистое.
printer-defect-server и исходный релиз моделей используются только для чтения.
Без физических запусков, SSH, commit, push, deploy и обучения моделей.

| Результат | Приёмка |
|---|---|
| C1 | Актуальные точки интеграции и backend bed_check контракт сверены |
| C2 | Выбрана существующая E0 модель по задаче, метрикам и данным релиза |
| C3 | Локальный кадр, проверенная модель и ограниченный inference дают occupied/unknown; неподтверждённый empty недоступен |
| C4 | Ошибки, пустые/старые кадры, неверная модель и отсутствие квалификации блокируют запуск |
| C5 | Провайдер связан с durable executor, сохраняет boot/bed/run контекст и default-off |
| C6 | Адресные offline проверки, независимое ревью, граф и очистка завершены |

| Leaf | Owns | Needs | Tier | Wave | State |
|---|---|---|---|---|---|
| 1 | .unlazy/autoprinter-bed-check/gates/leaf-1.md | - | judgment | discovery | VERIFIED |
| 2 | .unlazy/autoprinter-bed-check/gates/leaf-2.md | - | judgment | discovery | VERIFIED |
| 3 | moonraker/components/treed_autoprinter_bed.py, moonraker/components/treed_autoprinter_bed_inference.py, tools/tests/test_treed_autoprinter_bed.py, tools/requirements-autoprinter-bed-check.txt | 1,2 | judgment | implementation | VERIFIED |
| 4 | moonraker/components/treed_autoprinter.py, moonraker/components/treed_autoprinter_execution.py, tools/tests/test_treed_autoprinter_component.py, tools/tests/test_treed_autoprinter_execution.py, moonraker/base/00-core.conf, docs/autoprinter.md, docs/autoprinter-bed-check.md, moonraker/components/README.md, tools/tests/README.md | 1,2 | judgment | implementation | VERIFIED |

Лист 1: модель E0, происхождение, runtime, ограничения оценки.
Лист 2: capture, executor, доставка и wire контракт.
Контракты и владение реализацией назначаются после проверки результатов.

Пользователь отдельно отложил установку ML-зависимостей и прогон реальных весов.
Это ограничение не относится к адресным тестам интеграции с подставным runtime.
Подтверждение empty на оборудовании не входит в приёмку этого этапа; E0 остаётся
кандидатом occupied/unknown из-за отсутствия оценки пустого стола.
