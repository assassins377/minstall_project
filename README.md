# MInstAll

Универсальный мастер тихой установки программ и системных твиков для Windows.

Автоматизирует развёртывание рабочего окружения с поддержкой `.exe`, `.msi`, `.bat`, `.reg` и PowerShell-скриптов.

---

## Скачать

Готовые файлы опубликованных версий находятся в [GitHub Releases](https://github.com/assassins377/minstall_project/releases). Если релиза ещё нет, используй сборку из исходников ниже или артефакты успешного [Build & Test](https://github.com/assassins377/minstall_project/actions/workflows/build.yml).

Release-пайплайн формирует следующие варианты:

| Система | Один EXE | Portable ZIP |
|---|---|---|
| 64-битная Windows — основной выбор для современных ПК | `MInstAll_x64.exe` | `MInstAll_x64_portable.zip` |
| 32-битная Windows | `MInstAll_x86.exe` | `MInstAll_x86_portable.zip` |

EXE запускается непосредственно. ZIP нужно распаковать целиком и запустить `MInstAll_x64_dir.exe` или `MInstAll_x86_dir.exe`, сохраняя остальные файлы рядом. Portable-вариант не распаковывает зависимости при каждом запуске.

Основная целевая платформа — Windows 10/11. Разрядность файла не гарантирует совместимость со старыми Windows: текущая сборка использует Python 3.10, а поддержка Windows 7 не подтверждена. Подробнее — [BUILD.md](BUILD.md#windows-7). Linux используется для разработки из исходников; готовый Linux-бинарник не публикуется.

### Проверка целостности

Скачай файл `.sha256` для выбранного EXE или ZIP из того же релиза. В PowerShell посчитай хеш именно скачанного файла и сравни его с суммой в соответствующем `.sha256`:

```powershell
Get-FileHash .\MInstAll_x64.exe -Algorithm SHA256
Get-Content .\MInstAll_x64.exe.sha256
```

Для архива:

```powershell
Get-FileHash .\MInstAll_x64_portable.zip -Algorithm SHA256
Get-Content .\MInstAll_x64_portable.zip.sha256
```

Для x86 замени `x64` на `x86` в обоих именах. Проверка ZIP выполняется до распаковки. Регистр букв хеша значения не имеет.

Если проверяешь скачанный Windows-файл на Linux, положи его и соответствующий `.sha256` в одну папку:

```bash
sha256sum --check MInstAll_x64.exe.sha256
# Или для ZIP:
sha256sum --check MInstAll_x64_portable.zip.sha256
```

Это проверка Windows-артефакта, а не инструкция запуска на Linux.

## ⚠ Предупреждение SmartScreen

Windows может показать предупреждение о неопознанном приложении. Сборки проекта пока не подписаны сертификатом подписи кода.

SmartScreen учитывает репутацию файла и издателя, историю загрузок и другие сигналы. Microsoft не публикует фиксированного количества загрузок или запусков, после которого предупреждение гарантированно исчезнет. Новая неподписанная сборка набирает репутацию заново. См. [документацию Microsoft](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).

Продолжай запуск только если доверяешь источнику и проверил целостность файла. В диалоге SmartScreen это **«Подробнее» → «Выполнить в любом случае»**, если политика Windows разрешает такой выбор. Совпадение SHA-256 подтверждает соответствие скачанного файла опубликованному, но само по себе не доказывает безопасность программы.

Исходники и конфигурация GitHub Actions доступны в этом репозитории для проверки.

---

## Возможности

- **Пакетная тихая установка** — выбираешь набор программ, нажимаешь "Установить", идёшь пить чай
- **Детекция установленного** — через реестр Windows, не ставит повторно
- **Системные компоненты** — .NET Framework 4.8, DirectX, Visual C++ Redistributables
- **Зависимости** — топологическая сортировка (например, VC++ ставится до зависящих приложений)
- **Retry с backoff** — повтор при retryable ошибках (1618, 1603); коды 3010 и 1641 означают успешную установку с перезагрузкой
- **Откат при ошибке** — если установка не удалась, запускается uninstall-команда
- **Автообновление** — проверяет новую версию через GitHub Releases, скачивает и проверяет SHA-256 перед заменой EXE; без корректной суммы обновление блокируется
- **Сохранение размера окна** — позиция и размер запоминаются между запусками

---

## Запуск из исходников

```powershell
git clone https://github.com/assassins377/minstall_project.git
cd minstall_project
pip install -r requirements.txt
python main.py
```

CLI флаги:
```powershell
python main.py --version
```

## Тесты

```powershell
python -m pytest tests/ -v
```

## Сборка .exe локально

```powershell
pip install pyinstaller
pyinstaller --clean --noconsole --onefile --uac-admin --name MInstAll_x64 --icon=icons/app.ico --add-data "i18n;i18n" --add-data "profiles;profiles" --add-data "icons;icons" main.py
```

Для x64 используй 64-битный Python. Для x86 — 32-битный Python и `--name MInstAll_x86`. Подробнее — [BUILD.md](BUILD.md).

## Структура проекта

```
├── main.py              # Точка входа + CLI
├── config.py            # Константы, пути, версия
├── core.py              # Логика: реестр, установка, retry, зависимости
├── gui.py               # wxPython интерфейс
├── updater.py           # Автообновление через GitHub
├── state.py             # Сохранение настроек окна
├── programs.json        # Внешний каталог программ (создаётся пользователем)
├── version.json         # Старые метаданные; автообновление использует GitHub Releases
├── tests/               # Unit-тесты
├── tools/               # Утилиты (scan_software.py — автогенерация programs.json)
├── icons/               # Иконки
├── software/            # Инсталляторы (локально, не в репозитории)
└── .github/workflows/   # CI: тесты + сборка + Release
```

## Лицензия

GNU GPL v3 — см. [LICENSE](LICENSE).
