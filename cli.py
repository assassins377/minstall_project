"""CLI-режим MInstAll — установка без GUI для скриптов и автоматизации.

Использование (примеры):
  MInstAll --install "Google Chrome,Telegram Desktop"
  MInstAll --install-profile developer
  MInstAll --list
  MInstAll --list-installed
  MInstAll --install all --missing-only --parallel
  MInstAll --install Chrome --dry-run

Коды выхода:
  0 — успех
  1 — частичный успех (некоторые программы не установились)
  2 — отменено пользователем (Ctrl+C)
  3 — ошибка аргументов / конфигурации
"""
from __future__ import annotations

import logging
import sys
import threading
import time

import config
import core
import profiles


# Severity → ANSI цвета для красивого вывода в терминал
ANSI_COLORS: dict[str, str] = {
    "info":     "",
    "progress": "\033[36m",   # cyan
    "warn":     "\033[33m",   # yellow
    "error":    "\033[31m",   # red
    "success":  "\033[32m",   # green
}
ANSI_RESET = "\033[0m"


def _supports_color() -> bool:
    """Поддерживает ли терминал ANSI-цвета."""
    if not sys.stdout.isatty():
        return False
    # Windows 10+ поддерживает ANSI через VT-режим, активируем
    import os
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


def _colorize(text: str, severity: str, use_color: bool) -> str:
    if not use_color or severity not in ANSI_COLORS:
        return text
    return f"{ANSI_COLORS[severity]}{text}{ANSI_RESET}"


# ====================================================================
# Команды CLI
# ====================================================================

def cmd_list_programs(programs_db: dict[str, list[dict]], installed_entries: list[tuple[str, str]],
                     filter_status: str | None = None) -> int:
    """Команда --list — вывести все программы с их статусом."""
    if not programs_db:
        print("Каталог пуст (programs.json не найден или пустой).", file=sys.stderr)
        return 3

    total = 0
    for category, progs in programs_db.items():
        cat_printed = False
        for p in progs:
            status, ver = core.check_status(p, installed_entries)
            if filter_status and status != filter_status:
                continue
            if not cat_printed:
                print(f"\n[{category}]")
                cat_printed = True
            marker = {"ok": "✓", "outdated": "↑", "missing": " ", "runnable": "·"}.get(status, "?")
            ver_str = f" ({ver})" if ver else ""
            print(f"  {marker} {p['name']}{ver_str}  — {status}")
            total += 1

    print(f"\nВсего: {total}", file=sys.stderr)
    return 0


def cmd_list_installed(installed_entries: list[tuple[str, str]]) -> int:
    """Команда --list-installed — все программы установленные в системе."""
    if not installed_entries:
        print("Не найдено установленных программ (либо мы не на Windows).", file=sys.stderr)
        return 0
    for name, version in sorted(installed_entries):
        print(f"{name}\t{version}" if version else name)
    print(f"\nВсего: {len(installed_entries)}", file=sys.stderr)
    return 0


def cmd_list_profiles() -> int:
    """Команда --list-profiles — все доступные профили."""
    loaded = profiles.list_profiles()
    if not loaded:
        print("Нет профилей в папке profiles/", file=sys.stderr)
        return 0
    for p in loaded:
        print(f"\n{p['name']} ({p['_filename']})")
        if p.get("description"):
            print(f"  {p['description']}")
        print(f"  Программ: {len(p.get('programs', []))}")
    return 0


# ====================================================================
# Установка из CLI
# ====================================================================

def resolve_targets(
    install_arg: str,
    programs_db: dict[str, list[dict]],
    missing_only: bool,
    installed_entries: list[tuple[str, str]],
) -> tuple[list[dict], list[str]]:
    """
    Разбирает --install аргумент в список tasks.

    install_arg может быть:
      "all"          — все программы из каталога
      "Chrome,Tg"    — список по именам через запятую
      "*Chrome*"     — wildcard (TODO: пока не реализовано)

    missing_only=True — фильтрует, оставляя только missing/outdated.
    """
    all_by_name: dict[str, dict] = {}
    for progs in programs_db.values():
        for p in progs:
            all_by_name[p["name"].lower()] = p

    if install_arg.strip().lower() == "all":
        candidates = list(all_by_name.values())
    else:
        names = [n.strip() for n in install_arg.split(",") if n.strip()]
        candidates = []
        not_found: list[str] = []
        for n in names:
            prog = all_by_name.get(n.lower())
            if prog:
                candidates.append(prog)
            else:
                not_found.append(n)
        if not_found:
            return ([], not_found)

    if missing_only:
        filtered = []
        for p in candidates:
            status, _ = core.check_status(p, installed_entries)
            if status in ("missing", "outdated"):
                filtered.append(p)
        candidates = filtered

    return (candidates, [])


def resolve_profile_targets(
    profile_name: str,
    programs_db: dict[str, list[dict]],
) -> tuple[list[dict], list[str]]:
    """Разрешает --install-profile в список tasks."""
    profile = profiles.find_profile_by_name(profile_name)
    if profile is None:
        return ([], [f"Профиль '{profile_name}' не найден"])

    found, missing = profiles.resolve_profile_programs(profile, programs_db)
    return (found, missing)


def install_cli(
    tasks: list[dict],
    programs_db: dict[str, list[dict]],
    parallel: bool,
    max_jobs: int,
    silent: bool,
    dry_run: bool,
    use_color: bool,
) -> int:
    """Запускает установку в CLI-режиме. Возвращает код выхода."""
    if not tasks:
        print("Нечего устанавливать (после фильтрации).", file=sys.stderr)
        return 0

    # Раскрываем зависимости + отсортируем
    if parallel:
        levels = core.topological_levels(tasks, programs_db)
        all_tasks = [t for lvl in levels for t in lvl]
    else:
        all_tasks = core.resolve_dependencies(tasks, programs_db)
        levels = [all_tasks]

    # --- Dry run: только показываем план ---
    if dry_run:
        print(f"\nDRY RUN — план установки ({len(all_tasks)} задач):\n")
        for i, level in enumerate(levels):
            if parallel:
                print(f"  Уровень {i + 1}:")
            for t in level:
                print(f"    • {t['name']}")
                print(f"      cmd: {t['cmd']}")
                if t.get("pre_cmd"):
                    print(f"      pre: {t['pre_cmd']}")
                if t.get("post_cmd"):
                    print(f"      post: {t['post_cmd']}")
                if t.get("depends_on"):
                    print(f"      deps: {', '.join(t['depends_on'])}")
        print(f"\nРежим: {'параллельный' if parallel else 'последовательный'}")
        print("Реальной установки не было.\n")
        return 0

    # --- Реальная установка ---
    print(f"\nЗапуск установки: {len(all_tasks)} задач, режим "
          f"{'параллельный' if parallel else 'последовательный'}\n")

    finished_event = threading.Event()
    final_result: dict = {}

    def dispatch(msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "progress" and not silent:
            severity = msg.get("severity", "info")
            text = _colorize(msg["text"], severity, use_color)
            print(f"  {text}")
        elif msg_type == "value" and not silent:
            pct = msg.get("percent", 0)
            # Простой ASCII прогресс-бар
            bar_width = 30
            filled = int(bar_width * pct / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            sys.stdout.write(f"\r  [{bar}] {pct}%   ")
            sys.stdout.flush()
            if pct >= 100:
                sys.stdout.write("\n")
        elif msg_type == "finished":
            final_result.update(msg)
            finished_event.set()

    worker = core.InstallWorker(
        all_tasks,
        dispatch,
        parallel=parallel,
        max_jobs=max_jobs,
        all_programs=programs_db,
    )

    start_time = time.time()
    try:
        worker.start()
        # Ждём пока worker не завершит работу, реагируем на Ctrl+C
        while not finished_event.wait(0.5):
            if not worker.is_alive():
                break
    except KeyboardInterrupt:
        print("\n\n⚠ Прерывание по Ctrl+C, отменяем...", file=sys.stderr)
        worker.stop()
        worker.join(timeout=10.0)
        return 2

    duration = time.time() - start_time

    # --- Итоги ---
    success = final_result.get("success", 0)
    fails = final_result.get("fails", 0)
    reboot = final_result.get("reboot", False)

    print("\n" + "═" * 50)
    print(f"  Успешно:    {_colorize(str(success), 'success', use_color)}")
    print(f"  Ошибок:     {_colorize(str(fails), 'error' if fails else 'info', use_color)}")
    print(f"  Время:      {duration:.1f}с")
    if reboot:
        print(_colorize("\n  ⚠ Требуется перезагрузка системы", "warn", use_color))
    print("═" * 50 + "\n")

    return 0 if fails == 0 else 1


# ====================================================================
# Точка входа
# ====================================================================

def run(args) -> int:
    """Точка входа CLI. args — namespace из argparse."""
    core.setup_logging()
    logging.info(f"CLI режим: args={vars(args)}")

    use_color = _supports_color() and not args.no_color

    programs_db = core.load_programs_from_json()
    installed_entries = core.get_installed_programs()

    # --- Информационные команды ---
    if args.list:
        return cmd_list_programs(programs_db, installed_entries,
                                  filter_status=args.filter_status)
    if args.list_installed:
        return cmd_list_installed(installed_entries)
    if args.list_profiles:
        return cmd_list_profiles()

    # --- Установка ---
    if not args.install and not args.install_profile:
        print("Ошибка: укажи --install или --install-profile", file=sys.stderr)
        return 3

    if args.install_profile:
        tasks, errors = resolve_profile_targets(args.install_profile, programs_db)
        if errors and not tasks:
            for e in errors:
                print(f"Ошибка: {e}", file=sys.stderr)
            return 3
        if errors:
            print(f"⚠ Не найдены в каталоге: {', '.join(errors)}", file=sys.stderr)
    else:
        tasks, not_found = resolve_targets(
            args.install, programs_db, args.missing_only, installed_entries
        )
        if not_found:
            for n in not_found:
                print(f"Программа не найдена: {n}", file=sys.stderr)
            return 3

    return install_cli(
        tasks=tasks,
        programs_db=programs_db,
        parallel=args.parallel,
        max_jobs=args.max_jobs or config.MAX_PARALLEL_JOBS,
        silent=args.silent,
        dry_run=args.dry_run,
        use_color=use_color,
    )
