"""Статистика установок: размеры файлов и история таймингов.

История хранится в state.json:
  prefs.install_times = {"Chrome": [45.2, 38.1, 52.0], ...}
- список длительностей в секундах
- максимум 10 значений на программу (ротация)
- для оценки используется медиана (устойчивее к выбросам)
"""
from __future__ import annotations

import logging
import os
import shlex
import statistics
from typing import Any

import config
import core


MAX_HISTORY = 10  # сколько последних замеров хранить на программу


# ====================================================================
# Размер файлов
# ====================================================================

def get_program_file_size(program: dict) -> int:
    """Возвращает размер файла инсталлятора в байтах. 0 если нет/недоступен."""
    cmd_str = program.get("cmd", "")
    if not cmd_str:
        return 0
    try:
        parts = shlex.split(cmd_str, posix=False)
        if not parts:
            return 0
        # winget/choco — нет локального файла
        first = parts[0].lower()
        if os.path.splitext(first)[1] == "" and os.path.basename(first) in config.ALLOWED_BARE_COMMANDS:
            return 0
        path = core.resolve_path(parts[0])
        return os.path.getsize(path) if os.path.exists(path) else 0
    except (ValueError, OSError):
        return 0


def format_size(bytes_total: int, t: Any = None) -> str:
    """Форматирует байты в КБ/МБ/ГБ. t — функция перевода (i18n.t) или None."""
    if t is None:
        # Fallback без i18n
        if bytes_total >= 1024 ** 3:
            return f"{bytes_total / 1024 ** 3:.1f} ГБ"
        if bytes_total >= 1024 ** 2:
            return f"{bytes_total / 1024 ** 2:.0f} МБ"
        return f"{bytes_total // 1024} КБ"
    if bytes_total >= 1024 ** 3:
        return t("size.gb", n=f"{bytes_total / 1024 ** 3:.1f}")
    if bytes_total >= 1024 ** 2:
        return t("size.mb", n=f"{bytes_total / 1024 ** 2:.0f}")
    return t("size.kb", n=bytes_total // 1024)


# ====================================================================
# История таймингов
# ====================================================================

def get_install_times(state_dict: dict) -> dict[str, list[float]]:
    """Достаёт историю таймингов из state."""
    prefs = state_dict.get("prefs", {})
    times = prefs.get("install_times", {})
    if not isinstance(times, dict):
        return {}
    return times


def record_install_time(state_dict: dict, program_name: str, duration_sec: float) -> None:
    """Добавляет тайминг в историю программы (с ротацией до MAX_HISTORY)."""
    if duration_sec <= 0:
        return
    prefs = state_dict.setdefault("prefs", {})
    times = prefs.setdefault("install_times", {})
    history = times.get(program_name, [])
    history.append(round(duration_sec, 1))
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    times[program_name] = history


def estimate_time(program: dict, times_db: dict[str, list[float]]) -> float | None:
    """
    Оценка времени установки в секундах.

    Использует медиану из истории.
    Если истории нет → None (для UI это означает "неизвестно").
    """
    name = program.get("name", "")
    history = times_db.get(name, [])
    if not history:
        return None
    return statistics.median(history)


def format_duration(seconds: float, t: Any = None) -> str:
    """Форматирует секунды в человекочитаемое время."""
    if t is None:
        if seconds < 60:
            return f"{int(seconds)}с"
        m = int(seconds // 60)
        s = int(seconds % 60)
        if s == 0:
            return f"{m}мин"
        return f"{m}мин {s}с"
    if seconds < 60:
        return t("time.seconds", n=int(seconds))
    m = int(seconds // 60)
    s = int(seconds % 60)
    if s == 0:
        return t("time.minutes", n=m)
    return t("time.minutes_seconds", m=m, s=s)


# ====================================================================
# Сводка по выбранным программам
# ====================================================================

def selection_summary(
    selected_programs: list[dict],
    state_dict: dict,
    t: Any = None,
) -> tuple[int, int, float | None]:
    """
    Считает сводку: (количество, суммарный_размер_байт, суммарное_время_сек).

    Время = None если ни для одной программы нет истории.
    """
    if not selected_programs:
        return (0, 0, None)

    times_db = get_install_times(state_dict)
    total_size = 0
    total_time = 0.0
    times_known = 0

    for prog in selected_programs:
        total_size += get_program_file_size(prog)
        t_est = estimate_time(prog, times_db)
        if t_est is not None:
            total_time += t_est
            times_known += 1

    final_time: float | None = total_time if times_known > 0 else None
    return (len(selected_programs), total_size, final_time)
