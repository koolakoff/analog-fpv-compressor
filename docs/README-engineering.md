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

Текущая установленная из исходников версия — 0.2.0. Опубликованный ZIP 0.1.0
остаётся прежним выпуском; batch и split появились после него.

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

CLI и будущий GUI используют один пакет, без отдельного сервера:

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

## Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Для интеграционных тестов FFmpeg должен находиться в PATH либо задайте
`FPV_FFMPEG_BIN` равным пути к его каталогу `bin`. Полные проверки реальных
AVI и повторение измерений описаны в [benchmarks/README.md](../benchmarks/README.md).
Пакетные проверки и разделение реальных записей выполняет
`benchmarks/validate_batch.py`; результаты остаются в `outputs/batch-validation/`.


## Сборка и публикация релиза

В разработке `.venv` остаётся локальной средой, а `pip install -e .` связывает
команду с редактируемыми исходниками. Релиз собирается отдельно: PyInstaller
упаковывает Python, наш код и NumPy; сценарий добавляет инструкции, лицензии,
описание сборки и контрольные суммы, затем создаёт ZIP. `.venv`, AVI, результаты
экспериментов и зависимости сборщика целиком в архив не копируются.

Из корня проекта на Windows x64 с Python 3.13:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe scripts/build_release.py
```

Результат — `dist/analog-fpv-compressor-VERSION-windows-x64/`, одноимённый ZIP
и `.zip.sha256`. `BUILD.json` хранит версии компонентов, исходный коммит
и наличие локальных правок. Повторная сборка не перезаписывает прежнюю:
выбрать свежий `--output-directory`. Побитовая воспроизводимость ZIP пока
не заявляется; записываются фактические версии и контрольные суммы.

Перед публикацией выполнить тесты и проверить **распакованный** EXE вне
репозитория, без PATH к Python/FFmpeg, с видеозаписью и путями с пробелами.
Для проверки можно отдельно подложить FFmpeg в `tools/`; эти бинарники не
должны попасть в публикуемый ZIP. Коммитить только рабочие файлы, затем
собирать публикуемый архив из чистого дерева, чтобы он соответствовал тегу.

Пример публикации версии `0.1.0` после успешных проверок. Этот выпуск уже
создан: для следующего релиза обновить номер версии в пакете, метаданных
и инструкции поставки, подготовить новые заметки и использовать новый тег
и имя архива в командах ниже.

```powershell
git push origin main
git tag v0.1.0
git push origin v0.1.0
.\.venv\Scripts\python.exe scripts/publish_release.py dist/analog-fpv-compressor-0.1.0-windows-x64.zip --tag v0.1.0 --notes docs/releases/v0.1.0.md
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
