# analog-fpv-compressor

Сжатие записей аналогового FPV DVR: минимальный размер при сохранении движения,
геометрии сцены и кратковременно видимых препятствий.

Реализованы Python-ядро и CLI для одного входного файла. Первый цикл
исследований на трёх локальных DVR-записях завершён; GUI пока не реализован.

- [Источник истины: итоговое видение, решения и обоснования](docs/source-of-truth.md)
- [Полный архив исходного исследования](docs/research/source-chat.md)
- [План первой версии](docs/plan-v1.md)
- [Состояние рабочей среды](docs/setup.md)
- [Журнал экспериментов и результаты](docs/research/experiments-2026-10-04.md)
- [Проверка готового ядра и CLI на исходных файлах](docs/research/cli-validation-2026-10-04.md)
- [Запуск исследовательских инструментов](benchmarks/README.md)

Документы разделены по назначению: источник истины отвечает на «что и почему»,
план — «в каком порядке и что уже сделано», setup — «что проверено на этом компьютере».
Исходные DVR-примеры: [описание и правила хранения](examples/README.md).

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

Проверенные версии и особенности этого компьютера: [локальная среда](docs/setup.md).
Это подготовка среды разработки; решение об инсталлере хранится в источнике истины.

## Запуск

Создайте каталог назначения. Минимальная команда:

```powershell
New-Item -ItemType Directory -Path outputs -Force
.\.venv\Scripts\fpv-compress.exe --input examples/air-school-stadion-oneflight.AVI --output outputs/flight.mkv
```

Равнозначный запуск: `.\.venv\Scripts\python.exe -m analog_fpv_compressor` с теми
же аргументами. В активированной среде доступна команда `fpv-compress`.
Программа ищет FFmpeg/ffprobe в PATH, затем установленный через WinGet FFmpeg
на Windows. Для другой установки задайте `--ffmpeg-dir PATH` или `FFMPEG_DIR`.

Начальная auto-политика: AV1 CRF 48/preset 6, HQDN3D medium, исходное
разрешение, проверка idet, осторожное вырезание длительного снега, удаление
звука. Это изменяемые настройки, основанные на первой выборке DVR.
Синий экран автоматически не удаляется: в нём могут кратко появляться полезные кадры.

План без кодирования, компактный вариант и отключение дополнительных этапов:

```powershell
.\.venv\Scripts\fpv-compress.exe -i examples/home-other-helmet.AVI -o outputs/home-plan.mkv --analyze-only
.\.venv\Scripts\fpv-compress.exe -i examples/air-school-stadion-oneflight.AVI -o outputs/compact.mkv --crf 48 --preset 6 --denoise medium --scale 480x360
.\.venv\Scripts\fpv-compress.exe -i examples/home-other-helmet.AVI -o outputs/no-filters.mkv --denoise off --deinterlace off --cut-no-signal off
.\.venv\Scripts\fpv-compress.exe -i examples/home-other-helmet.AVI -o outputs/with-audio.mkv --audio keep
.\.venv\Scripts\fpv-compress.exe --help
```

Поддержаны MKV и MP4, AV1 и HEVC (`--codec hevc --preset medium`), CRF или
целевой битрейт (`--bitrate 500k`). Явные CRF и bitrate одновременно запрещены.
При сохранении звука используется FLAC для MKV и AAC для MP4; применяются
те же интервалы исходной шкалы, что и для видео. Если звука нет, задание
создаёт видео без аудиопотока и объясняет решение в отчёте.

Рядом с результатом сохраняются `OUTPUT.report.json`, `OUTPUT.log`,
`OUTPUT.log.jsonl` и отдельный подробный FFmpeg log. Отчёт содержит запрошенные
и выбранные настройки, причины, удалённые интервалы и mapping исходных/выходных
таймкодов, версии инструментов и проверки результата. `--report`, `--log-file`
меняют назначения; `--events-jsonl` выводит машинные события в stdout.
Все сообщения CLI на английском, читаемый лог выводится в stderr.

Исходники и существующие результаты не перезаписываются. Отмена — Ctrl+C;
неполный временный результат удаляется. Полный входной снег с `auto` вызывает
понятную ошибку вместо создания вводящего в заблуждение видео; для намеренного
сохранения такого файла используйте `--cut-no-signal off`.

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

## Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Для интеграционных тестов FFmpeg должен находиться в PATH либо задайте
`FPV_FFMPEG_BIN` равным пути к его каталогу `bin`. Полные проверки реальных
AVI и повторение измерений описаны в [benchmarks/README.md](benchmarks/README.md).

## Готовый Windows-релиз

Архивы публикуются в [GitHub Releases](https://github.com/koolakoff/analog-fpv-compressor/releases).
Скачать `analog-fpv-compressor-VERSION-windows-x64.zip` и распаковать **всю**
папку: `fpv-compress.exe` использует комплектный Python и NumPy из `_internal/`.
Устанавливать Python и создавать `.venv` пользователю релиза не требуется.
Это консольная версия для Windows 10/11 x64; GUI и инсталлер пока отсутствуют.

FFmpeg поставляется отдельно. Если совместимой full-сборки ещё нет, запустить
`setup-ffmpeg.cmd`: скрипт устанавливает `Gyan.FFmpeg` через WinGet и требует
интернет. При отсутствии WinGet установить Microsoft App Installer либо
скачать full-сборку Gyan вручную. Essentials не подходит для AV1/SVT.
Можно положить `ffmpeg.exe` и `ffprobe.exe` в `tools/` рядом с программой
или задать `--ffmpeg-dir`. Уже установленная full-сборка WinGet обнаруживается
автоматически. Бинарники FFmpeg в ZIP этого релиза **не включены**.

Запуск из распакованной папки в PowerShell:

```powershell
.\fpv-compress.exe -i "C:\Videos\flight.avi" -o "C:\Videos\flight-small.mkv"
.\fpv-compress.exe --help
```

### Как создаётся архив

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

Выпуск для текущей версии `0.1.0` после успешных проверок:

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
[отчёте проверки релиза](docs/research/release-validation-2026-10-04.md).

## Лицензия

Код проекта распространяется под [MIT](LICENSE), выбранной пользователем:
разрешены изменение, распространение и коммерческое использование с сохранением
уведомления об авторстве и текста лицензии. Обязанности публиковать производные
исходники нет. Лицензии Python, NumPy и загрузчика PyInstaller сохраняются
отдельно в архиве; [уведомления о компонентах](THIRD-PARTY-NOTICES.md).
Лицензия MIT нашего проекта не заменяет лицензии сторонних программ.
