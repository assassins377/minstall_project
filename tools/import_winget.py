#!/usr/bin/env python3
"""
Импорт списка программ из winget export → programs.json.

Использование:
  # 1. На исходной машине:
  winget export -o packages.json

  # 2. На целевой:
  python tools/import_winget.py packages.json
  python tools/import_winget.py packages.json --write       # записать в programs.json
  python tools/import_winget.py packages.json --merge       # добавить только новые
  python tools/import_winget.py packages.json --category "ИЗ WINGET"

В programs.json создаются записи вида:
  {
    "name": "Google.Chrome",
    "cmd": "winget install --id Google.Chrome --silent --accept-source-agreements --accept-package-agreements",
    "winget_id": "Google.Chrome",
    "desc": "Установка через winget",
    "detect": {}
  }

ВАЖНО: команды используют winget — это значит на целевой машине должен
быть установлен Windows Package Manager (входит в Windows 10 1809+ / 11).
Файл tools/ запускает sysmin/devs которые понимают что делают.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


DEFAULT_CATEGORY = "ИЗ WINGET"


def parse_winget_export(path: str) -> list[str]:
    """
    Парсит JSON-экспорт winget и возвращает список PackageIdentifier'ов.

    Формат экспорта:
      {
        "Sources": [
          {
            "Packages": [
              {"PackageIdentifier": "Google.Chrome", ...},
              ...
            ]
          }
        ]
      }
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("Корневой элемент должен быть объектом")

    package_ids: list[str] = []
    sources = data.get("Sources", [])
    if not isinstance(sources, list):
        raise ValueError("Поле 'Sources' должно быть массивом")

    for source in sources:
        packages = source.get("Packages", []) if isinstance(source, dict) else []
        for pkg in packages:
            if isinstance(pkg, dict):
                pkg_id = pkg.get("PackageIdentifier")
                if pkg_id and isinstance(pkg_id, str):
                    package_ids.append(pkg_id)

    return package_ids


def make_program_entry(package_id: str) -> dict:
    """Создаёт entry для programs.json из winget PackageIdentifier."""
    # winget cmd с silent-флагами и автоматическим согласием на лицензии
    cmd = (
        f"winget install --id {package_id} --silent "
        f"--accept-source-agreements --accept-package-agreements"
    )
    return {
        "name": package_id,
        "cmd": cmd,
        "winget_id": package_id,
        "desc": f"Установка через winget: {package_id}",
        "icon": "icons/system.png",
        "detect": {},
        "retry": 1,
    }


def merge_into_programs(
    existing: dict[str, list[dict]],
    new_entries: list[dict],
    category: str,
) -> tuple[dict[str, list[dict]], int]:
    """Добавляет new_entries в existing[category], пропуская дубликаты по name."""
    merged = {cat: list(progs) for cat, progs in existing.items()}

    existing_names: set[str] = set()
    for progs in merged.values():
        for p in progs:
            existing_names.add(p.get("name", "").lower())

    cat_list = merged.setdefault(category, [])
    count_new = 0
    for entry in new_entries:
        if entry["name"].lower() in existing_names:
            continue
        cat_list.append(entry)
        existing_names.add(entry["name"].lower())
        count_new += 1

    return merged, count_new


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Импорт winget export в programs.json",
        epilog="Пример: winget export -o pkgs.json && python tools/import_winget.py pkgs.json --merge",
    )
    parser.add_argument("winget_json", help="JSON-файл от winget export")
    parser.add_argument("--write", action="store_true",
                        help="Записать в programs.json (иначе — stdout)")
    parser.add_argument("--merge", action="store_true",
                        help="Добавить к существующему programs.json (без дубликатов)")
    parser.add_argument("--category", default=DEFAULT_CATEGORY,
                        help=f"Категория для новых записей (по умолчанию: {DEFAULT_CATEGORY!r})")
    args = parser.parse_args()

    if not os.path.isfile(args.winget_json):
        print(f"Файл не найден: {args.winget_json}", file=sys.stderr)
        sys.exit(1)

    try:
        package_ids = parse_winget_export(args.winget_json)
    except Exception as e:
        print(f"Ошибка парсинга {args.winget_json}: {e}", file=sys.stderr)
        sys.exit(1)

    if not package_ids:
        print("Не найдено ни одного PackageIdentifier в файле.", file=sys.stderr)
        sys.exit(1)

    print(f"Найдено {len(package_ids)} пакетов:", file=sys.stderr)
    for pid in package_ids[:10]:
        print(f"  • {pid}", file=sys.stderr)
    if len(package_ids) > 10:
        print(f"  ... и ещё {len(package_ids) - 10}", file=sys.stderr)

    new_entries = [make_program_entry(pid) for pid in package_ids]

    if args.merge and os.path.isfile(config.CONFIG_FILE):
        with open(config.CONFIG_FILE, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
        existing = existing_data.get("categories", {})
        merged, count_new = merge_into_programs(existing, new_entries, args.category)
        print(f"\nДобавлено новых: {count_new} (всего в импорте: {len(new_entries)})",
              file=sys.stderr)
        output = {"_version": config.CONFIG_VERSION, "categories": merged}
    else:
        categories = {args.category: new_entries}
        output = {"_version": config.CONFIG_VERSION, "categories": categories}

    if args.write:
        with open(config.CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=4)
        print(f"\nЗаписано в {config.CONFIG_FILE}", file=sys.stderr)
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
