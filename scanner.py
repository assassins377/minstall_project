"""Сканер папки software/ для автоматического обнаружения инсталляторов.

Логика:
  software/foo.exe         → программа в категорию по эвристике (CATEGORY_HINTS)
  software/Office/foo.msi  → программа в категорию "Office" (имя подпапки)

При нахождении новых файлов которых нет в programs.json:
  • auto_merge_into_db()  — добавляет в dict в памяти (не трогает файл)
  • save_merged_to_disk() — сохраняет результат обратно в programs.json
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import config


# Поддерживаемые расширения инсталляторов (без точки)
SUPPORTED_EXTENSIONS: set[str] = set(config.ALLOWED_CMD_EXTENSIONS)


# Эвристика "имя файла → категория" — для файлов лежащих прямо в software/
CATEGORY_HINTS: dict[str, str] = {
    "net":       "СИСТЕМНЫЕ КОМПОНЕНТЫ",
    "dotnet":    "СИСТЕМНЫЕ КОМПОНЕНТЫ",
    "directx":   "СИСТЕМНЫЕ КОМПОНЕНТЫ",
    "vcredist":  "СИСТЕМНЫЕ КОМПОНЕНТЫ",
    "chrome":    "ИНТЕРНЕТ И БРАУЗЕРЫ",
    "firefox":   "ИНТЕРНЕТ И БРАУЗЕРЫ",
    "opera":     "ИНТЕРНЕТ И БРАУЗЕРЫ",
    "telegram":  "ИНТЕРНЕТ И БРАУЗЕРЫ",
    "discord":   "ИНТЕРНЕТ И БРАУЗЕРЫ",
    "libre":     "ОФИСНОЕ ПО",
    "office":    "ОФИСНОЕ ПО",
    "7zip":      "УТИЛИТЫ",
    "winrar":    "УТИЛИТЫ",
    "notepad":   "УТИЛИТЫ",
}

DEFAULT_CATEGORY = "ПРОЧЕЕ"

# Разделитель путей подкатегорий: "INTERFACE / THEMES"
# GUI парсит эту строку обратно в иерархию tree-узлов.
CATEGORY_SEPARATOR = " / "

# Стандартные silent-флаги по расширению
SILENT_FLAGS: dict[str, str] = {
    ".exe": "/S",
    ".msi": "",
    ".bat": "",
    ".cmd": "",
    ".ps1": "",
    ".reg": "",
}


# ====================================================================
# Эвристики имени
# ====================================================================

def guess_category(filename: str) -> str:
    """Угадывает категорию по имени файла (только для файлов в корне software/)."""
    lower = filename.lower()
    for hint, category in CATEGORY_HINTS.items():
        if hint in lower:
            return category
    return DEFAULT_CATEGORY


def filename_to_name(filename: str) -> str:
    """Превращает имя файла в человекочитаемое название."""
    name = os.path.splitext(filename)[0]
    # Убираем технические суффиксы: _setup, -install, x64, версии
    name = re.sub(
        r"[-_.]?(setup|install(er)?|x(86|64)|v?\d+(\.\d+)+)",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Точки/дефисы/подчёркивания → пробелы (так имена выглядят естественнее
    # и матчатся с записями реестра типа "FormatFactory" без точки)
    name = name.replace("_", " ").replace("-", " ").replace(".", " ")
    # Сжимаем множественные пробелы
    name = re.sub(r"\s+", " ", name).strip()
    return name.title() if name else filename


def _make_entry(category: str, filename: str, rel_path: str, ext: str) -> dict[str, Any]:
    """Создаёт program entry для programs.json."""
    name = filename_to_name(filename)
    silent = SILENT_FLAGS.get(ext, "")
    cmd = f"{rel_path} {silent}".strip() if silent else rel_path
    return {
        "name": name,
        "cmd": cmd.replace("/", "\\"),
        "desc": f"Автоматически обнаружено: {filename}",
        "icon": "icons/system.png",
        "detect": {},
    }


# ====================================================================
# Сканирование
# ====================================================================

def scan_directory(
    software_dir: str | None = None,
    max_depth: int = 8,
) -> dict[str, list[dict]]:
    """
    Сканирует software/ рекурсивно и возвращает {категория: [программы]}.

    Структура:
      software/foo.exe                       → категория по эвристике (или ПРОЧЕЕ)
      software/Office/foo.msi                → категория "OFFICE"
      software/Interface/Themes/foo.exe      → категория "INTERFACE / THEMES"
      software/A/B/C/foo.exe                 → категория "A / B / C"

    max_depth — защита от бесконечной рекурсии (например, если кто-то создал
    симлинк на родителя). 8 уровней — с большим запасом для реальных случаев.
    """
    software_dir = software_dir or os.path.join(config.SCRIPT_DIR, "software")
    categories: dict[str, list[dict]] = {}

    if not os.path.isdir(software_dir):
        return categories

    # Файлы в корне software/ — категория по эвристике
    # Файлы в подпапках — категория = путь подпапок через CATEGORY_SEPARATOR
    _scan_recursive(software_dir, software_dir, [], categories, max_depth)
    return categories


def _scan_recursive(
    root: str,
    current: str,
    path_parts: list[str],
    categories: dict[str, list[dict]],
    max_depth: int,
) -> None:
    """
    Рекурсивный обход.

    root       — корень software/ (не меняется)
    current    — текущая обходимая папка
    path_parts — список имён родительских папок относительно root
                  (пустой когда мы в самом root)
    """
    if len(path_parts) > max_depth:
        logging.warning(
            f"Достигнут max_depth={max_depth} в {current}, дальше не идём"
        )
        return

    try:
        entries = sorted(os.listdir(current))
    except OSError as e:
        logging.warning(f"Не удалось прочитать {current}: {e}")
        return

    for entry in entries:
        full_path = os.path.join(current, entry)

        if os.path.isdir(full_path):
            # Рекурсивно идём вглубь, добавляя имя папки в путь
            _scan_recursive(root, full_path, path_parts + [entry],
                            categories, max_depth)
            continue

        if not os.path.isfile(full_path):
            continue

        ext = os.path.splitext(entry)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue

        # Категория: либо по эвристике (если в корне), либо по пути папок
        if not path_parts:
            category = guess_category(entry)
        else:
            category = CATEGORY_SEPARATOR.join(part.upper() for part in path_parts)

        # rel_path — относительно SCRIPT_DIR, форматом для cmd
        rel_from_root = os.path.relpath(full_path, root).replace(os.sep, "/")
        rel_path = f"software/{rel_from_root}"

        categories.setdefault(category, []).append(
            _make_entry(category, entry, rel_path, ext)
        )


# ====================================================================
# Слияние с существующим каталогом
# ====================================================================

def auto_merge_into_db(
    existing: dict[str, list[dict]],
    scanned: dict[str, list[dict]],
) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    Добавляет новые программы из scanned в existing (не трогает имеющиеся).

    Дубликат определяется по полю "cmd" (case-insensitive).
    Это правильно — если пользователь руками сделал запись с тем же cmd,
    мы не подменяем его кастомизацию.

    Возвращает (merged_db, new_entries) — new_entries для отчёта в GUI.
    """
    merged: dict[str, list[dict]] = {cat: list(progs) for cat, progs in existing.items()}

    existing_cmds: set[str] = set()
    for progs in merged.values():
        for p in progs:
            existing_cmds.add(p.get("cmd", "").lower())

    new_entries: list[dict] = []

    for category, progs in scanned.items():
        target = merged.setdefault(category, [])
        for prog in progs:
            cmd_key = prog["cmd"].lower()
            if cmd_key in existing_cmds:
                continue
            target.append(prog)
            existing_cmds.add(cmd_key)
            new_entries.append({**prog, "_category": category})

    return merged, new_entries


def save_merged_to_disk(programs_db: dict[str, list[dict]]) -> bool:
    """Записывает обновлённый каталог обратно в programs.json. True при успехе."""
    try:
        payload = {"_version": config.CONFIG_VERSION, "categories": programs_db}
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        logging.error(f"Не удалось сохранить {config.CONFIG_FILE}: {e}")
        return False


# ====================================================================
# Удобная обёртка для GUI
# ====================================================================

def directory_snapshot(software_dir: str | None = None) -> frozenset[tuple[str, int, float]]:
    """
    Быстрый снимок содержимого software/ — (relpath, size, mtime) для каждого файла.

    Используется watcher'ом для детекции изменений без полного скана.
    Возвращает frozenset чтобы можно было быстро сравнивать через ==.
    """
    software_dir = software_dir or os.path.join(config.SCRIPT_DIR, "software")
    if not os.path.isdir(software_dir):
        return frozenset()

    items: list[tuple[str, int, float]] = []
    for root, _dirs, files in os.walk(software_dir):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            full = os.path.join(root, fname)
            try:
                st = os.stat(full)
                rel = os.path.relpath(full, software_dir)
                items.append((rel.replace("\\", "/"), st.st_size, st.st_mtime))
            except OSError:
                continue
    return frozenset(items)


def scan_and_merge(
    programs_db: dict[str, list[dict]],
    software_dir: str | None = None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """
    Сканирует software/ и сливает с переданным programs_db.
    Возвращает (новый_db, список_новых_программ).
    """
    scanned = scan_directory(software_dir)
    return auto_merge_into_db(programs_db, scanned)


# ====================================================================
# Полная регенерация каталога из software/
# ====================================================================

def _cmd_key(cmd_str: str) -> str:
    """
    Извлекает путь к файлу из cmd-строки для сопоставления с другой записью.
    "software\\chrome.exe --silent" → "software/chrome.exe"
    """
    if not cmd_str:
        return ""
    first = cmd_str.strip().split()[0]
    return first.replace("\\", "/").lower()


def build_catalog_from_scan(
    software_dir: str | None = None,
    existing_db: dict[str, list[dict]] | None = None,
) -> dict[str, list[dict]]:
    """
    Полная регенерация каталога из содержимого software/.

    Файлов нет в папке → нет в каталоге. Это режим "доверяем папке".

    Если есть existing_db — для каждого найденного файла ищем запись с тем же cmd-путём
    и переносим оттуда богатые метаданные:
      • desc, icon
      • detect, depends_on, retry, timeout
      • pre_cmd, post_cmd, uninstall_cmd
      • min_version
    Это позволяет хранить ручные кастомизации в programs.json, при этом
    список программ автоматически отражает реальное содержимое папки.

    Если existing_db нет — все записи создаются как auto-generated (минимум полей).
    """
    scanned = scan_directory(software_dir)
    if not existing_db:
        return scanned

    # Индекс существующих записей по пути файла внутри cmd
    by_cmd: dict[str, dict] = {}
    for progs in existing_db.values():
        for prog in progs:
            key = _cmd_key(prog.get("cmd", ""))
            if key:
                by_cmd[key] = prog

    # Поля которые имеет смысл сохранять из существующей записи
    PRESERVE_FIELDS = (
        "name", "desc", "icon", "detect",
        "depends_on", "retry", "timeout",
        "pre_cmd", "post_cmd", "uninstall_cmd",
    )

    result: dict[str, list[dict]] = {}
    for category, progs in scanned.items():
        new_progs: list[dict] = []
        for prog in progs:
            key = _cmd_key(prog.get("cmd", ""))
            existing = by_cmd.get(key)
            if existing is not None:
                # Берём cmd из скана (на случай если флаги поменялись),
                # остальное из existing
                merged_entry = {**prog}
                for field in PRESERVE_FIELDS:
                    if field in existing:
                        merged_entry[field] = existing[field]
                new_progs.append(merged_entry)
            else:
                new_progs.append(prog)
        result[category] = new_progs

    return result
