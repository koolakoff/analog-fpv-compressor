# analog-fpv-compressor

Сжатие записей аналогового FPV DVR: минимальный размер при сохранении движения,
геометрии сцены и кратковременно видимых препятствий.

Проект на стадии фиксации требований; работающей программы пока нет.

- [Источник истины: итоговое видение, решения и обоснования](docs/source-of-truth.md)
- [Полный архив исходного исследования](docs/research/source-chat.md)
- [План первой версии](docs/plan-v1.md)
- [Состояние рабочей среды](docs/setup.md)

Документы разделены по назначению: источник истины отвечает на «что и почему»,
план — «в каком порядке и что уже сделано», setup — «что проверено на этом компьютере».
Исходные DVR-примеры: [описание и правила хранения](examples/README.md).

## Зависимости и подготовка Windows

Для исследовательской версии нужны:

- Git для работы с репозиторием.
- Поддерживаемый Python, 64-bit, с `pip` и `venv`; для проекта выбран Python 3.13:
  [официальная загрузка](https://www.python.org/downloads/windows/).
- NumPy в отдельной виртуальной среде проекта.
- FFmpeg и ffprobe из **full** сборки с `libsvtav1`, `libx265` и фильтрами
  `idet`, `bwdif`, `hqdn3d`. SVT-AV1 отдельно устанавливать не нужно.
  `libx265` нужен для сравнения в benchmark.

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
