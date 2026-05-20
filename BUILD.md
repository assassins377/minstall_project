# Сборка MInstAll

Инструкция по компиляции MInstAll из исходников для Windows 7/10/11 и Linux.

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

**Главное приложение работает на:** Windows 7+ (32-bit и 64-bit), Linux с GTK3.

**Сборка `.exe`-инсталлятора:** только Windows (PyInstaller компилирует под целевую ОС).

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
pyinstaller --clean --noconsole --onefile --uac-admin --name MInstAll_x64 --icon=icons/system.png main.py
```

Готовый файл будет в `dist\MInstAll_x64.exe`.

Для 32-битной версии повтори всё то же самое с 32-битным Python и параметром `--name MInstAll_x86`.

---

## Windows 7

Windows 7 официально поддерживается, но требует осторожности с версией Python.

### Какой Python использовать

| Версия Python | Win7 SP1 |
|---|---|
| 3.8 | ✅ Последняя официально поддерживаемая |
| 3.9 — 3.11 | ⚠ Работают неофициально, нужны KB-обновления |
| 3.12+ | ❌ Не запустится |

**Рекомендация для Win7:** Python **3.8.10** (последний релиз с официальным инсталлятором для Win7).

### Шаг 1 — Подготовка системы

Установите обязательные обновления Windows 7:

- **KB4474419** — поддержка SHA-2 (обязательно для всех скачиваний)
- **KB4490628** — Servicing Stack Update
- **Visual C++ Redistributable 2015-2022** ([download](https://aka.ms/vs/17/release/vc_redist.x86.exe))

Без этих обновлений Python 3.10+ не запустится, а pip не скачает пакеты (TLS 1.2 не работает без SHA-2).

### Шаг 2 — Установка Python 3.8

Скачайте [Python 3.8.10](https://www.python.org/downloads/release/python-3810/) → Windows x86 installer.

При установке:
- ✅ **Add Python 3.8 to PATH**
- Customize → **Install for all users**

### Шаг 3 — Обновление pip

Стандартный pip в Python 3.8 не умеет TLS 1.2 — нужно обновить через прокси-команду:

```powershell
python -m pip install --upgrade pip --trusted-host pypi.org --trusted-host files.pythonhosted.org
```

### Шаг 4 — Установка зависимостей

```powershell
pip install wxPython==4.2.1 psutil pyinstaller pytest
```

⚠ **Не используй `requirements.txt` напрямую** на Win7 — там может быть `wxPython>=4.2.0` без верхней границы, и pip попробует поставить более новую версию которая не соберётся.

### Шаг 5 — Сборка

```powershell
pyinstaller --clean --noconsole --onefile --uac-admin --name MInstAll_x86 --icon=icons/system.png main.py
```

Получившийся `dist\MInstAll_x86.exe` будет запускаться на Win7/8/10/11.

### Известные проблемы Windows 7

- **"VCRUNTIME140_1.dll отсутствует"** — установи Visual C++ Redistributable 2015-2022
- **Высокий DPI выглядит размыто** — добавь к `.exe` Manifest с `dpiAware = true` (PyInstaller делает это автоматически)
- **Pip не работает с pypi.org** — обнови корневые сертификаты Windows через `certutil -generateSSTFromWU rootscerts.sst`

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

### Базовая команда

```bash
pyinstaller \
    --clean \
    --noconsole \
    --onefile \
    --name MInstAll_x64 \
    --icon=icons/system.png \
    main.py
```

### Что означают флаги

| Флаг | Что делает |
|---|---|
| `--clean` | Удаляет временные файлы предыдущей сборки |
| `--noconsole` | Не показывать чёрное консольное окно при запуске GUI |
| `--onefile` | Один `.exe` вместо папки с зависимостями |
| `--name X` | Имя выходного файла |
| `--icon=path.png` | Иконка `.exe` |

### Дополнительные оптимизации

**Уменьшить размер `.exe`:**

```bash
pyinstaller --clean --noconsole --onefile \
    --strip \
    --exclude-module tkinter \
    --exclude-module unittest \
    --exclude-module test \
    --name MInstAll_x64 \
    --icon=icons/system.png \
    main.py
```

Это убирает 5-10 МБ ненужных модулей.

**Включить дополнительные файлы (иконки и т.д.):**

```bash
pyinstaller --clean --noconsole --onefile \
    --add-data "icons;icons" \
    --add-data "programs.json;." \
    --name MInstAll_x64 \
    main.py
```

⚠ Сейчас файлы читаются из соседней папки (PyInstaller `--onefile` распаковывает их во временную директорию), поэтому `--add-data` не обязателен — но если хочешь полностью самодостаточный `.exe`, добавь его.

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
2. ✅ Список программ загружается из `programs.json` (положи его рядом с `.exe`)
3. ✅ Иконки отображаются (положи папку `icons/` рядом с `.exe`)
4. ✅ Меню "Справка → О программе" работает
5. ✅ Поиск фильтрует список

### Запуск тестов перед сборкой

Всегда запускай unit-тесты перед коммитом / сборкой:

```bash
python -m pytest tests/ -v
```

Ожидаемо: **64 passed in ~0.05s**.

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

**Решение:** собирай без `--onefile` (получится папка с `.exe` + DLL'ками, но запускается мгновенно):

```bash
pyinstaller --clean --noconsole --name MInstAll_x64 --icon=icons/system.png main.py
```

### `wxPython` не собирается на Linux: `error: GTK+ 3.0 not found`

Установи GTK-dev пакеты (см. секцию своего дистрибутива выше).

### `psutil` не ставится на Win7

Возьми wheel напрямую:

```powershell
pip install psutil --only-binary :all:
```

Или установи Visual C++ Build Tools для компиляции из исходников.

### Антивирус удаляет `.exe`

Добавь папку `dist/` в исключения антивируса на время разработки.

Для production-релиза опубликуй `.exe` через GitHub Releases — Microsoft постепенно набирает репутацию для часто скачиваемых файлов.

### Сборка вылетает с `RecursionError`

PyInstaller иногда упирается в Python default `sys.setrecursionlimit`. Увеличь:

```bash
pyinstaller --clean --noconsole --onefile \
    --runtime-tmpdir . \
    main.py
```

Или добавь в начало `main.py`:

```python
import sys
sys.setrecursionlimit(5000)
```

---

## Сборка через CI (GitHub Actions)

Самый простой способ собрать `.exe` для x86 и x64 — пушнуть тег в GitHub:

```bash
git tag v2.1.0
git push origin v2.1.0
```

CI автоматически:
1. Запустит 64 теста (`pytest`)
2. Соберёт `MInstAll_x86.exe` (на 32-битном Python)
3. Соберёт `MInstAll_x64.exe` (на 64-битном Python) — **параллельно**
4. Создаст SHA-256 для каждого файла
5. Опубликует GitHub Release с инструкцией для пользователей

Полный пайплайн занимает ~5 минут. См. [.github/workflows/release.yml](.github/workflows/release.yml).

### Скачивание готовых сборок без локальной компиляции

[**MInstAll Releases**](https://github.com/assassins377/minstall_project/releases/latest) — всегда содержит последние x86 и x64 сборки.

---

## Что дальше

- [README.md](README.md) — что умеет MInstAll, как использовать
- [tests/](tests/) — 64 unit-теста, изучи перед добавлением фич
- [.github/workflows/](.github/workflows/) — CI/CD конфигурация

## Лицензия

GNU GPL v3 — см. [LICENSE](LICENSE).
