# Проверка Windows ZIP-релиза — 2026-10-04

Проверена сборка PyInstaller 6.22.0 / hooks 2026.8, CPython 3.13.15,
NumPy 2.2.6, приложение 0.1.0. Пробный ZIP занимает 20 852 507 байт.
Финальный ZIP собирается отдельно из закоммиченного дерева; размер и SHA256
записываются при сборке, а SHA256 загружается отдельным asset в GitHub Release.
Побитовая воспроизводимость разных сборок не заявляется.

## Изолированный запуск

`scripts/verify_release.py` распаковал архив в отдельную временную папку вне
репозитория. PATH дочернего процесса содержал только Windows System32;
переменные PYTHONPATH, FFMPEG_DIR и LOCALAPPDATA не передавались. Поэтому
запуск не мог опираться на Python из `.venv` или поиск FFmpeg через WinGet.
Проверены checksum всех файлов внутри распакованного архива и отсутствие
бинарников FFmpeg/ffprobe в ZIP.

Для тестовых encode отдельно скопированы FFmpeg/ffprobe 9.0.2 в `tools/`
**только временной тестовой распаковки**, а не исходной поставки. Проверочные
файлы и временная папка оставлены для диагностики.

| Проверка | Результат | Время, с |
|---|---|---:|
| `--help` без Python в PATH | PASS | 2.63 |
| `--version` | PASS | 1.90 |
| Понятная ошибка при отсутствии FFmpeg | PASS, exit 1 | 0.40 |
| Минимальный запуск на 20 с sun | PASS; видеопакеты совпали с CLI | 18.23 |
| MP4/AAC на home, пробелы и Unicode в пути | PASS | 4.14 |
| HEVC, scale 480×360, отключённые дополнительные этапы | PASS | 1.35 |
| Полный oneflight с auto | PASS; видеопакеты совпали с CLI | 131.30 |

Полные данные проверочного запуска находятся локально в
`outputs/release-candidate-smoke-v2/`: команды, stdout/stderr, отчёты и
`summary.json`. Итоговый набор после добавления portable discovery:
**37/37 PASS**, 19.059 с; `outputs/release-tests.log`.
Для публикуемого ZIP проверка повторяется, материалы — в
`outputs/release-final-smoke/`.

Это проверка работы без установленного Python **в PATH** на текущем Windows.
Отдельная чистая Windows VM и другие версии ОС не проверялись. Установка
FFmpeg через WinGet в рамках теста не повторялась: текущая full-сборка уже
установлена. Синтаксис setup-скрипта проверен парсером PowerShell.

## Состав и повторение

ZIP включает EXE, `_internal/`, инструкции, setup-скрипт FFmpeg, полный MIT
текст нашего проекта и лицензии комплектных Python/NumPy/PyInstaller.
FFmpeg устанавливается пользователем отдельно, исходные AVI не входят в поставку.
`BUILD.json` показывает исходный коммит, версии и отсутствие локальных правок.

```powershell
.\.venv\Scripts\python.exe scripts/build_release.py
.\.venv\Scripts\python.exe scripts/verify_release.py dist/analog-fpv-compressor-0.1.0-windows-x64.zip --ffmpeg-dir "C:\path\to\ffmpeg\bin" --full --output-directory outputs/release-final-smoke
```

Проверочный сценарий использует локальные образцы и короткие source-клипы
предыдущего цикла. На другом компьютере сначала подготовить их с помощью
`benchmarks/validate_cli_edges.py` и `benchmarks/validate_cli_audio_mp4.py`;
пути этого цикла явно видны в сценарии. Это внутренний регрессионный инструмент,
пользователю готового ZIP он не нужен.

Требования и выбор лицензии — в [источнике истины](../source-of-truth.md),
процесс сборки/публикации — в [инженерном README](../README-engineering.md).
