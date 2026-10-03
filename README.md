# analog-fpv-compressor

Сжатие записей аналогового FPV DVR: минимальный размер при сохранении движения,
геометрии сцены и кратковременно видимых препятствий.

Проект на стадии фиксации требований; работающей программы пока нет.

- [Источник истины: итоговое видение, решения и обоснования](docs/source-of-truth.md)
- [Полный архив исходного исследования](docs/research/source-chat.md)
- [Требования первой версии](docs/requirements-v1.md)
- [План первой версии](docs/plan-v1.md)
- [Состояние рабочей среды](docs/setup.md)

Исходные DVR-примеры находятся локально в `examples/`; видео и результаты обработки
исключены из Git.

## Зависимости и подготовка Windows

Для исследовательской версии нужны:

- Git для работы с репозиторием.
- Python 3.10 или новее, 64-bit, с `pip` и `venv`:
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

В корне проекта создать среду и установить Python-зависимости:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Если установлен другой Python, заменить `-3.10` на его версию.
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

На текущем компьютере проверены Python 3.10.11, NumPy 2.2.6,
FFmpeg/ffprobe 9.0.2 full и SVT-AV1 4.2.0-123-g0c1c4deca.
Короткое AV1/HEVC-кодирование и нужные фильтры работают.
Особенности локальной установки описаны в [состоянии среды](docs/setup.md).

Инсталлер приложения отложен до отдельного решения. Пока это ручная подготовка
среды разработки, а не готовый установочный пакет.
