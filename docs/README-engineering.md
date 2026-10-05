# Инженерный README

Этот документ описывает зависимости, установку из исходников, среду разработки,
Python API, проверки и выпуск релиза. Описание программы и использование CLI —
в [основном README](../README.md).

## Получение исходников

```powershell
git clone https://github.com/koolakoff/analog-fpv-compressor.git
Set-Location analog-fpv-compressor
```

Все команды ниже выполняются из корня репозитория. Git нужен для разработки
и получения исходников; пользователю готового ZIP он не требуется.

## Зависимости и подготовка Windows

Для программы и исследовательских инструментов нужны:

- Git для работы с репозиторием.
- Поддерживаемый Python, 64-bit, с `pip` и `venv`; для проекта выбран Python 3.13:
  [официальная загрузка](https://www.python.org/downloads/windows/).
- NumPy в отдельной виртуальной среде проекта.
- FFmpeg и ffprobe из **full** сборки с `libsvtav1`, `libx265` и фильтрами
  `idet`, `bwdif`, `hqdn3d`. SVT-AV1 отдельно устанавливать не нужно.
  `libx265` нужен для режима HEVC и сравнения в benchmark.

Установка FFmpeg через Windows Package Manager:

```powershell
winget install --exact --id Gyan.FFmpeg --source winget --scope user --accept-package-agreements --accept-source-agreements
```

Альтернатива — [полная сборка Gyan](https://www.gyan.dev/ffmpeg/builds/):
распаковать архив и добавить каталог `bin` в пользовательский PATH.
После установки или изменения PATH открыть новый терминал; если команды
недоступны в уже запущенном Codex, перезапустить приложение.

Если Python 3.13 ещё не установлен:

```powershell
winget install --exact --id Python.Python.3.13 --source winget --scope user --accept-package-agreements --accept-source-agreements
```

В корне проекта создать среду и установить Python-зависимости:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
```

Если используется другой поддерживаемый Python, заменить `-3.13` на его версию
и проверить совместимость зависимостей.
Активация среды не обязательна: команды используют её интерпретатор напрямую.

Проверка:

```powershell
ffmpeg -version
ffprobe -version
ffmpeg -hide_banner -encoders | Select-String 'libsvtav1|libx265'
ffmpeg -hide_banner -filters | Select-String '\bidet\b|\bbwdif\b|\bhqdn3d\b'
.\.venv\Scripts\python.exe -c "import numpy; print(numpy.__version__)"
.\.venv\Scripts\python.exe -m pip check
```

Проверенные версии и особенности этого компьютера: [локальная среда](setup.md).
Это подготовка среды разработки; решение об инсталлере хранится в источнике истины.


## Локальная установка и разработка

Команда `pip install -e .` выше устанавливает проект в editable-режиме:
изменения в `src/` применяются при следующем запуске без переустановки.
При изменении зависимостей или entry point в `pyproject.toml` повторить установку.
`requirements.txt` фиксирует NumPy для повторения измерений; `pyproject.toml`
описывает пакет и допустимый диапазон зависимостей.

Если требуется обычная установка из исходников, вместо `-e .` использовать:

```powershell
.\.venv\Scripts\python.exe -m pip install .
```

Pip создаёт `.venv\Scripts\fpv-compress.exe` по записи `[project.scripts]`
в `pyproject.toml`. Это запускатель установленного пакета, зависящий от `.venv`.
Сама среда создаётся локально на каждом компьютере и не распространяется.
Релизный EXE создаёт отдельный процесс сборки, описанный ниже.

Текущая установленная из исходников версия — 0.3.0 с GUI. Опубликованный ZIP
0.3.0 включает CLI и GUI; старый 0.1.0 хранится как исторический выпуск.

### Установка GUI

GUI — optional extra: CLI/ядру нужен только NumPy, Qt импортируется только
при запуске графического интерфейса. Для существующей среды:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\fpv-compress-gui.exe
```

Extra устанавливает `PySide6>=6.8,<7` с Qt и Shiboken. Дополнительная команда
`fpv-compress-gui.exe` создаётся pip как Windows GUI launcher; это локальная
команда среды, не самостоятельный portable EXE. Альтернативный запуск:
`python -m analog_fpv_compressor.gui`. Qt SDK/Designer отдельно не нужны.
FFmpeg ищется тем же механизмом, его каталог можно задать в расширенных
настройках. `.venv` не распространяется.

Переводы хранятся в `gui/translations/{en,ru,uk,sk}.json` и включаются
в Python package data. Подкласс `QTranslator` читает JSON-каталог с английскими
source strings и fallback; `.ts/.qm`-компиляция для этой версии не нужна.
Новый язык требует каталог и запись в `LANGUAGES`, без изменения ядра.
Настройки языка и пути FFmpeg сохраняются через `QSettings`.
Нативные диалоги Windows могут использовать язык ОС.

`gui/worker.py` выполняет общий `runner.run_job` в `QThread`, события поступают
в UI через Qt signals/slots. `runner.py` отвечает за общий цикл
analyze/plan/parts/execute, English logs и итоговые отчёты; CLI импортирует его.
Отмена — общий `CancelToken`, завершение потока ожидается асинхронно.
Логика обработки и выбора defaults в GUI не дублируется.

Запуск разработки:

```powershell
New-Item -ItemType Directory -Path outputs -Force
.\.venv\Scripts\fpv-compress.exe -i examples/air-school-stadion-oneflight.AVI -o outputs/flight.mkv
.\.venv\Scripts\python.exe -m analog_fpv_compressor --help
```

В активированной среде доступна команда `fpv-compress`. Остальные параметры
и поведение одинаковы с релизной версией; примеры — в основном README.

### FFmpeg для релиза и разработки

ZIP содержит Python и NumPy, но не FFmpeg. Приложенный `setup-ffmpeg.cmd`
запускает PowerShell-скрипт установки full-сборки Gyan через WinGet; нужен интернет.
При отсутствии WinGet установить Microsoft App Installer либо скачать full-сборку
вручную. Essentials не содержит SVT-AV1 и не подходит для default AV1.

Порядок поиска FFmpeg/ffprobe: `--ffmpeg-dir`, переменная `FFMPEG_DIR`,
`tools/` рядом с замороженным EXE, PATH, затем full-сборка WinGet на Windows.
В portable-папке `tools/` должны находиться оба файла: `ffmpeg.exe` и `ffprobe.exe`.
Python, NumPy и зависимости сборщика отдельно пользователю ZIP не нужны.

### Структура и локальные файлы

- `src/analog_fpv_compressor/` — ядро и CLI; `tests/` — автоматические проверки.
- `benchmarks/` — исследовательские инструменты и сценарии проверки CLI.
- `scripts/` — сборка, проверка и публикация ZIP; `packaging/` — entry point
  и файлы, добавляемые в поставку.
- `examples/` — локальные исходные AVI; `outputs/` и `benchmarks/results/` —
  локальные результаты и логи. Правила: [examples/README.md](../examples/README.md).
- `.venv/`, `build/` и `dist/` — локальная среда, промежуточные и готовые сборки.
  Эти каталоги исключены из Git; ZIP публикуется как asset GitHub Release.

## Python API

CLI и GUI используют один пакет, без отдельного сервера:

```python
from pathlib import Path
from analog_fpv_compressor import Settings, analyze, build_plan, process

settings = Settings(Path("input.avi"), Path("output.mkv"))
analysis = analyze(settings)
plan = build_plan(settings, analysis)
result = process(plan, on_event=lambda event: print(event.code, event.data))
```

Ядро само не печатает сообщения интерфейса. События состоят из кода и данных;
`CancelToken` поддерживает отмену из другого потока. Производственный пакет
в `src/`, исследовательские инструменты отдельно в `benchmarks/`.

Для batch/naming и split доступны общие функции:

```python
from analog_fpv_compressor import make_jobs, plan_outputs
from analog_fpv_compressor.jobs import destination_paths, validate_jobs

jobs = make_jobs(["*.avi"], output_dir="converted", output_suffix="_small")
reserved = {job.input_path.resolve() for job in jobs}
reserved.update(path for job in jobs for path in destination_paths(job))
for settings in jobs:
    analysis = analyze(settings)
    source_plan = build_plan(settings, analysis)
    outputs = plan_outputs(source_plan, split_flights=True)
    validate_jobs([plan.settings for plan in outputs], protected_paths=reserved)
    for output_plan in outputs:
        result = process(output_plan)
```

`make_jobs` проверяет входы и коллизии базовых имён. Когда части известны,
перед выполнением всего набора проверять их через
`analog_fpv_compressor.jobs.validate_jobs`, защищая также все входы и уже
зарезервированные пути. CLI это делает автоматически. API `process` остаётся
выполнением одного плана; управление очередью, журналами и общей отменой —
за вызывающей оболочкой. Детектор и кодирование не дублируются.

Для индикатора GUI используйте `event.code == "progress"`. Поля `stage`,
`completed`, `total`, `fraction`, `state`, `unit` имеют одинаковый смысл
во всех этапах; `fraction` — доля 0–1 текущего этапа, `None` — процент неизвестен.
`state` сообщает `started`, `running` или `completed`. Например,
`stage="encode", completed=640, total=1000, fraction=0.64, unit="frames"`.
Список этапов и правила успеха — в [источнике истины](source-of-truth.md).
100% кодирования сменяется проверкой с неопределённым индикатором;
показывать «Готово» только по `processing_completed`.
Подписывайте один обработчик и на `analyze(settings, emit=handler)`,
и на `process(plan, on_event=handler)`; выполняйте их в фоне, передавайте
события в UI-поток средствами GUI framework. `analysis_progress` и прежние
поля `frames/total_frames/seconds` encode сохраняются для существующих клиентов.

## Диагностический лог запуска

CLI и GUI используют один `EventLogger` на время работы приложения.
`fpv-compress.log` находится рядом с launcher `.exe`; при `python -m` —
рядом с Python текущей среды. Файл открывается с перезаписью один раз при запуске.
Несколько очередей одного окна GUI дописываются в него. `--log-file PATH`
в CLI меняет путь этого общего файла и работает также с несколькими входами.
Папка должна быть доступна для записи; коллизии с входами, результатами и
отчётами отклоняются. В GUI файл открывается кнопкой «Открыть лог».

Формат — UTF-8 JSON Lines внутри `.log`: `schema_version=1`, UTC `timestamp`,
`level`, английский `code`, структурированный `data`. Записываются окружение,
версии приложения/зависимостей/FFmpeg, входные свойства, запрошенные и выбранные
настройки с причинами, интервалы и mapping, команды, начало/конец этапов и
заданий, результат проверки, размер, длительность и время, отмены и ошибки
с traceback. При ошибке FFmpeg сохраняется хвост stderr до 80 строк/64 KiB;
временный буфер автоматически удаляется. Отдельных логов частей/FFmpeg и
`.log.jsonl` нет. События `progress/running` и `analysis_progress` в файл не
пишутся; живые события GUI и `--events-jsonl` продолжают получать прогресс.
Отдельные JSON-отчёты не создаются. `processing_completed.data.report` содержит
полный отчёт в общем логе; `job.analyzed.data.report` — план без encode,
`flights.summary` — сводку частей, включая частичный успех при ошибке/отмене.
В API `result.report` остаётся словарём в памяти, `result.report_path` равен `None`.
Старые поля `Settings.report_path/log_path` сохранены для совместимости структуры,
но оболочки их не задают и файлы по ним не создаются. CLI `--report` удалён.

## Иконки и локальный ярлык

Ресурсы находятся в `src/analog_fpv_compressor/gui/assets/` и включаются в wheel:
`icon-big.png` — исходник значка программы, `icon-big.ico` — Windows ICO
с размерами 16–256 px; `icon-small.png` — отдельный значок окна GUI.
ICO пересоздаётся командой `.\.venv\Scripts\python.exe scripts/build_icons.py`
с установленным GUI extra. `build_release.py` передаёт большой ICO в PyInstaller.
Текущий сценарий ZIP собирает CLI и GUI с общей средой через `packaging/windows.spec`.

`.\scripts\create_shortcut.ps1` создаёт локальный `fpv-compress.lnk` в корне
проекта для `.venv\Scripts\fpv-compress-gui.exe`, с большой иконкой.
Параметры `-Program` и `-Destination` позволяют указать другое размещение.
Ярлык содержит локальные пути и исключён из Git. При перемещении среды/проекта
пересоздать ярлык. pip создаёт launcher с встроенным значком Python;
Windows-ярлык и ZIP-сборка используют наш ICO.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Для интеграционных тестов FFmpeg должен находиться в PATH либо задайте
`FPV_FFMPEG_BIN` равным пути к его каталогу `bin`. Полные проверки реальных
AVI и повторение измерений описаны в [benchmarks/README.md](../benchmarks/README.md).
Пакетные проверки и разделение реальных записей выполняет
`benchmarks/validate_batch.py`; результаты остаются в `outputs/batch-validation/`.


## Иллюстрации пользовательского README

`scripts/capture_readme_visuals.py` создаёт сравнение шумоподавления и схему
удаления снега на четырёх языках из локальных `air-school-stadion-oneflight.AVI` и
`Frantisek.AVI`. Требуется установленный extra `.[gui]` и FFmpeg/ffprobe.
Подписи вынесены в `scripts/readme-visual-labels.json`; временные кадры,
точные индексы/PTS, команды и SHA256 исходников сохраняются только в
игнорируемом `outputs/readme-visuals/`. Финальные PNG находятся в `docs/images/`.
Сравнение использует одинаковый кадр с небом на 02:24 с выключенным/medium HQDN3D до
кодирования, прогретым на предшествующих кадрах. Деталь увеличена 2× без
усиления контраста. В кадре нет людей; координаты контекста и детали записываются
в provenance. Скромный визуальный эффект отделён от измеренного выигрыша размера:
2–3% на полётных отрывках, до 16% на отдельных участках при иных настройках CRF.
Лента схематическая: реальные кадры, масштаб времени
не соблюдается; это иллюстрация процесса, не снимок видеоредактора.

Дополнительное сравнение AV1 использует `home-other-helmet.AVI` на 01:36,067
и контрольный `weak-signal-av1-c48-off-r1.mkv` из
`outputs/denoise-recheck-2026-10-05/`. При отсутствии подготовить опыт командой
`benchmarks/recheck_denoise.py --directory outputs/denoise-recheck-2026-10-05`.
Скрипт проверяет совпадение исходного и выходного времени с учётом сдвига 95 с.
Подписи — `scripts/readme-codec-labels.json`; итог — `docs/images/codec-av1-*.png`.
Потолок обрезан без людей. Здесь denoise выключен: пример показывает codec,
не суммарный эффект codec и HQDN3D. Команды/индексы включены в provenance.

```powershell
.\.venv\Scripts\python.exe scripts/capture_readme_visuals.py
```

## Сборка и публикация релиза

Сценарий собирает CLI и GUI в один ZIP с общей средой и отдельными EXE.
Проверяется распакованная поставка вне среды разработки.

В разработке `.venv` остаётся локальной средой, а `pip install -e .` связывает
команду с редактируемыми исходниками. Релиз собирается отдельно: PyInstaller
упаковывает Python, наш код, NumPy и Qt/PySide6; сценарий добавляет инструкции, лицензии,
описание сборки и контрольные суммы, затем создаёт ZIP. `.venv`, AVI, результаты
экспериментов и зависимости сборщика целиком в архив не копируются.

Из корня проекта на Windows x64 с Python 3.13:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_release.py
```

Результат — `dist/analog-fpv-compressor-VERSION-windows-x64/`, одноимённый ZIP
и `.zip.sha256`. `BUILD.json` хранит версии компонентов, исходный коммит
и список включённых модулей Qt. Архив содержит `fpv-compress.exe`,
`fpv-compress-gui.exe`, общую `_internal/`, иконки, переводы и инструкции.
Исходники Qt Base/Qt Svg/PySide6 той же версии включены в `sources/`, лицензии
и атрибуции — в `licenses/`. Первый запуск сборщика скачивает их, кеширует
в игнорируемом `build/upstream-sources/`. Qt Virtual Keyboard, PDF и QML/Quick
исключены; сборка отклоняет непроверенные Qt DLL.
PATH при сборке ограничен Python и системными каталогами Windows: DLL других
инструментов не должны подменять системные зависимости Qt. В частности,
ICU из Poppler несовместима с используемой Qt и вызывала ошибку загрузки QtCore.
Манифест также отмечает наличие локальных правок. Повторная сборка не перезаписывает прежнюю:
выбрать свежий `--output-directory`. Побитовая воспроизводимость ZIP пока
не заявляется; записываются фактические версии и контрольные суммы.

Перед публикацией выполнить тесты и проверить **распакованный** EXE вне
репозитория, без PATH к Python/FFmpeg, с видеозаписью и путями с пробелами.
Для проверки можно отдельно подложить FFmpeg в `tools/`; эти бинарники не
должны попасть в публикуемый ZIP. Коммитить только рабочие файлы, затем
собирать публикуемый архив из чистого дерева, чтобы он соответствовал тегу.

Публикация версии `0.3.0` после успешных проверок. Для следующего релиза
обновить номер версии, заметки, тег и имя архива.

```powershell
git push origin main
git tag v0.3.0
git push origin v0.3.0
.\.venv\Scripts\python.exe scripts/publish_release.py dist/analog-fpv-compressor-0.3.0-windows-x64.zip --tag v0.3.0 --notes docs/releases/v0.3.0.md
```

Сценарий публикации использует авторизацию Git Credential Manager, проверяет
SHA256, чистоту исходников и совпадение удалённого тега с коммитом сборки.
Создаёт черновик, загружает ZIP и checksum, затем публикует GitHub Release.
Первый выпуск отмечается как prerelease; `--stable` предусмотрен для последующих
принятых стабильных версий. При ошибке загрузки черновик остаётся для диагностики.
Не запускать сценарий повторно вслепую: проверить существующий выпуск.
Также можно создать Release и прикрепить файлы вручную через GitHub.
Результаты изолированного запуска — в
[отчёте проверки релиза](research/release-validation-2026-10-04.md).


## Лицензия

Код проекта распространяется под [MIT](../LICENSE), выбранной пользователем:
разрешены изменение, распространение и коммерческое использование с сохранением
уведомления об авторстве и текста лицензии. Обязанности публиковать производные
исходники нет. Лицензии Python, NumPy и загрузчика PyInstaller сохраняются
отдельно в архиве; [уведомления о компонентах](../THIRD-PARTY-NOTICES.md).
Лицензия MIT нашего проекта не заменяет лицензии сторонних программ.

## Документы проекта

- [Источник истины: требования, архитектура и обоснования](source-of-truth.md).
- [План первой версии и прогресс](plan-v1.md).
- [Снимок локальной среды](setup.md).
- [Неизменяемый архив исходного исследования](research/source-chat.md).
- [Журнал экспериментов](research/experiments-2026-10-04.md).
- [Проверка ядра и CLI](research/cli-validation-2026-10-04.md).
- [Проверка ZIP-релиза](research/release-validation-2026-10-04.md).
- [Запуск исследовательских инструментов](../benchmarks/README.md).

Требования и причины решений ведутся в источнике истины; этот README содержит
инженерные инструкции. `setup.md` хранит особенности конкретного компьютера.
