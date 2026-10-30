# Smart Launcher for ComfyUI

Windows-приложение для установки и запуска ComfyUI, обновления ядра,
обслуживания Python-окружения, загрузки моделей и управления Custom Nodes.

`source_v2` — основная рабочая версия Smart Launcher 2.0.
ComfyUI устанавливается отдельно; его исходники и модели не входят в этот репозиторий.

## Возможности

- Новая установка ComfyUI или подключение существующей.
- Профили NVIDIA CUDA и CPU, выбор совместимого Python.
- Запуск, остановка, открытие браузера и копирование LAN-адреса.
- Автоматическая проверка обновлений с ручной установкой.
- Отдельное обслуживание окружения без пересоздания venv при обычном обновлении.
- Очередь загрузки моделей Civitai, Hugging Face и прямых ссылок.
- Управление Custom Nodes и интеграция с ComfyUI Manager.
- События, консоль, рекомендации и прогресс операций.

## Запуск EXE

Скачайте `SmartLauncher2.exe` из раздела Releases и запустите его.
При первом запуске выберите новую установку или существующую папку ComfyUI.
Понадобятся установленный Git и совместимый 64-битный CPython.
Для профиля NVIDIA необходим совместимый драйвер видеокарты.

Профиль сохраняется в `%LOCALAPPDATA%\SmartLauncherComfyUI2\profile.json`.
Токены Civitai и Hugging Face хранятся в этом файле в открытом виде:
не публикуйте профиль и не прикладывайте его к сообщениям об ошибках.

## Запуск Из Исходников

Требуется Python 3.12–3.14 с Tkinter. Окружение разработки лаунчера
отделено от окружения ComfyUI.

```powershell
Set-Location source_v2
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install "customtkinter>=5.2,<6" "packaging>=24,<27" "pillow>=10,<13" "websockets>=14,<17"
.\.venv\Scripts\python.exe -B -m smart_launcher run
```

Для отдельного тестового профиля:

```powershell
.\.venv\Scripts\python.exe -B -m smart_launcher run --config "$env:TEMP\smart-launcher-test\profile.json"
```

## Тесты

Из каталога `source_v2`:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
```

GUI-тесты требуют Windows desktop и переменную `SMART_LAUNCHER_UI_TEST=1`.
Тесты используют временные каталоги, Git-репозитории, HTTP-серверы и процессы.

## Сборка

Из каталога `source_v2`:

```powershell
.\.venv\Scripts\python.exe -m pip install "pyinstaller>=6,<7"
.\.venv\Scripts\python.exe build_exe.py
```

Результат: `release_v2/SmartLauncher2.exe` относительно корня репозитория.
Сборка включает минимальную проверку запуска Tk; она не заменяет проверку
установки ComfyUI и генерации.

## Структура

- `source_v2/smart_launcher/` — приложение: UI, сценарии, модели данных и адаптеры.
- `source_v2/tests/` — автоматизированные проверки.
- `source_v2/build_exe.py` и `build_exe.bat` — упаковка Windows EXE.
- `source_v2/assets/` и `build_hooks/` — ресурсы сборки.

ComfyUI, PyTorch, Manager и Custom Nodes — внешние компоненты со своими
версиями, требованиями и лицензиями. Smart Launcher не является официальным
продуктом ComfyUI.
