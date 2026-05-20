"""Профили программ — готовые наборы для разных сценариев.

Файлы лежат в папке profiles/ — по одному JSON на профиль.
Формат:
{
  "name": "Developer",
  "description": "Набор для разработчика",
  "programs": ["Google Chrome", "Microsoft .NET Framework 4.8"]
}
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import config


PROFILES_DIR = os.path.join(config.SCRIPT_DIR, "profiles")


def load_profile(path: str) -> dict[str, Any] | None:
    """Читает один JSON-файл профиля. Возвращает None при ошибке."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.warning(f"Не удалось прочитать {path}: {e}")
        return None

    if not isinstance(data, dict):
        logging.warning(f"{path}: ожидается объект")
        return None
    if "name" not in data or "programs" not in data:
        logging.warning(f"{path}: отсутствует 'name' или 'programs'")
        return None
    if not isinstance(data["programs"], list):
        logging.warning(f"{path}: 'programs' должен быть списком")
        return None

    return data


def list_profiles() -> list[dict[str, Any]]:
    """Возвращает список всех профилей из papka profiles/."""
    if not os.path.isdir(PROFILES_DIR):
        return []

    profiles: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(PROFILES_DIR)):
        if not fname.lower().endswith(".json"):
            continue
        path = os.path.join(PROFILES_DIR, fname)
        profile = load_profile(path)
        if profile is not None:
            profile["_filename"] = fname
            profiles.append(profile)
    return profiles


def find_profile_by_name(name: str) -> dict[str, Any] | None:
    """Находит профиль по полю name или filename (без .json)."""
    name_lower = name.lower()
    for profile in list_profiles():
        if profile["name"].lower() == name_lower:
            return profile
        if profile["_filename"][:-5].lower() == name_lower:  # без .json
            return profile
    return None


def resolve_profile_programs(
    profile: dict[str, Any],
    all_programs: dict[str, list[dict]],
) -> tuple[list[dict], list[str]]:
    """
    Превращает список имён из профиля в список dict'ов программ из all_programs.

    Возвращает (найденные программы, имена не найденных).
    """
    by_name: dict[str, dict] = {}
    for progs in all_programs.values():
        for p in progs:
            by_name[p["name"].lower()] = p

    found: list[dict] = []
    missing: list[str] = []
    for name in profile.get("programs", []):
        prog = by_name.get(name.lower())
        if prog is not None:
            found.append(prog)
        else:
            missing.append(name)
    return found, missing
