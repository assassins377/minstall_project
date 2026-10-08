# Сборка MInstAll

Инструкция по сборке MInstAll для Windows 10/11 и запуску исходников для разработки на Linux.

---

## Содержание

- [Требования](#требования)
- [Windows 10 / Windows 11](#windows-10--windows-11)
- [Windows 7](#windows-7)
- [Linux (Ubuntu / Debian)](#linux-ubuntu--debian)
- [Linux (Fedora / RHEL)](#linux-fedora--rhel)
- [Linux (Arch / Manjaro)](#linux-arch--manjaro)
- [Сборка .exe через PyInstaller](#сборка-exe-через-pyinstaller)
- [Тестирование сборки](#тестирование-сборки)
- [Частые проблемы](#частые-проблемы)
- [Сборка через CI (GitHub Actions)](#сборка-через-ci-github-actions)

---

## Требования

| Инструмент | Минимум | Рекомендуется |
|---|---|---|
| Python | 3.10 | 3.11 или 3.12 |
| pip | 23.0 | последний |
| Git | любой | последний |
| Свободное место | 500 МБ | 1 ГБ |
| ОЗУ для сборки | 2 ГБ | 4 ГБ |

**Целевая платформа установки:** Windows 10/11; архитектуры сборки — x86 и x64. На Linux с GTK3 доступна разработка и проверка логики, но не установка Windows-программ. Совместимость со старыми Windows отдельно не подтверждена.

**Сборка приложения `.exe`:** только Windows (PyInstaller компилирует под целевую ОС).

---

## Windows 10 / Windows 11

### Шаг 1 — Установка Python

Скачайте Python 3.10+ с [python.org](https://www.python.org/downloads/windows/).

⚠ **Важно:** при установке поставьте галочки:
- ✅ **Add Python to PATH**
- ✅ **Install pip**

Для сборки 32-битного `.exe` нужен 32-битный Python (Windows installer (32-bit)).
Для 64-битного — соответственно 64-битный.

Проверь установку:

```powershell
python --version
# Python 3.10.x
pip --version
```

### Шаг 2 — Клонирование репозитория

```powershell
git clone https://github.com/assassins377/minstall_project.git
cd minstall_project
```

### Шаг 3 — Виртуальное окружение (опционально, но рекомендуется)

```powershell
python -m venv .venv
.venv\Scripts\activate
```

### Шаг 4 — Установка зависимостей

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

Или через `pyproject.toml`:

```powershell
pip install -e .[dev,build]
```

### Шаг 5 — Запуск из исходников

```powershell
python main.py
```

### Шаг 6 — Сборка `.exe`

```powershell
pip install pyinstaller
pyinstaller --clean --noconsole --onefile --uac-admin --name MInstAll_x64 --icon=icons/app.ico --add-data "i18n;i18n" --add-data "profiles;profiles" --add-data "icons;icons" main.py
```

Готовый файл будет в `dist\MInstAll_x64.exe`.

Для 32-битной версии повтори всё то же самое с 32-битным Python и параметром `--name MInstAll_x86`.

---

## Windows 7

Текущий проект требует Python >=3.10, и CI собирает EXE на Python 3.10. По [документации Python](https://docs.python.org/3.10/using/windows.html) эта версия поддерживает Windows 8.1 и новее, поэтому обещать работу текущих сборок на Windows 7 нельзя.

Python 3.8 поддерживал Windows 7, но не удовлетворяет требованиям этого проекта. Для Windows 7 нужна отдельная адаптация кода и зависимостей и проверка на реальной системе; готового подтверждённого варианта сейчас нет. Выбор x86 вместо x64 эту проблему не решает.

---

## Linux (Ubuntu / Debian)

### Шаг 1 — Системные зависимости

wxPython на Linux требует GTK3 и пакеты для компиляции:

```bash
sudo apt update
sudo apt install -y \
    python3.10 python3.10-venv python3-pip \
    libgtk-3-dev libgtk-3-0 \
    libwebkit2gtk-4.0-dev libwebkit2gtk-4.0-37 \
    libnotify-dev libnotify4 \
    libsm-dev libsm6 \
    libsdl2-dev libsdl2-2.0-0 \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    freeglut3-dev libpng-dev libjpeg-dev \
    build-essential
```

### Шаг 2 — Клонирование и venv

```bash
git clone https://github.com/assassins377/minstall_project.git
cd minstall_project
python3.10 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```

### Шаг 3 — Установка wxPython

На Linux официальные wheel-файлы wxPython доступны не для всех дистрибутивов. Если `pip install wxPython` падает с компиляцией — используй extras-репозиторий:

```bash
# Получи кодовое имя своего Ubuntu (например, "jammy" для 22.04)
. /etc/os-release
echo $UBUNTU_CODENAME

# Установи wxPython с extras index
pip install -U \
    -f https://extras.wxpython.org/wxPython4/extras/linux/gtk3/ubuntu-$UBUNTU_CODENAME \
    wxPython
```

Список доступных билдов: [extras.wxpython.org/wxPython4/extras/linux/gtk3/](https://extras.wxpython.org/wxPython4/extras/linux/gtk3/)

Если твоего дистрибутива нет — будет компиляция из исходников (15-30 минут).

### Шаг 4 — Установка остальных зависимостей

```bash
pip install psutil pytest pyinstaller
```

### Шаг 5 — Запуск

```bash
python main.py
```

### ⚠ Особенности работы на Linux

MInstAll спроектирован для Windows — на Linux работают:

- ✅ GUI (отображение программ, поиск, выбор)
- ✅ Тесты (`pytest tests/`)
- ❌ Реальная установка `.exe`/`.msi` (нет Windows API)
- ❌ Проверка реестра (используется заглушка)
- ❌ Проверка `.NET Framework`

Linux-версия полезна для **разработки и тестирования логики**, но конечный пользователь должен запускать `.exe` на Windows.

---

## Linux (Fedora / RHEL)

```bash
sudo dnf install -y \
    python3.10 python3-pip python3-virtualenv \
    gtk3-devel webkit2gtk3-devel \
    libnotify-devel SDL2-devel \
    gstreamer1-devel gstreamer1-plugins-base-devel \
    freeglut-devel libpng-devel libjpeg-turbo-devel \
    gcc gcc-c++ make
```

Дальше — как в Ubuntu (venv, pip install).

---

## Linux (Arch / Manjaro)

```bash
sudo pacman -S --needed \
    python python-pip python-virtualenv \
    gtk3 webkit2gtk \
    libnotify sdl2 \
    gstreamer gst-plugins-base \
    freeglut libpng libjpeg-turbo \
    base-devel
```

В Arch wxPython доступен в AUR:

```bash
yay -S python-wxpython
```

Это быстрее чем компиляция через pip.

---

## Сборка `.exe` через PyInstaller

### Базовая команда (PowerShell на Windows)

```powershell
pyinstaller --clean --noconsole --onefile --uac-admin --name MInstAll_x64 --icon=icons/app.ico --add-data "i18n;i18n" --add-data "profiles;profiles" --add-data "icons;icons" main.py
```

| Флаг | Что делает |
|---|---|
| `--clean` | Удаляет временные файлы предыдущей сборки |
| `--noconsole` | Создаёт GUI-приложение без консольного окна |
| `--onefile` | Собирает один EXE; без этого флага получается папка с зависимостями |
| `--uac-admin` | Запрашивает права администратора при запуске |
| `--name X` | Задаёт имя файла; архитектура определяется Python, а не именем |
| `--icon=icons/app.ico` | Использует готовую Windows-иконку без конвертации PNG |
| `--add-data` | Включает переводы, профили и иконки |

### Ресурсы и пользовательские файлы

Каталоги `i18n`, `profiles` и `icons` включаются в EXE. Файлы рядом с приложением имеют приоритет над встроенными ресурсами. Кеш извлечённых иконок хранится в `%LOCALAPPDATA%\MInstAll\icons\cache`.

`programs.json` и каталог `software/` остаются внешними пользовательскими данными рядом с EXE. Добавлять их внутрь EXE не нужно. Для Python-пакета `pip install .` упаковывает модули и ресурсы через `pyproject.toml`.

### Результат сборки

```
dist/
└── MInstAll_x64.exe   ← готовый файл (~30-40 МБ)

build/    ← промежуточные файлы (можно удалить)
MInstAll_x64.spec  ← конфиг PyInstaller (для повторных сборок)
```

---

## Тестирование сборки

После сборки запусти `dist\MInstAll_x64.exe` и проверь:

1. ✅ Окно открывается без ошибок
2. ✅ Список программ строится из `software/`; при необходимости используются метаданные внешнего `programs.json`
3. ✅ Встроенные иконки и переводы отображаются без внешних папок `icons/` и `i18n/`
4. ✅ Меню "Справка → О программе" работает
5. ✅ Поиск фильтрует список, доступны три встроенных профиля
6. ✅ На тестовой системе проверены установка и отмена; обновление проверено с совпадающим и несовпадающим SHA-256

### Запуск тестов перед сборкой

Всегда запускай unit-тесты перед коммитом / сборкой:

```bash
python -m pytest tests/ -v
```

Все тесты должны проходить. Количество и длительность зависят от текущей версии; успешная сборка в CI не заменяет ручную проверку GUI и установки на Windows.

### Проверка `.exe` антивирусом

PyInstaller-сборки иногда триггерят эвристики антивирусов (false positive). Проверь на [VirusTotal](https://www.virustotal.com/) перед публикацией.

---

## Частые проблемы

### `ModuleNotFoundError: No module named 'wx'`

Не активировано виртуальное окружение или wxPython не установлен.

```bash
# Активация venv
source .venv/bin/activate     # Linux
.venv\Scripts\activate        # Windows

# Переустановка
pip install --force-reinstall wxPython
```

### `OSError: [WinError 87] Параметр задан неверно` при сборке

Старая версия PyInstaller. Обнови:

```bash
pip install --upgrade pyinstaller
```

### `.exe` запускается медленно (5+ секунд)

Это нормально для PyInstaller `--onefile` — файл распаковывается во временную папку при каждом запуске.

**Решение:** собирай без `--onefile` (получится папка с `.exe` + DLL'ками, без распаковки зависимостей при каждом запуске):

```powershell
pyinstaller --clean --noconsole --uac-admin --name MInstAll_x64 --icon=icons/app.ico --add-data "i18n;i18n" --add-data "profiles;profiles" --add-data "icons;icons" main.py
```

### `wxPython` не собирается на Linux: `error: GTK+ 3.0 not found`

Установи GTK-dev пакеты (см. секцию своего дистрибутива выше).

### Антивирус удаляет `.exe`

Проверь источник, контрольную сумму и причину обнаружения. Если считаешь обнаружение ложным, отправь файл на проверку производителю антивируса.

Публикация в GitHub Releases сама по себе не гарантирует исчезновение предупреждений SmartScreen. Репутация зависит от нескольких сигналов, фиксированного порога нет; новая неподписанная сборка набирает её заново. См. [Microsoft Learn](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

### Сборка вылетает с `RecursionError`

Проверь traceback: ошибка может возникать в анализаторе PyInstaller или в самом приложении. Параметр `--runtime-tmpdir` меняет каталог распаковки и не исправляет глубину рекурсии. Для ошибки анализа сборки настрой предел рекурсии в `.spec` согласно traceback и повтори сборку.

---

## Сборка через CI (GitHub Actions)

Build & Test проверяет изменения и собирает EXE для x86/x64 при push в `main` и при pull request. Для публикации Release используется новый тег версии (подставь согласованную версию вместо примера):

```bash
git tag vX.Y.Z
git push origin vX.Y.Z
```

CI автоматически:
1. Запустит тесты (`pytest`)
2. Соберёт `MInstAll_x86.exe` (на 32-битном Python)
3. Соберёт `MInstAll_x64.exe` (на 64-битном Python) — **параллельно**
4. Соберёт `MInstAll_x86_portable.zip` и `MInstAll_x64_portable.zip`, создаст `.sha256` для каждого EXE и ZIP
5. Опубликует GitHub Release с инструкцией для пользователей

Время выполнения зависит от доступности runners и установки зависимостей. См. [.github/workflows/release.yml](.github/workflows/release.yml).

### Скачивание готовых сборок без локальной компиляции

[**MInstAll Releases**](https://github.com/assassins377/minstall_project/releases) содержит опубликованные релизы. Если их ещё нет, EXE доступны в артефактах успешного [Build & Test](https://github.com/assassins377/minstall_project/actions/workflows/build.yml). Portable ZIP и `.sha256` формирует Release-пайплайн при публикации тега.

Для современной 64-битной Windows выбирай x64; для 32-битной — x86. Имена файлов и команды SHA-256 приведены в [README.md](README.md#скачать). Готовый Linux-бинарник не публикуется.

---

## Что дальше

- [README.md](README.md) — что умеет MInstAll, как использовать
- [tests/](tests/) — unit-тесты, изучи перед добавлением фич
- [.github/workflows/](.github/workflows/) — CI/CD конфигурация

## Лицензия

GNU GPL v3 — см. [LICENSE](LICENSE).
