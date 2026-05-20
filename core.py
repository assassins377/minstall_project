from __future__ import annotations

import os
import json
import ctypes
import re
import shlex
import subprocess
import logging
import threading
import time
import sys
from collections import defaultdict

import config


# ------------------------------------------------------------------
# Логирование
# ------------------------------------------------------------------
def setup_logging() -> None:
    """Инициализация логгера. Вызывается ОДИН РАЗ из main.py."""
    try:
        logging.basicConfig(
            filename=config.LOG_FILE, level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s", encoding="utf-8"
        )
    except TypeError:  # Python < 3.9: basicConfig без encoding
        _logger = logging.getLogger()
        _logger.setLevel(logging.INFO)
        _fh = logging.FileHandler(config.LOG_FILE)
        _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        _logger.addHandler(_fh)


# ------------------------------------------------------------------
# Утилиты путей / конфига
# ------------------------------------------------------------------
def resolve_path(rel_path: str) -> str:
    return os.path.join(config.SCRIPT_DIR, rel_path)


def load_programs_from_json() -> dict[str, list[dict]]:
    if not os.path.exists(config.CONFIG_FILE):
        logging.error(f"Файл конфигурации не найден: {config.CONFIG_FILE}")
        return {}

    try:
        with open(config.CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logging.error(f"Не удалось прочитать {config.CONFIG_FILE}: {e}")
        return {}

    if not isinstance(data, dict):
        logging.error(f"Некорректная структура {config.CONFIG_FILE}: ожидается объект")
        return {}

    categories = data.get("categories")
    if categories is None:
        logging.error(f"В {config.CONFIG_FILE} отсутствует ключ 'categories'")
        return {}
    if not isinstance(categories, dict):
        logging.error(f"Некорректный тип 'categories' в {config.CONFIG_FILE}: ожидается объект")
        return {}

    valid: dict[str, list[dict]] = {}
    for cat_name, programs in categories.items():
        if not isinstance(programs, list):
            logging.warning(f"Категория '{cat_name}': ожидается список программ, пропущена")
            continue
        valid_progs: list[dict] = []
        for i, prog in enumerate(programs):
            if not isinstance(prog, dict):
                logging.warning(f"Категория '{cat_name}', элемент {i}: ожидается объект, пропущен")
                continue
            if "name" not in prog or "cmd" not in prog:
                logging.warning(f"Категория '{cat_name}', элемент {i}: отсутствует 'name' или 'cmd', пропущен")
                continue
            valid_progs.append(prog)
        valid[cat_name] = valid_progs

    return valid


# ------------------------------------------------------------------
# Валидация команд
# ------------------------------------------------------------------
def validate_cmd(cmd_str: str) -> str | None:
    """Проверяет команду на shell-инъекции. Возвращает текст ошибки или None."""
    for char in config.SHELL_METACHARACTERS:
        if char in cmd_str:
            return f"Недопустимый символ '{char}' в команде"

    parts = shlex.split(cmd_str, posix=False)
    if not parts:
        return "Пустая команда"

    first = parts[0].lower()
    ext = os.path.splitext(first)[1]

    # Системные утилиты вроде winget/choco — без расширения, но разрешены
    if not ext and os.path.basename(first) in config.ALLOWED_BARE_COMMANDS:
        return None

    if ext and ext not in config.ALLOWED_CMD_EXTENSIONS:
        return f"Недопустимое расширение '{ext}'"

    if not ext:
        return f"Команда без расширения и не в списке разрешённых: '{parts[0]}'"

    return None


# ------------------------------------------------------------------
# Права администратора
# ------------------------------------------------------------------
def is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    if os.name != "nt":
        return False
    exe = sys.executable
    if getattr(sys, "frozen", False):
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        params = subprocess.list2cmdline([os.path.abspath(sys.argv[0])] + sys.argv[1:])
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, config.SCRIPT_DIR, 1) > 32
    except Exception:
        return False


# ------------------------------------------------------------------
# Реестр / установленные программы
# ------------------------------------------------------------------
UNINSTALL_KEYS = [
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
]


def _get_installed_programs_uncached() -> list[tuple[str, str]]:
    """Полное чтение реестра — без кеша. ~200-500мс."""
    if os.name != "nt":
        return []
    import winreg
    entries: list[tuple[str, str]] = []
    for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for key_path in UNINSTALL_KEYS:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        try:
                            sub_name = winreg.EnumKey(key, i)
                            with winreg.OpenKey(key, sub_name) as sub:
                                try:
                                    name, _ = winreg.QueryValueEx(sub, "DisplayName")
                                except FileNotFoundError:
                                    continue
                                try:
                                    version, _ = winreg.QueryValueEx(sub, "DisplayVersion")
                                except FileNotFoundError:
                                    version = ""
                                entries.append((name, str(version)))
                        except OSError:
                            continue
            except FileNotFoundError:
                continue
    return entries


def get_installed_programs(
    state_dict: dict | None = None,
    use_cache: bool = True,
) -> list[tuple[str, str]]:
    """
    Список установленных программ из реестра.

    Если state_dict передан и use_cache=True, использует кеш с TTL.
    Кеш сохраняется в state_dict["installed_cache"] = {"entries": [...], "ts": time.time()}.
    """
    if not use_cache or state_dict is None:
        return _get_installed_programs_uncached()

    cache = state_dict.get("installed_cache")
    if isinstance(cache, dict):
        ts = cache.get("ts", 0)
        if time.time() - ts < config.INSTALLED_CACHE_TTL_SECONDS:
            entries = cache.get("entries", [])
            # entries в JSON хранятся как списки [name, version] — приводим к tuples
            if isinstance(entries, list):
                return [tuple(e) for e in entries if isinstance(e, (list, tuple)) and len(e) == 2]

    # Кеш протух или его нет — читаем реестр и сохраняем
    fresh = _get_installed_programs_uncached()
    state_dict["installed_cache"] = {
        "entries": [list(e) for e in fresh],  # JSON-serializable
        "ts": time.time(),
    }
    return fresh


def invalidate_installed_cache(state_dict: dict | None) -> None:
    """Сбрасывает кеш реестра — вызывать после установки/удаления программ."""
    if state_dict is not None and "installed_cache" in state_dict:
        del state_dict["installed_cache"]


def parse_version(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", str(v))) if v else ()


def compare_versions(a: str, b: str) -> int:
    """Возвращает -1, 0, 1 (как cmp). Сравнивает только числовые компоненты."""
    ta, tb = parse_version(a), parse_version(b)
    n = max(len(ta), len(tb))
    ta, tb = ta + (0,) * (n - len(ta)), tb + (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


_net_release_cache: tuple[bool, int | None] = (False, None)


def get_net_framework_release(use_cache: bool = True) -> int | None:
    """Версия .NET Framework. Кешируется, т.к. лезет в реестр."""
    global _net_release_cache
    if use_cache and _net_release_cache[0]:
        return _net_release_cache[1]

    if os.name != "nt":
        _net_release_cache = (True, None)
        return None

    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as key:
            value, _ = winreg.QueryValueEx(key, "Release")
            result = int(value)
    except OSError:
        result = None

    _net_release_cache = (True, result)
    return result


def invalidate_caches() -> None:
    """Сбрасывает кеши после установки — чтобы свежие данные были подхвачены."""
    global _net_release_cache
    _net_release_cache = (False, None)


def _normalize_for_match(s: str) -> str:
    """
    Приводит строку к виду удобному для нечёткого сравнения имён программ.

    "Format.Factory"     → "formatfactory"
    "FormatFactory 5.12" → "formatfactory512"
    "7-Zip"              → "7zip"
    "Google Chrome"      → "googlechrome"

    Так что Format.Factory из programs.json матчится с записью реестра
    "FormatFactory 5.12.2.0" даже если знаки препинания отличаются.
    """
    if not s:
        return ""
    # Убираем пробелы, точки, дефисы, подчёркивания, скобки, запятые и пр.
    return re.sub(r"[\s._\-/\\:;,!?()'\"]+", "", s.lower())


def check_status(program: dict, installed_entries: list[tuple[str, str]]) -> tuple[str, str]:
    detect = program.get("detect", {}) or {}
    if detect.get("always_runnable"):
        return ("runnable", "")

    if (net_min := detect.get("net_framework_release")) is not None:
        release = get_net_framework_release()
        if release is None:
            return ("missing", "")
        return ("outdated", str(release)) if release < net_min else ("ok", str(release))

    if path := detect.get("path"):
        return ("ok", "") if os.path.exists(os.path.expandvars(path)) else ("missing", "")

    # Нормализуем обе стороны перед сравнением, чтобы пунктуация не мешала
    needle_raw = (detect.get("registry_name") or program["name"])
    needle_norm = _normalize_for_match(needle_raw)

    found_version: str | None = None
    if needle_norm:
        for n, v in installed_entries:
            if needle_norm in _normalize_for_match(n):
                found_version = v
                break

    if found_version is None:
        return ("missing", "")

    min_v = detect.get("min_version")
    if min_v and compare_versions(found_version, min_v) < 0:
        return ("outdated", found_version)
    return ("ok", found_version)


def is_installer_available(program: dict) -> bool:
    """
    Проверяет, существует ли файл инсталлятора на диске.

    Возвращает:
      True  — файл есть, системная команда (winget/choco), или указан URL для скачивания
      False — путь указан, но файла нет
    """
    # Если задан URL — программа доступна (файл скачается при установке)
    if program.get("url"):
        return True

    cmd_str = program.get("cmd", "")
    if not cmd_str:
        return False
    try:
        _args, script_path = build_cmd(cmd_str)
    except ValueError:
        return False
    if not script_path:
        return True
    return os.path.exists(script_path)


# ------------------------------------------------------------------
# Скачивание инсталлятора по URL во временную папку
# ------------------------------------------------------------------
def download_installer(
    url: str,
    dest_dir: str,
    expected_sha256: str | None = None,
    progress_cb: callable | None = None,
) -> str:
    """
    Скачивает файл по URL в dest_dir и возвращает локальный путь.

    expected_sha256 — если задан, проверяется после скачивания.
    progress_cb({"downloaded": N, "total": M}) — для прогресса.

    Бросает RuntimeError при ошибке скачивания или несовпадении SHA-256.
    """
    import hashlib
    import urllib.parse
    import urllib.request

    os.makedirs(dest_dir, exist_ok=True)

    # Имя файла из URL
    parsed = urllib.parse.urlparse(url)
    fname = os.path.basename(parsed.path) or "installer.bin"
    dest_path = os.path.join(dest_dir, fname)

    sha = hashlib.sha256()
    downloaded = 0

    req = urllib.request.Request(url, headers={"User-Agent": f"MInstAll/{config.APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=config.DOWNLOAD_TIMEOUT) as resp:
            total = 0
            try:
                cl = resp.headers.get("Content-Length")
                if cl:
                    total = int(cl)
            except Exception:
                pass

            with open(dest_path, "wb") as out:
                while True:
                    chunk = resp.read(config.DOWNLOAD_CHUNK_SIZE)
                    if not chunk:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    downloaded += len(chunk)
                    if progress_cb:
                        try:
                            progress_cb({"downloaded": downloaded, "total": total})
                        except Exception:
                            pass
    except Exception as e:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError:
            pass
        raise RuntimeError(f"Ошибка скачивания {url}: {e}") from e

    actual_sha = sha.hexdigest().lower()
    if expected_sha256 and actual_sha != expected_sha256.lower():
        try:
            os.remove(dest_path)
        except OSError:
            pass
        raise RuntimeError(
            f"SHA-256 не совпадает: ожидалось {expected_sha256}, получено {actual_sha}"
        )

    return dest_path


def build_status_cache(
    programs_db: dict[str, list[dict]],
    installed_entries: list[tuple[str, str]],
) -> dict[str, tuple[str, str]]:
    """
    Считает статусы всех программ один раз, возвращает {name -> (status, version)}.

    Используется чтобы избежать повторного вызова check_status для каждой
    программы в populate_tree и _initial_status_text — это сильно ускоряет
    запуск при росте каталога.
    """
    cache: dict[str, tuple[str, str]] = {}
    # Предкешируем .NET release один раз, чтобы не лезть в реестр на каждом вызове
    for programs in programs_db.values():
        for prog in programs:
            cache[prog["name"]] = check_status(prog, installed_entries)
    return cache


# ------------------------------------------------------------------
# Построение команды запуска
# ------------------------------------------------------------------
def build_cmd(cmd_str: str) -> tuple[list[str], str]:
    """
    Возвращает кортеж (cmd_args, script_path), где:
      - cmd_args   — что передавать в subprocess.Popen
      - script_path — реальный путь к скрипту/установщику для проверки существования.
                     Для системных команд (winget) — пустая строка: проверять не надо.
    """
    error = validate_cmd(cmd_str)
    if error:
        raise ValueError(error)

    parts = shlex.split(cmd_str, posix=False)
    first = parts[0]
    user_args = parts[1:]
    ext = os.path.splitext(first)[1].lower()

    # Системные команды (winget/choco) — без resolve_path, без проверки наличия файла
    if not ext and os.path.basename(first).lower() in config.ALLOWED_BARE_COMMANDS:
        return ([first] + user_args, "")

    script_path = resolve_path(first)

    if ext == ".reg":
        return (["regedit", "/s", script_path], script_path)
    if ext in (".bat", ".cmd"):
        return (["cmd", "/c", script_path] + user_args, script_path)
    if ext == ".ps1":
        return (["powershell", "-ExecutionPolicy", "Bypass", "-NonInteractive",
                 "-File", script_path] + user_args, script_path)
    if ext == ".msi":
        return (["msiexec", "/i", script_path, "/qn", "/norestart"] + user_args, script_path)
    return ([script_path] + user_args, script_path)


# ------------------------------------------------------------------
# Граф зависимостей — топологическая сортировка задач
# ------------------------------------------------------------------
def resolve_dependencies(tasks: list[dict], all_programs: dict[str, list[dict]]) -> list[dict]:
    """
    Сортирует tasks так, что зависимости идут перед зависимыми программами.
    Если зависимость отсутствует в tasks — она добавляется автоматически.

    Использует топологическую сортировку (Кана) для обнаружения циклов.
    """
    # Индекс всех программ по имени
    all_by_name: dict[str, dict] = {}
    for progs in all_programs.values():
        for p in progs:
            all_by_name[p["name"]] = p

    # Индекс задач по имени
    task_names: set[str] = {t["name"] for t in tasks}
    task_by_name: dict[str, dict] = {t["name"]: t for t in tasks}

    # Автоматическое добавление отсутствующих зависимостей
    queue = list(tasks)
    while queue:
        task = queue.pop()
        for dep_name in task.get("depends_on", []):
            if dep_name not in task_names and dep_name in all_by_name:
                dep = dict(all_by_name[dep_name])
                task_names.add(dep_name)
                task_by_name[dep_name] = dep
                queue.append(dep)

    # Граф: name -> список имён, от которых зависит
    graph: dict[str, list[str]] = {}
    in_degree: dict[str, int] = defaultdict(int)

    for name in task_names:
        graph.setdefault(name, [])
        in_degree.setdefault(name, 0)

    for name in task_names:
        task = task_by_name[name]
        for dep_name in task.get("depends_on", []):
            if dep_name in task_names:
                graph[dep_name].append(name)
                in_degree[name] += 1

    # Алгоритм Кана
    queue_kahn: list[str] = [n for n in task_names if in_degree[n] == 0]
    sorted_names: list[str] = []

    while queue_kahn:
        node = queue_kahn.pop(0)
        sorted_names.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue_kahn.append(neighbor)

    if len(sorted_names) != len(task_names):
        # Обнаружен цикл — возвращаем исходный порядок + логируем
        cycle_nodes = task_names - set(sorted_names)
        logging.warning(f"Обнаружен цикл зависимостей: {cycle_nodes}. Порядок не изменён.")
        return list(task_by_name.values())

    return [task_by_name[name] for name in sorted_names]


def topological_levels(
    tasks: list[dict],
    all_programs: dict[str, list[dict]],
) -> list[list[dict]]:
    """
    Группирует tasks по топологическим уровням для параллельного выполнения.

    Уровень 0 — задачи без зависимостей (или зависящие только от уже установленных).
    Уровень 1 — задачи, зависящие только от уровня 0.
    И т.д.

    Внутри одного уровня задачи можно запускать параллельно — между ними нет зависимостей.
    Между уровнями порядок строгий: уровень N запускается только после завершения N-1.

    При обнаружении цикла — возвращает все задачи как один уровень (последовательно).
    """
    # Сначала восстановим полный список включая авто-подтянутые зависимости
    sorted_tasks = resolve_dependencies(tasks, all_programs)

    # Индексы по имени
    task_names = {t["name"] for t in sorted_tasks}
    task_by_name = {t["name"]: t for t in sorted_tasks}

    # Граф и in_degree (как в resolve_dependencies, но повторим — функция автономная)
    graph: dict[str, list[str]] = {n: [] for n in task_names}
    in_degree: dict[str, int] = {n: 0 for n in task_names}

    for name in task_names:
        for dep_name in task_by_name[name].get("depends_on", []):
            if dep_name in task_names:
                graph[dep_name].append(name)
                in_degree[name] += 1

    # BFS по уровням
    levels: list[list[dict]] = []
    current: list[str] = [n for n in task_names if in_degree[n] == 0]

    while current:
        levels.append([task_by_name[n] for n in current])
        next_level: list[str] = []
        for node in current:
            for neighbor in graph[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    next_level.append(neighbor)
        current = next_level

    # Проверка цикла: если кто-то не попал в уровни — возвращаем как fallback
    placed = sum(len(lvl) for lvl in levels)
    if placed != len(task_names):
        logging.warning("Цикл зависимостей при разбиении на уровни, fallback к одному уровню")
        return [sorted_tasks]

    return levels


# ------------------------------------------------------------------
# Pre/Post hooks — команды до и после установки
# ------------------------------------------------------------------
def run_hook(cmd_str: str, hook_name: str = "hook", task_name: str = "") -> bool:
    """
    Запускает hook-команду. Возвращает True при успехе.

    В отличие от основной команды — не валится при retryable кодах, не делает откат.
    Логирует результат и идёт дальше. Используется для pre_cmd/post_cmd.
    """
    if not cmd_str:
        return True

    try:
        cmd_args, script_path = build_cmd(cmd_str)
    except ValueError as e:
        logging.error(f"{hook_name} {task_name}: невалидная команда: {e}")
        return False

    if not os.path.exists(script_path):
        logging.warning(f"{hook_name} {task_name}: файл не найден: {script_path}")
        return False

    try:
        proc = subprocess.Popen(
            cmd_args,
            cwd=os.path.dirname(script_path) or config.SCRIPT_DIR,
            creationflags=config.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=config.DEFAULT_INSTALL_TIMEOUT)
        rc = proc.returncode
        if rc == 0:
            logging.info(f"{hook_name} {task_name}: OK")
            return True
        else:
            logging.warning(f"{hook_name} {task_name}: код {rc}")
            return False
    except Exception as e:
        logging.exception(f"{hook_name} {task_name}: исключение: {e}")
        return False


# ------------------------------------------------------------------
# Откат установки (uninstall)
# ------------------------------------------------------------------
def run_uninstall(task: dict) -> bool:
    """
    Запускает команду удаления программы. Возвращает True при успехе.
    """
    uninstall_cmd = task.get("uninstall_cmd", "")
    if not uninstall_cmd:
        logging.warning(f"Нет команды удаления для {task['name']}")
        return False

    try:
        cmd_args, script_path = build_cmd(uninstall_cmd)
    except ValueError as e:
        logging.error(f"Невалидная команда удаления {task['name']}: {e}")
        return False

    if not os.path.exists(script_path):
        logging.error(f"Файл удаления не найден: {script_path}")
        return False

    try:
        proc = subprocess.Popen(
            cmd_args,
            cwd=os.path.dirname(script_path) or config.SCRIPT_DIR,
            creationflags=config.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=config.DEFAULT_INSTALL_TIMEOUT)
        if proc.returncode == 0:
            logging.info(f"Откат OK: {task['name']}")
            return True
        else:
            logging.error(f"Откат {task['name']}: код {proc.returncode}")
            return False
    except Exception as e:
        logging.exception(f"Ошибка отката {task['name']}: {e}")
        return False


# ------------------------------------------------------------------
# Retry-коды: при этих exit-кодах имеет смысл повторять
# ------------------------------------------------------------------
RETRYABLE_EXIT_CODES = {
    1618,   # ERROR_INSTALL_ALREADY_RUNNING — другой MSI запущен
    1603,   # ERROR_INSTALL_FAILURE — общая ошибка (иногда transient)
    1641,   # ERROR_SUCCESS_REBOOT_INITIATED (установщик перезапускается)
}


# ------------------------------------------------------------------
# Watchdog: мониторит процесс, kill-ает если завис (нет CPU-активности)
# ------------------------------------------------------------------
def _watchdog_monitor(
    pid: int,
    stop_event: threading.Event,
    hung_event: threading.Event,
) -> None:
    """
    Раз в WATCHDOG_SAMPLE_INTERVAL секунд снимает CPU% процесса.
    Если CPU < WATCHDOG_CPU_THRESHOLD WATCHDOG_HANG_THRESHOLD раз подряд — kill.
    """
    try:
        import psutil
    except ImportError:
        logging.warning("psutil не установлен — watchdog отключён")
        return

    try:
        proc = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    try:
        proc.cpu_percent(interval=None)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    silent_count = 0
    while not stop_event.wait(config.WATCHDOG_SAMPLE_INTERVAL):
        try:
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                return

            cpu = proc.cpu_percent(interval=None)
            for child in proc.children(recursive=True):
                try:
                    cpu += child.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if cpu < config.WATCHDOG_CPU_THRESHOLD:
                silent_count += 1
                logging.debug(
                    f"Watchdog PID={pid}: тихий замер {silent_count}/"
                    f"{config.WATCHDOG_HANG_THRESHOLD} (CPU={cpu:.2f}%)"
                )
                if silent_count >= config.WATCHDOG_HANG_THRESHOLD:
                    logging.warning(
                        f"Watchdog PID={pid}: процесс завис "
                        f"({silent_count} замеров без CPU), завершаем"
                    )
                    hung_event.set()
                    try:
                        for child in proc.children(recursive=True):
                            try:
                                child.kill()
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                pass
                        proc.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    return
            else:
                silent_count = 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return
        except Exception as e:
            logging.exception(f"Watchdog ошибка: {e}")
            return


# ------------------------------------------------------------------
# Воркер установки — поддерживает sequential и parallel режимы
# ------------------------------------------------------------------
class InstallWorker(threading.Thread):
    """
    Эмитит сообщения через dispatch(message_dict).
    GUI отвечает за маршалинг dispatch в UI-поток.

    Формат сообщений:
      {"type": "progress",  "text": "...", "severity": "info|progress|warn|error|success"}
      {"type": "value",     "percent": 42}
      {"type": "scroll_to", "item_id": <object>}
      {"type": "finished",  "success": N, "fails": M, "reboot": bool,
                            "results": {...}, "rollbacks": {...}}

    Параллельный режим:
      - tasks разбиваются на топологические уровни через topological_levels()
      - внутри уровня — ThreadPoolExecutor с max_workers
      - .msi принудительно сериализуется через семафор (Windows Installer эксклюзивен)
    """

    def __init__(
        self,
        tasks: list[dict],
        dispatch: callable,
        parallel: bool = False,
        max_jobs: int | None = None,
        all_programs: dict[str, list[dict]] | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.tasks = tasks
        self.dispatch = dispatch
        self.parallel = parallel
        self.max_jobs = max_jobs or config.MAX_PARALLEL_JOBS
        self.all_programs = all_programs or {}
        self.total_tasks = len(tasks)
        self._is_running = True
        self.success_count = 0
        self.fail_count = 0
        self.reboot_needed = False
        self.results: dict = {}
        self.rollbacks: dict[str, str] = {}

        # Параллелизация: набор активных процессов и lock для shared-state
        self._active_procs: set[subprocess.Popen] = set()
        self._procs_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._completed_count = 0

        # MSI запускается только по одному за раз
        self._msi_semaphore = threading.Semaphore(1)

    def stop(self) -> None:
        self._is_running = False
        with self._procs_lock:
            for proc in list(self._active_procs):
                try:
                    proc.terminate()
                except Exception:
                    pass

    def _emit(self, **kwargs: object) -> None:
        self.dispatch(kwargs)

    # ----------------------------------------------------------
    # Запуск одного процесса с watchdog
    # ----------------------------------------------------------
    def _spawn_process(
        self,
        cmd_args: list[str],
        script_path: str,
        timeout: int,
    ) -> int:
        """
        Запускает один subprocess + watchdog, ждёт завершения. Возвращает returncode.
        Поднимает subprocess.TimeoutExpired или RuntimeError (для watchdog).
        """
        proc = subprocess.Popen(
            cmd_args,
            cwd=os.path.dirname(script_path) or config.SCRIPT_DIR,
            creationflags=config.CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        with self._procs_lock:
            self._active_procs.add(proc)

        watchdog_stop = threading.Event()
        watchdog_hung = threading.Event()
        watchdog_thread = None
        if config.WATCHDOG_ENABLED:
            watchdog_thread = threading.Thread(
                target=_watchdog_monitor,
                args=(proc.pid, watchdog_stop, watchdog_hung),
                daemon=True,
            )
            watchdog_thread.start()

        try:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise subprocess.TimeoutExpired(cmd_args, timeout)

            if watchdog_hung.is_set():
                raise RuntimeError("Процесс завис и был принудительно завершён watchdog'ом")

            return proc.returncode
        finally:
            watchdog_stop.set()
            if watchdog_thread:
                watchdog_thread.join(timeout=1.0)
            with self._procs_lock:
                self._active_procs.discard(proc)

    # ----------------------------------------------------------
    # Установка одной задачи целиком (pre, retry-loop, post, rollback)
    # ----------------------------------------------------------
    def _install_one_task(self, task: dict, emit_scroll: bool = True) -> None:
        """Полный жизненный цикл установки одной задачи. Thread-safe."""
        item_id = task.get("_item_id")
        name = task["name"]
        timeout = task.get("timeout", config.DEFAULT_INSTALL_TIMEOUT)
        max_retries = task.get("retry", 0)

        self._emit(type="progress", text=f"Установка: {name}...", severity="progress")
        if emit_scroll and item_id:
            self._emit(type="scroll_to", item_id=item_id)

        # --- Скачивание из URL (если задано) ---
        if url := task.get("url"):
            self._emit(type="progress", text=f"Скачивание: {name}...", severity="progress")
            try:
                download_dir = os.path.join(
                    os.environ.get("TEMP", config.SCRIPT_DIR),
                    "minstall_downloads",
                )

                def _dl_progress(info: dict) -> None:
                    total = info.get("total", 0)
                    if total > 0:
                        pct = int(info["downloaded"] * 100 / total)
                        self._emit(type="progress",
                                   text=f"Скачивание {name}: {pct}%",
                                   severity="progress")

                downloaded_path = download_installer(
                    url, download_dir,
                    expected_sha256=task.get("sha256"),
                    progress_cb=_dl_progress,
                )
                # Подставляем скачанный путь в cmd: первый токен — это файл,
                # остальное (флаги) — после
                import shlex as _shlex
                parts = _shlex.split(task["cmd"], posix=False)
                # Если в cmd только флаги (нет имени файла) — добавляем downloaded в начало
                if parts and not os.path.splitext(parts[0])[1]:
                    # cmd начинается не с файла — например "/silent --install"
                    new_cmd = _shlex.join([downloaded_path] + parts) if hasattr(_shlex, "join") \
                        else f'"{downloaded_path}" ' + " ".join(parts)
                else:
                    # cmd начинается с файла — заменяем первый токен
                    new_cmd = _shlex.join([downloaded_path] + parts[1:]) if hasattr(_shlex, "join") \
                        else f'"{downloaded_path}" ' + " ".join(parts[1:])
                # build_cmd ожидает не абсолютный путь, а через resolve_path —
                # поэтому конструируем cmd_args напрямую, в обход validate
                ext = os.path.splitext(downloaded_path)[1].lower()
                if ext == ".msi":
                    cmd_args = ["msiexec", "/i", downloaded_path, "/qn", "/norestart"] + parts[1:]
                elif ext == ".reg":
                    cmd_args = ["regedit", "/s", downloaded_path]
                else:
                    cmd_args = [downloaded_path] + parts[1:]
                script_path = downloaded_path
            except Exception as e:
                logging.exception(f"Скачивание {name} упало: {e}")
                self._emit(type="progress", text=f"Ошибка скачивания {name}: {e}",
                           severity="error")
                with self._state_lock:
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                return
        else:
            # --- Построение команды из локального файла ---
            try:
                cmd_args, script_path = build_cmd(task["cmd"])
            except ValueError as exc:
                self._emit(type="progress", text=f"{exc}: {name}", severity="error")
                with self._state_lock:
                    self.fail_count += 1
                    if item_id:
                        self.results[item_id] = "fail"
                return

        # script_path может быть "" для системных команд (winget) — там нет файла для проверки
        if script_path and not os.path.exists(script_path):
            logging.error(f"Файл не найден: {script_path}")
            self._emit(type="progress", text=f"Файл не найден: {script_path}", severity="error")
            with self._state_lock:
                self.fail_count += 1
                if item_id:
                    self.results[item_id] = "fail"
            return

        # --- Pre-hook ---
        if pre_cmd := task.get("pre_cmd"):
            self._emit(type="progress", text=f"Подготовка: {name}...", severity="progress")
            if not run_hook(pre_cmd, "pre_cmd", name):
                self._emit(type="progress",
                           text=f"Pre-команда {name} вернула ошибку, продолжаем",
                           severity="warn")

        # --- MSI должен запускаться эксклюзивно (Windows Installer) ---
        is_msi = os.path.splitext(task["cmd"].split()[0])[1].lower() == ".msi"
        if is_msi:
            self._msi_semaphore.acquire()

        try:
            attempt = 0
            last_rc = -1
            success = False

            while attempt <= max_retries:
                if not self._is_running:
                    break

                try:
                    if os.name == "nt":
                        if attempt > 0:
                            delay = min(5 * (2 ** (attempt - 1)), 30)
                            self._emit(
                                type="progress",
                                text=f"Повтор {attempt}/{max_retries} для {name} "
                                     f"(через {delay}с)...",
                                severity="warn",
                            )
                            time.sleep(delay)

                        last_rc = self._spawn_process(cmd_args, script_path, timeout)

                        if not self._is_running:
                            break

                        if last_rc == 0:
                            success = True
                            break
                        elif last_rc == 3010:
                            success = True
                            with self._state_lock:
                                self.reboot_needed = True
                            self._emit(type="progress",
                                       text=f"Требуется перезагрузка для {name}",
                                       severity="warn")
                            logging.info(f"OK (нужна перезагрузка): {name}")
                            break
                        elif last_rc in RETRYABLE_EXIT_CODES and attempt < max_retries:
                            logging.warning(
                                f"Retryable код {last_rc} для {name}, "
                                f"попытка {attempt + 1}/{max_retries + 1}"
                            )
                            attempt += 1
                            continue
                        else:
                            break
                    else:
                        time.sleep(1.5)
                        success = True
                        break

                except subprocess.TimeoutExpired:
                    logging.error(f"Таймаут {timeout}с для {name} (попытка {attempt + 1})")
                    self._emit(type="progress",
                               text=f"Таймаут {name} ({timeout}с)", severity="error")
                    if attempt < max_retries:
                        attempt += 1
                        continue
                    break
                except RuntimeError as e:
                    logging.error(f"Watchdog для {name}: {e}")
                    self._emit(type="progress",
                               text=f"Зависание: {name}", severity="error")
                    if attempt < max_retries:
                        attempt += 1
                        continue
                    break
                except Exception as e:
                    logging.exception(f"Исключение при установке {name}: {e}")
                    self._emit(type="progress", text=f"Ошибка {name}", severity="error")
                    break
        finally:
            if is_msi:
                self._msi_semaphore.release()

        # --- Результат ---
        if not self._is_running:
            with self._state_lock:
                if item_id:
                    self.results[item_id] = "cancelled"
            self._emit(type="progress", text=f"Отменено: {name}", severity="warn")
            return

        if success:
            with self._state_lock:
                self.success_count += 1
                if item_id:
                    self.results[item_id] = "ok"
            if attempt > 0:
                logging.info(f"OK (после {attempt + 1} попыток): {name}")
            else:
                logging.info(f"OK: {name}")

            if post_cmd := task.get("post_cmd"):
                self._emit(type="progress", text=f"Завершение: {name}...", severity="progress")
                if not run_hook(post_cmd, "post_cmd", name):
                    self._emit(type="progress",
                               text=f"Post-команда {name} вернула ошибку",
                               severity="warn")
        else:
            with self._state_lock:
                self.fail_count += 1
                if item_id:
                    self.results[item_id] = "fail"
            self._emit(type="progress",
                       text=f"Ошибка {name} (код {last_rc})", severity="error")
            logging.error(f"Ошибка {name}: код {last_rc} (после {attempt + 1} попыток)")

            if task.get("uninstall_cmd"):
                self._emit(type="progress", text=f"Откат {name}...", severity="warn")
                if run_uninstall(task):
                    with self._state_lock:
                        self.rollbacks[name] = "rolled_back"
                    self._emit(type="progress",
                               text=f"Откат {name}: успешно", severity="info")
                else:
                    with self._state_lock:
                        self.rollbacks[name] = "rollback_failed"
                    self._emit(type="progress",
                               text=f"Откат {name}: не удался", severity="error")
            else:
                with self._state_lock:
                    self.rollbacks[name] = "no_uninstall"

    # ----------------------------------------------------------
    # Sequential / Parallel runners
    # ----------------------------------------------------------
    def _emit_progress_pct(self) -> None:
        """Эмитит value-сообщение с процентом завершения."""
        with self._state_lock:
            self._completed_count += 1
            pct = int(self._completed_count / self.total_tasks * 100)
        self._emit(type="value", percent=pct)

    def _run_sequential(self) -> None:
        """Последовательная установка."""
        for task in self.tasks:
            if not self._is_running:
                item_id = task.get("_item_id")
                if item_id:
                    with self._state_lock:
                        self.results[item_id] = "cancelled"
                self._emit(type="progress", text="Установка отменена.", severity="warn")
                break

            self._install_one_task(task, emit_scroll=True)
            self._emit_progress_pct()

    def _run_parallel(self) -> None:
        """Параллельная установка по топологическим уровням."""
        from concurrent.futures import ThreadPoolExecutor

        levels = topological_levels(self.tasks, self.all_programs)
        logging.info(f"Параллельная установка: {len(levels)} уровней, max_jobs={self.max_jobs}")

        for level_idx, level in enumerate(levels):
            if not self._is_running:
                break

            self._emit(
                type="progress",
                text=f"Уровень {level_idx + 1}/{len(levels)}: {len(level)} программ параллельно",
                severity="info",
            )

            with ThreadPoolExecutor(max_workers=self.max_jobs) as executor:
                futures = [
                    executor.submit(self._install_one_task, task, False)
                    for task in level
                ]
                for fut in futures:
                    try:
                        fut.result()
                    except Exception as e:
                        logging.exception(f"Поток-исполнитель упал: {e}")
                    self._emit_progress_pct()

    def run(self) -> None:
        """Точка входа потока — выбирает sequential или parallel."""
        try:
            if self.parallel and self.total_tasks > 1:
                self._run_parallel()
            else:
                self._run_sequential()
        except Exception as e:
            logging.exception(f"InstallWorker.run упал: {e}")
        finally:
            self._emit(type="finished",
                       success=self.success_count,
                       fails=self.fail_count,
                       reboot=self.reboot_needed,
                       results=self.results,
                       rollbacks=self.rollbacks)
