# Локальный vision runtime

`service.py` принимает только newline-delimited JSON через приватный Unix socket
`${PI_HOME}/treed/vision/run/vision.sock`. На запросе передаются `protocol=1`,
UUID `request_id`, задача `spaghetti|bed`, объект `context` и JPEG в Base64.
Лимиты: JPEG 8 MiB, запрос 12 MiB, ответ 64 KiB. Сервис не принимает URL или
пути к кадрам и не обращается к Moonraker, камере или сети.

Ответ содержит `protocol/request_id/task/context`, затем provenance
`worker_generation` (UUID) и `model_version`, `verdict/score/threshold/boxes`,
`reason` и конечный `processing_ms`. Недоступность всё равно echo-ит запрос,
возвращает `verdict=unavailable`, `score=null`, пустые `boxes`, причину и
`processing_ms=0.0`. E0 для стола возвращает только `occupied` или `unknown`;
`empty` не поддержан.

`models.json` фиксирует версии, размеры, SHA-256 и пороги моделей. Loader принимает
модели только по явным локальным путям `TREED_VISION_SPAGHETTI_ARTIFACT` и
`TREED_VISION_BED_ARTIFACT`, проверяет размер и SHA-256 перед копированием.
Закреплены spaghetti v12 (`44 790 539` байт, SHA-256
`95c6e676301c77f294250961f77e5773707a0a0d57a5b7fe7e8b6fbe54eb81e9`) и E0
(`127 479 855` байт, SHA-256
`8de7e8aa22e20893a563c0c04d28f0019dcad9ef8d805818df6fd2ec8b10866e`).
В metadata релиза E0 имя класса сохранено с ошибкой кодировки; manifest закрепляет
эту исходную строку, и worker сравнивает её точно, без нормализации. Смысл класса 0
остаётся «деталь», контроль размера и SHA-256 checkpoint не меняется.
Опциональный `TREED_VISION_WHEELHOUSE` задаёт локальный wheelhouse; pip запускается
с `--no-index` и точными версиями Torch, torchvision, RF-DETR и Pillow.
Без модели или wheelhouse provisioning продолжается, а запрос анализа отвечает
`unavailable`. Во время печати скачивание весов и пакетов не выполняется.

Unit `treed-vision.service` ограничивает память 1.5 GiB, CPU 150%, использует
один inference worker, Nice 10, swap 0 и не более двух ML-потоков. Первый запрос после
запуска или смены модели получает `unavailable/warming`; сервис прогревает модель
не более 60 секунд, отбрасывает кадр прогрева и обрабатывает следующий свежий
кадр. Предел инференса для spaghetti — 6 секунд, для bed — 25 секунд. Клиент камеры
остаётся отдельным адаптером и обращается только к локальному socket.

Старт и переключение модели имеют отдельное окно прогрева до 60 секунд; зависший
startup завершается watchdog-ом даже без новых запросов. После готовности свежий
запрос spaghetti ограничен 6 секундами, bed — серверным максимумом 25 секунд;
Moonraker-клиент bed может прервать раньше по оставшемуся deadline операции.

Пакеты runtime закреплены: torch 2.11.0, torchvision 0.26.0, RF-DETR 1.11.0 и
Pillow 12.3.0. `TREED_VISION_PYTHON` выбирает абсолютный путь к уже имеющемуся
окружению с этими версиями. Loader сохраняет валидированный путь в
`/etc/default/treed-vision` и повторно использует его при обновлении; если путь
не задан, используется `${PI_HOME}/treed/vision/venv/bin/python`. Новое окружение
создаётся только из локального `TREED_VISION_WHEELHOUSE` с `--no-index`.
Первая установка vision unit выполняется loader-ом. Updater core до любых
изменений runtime-файлов проверяет наличие обоих unit и при отсутствии просит
сначала запустить loader.

Камера и inference доставляются совместно core runtime updater-ом: пакет включает
`service.py`, `models.json`, `stream_detect.py` и AutoPrinter vision client,
но не включает весовые файлы. Изменение модели происходит только loader-ом из
явно заданного локального артефакта после проверки staged-файла и атомарной
замены. Весовые файлы не скачиваются во время печати.
