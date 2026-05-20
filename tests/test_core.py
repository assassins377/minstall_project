"""Unit-тесты для core.py — чистая логика, не требует wxPython."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import core


# ------------------------------------------------------------------
# parse_version / compare_versions
# ------------------------------------------------------------------
class TestVersionParsing(unittest.TestCase):
    def test_parse_simple(self) -> None:
        self.assertEqual(core.parse_version("1.2.3"), (1, 2, 3))

    def test_parse_with_text(self) -> None:
        self.assertEqual(core.parse_version("v4.0.1-beta"), (4, 0, 1))

    def test_parse_empty(self) -> None:
        self.assertEqual(core.parse_version(""), ())

    def test_parse_none(self) -> None:
        self.assertEqual(core.parse_version(None), ())

    def test_parse_single(self) -> None:
        self.assertEqual(core.parse_version("10"), (10,))

    def test_compare_equal(self) -> None:
        self.assertEqual(core.compare_versions("1.0.0", "1.0.0"), 0)

    def test_compare_greater(self) -> None:
        self.assertEqual(core.compare_versions("2.0.0", "1.9.9"), 1)

    def test_compare_less(self) -> None:
        self.assertEqual(core.compare_versions("1.0.0", "1.0.1"), -1)

    def test_compare_different_lengths(self) -> None:
        self.assertEqual(core.compare_versions("1.0", "1.0.0"), 0)
        self.assertEqual(core.compare_versions("1.0.1", "1.0"), 1)

    def test_compare_with_text(self) -> None:
        self.assertEqual(core.compare_versions("v2.1.0-rc1", "2.0.9"), 1)

    def test_compare_empty(self) -> None:
        self.assertEqual(core.compare_versions("", ""), 0)
        self.assertEqual(core.compare_versions("1.0", ""), 1)


# ------------------------------------------------------------------
# validate_cmd
# ------------------------------------------------------------------
class TestValidateCmd(unittest.TestCase):
    def test_valid_exe(self) -> None:
        self.assertIsNone(core.validate_cmd("software\\app.exe /silent"))

    def test_valid_msi(self) -> None:
        self.assertIsNone(core.validate_cmd("software\\pkg.msi /quiet"))

    def test_valid_bat(self) -> None:
        self.assertIsNone(core.validate_cmd("scripts\\setup.bat"))

    def test_valid_ps1(self) -> None:
        self.assertIsNone(core.validate_cmd("scripts\\tweak.ps1 -Force"))

    def test_valid_reg(self) -> None:
        self.assertIsNone(core.validate_cmd("tweaks\\fix.reg"))

    def test_reject_pipe(self) -> None:
        err = core.validate_cmd("app.exe | malicious.exe")
        self.assertIsNotNone(err)
        self.assertIn("|", err)

    def test_reject_ampersand(self) -> None:
        err = core.validate_cmd("app.exe & del /f C:\\*")
        self.assertIsNotNone(err)
        self.assertIn("&", err)

    def test_reject_semicolon(self) -> None:
        err = core.validate_cmd("app.exe ; rm -rf /")
        self.assertIsNotNone(err)

    def test_reject_backtick(self) -> None:
        err = core.validate_cmd("app.exe `whoami`")
        self.assertIsNotNone(err)

    def test_reject_bad_extension(self) -> None:
        err = core.validate_cmd("payload.py --evil")
        self.assertIsNotNone(err)
        self.assertIn(".py", err)

    def test_empty_command(self) -> None:
        err = core.validate_cmd("")
        self.assertIsNotNone(err)


# ------------------------------------------------------------------
# build_cmd
# ------------------------------------------------------------------
class TestBuildCmd(unittest.TestCase):
    def test_exe(self) -> None:
        args, path = core.build_cmd("software\\app.exe /silent")
        self.assertTrue(path.endswith("app.exe"))
        self.assertEqual(args[0], path)
        self.assertIn("/silent", args)

    def test_msi(self) -> None:
        args, path = core.build_cmd("software\\pkg.msi")
        self.assertEqual(args[0], "msiexec")
        self.assertIn("/qn", args)

    def test_bat(self) -> None:
        args, path = core.build_cmd("scripts\\run.bat")
        self.assertEqual(args[0], "cmd")
        self.assertEqual(args[1], "/c")

    def test_ps1(self) -> None:
        args, path = core.build_cmd("scripts\\tweak.ps1 -Param value")
        self.assertEqual(args[0], "powershell")
        self.assertIn("-NonInteractive", args)

    def test_reg(self) -> None:
        args, path = core.build_cmd("tweaks\\fix.reg")
        self.assertEqual(args[0], "regedit")
        self.assertIn("/s", args)

    def test_injection_raises(self) -> None:
        with self.assertRaises(ValueError):
            core.build_cmd("app.exe & whoami")

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            core.build_cmd("")


# ------------------------------------------------------------------
# check_status
# ------------------------------------------------------------------
class TestCheckStatus(unittest.TestCase):
    def test_always_runnable(self) -> None:
        prog = {"name": "Tweak", "cmd": "t.bat", "detect": {"always_runnable": True}}
        status, ver = core.check_status(prog, [])
        self.assertEqual(status, "runnable")

    def test_missing_program(self) -> None:
        prog = {"name": "Missing App", "cmd": "m.exe", "detect": {}}
        status, ver = core.check_status(prog, [("Other App", "1.0")])
        self.assertEqual(status, "missing")

    def test_found_program(self) -> None:
        prog = {"name": "MyApp", "cmd": "m.exe", "detect": {}}
        status, ver = core.check_status(prog, [("MyApp", "2.0.0")])
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "2.0.0")

    def test_outdated_program(self) -> None:
        prog = {"name": "MyApp", "cmd": "m.exe", "detect": {"min_version": "3.0"}}
        status, ver = core.check_status(prog, [("MyApp", "2.5")])
        self.assertEqual(status, "outdated")
        self.assertEqual(ver, "2.5")

    def test_registry_name_override(self) -> None:
        prog = {"name": "Chrome", "cmd": "c.exe", "detect": {"registry_name": "Google Chrome"}}
        status, ver = core.check_status(prog, [("Google Chrome", "120.0")])
        self.assertEqual(status, "ok")

    def test_path_detection_exists(self) -> None:
        prog = {"name": "X", "cmd": "x.exe", "detect": {"path": __file__}}
        status, _ = core.check_status(prog, [])
        self.assertEqual(status, "ok")

    def test_path_detection_missing(self) -> None:
        prog = {"name": "X", "cmd": "x.exe", "detect": {"path": "/no/such/file"}}
        status, _ = core.check_status(prog, [])
        self.assertEqual(status, "missing")

    def test_no_detect_key(self) -> None:
        prog = {"name": "FooBar", "cmd": "f.exe"}
        status, _ = core.check_status(prog, [("FooBar", "1.0")])
        self.assertEqual(status, "ok")

    def test_case_insensitive_match(self) -> None:
        prog = {"name": "myapp", "cmd": "m.exe", "detect": {}}
        status, _ = core.check_status(prog, [("MyApp Pro Edition", "1.0")])
        self.assertEqual(status, "ok")

    def test_match_ignores_punctuation(self) -> None:
        """Format.Factory с точкой матчится с FormatFactory без точки в реестре."""
        prog = {"name": "Format.Factory", "cmd": "f.exe", "detect": {}}
        status, ver = core.check_status(prog, [("FormatFactory 5.12.2.0", "5.12.2.0")])
        self.assertEqual(status, "ok")
        self.assertEqual(ver, "5.12.2.0")

    def test_match_ignores_dashes(self) -> None:
        """7-Zip из JSON матчится с 7Zip в реестре."""
        prog = {"name": "7-Zip", "cmd": "z.exe", "detect": {}}
        status, _ = core.check_status(prog, [("7Zip 23.01 (x64)", "23.01")])
        self.assertEqual(status, "ok")

    def test_normalize_basic(self) -> None:
        from core import _normalize_for_match as norm
        self.assertEqual(norm("Format.Factory"), "formatfactory")
        self.assertEqual(norm("FormatFactory 5.12.2.0"), "formatfactory51220")
        self.assertEqual(norm("7-Zip"), "7zip")
        self.assertEqual(norm("Google Chrome"), "googlechrome")
        self.assertEqual(norm(""), "")


# ------------------------------------------------------------------
# load_programs_from_json
# ------------------------------------------------------------------
class TestLoadPrograms(unittest.TestCase):
    def test_load_valid_file(self) -> None:
        data = {
            "_version": 2,
            "categories": {
                "TEST": [{"name": "App", "cmd": "a.exe", "desc": "Test"}]
            }
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            tmp = f.name
        try:
            with patch.object(config, "CONFIG_FILE", tmp):
                result = core.load_programs_from_json()
            self.assertIn("TEST", result)
            self.assertEqual(result["TEST"][0]["name"], "App")
        finally:
            os.unlink(tmp)

    def test_missing_file(self) -> None:
        with patch.object(config, "CONFIG_FILE", "/no/such/file.json"):
            result = core.load_programs_from_json()
        self.assertEqual(result, {})

    def test_corrupted_json(self) -> None:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{broken json!!!")
            tmp = f.name
        try:
            with patch.object(config, "CONFIG_FILE", tmp):
                result = core.load_programs_from_json()
            self.assertEqual(result, {})
        finally:
            os.unlink(tmp)


# ------------------------------------------------------------------
# resolve_path
# ------------------------------------------------------------------
class TestResolvePath(unittest.TestCase):
    def test_relative(self) -> None:
        result = core.resolve_path("software\\app.exe")
        self.assertTrue(result.startswith(config.SCRIPT_DIR))
        self.assertTrue(result.endswith("app.exe"))


# ------------------------------------------------------------------
# resolve_dependencies (топологическая сортировка)
# ------------------------------------------------------------------
class TestResolveDependencies(unittest.TestCase):
    def _make_task(self, name: str, depends_on: list[str] | None = None) -> dict:
        task = {"name": name, "cmd": f"{name.lower()}.exe", "detect": {}}
        if depends_on:
            task["depends_on"] = depends_on
        return task

    def test_no_deps(self) -> None:
        a = self._make_task("A")
        b = self._make_task("B")
        result = core.resolve_dependencies([a, b], {})
        names = [t["name"] for t in result]
        self.assertEqual(set(names), {"A", "B"})

    def test_simple_order(self) -> None:
        base = self._make_task("Base")
        app = self._make_task("App", depends_on=["Base"])
        result = core.resolve_dependencies([app, base], {"cat": [base, app]})
        names = [t["name"] for t in result]
        self.assertLess(names.index("Base"), names.index("App"))

    def test_chain(self) -> None:
        a = self._make_task("A")
        b = self._make_task("B", depends_on=["A"])
        c = self._make_task("C", depends_on=["B"])
        result = core.resolve_dependencies([c, b, a], {"cat": [a, b, c]})
        names = [t["name"] for t in result]
        self.assertEqual(names, ["A", "B", "C"])

    def test_auto_add_missing_dependency(self) -> None:
        """Если зависимость не выбрана пользователем, она добавляется автоматически."""
        base = self._make_task("Base")
        app = self._make_task("App", depends_on=["Base"])
        all_progs = {"cat": [base, app]}
        # Пользователь выбрал только App
        result = core.resolve_dependencies([app], all_progs)
        names = [t["name"] for t in result]
        self.assertIn("Base", names)
        self.assertLess(names.index("Base"), names.index("App"))

    def test_cycle_returns_original(self) -> None:
        """Циклическая зависимость не ломает — возвращает исходный набор."""
        a = self._make_task("A", depends_on=["B"])
        b = self._make_task("B", depends_on=["A"])
        result = core.resolve_dependencies([a, b], {"cat": [a, b]})
        names = {t["name"] for t in result}
        self.assertEqual(names, {"A", "B"})

    def test_missing_dep_not_in_db(self) -> None:
        """Зависимость на несуществующую программу — игнорируется без ошибки."""
        app = self._make_task("App", depends_on=["Phantom"])
        result = core.resolve_dependencies([app], {"cat": [app]})
        names = [t["name"] for t in result]
        self.assertEqual(names, ["App"])


# ------------------------------------------------------------------
# topological_levels
# ------------------------------------------------------------------
class TestTopologicalLevels(unittest.TestCase):
    def _make_task(self, name: str, depends_on: list[str] | None = None) -> dict:
        task = {"name": name, "cmd": f"{name.lower()}.exe", "detect": {}}
        if depends_on:
            task["depends_on"] = depends_on
        return task

    def test_no_deps_one_level(self) -> None:
        """Все без зависимостей → один уровень."""
        a = self._make_task("A")
        b = self._make_task("B")
        c = self._make_task("C")
        levels = core.topological_levels([a, b, c], {"cat": [a, b, c]})
        self.assertEqual(len(levels), 1)
        names = {t["name"] for t in levels[0]}
        self.assertEqual(names, {"A", "B", "C"})

    def test_chain_creates_separate_levels(self) -> None:
        """A → B → C: три уровня по одному."""
        a = self._make_task("A")
        b = self._make_task("B", depends_on=["A"])
        c = self._make_task("C", depends_on=["B"])
        levels = core.topological_levels([a, b, c], {"cat": [a, b, c]})
        self.assertEqual(len(levels), 3)
        self.assertEqual([t["name"] for t in levels[0]], ["A"])
        self.assertEqual([t["name"] for t in levels[1]], ["B"])
        self.assertEqual([t["name"] for t in levels[2]], ["C"])

    def test_diamond(self) -> None:
        """A → B, A → C, B+C → D: три уровня (A, {B,C}, D)."""
        a = self._make_task("A")
        b = self._make_task("B", depends_on=["A"])
        c = self._make_task("C", depends_on=["A"])
        d = self._make_task("D", depends_on=["B", "C"])
        levels = core.topological_levels([a, b, c, d], {"cat": [a, b, c, d]})
        self.assertEqual(len(levels), 3)
        self.assertEqual([t["name"] for t in levels[0]], ["A"])
        self.assertEqual({t["name"] for t in levels[1]}, {"B", "C"})
        self.assertEqual([t["name"] for t in levels[2]], ["D"])

    def test_cycle_fallback_to_single_level(self) -> None:
        """Цикл — все в один уровень (sequential fallback)."""
        a = self._make_task("A", depends_on=["B"])
        b = self._make_task("B", depends_on=["A"])
        levels = core.topological_levels([a, b], {"cat": [a, b]})
        self.assertEqual(len(levels), 1)


# ------------------------------------------------------------------
# RETRYABLE_EXIT_CODES
# ------------------------------------------------------------------
class TestRetryableExitCodes(unittest.TestCase):
    def test_known_codes_exist(self) -> None:
        self.assertIn(1618, core.RETRYABLE_EXIT_CODES)
        self.assertIn(1603, core.RETRYABLE_EXIT_CODES)

    def test_zero_not_retryable(self) -> None:
        self.assertNotIn(0, core.RETRYABLE_EXIT_CODES)


# ------------------------------------------------------------------
# build_status_cache
# ------------------------------------------------------------------
class TestDirectorySnapshot(unittest.TestCase):
    def test_snapshot_empty(self) -> None:
        import scanner
        result = scanner.directory_snapshot("/no/such/dir")
        self.assertEqual(result, frozenset())

    def test_snapshot_detects_files(self) -> None:
        import scanner
        import shutil
        import tempfile
        root = tempfile.mkdtemp(prefix="minst_snap_")
        try:
            with open(os.path.join(root, "app.exe"), "wb") as f:
                f.write(b"x" * 100)
            snap1 = scanner.directory_snapshot(root)
            self.assertEqual(len(snap1), 1)
            entry = next(iter(snap1))
            self.assertEqual(entry[0], "app.exe")
            self.assertEqual(entry[1], 100)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_snapshot_changes_after_modification(self) -> None:
        """Снимок меняется после изменения файла."""
        import scanner
        import shutil
        import tempfile
        import time
        root = tempfile.mkdtemp(prefix="minst_snap_")
        try:
            path = os.path.join(root, "app.exe")
            with open(path, "wb") as f:
                f.write(b"x")
            snap1 = scanner.directory_snapshot(root)
            time.sleep(0.05)
            with open(path, "wb") as f:
                f.write(b"xx")  # размер другой
            snap2 = scanner.directory_snapshot(root)
            self.assertNotEqual(snap1, snap2)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestInstalledCache(unittest.TestCase):
    def test_no_cache_when_no_state(self) -> None:
        """Без state_dict — всегда читает реестр."""
        # Должно не падать (возможно вернуть [] на не-Windows)
        result = core.get_installed_programs(state_dict=None, use_cache=False)
        self.assertIsInstance(result, list)

    def test_cache_persists_in_state(self) -> None:
        """После первого вызова в state.installed_cache появляется запись."""
        state_dict: dict = {}
        core.get_installed_programs(state_dict=state_dict, use_cache=True)
        self.assertIn("installed_cache", state_dict)
        cache = state_dict["installed_cache"]
        self.assertIn("entries", cache)
        self.assertIn("ts", cache)

    def test_cache_returns_stored_entries(self) -> None:
        """Если кеш свежий — возвращает его без обращения к реестру."""
        import time as _time
        state_dict = {
            "installed_cache": {
                "entries": [["Fake App", "1.0"], ["Other", "2.0"]],
                "ts": _time.time(),  # свежий
            }
        }
        result = core.get_installed_programs(state_dict=state_dict, use_cache=True)
        self.assertEqual(result, [("Fake App", "1.0"), ("Other", "2.0")])

    def test_expired_cache_refreshes(self) -> None:
        """Если TTL истёк — кеш переписывается."""
        import time as _time
        state_dict = {
            "installed_cache": {
                "entries": [["Stale", "0.1"]],
                "ts": _time.time() - config.INSTALLED_CACHE_TTL_SECONDS - 100,
            }
        }
        core.get_installed_programs(state_dict=state_dict, use_cache=True)
        # ts должен обновиться
        self.assertGreater(
            state_dict["installed_cache"]["ts"],
            _time.time() - 5,
        )

    def test_invalidate_clears_cache(self) -> None:
        state_dict = {"installed_cache": {"entries": [["X", "1.0"]], "ts": 0}}
        core.invalidate_installed_cache(state_dict)
        self.assertNotIn("installed_cache", state_dict)

    def test_invalidate_handles_no_cache(self) -> None:
        """Сброс кеша когда его нет — не должен падать."""
        state_dict: dict = {}
        core.invalidate_installed_cache(state_dict)  # no-op
        self.assertEqual(state_dict, {})


class TestIconExtraction(unittest.TestCase):
    def test_non_exe_returns_none(self) -> None:
        import icons
        # .msi и другие — не пытаемся извлечь
        self.assertIsNone(icons.try_extract_exe_icon("/path/to/file.msi"))
        self.assertIsNone(icons.try_extract_exe_icon("/path/to/file.bat"))

    def test_missing_file_returns_none(self) -> None:
        import icons
        self.assertIsNone(icons.try_extract_exe_icon("/no/such/file.exe"))

    def test_empty_path_returns_none(self) -> None:
        import icons
        self.assertIsNone(icons.try_extract_exe_icon(""))

    def test_cache_path_deterministic(self) -> None:
        """Один и тот же путь → один и тот же cache_path."""
        import icons
        p1 = icons._cache_path_for("C:\\soft\\app.exe")
        p2 = icons._cache_path_for("C:\\soft\\app.exe")
        self.assertEqual(p1, p2)
        # Разные пути → разные кеши
        p3 = icons._cache_path_for("C:\\soft\\other.exe")
        self.assertNotEqual(p1, p3)

    def test_resolve_uses_explicit_icon_when_exists(self) -> None:
        """Если icon из programs.json существует и не system.png — используется он."""
        import icons
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"x")
            tmp_icon = f.name
        try:
            prog = {"name": "X", "icon": tmp_icon, "cmd": ""}
            result = icons.resolve_program_icon(prog, lambda p: p)
            self.assertEqual(result, tmp_icon)
        finally:
            os.unlink(tmp_icon)


class TestStats(unittest.TestCase):
    def test_format_size(self) -> None:
        import stats
        self.assertIn("КБ", stats.format_size(1024))
        self.assertIn("МБ", stats.format_size(5 * 1024 * 1024))
        self.assertIn("ГБ", stats.format_size(2 * 1024 ** 3))

    def test_format_duration(self) -> None:
        import stats
        self.assertEqual(stats.format_duration(45), "45с")
        self.assertEqual(stats.format_duration(120), "2мин")
        self.assertEqual(stats.format_duration(125), "2мин 5с")

    def test_record_and_estimate(self) -> None:
        import stats
        state_dict = {}
        stats.record_install_time(state_dict, "Chrome", 30.0)
        stats.record_install_time(state_dict, "Chrome", 40.0)
        stats.record_install_time(state_dict, "Chrome", 35.0)

        times = stats.get_install_times(state_dict)
        est = stats.estimate_time({"name": "Chrome"}, times)
        self.assertEqual(est, 35.0)  # медиана из [30, 40, 35]

    def test_estimate_unknown_returns_none(self) -> None:
        import stats
        est = stats.estimate_time({"name": "Phantom"}, {})
        self.assertIsNone(est)

    def test_history_rotation(self) -> None:
        """История не должна расти бесконечно — после MAX_HISTORY ротируется."""
        import stats
        state_dict = {}
        for i in range(stats.MAX_HISTORY + 5):
            stats.record_install_time(state_dict, "X", float(i))
        times = stats.get_install_times(state_dict)
        self.assertEqual(len(times["X"]), stats.MAX_HISTORY)
        # Последнее значение — i=stats.MAX_HISTORY+4
        self.assertEqual(times["X"][-1], float(stats.MAX_HISTORY + 4))


class TestIsInstallerAvailable(unittest.TestCase):
    def test_existing_file(self) -> None:
        """Реальный файл — доступен."""
        prog = {"name": "X", "cmd": __file__}  # сам файл теста — точно есть
        # __file__ может быть .py — не в ALLOWED_CMD_EXTENSIONS, проверим иначе
        # Используем существующий .exe который мы делаем cmd-ом
        # Проверим через winget — для bare-команды script_path=="" → True
        prog = {"name": "Y", "cmd": "winget install foo"}
        self.assertTrue(core.is_installer_available(prog))

    def test_missing_file(self) -> None:
        prog = {"name": "X", "cmd": "software\\nonexistent_xyz_123.exe"}
        self.assertFalse(core.is_installer_available(prog))

    def test_empty_cmd(self) -> None:
        prog = {"name": "X", "cmd": ""}
        self.assertFalse(core.is_installer_available(prog))

    def test_invalid_cmd(self) -> None:
        """Невалидная команда (с инъекцией) — недоступна."""
        prog = {"name": "X", "cmd": "evil.exe & rm -rf"}
        self.assertFalse(core.is_installer_available(prog))

    def test_url_makes_available(self) -> None:
        """Программа с URL — доступна, файл скачается."""
        prog = {
            "name": "X",
            "url": "https://example.com/installer.exe",
            "cmd": "/silent",
        }
        self.assertTrue(core.is_installer_available(prog))


class TestBuildStatusCache(unittest.TestCase):
    def test_empty_db(self) -> None:
        cache = core.build_status_cache({}, [])
        self.assertEqual(cache, {})

    def test_caches_all_programs(self) -> None:
        db = {
            "CAT1": [
                {"name": "App1", "cmd": "a.exe", "detect": {}},
                {"name": "App2", "cmd": "b.exe", "detect": {"always_runnable": True}},
            ],
            "CAT2": [
                {"name": "App3", "cmd": "c.exe", "detect": {}},
            ],
        }
        installed = [("App1", "1.0")]
        cache = core.build_status_cache(db, installed)

        self.assertEqual(set(cache.keys()), {"App1", "App2", "App3"})
        self.assertEqual(cache["App1"], ("ok", "1.0"))
        self.assertEqual(cache["App2"], ("runnable", ""))
        self.assertEqual(cache["App3"], ("missing", ""))


# ------------------------------------------------------------------
# Cache invalidation
# ------------------------------------------------------------------
class TestCacheInvalidation(unittest.TestCase):
    def test_invalidate_resets_net_cache(self) -> None:
        # Ставим что-то в кеш
        core._net_release_cache = (True, 528040)
        self.assertEqual(core._net_release_cache, (True, 528040))

        core.invalidate_caches()
        self.assertEqual(core._net_release_cache, (False, None))


# ------------------------------------------------------------------
# State (save/load)
# ------------------------------------------------------------------
class TestState(unittest.TestCase):
    def test_load_missing_returns_empty(self) -> None:
        import state
        with patch.object(state, "STATE_FILE", "/no/such/state.json"):
            self.assertEqual(state.load_state(), {})

    def test_save_then_load_roundtrip(self) -> None:
        import state
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            with patch.object(state, "STATE_FILE", tmp):
                state.save_state({"window": {"width": 1000, "height": 700}})
                loaded = state.load_state()
            self.assertEqual(loaded["window"]["width"], 1000)
            self.assertEqual(loaded["window"]["height"], 700)
        finally:
            os.unlink(tmp)

    def test_load_corrupted_returns_empty(self) -> None:
        import state
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
            tmp = f.name
        try:
            with patch.object(state, "STATE_FILE", tmp):
                self.assertEqual(state.load_state(), {})
        finally:
            os.unlink(tmp)


# ------------------------------------------------------------------
# Updater: architecture detection
# ------------------------------------------------------------------
class TestUpdaterArch(unittest.TestCase):
    def test_current_arch_returns_known_value(self) -> None:
        import updater
        arch = updater.current_arch()
        self.assertIn(arch, ("x86", "x64"))

    def test_exe_asset_name_matches_arch(self) -> None:
        import updater
        arch = updater.current_arch()
        self.assertEqual(updater.exe_asset_name(), f"MInstAll_{arch}.exe")

    def test_sha256_asset_name_matches_exe(self) -> None:
        import updater
        self.assertEqual(
            updater.sha256_asset_name(),
            f"{updater.exe_asset_name()}.sha256",
        )


# ------------------------------------------------------------------
# run_hook (pre_cmd / post_cmd)
# ------------------------------------------------------------------
class TestRunHook(unittest.TestCase):
    def test_empty_cmd_returns_true(self) -> None:
        """Пустая команда — это нормально, hook просто пропускается."""
        self.assertTrue(core.run_hook("", "pre_cmd", "App"))
        self.assertTrue(core.run_hook(None or "", "post_cmd", "App"))

    def test_invalid_cmd_returns_false(self) -> None:
        """Невалидная команда (shell-инъекция) — False."""
        self.assertFalse(core.run_hook("payload.py & evil.exe", "pre_cmd", "App"))

    def test_missing_file_returns_false(self) -> None:
        """Файл не существует — False, но без исключения."""
        self.assertFalse(core.run_hook("nonexistent_xyz_123.bat", "post_cmd", "App"))


# ------------------------------------------------------------------
# Watchdog config
# ------------------------------------------------------------------
class TestWatchdogConfig(unittest.TestCase):
    def test_watchdog_constants_exist(self) -> None:
        self.assertTrue(hasattr(config, "WATCHDOG_ENABLED"))
        self.assertTrue(hasattr(config, "WATCHDOG_SAMPLE_INTERVAL"))
        self.assertTrue(hasattr(config, "WATCHDOG_HANG_THRESHOLD"))
        self.assertTrue(hasattr(config, "WATCHDOG_CPU_THRESHOLD"))

    def test_thresholds_are_positive(self) -> None:
        self.assertGreater(config.WATCHDOG_SAMPLE_INTERVAL, 0)
        self.assertGreater(config.WATCHDOG_HANG_THRESHOLD, 0)
        self.assertGreaterEqual(config.WATCHDOG_CPU_THRESHOLD, 0)


# ------------------------------------------------------------------
# Scanner: автосканирование software/
# ------------------------------------------------------------------
class TestScanner(unittest.TestCase):
    def _make_software_dir(self, structure: dict) -> str:
        """Создаёт временную структуру файлов под software/.

        structure: {"file.exe": None, "Office/sub.msi": None}
        Возвращает путь к корневой папке.
        """
        import tempfile
        root = tempfile.mkdtemp(prefix="minst_scan_")
        for rel_path in structure:
            full = os.path.join(root, rel_path.replace("/", os.sep))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(b"")
        return root

    def test_scan_empty_returns_empty(self) -> None:
        import scanner
        result = scanner.scan_directory("/no/such/dir")
        self.assertEqual(result, {})

    def test_scan_finds_subfolder_as_category(self) -> None:
        import scanner
        import shutil
        root = self._make_software_dir({
            "Office/libreoffice.msi": None,
            "Office/onlyoffice.exe": None,
        })
        try:
            result = scanner.scan_directory(root)
            self.assertIn("OFFICE", result)
            self.assertEqual(len(result["OFFICE"]), 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_scan_finds_root_files_by_heuristic(self) -> None:
        import scanner
        import shutil
        root = self._make_software_dir({
            "chrome_setup.exe": None,
            "vcredist_x64.exe": None,
        })
        try:
            result = scanner.scan_directory(root)
            self.assertIn("ИНТЕРНЕТ И БРАУЗЕРЫ", result)
            self.assertIn("СИСТЕМНЫЕ КОМПОНЕНТЫ", result)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_scan_skips_unsupported_extensions(self) -> None:
        import scanner
        import shutil
        root = self._make_software_dir({
            "readme.txt": None,
            "script.py": None,
            "app.exe": None,
        })
        try:
            result = scanner.scan_directory(root)
            total = sum(len(v) for v in result.values())
            self.assertEqual(total, 1)  # только app.exe
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_merge_skips_duplicates(self) -> None:
        import scanner
        existing = {"CAT1": [{"name": "App", "cmd": "software\\app.exe /S"}]}
        scanned = {"CAT1": [{"name": "App", "cmd": "software\\app.exe /S"}]}
        merged, new = scanner.auto_merge_into_db(existing, scanned)
        self.assertEqual(len(merged["CAT1"]), 1)
        self.assertEqual(new, [])

    def test_merge_adds_new(self) -> None:
        import scanner
        existing = {"CAT1": [{"name": "App", "cmd": "software\\app.exe /S"}]}
        scanned = {
            "CAT1": [{"name": "App2", "cmd": "software\\app2.exe /S"}],
            "CAT2": [{"name": "App3", "cmd": "software\\app3.exe /S"}],
        }
        merged, new = scanner.auto_merge_into_db(existing, scanned)
        self.assertEqual(len(merged["CAT1"]), 2)
        self.assertEqual(len(merged["CAT2"]), 1)
        self.assertEqual(len(new), 2)
        # У новых проставлена категория для отчёта
        self.assertIn("_category", new[0])

    def test_filename_to_name(self) -> None:
        import scanner
        self.assertEqual(scanner.filename_to_name("chrome_setup.exe"), "Chrome")
        self.assertEqual(scanner.filename_to_name("vcredist_x64.exe"), "Vcredist")
        self.assertEqual(scanner.filename_to_name("LibreOffice_7.5.msi"), "Libreoffice")
        # Точки превращаются в пробелы → имя матчится с реестром без точки
        self.assertEqual(scanner.filename_to_name("Format.Factory-5.12.2.0.exe"),
                          "Format Factory")

    def test_scan_nested_subcategory(self) -> None:
        """software/Interface/Themes/foo.exe → категория 'INTERFACE / THEMES'."""
        import scanner
        import shutil
        root = self._make_software_dir({
            "Interface/Themes/dark_setup.exe": None,
            "Interface/Themes/light_setup.exe": None,
            "Interface/customizer.msi": None,
        })
        try:
            result = scanner.scan_directory(root)
            # Корневые программы Interface/customizer.msi
            self.assertIn("INTERFACE", result)
            self.assertEqual(len(result["INTERFACE"]), 1)
            # Вложенные программы Interface/Themes/*
            self.assertIn("INTERFACE / THEMES", result)
            self.assertEqual(len(result["INTERFACE / THEMES"]), 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_scan_deeply_nested(self) -> None:
        """software/A/B/C/foo.exe → 'A / B / C'."""
        import scanner
        import shutil
        root = self._make_software_dir({
            "A/B/C/setup.exe": None,
        })
        try:
            result = scanner.scan_directory(root)
            self.assertIn("A / B / C", result)
            self.assertEqual(len(result["A / B / C"]), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_scan_relative_path_correct_for_nested(self) -> None:
        """rel_path должен учитывать всю глубину подпапок."""
        import scanner
        import shutil
        root = self._make_software_dir({
            "Interface/Themes/dark.exe": None,
        })
        try:
            result = scanner.scan_directory(root)
            entry = result["INTERFACE / THEMES"][0]
            # Путь должен быть software/Interface/Themes/dark.exe со слешами
            self.assertIn("Interface", entry["cmd"])
            self.assertIn("Themes", entry["cmd"])
            self.assertIn("dark.exe", entry["cmd"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_max_depth_respected(self) -> None:
        """max_depth защищает от бесконечной вложенности."""
        import scanner
        import shutil
        # Создаём 10 уровней вложенности
        deep_path = "a/b/c/d/e/f/g/h/i/j/setup.exe"
        root = self._make_software_dir({deep_path: None})
        try:
            result = scanner.scan_directory(root, max_depth=3)
            # На глубине 10 при max_depth=3 — программа не должна быть найдена
            total = sum(len(v) for v in result.values())
            self.assertEqual(total, 0)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_build_catalog_from_scan_no_existing(self) -> None:
        """Без existing_db — просто scan_directory."""
        import scanner
        import shutil
        root = self._make_software_dir({"chrome.exe": None})
        try:
            result = scanner.build_catalog_from_scan(software_dir=root)
            self.assertEqual(sum(len(v) for v in result.values()), 1)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_build_catalog_preserves_metadata(self) -> None:
        """existing_db: depends_on, retry, icon — переносятся в новые записи."""
        import scanner
        import shutil
        root = self._make_software_dir({"chrome.exe": None})
        existing = {
            "ИНТЕРНЕТ И БРАУЗЕРЫ": [
                {
                    "name": "Google Chrome",  # кастомное имя
                    "cmd": "software\\chrome.exe --silent",
                    "icon": "icons/chrome.png",
                    "depends_on": ["Microsoft .NET Framework 4.8"],
                    "retry": 3,
                    "pre_cmd": "tweaks\\kill_chrome.bat",
                    "uninstall_cmd": "chrome.exe --uninstall",
                }
            ]
        }
        try:
            result = scanner.build_catalog_from_scan(
                software_dir=root, existing_db=existing
            )
            # Найти запись chrome.exe в результате
            all_entries = [p for progs in result.values() for p in progs]
            chrome_entry = next(p for p in all_entries if "chrome" in p["cmd"].lower())

            # Должны сохраниться все поля из existing
            self.assertEqual(chrome_entry["name"], "Google Chrome")
            self.assertEqual(chrome_entry["icon"], "icons/chrome.png")
            self.assertEqual(chrome_entry["depends_on"], ["Microsoft .NET Framework 4.8"])
            self.assertEqual(chrome_entry["retry"], 3)
            self.assertEqual(chrome_entry["pre_cmd"], "tweaks\\kill_chrome.bat")
            self.assertEqual(chrome_entry["uninstall_cmd"], "chrome.exe --uninstall")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_build_catalog_removes_missing_files(self) -> None:
        """Файлы которых нет на диске — выбрасываются из каталога."""
        import scanner
        import shutil
        root = self._make_software_dir({"keep.exe": None})
        existing = {
            "CAT": [
                {"name": "Keep", "cmd": "software\\keep.exe"},
                {"name": "Removed", "cmd": "software\\removed.exe"},
            ]
        }
        try:
            result = scanner.build_catalog_from_scan(
                software_dir=root, existing_db=existing
            )
            all_entries = [p for progs in result.values() for p in progs]
            names = {p["name"] for p in all_entries}
            self.assertIn("Keep", names)
            self.assertNotIn("Removed", names)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------------------
# Profiles
# ------------------------------------------------------------------
class TestProfiles(unittest.TestCase):
    def test_resolve_programs_found(self) -> None:
        import profiles
        db = {
            "cat": [
                {"name": "Chrome", "cmd": "c.exe"},
                {"name": "Telegram", "cmd": "t.exe"},
                {"name": "VLC", "cmd": "v.exe"},
            ]
        }
        profile = {"name": "test", "programs": ["Chrome", "VLC"]}
        found, missing = profiles.resolve_profile_programs(profile, db)
        self.assertEqual([p["name"] for p in found], ["Chrome", "VLC"])
        self.assertEqual(missing, [])

    def test_resolve_programs_missing(self) -> None:
        import profiles
        db = {"cat": [{"name": "Chrome", "cmd": "c.exe"}]}
        profile = {"name": "test", "programs": ["Chrome", "Phantom"]}
        found, missing = profiles.resolve_profile_programs(profile, db)
        self.assertEqual([p["name"] for p in found], ["Chrome"])
        self.assertEqual(missing, ["Phantom"])

    def test_resolve_case_insensitive(self) -> None:
        import profiles
        db = {"cat": [{"name": "Google Chrome", "cmd": "c.exe"}]}
        profile = {"name": "test", "programs": ["google chrome"]}
        found, _ = profiles.resolve_profile_programs(profile, db)
        self.assertEqual(len(found), 1)


# ------------------------------------------------------------------
# CLI: validate_cmd для системных команд (winget)
# ------------------------------------------------------------------
class TestWingetCommand(unittest.TestCase):
    def test_winget_command_valid(self) -> None:
        self.assertIsNone(core.validate_cmd("winget install --id Google.Chrome"))

    def test_choco_command_valid(self) -> None:
        self.assertIsNone(core.validate_cmd("choco install vlc -y"))

    def test_bare_unknown_rejected(self) -> None:
        """Голая команда без расширения и не в списке — отклоняется."""
        err = core.validate_cmd("rm -rf /")
        self.assertIsNotNone(err)

    def test_build_winget_cmd(self) -> None:
        args, script_path = core.build_cmd("winget install --id Google.Chrome --silent")
        self.assertEqual(args[0], "winget")
        self.assertEqual(script_path, "")  # нет файла для проверки


# ------------------------------------------------------------------
# CLI: resolve_targets
# ------------------------------------------------------------------
class TestCliResolveTargets(unittest.TestCase):
    def setUp(self) -> None:
        self.db = {
            "cat": [
                {"name": "Chrome", "cmd": "c.exe", "detect": {}},
                {"name": "Telegram", "cmd": "t.exe", "detect": {}},
                {"name": "VLC", "cmd": "v.exe", "detect": {}},
            ]
        }

    def test_install_all(self) -> None:
        import cli
        tasks, not_found = cli.resolve_targets("all", self.db, False, [])
        self.assertEqual(len(tasks), 3)
        self.assertEqual(not_found, [])

    def test_install_by_names(self) -> None:
        import cli
        tasks, not_found = cli.resolve_targets("Chrome, VLC", self.db, False, [])
        names = {t["name"] for t in tasks}
        self.assertEqual(names, {"Chrome", "VLC"})

    def test_install_not_found(self) -> None:
        import cli
        tasks, not_found = cli.resolve_targets("Chrome,Phantom", self.db, False, [])
        # При наличии хотя бы одной отсутствующей программы — возвращаем пустые tasks
        # и список ненайденных (только реально ненайденных)
        self.assertEqual(tasks, [])
        self.assertEqual(not_found, ["Phantom"])

    def test_missing_only_filter(self) -> None:
        import cli
        installed = [("Chrome", "100.0")]
        tasks, _ = cli.resolve_targets("all", self.db, True, installed)
        names = {t["name"] for t in tasks}
        self.assertEqual(names, {"Telegram", "VLC"})  # Chrome исключён


# ------------------------------------------------------------------
# i18n
# ------------------------------------------------------------------
class TestI18n(unittest.TestCase):
    def setUp(self) -> None:
        import i18n
        i18n.load_translations()

    def test_supported_languages_have_ru_en(self) -> None:
        import i18n
        self.assertIn("ru", i18n.SUPPORTED_LANGUAGES)
        self.assertIn("en", i18n.SUPPORTED_LANGUAGES)

    def test_t_returns_russian_by_default(self) -> None:
        import i18n
        i18n.set_language("ru")
        self.assertEqual(i18n.t("btn.install"), "▶ Установить")

    def test_t_returns_english(self) -> None:
        import i18n
        i18n.set_language("en")
        self.assertEqual(i18n.t("btn.install"), "▶ Install")

    def test_t_format_kwargs(self) -> None:
        import i18n
        i18n.set_language("ru")
        result = i18n.t("status.installed_count", installed=5, total=10)
        self.assertEqual(result, "Установлено: 5 из 10")

    def test_t_missing_key_returns_key(self) -> None:
        """Непереведённый ключ — возвращаем сам ключ (видно программисту)."""
        import i18n
        self.assertEqual(i18n.t("nonexistent.key.xyz"), "nonexistent.key.xyz")

    def test_t_fallback_to_russian(self) -> None:
        """Если ключа нет в en, но есть в ru — берём ru."""
        import i18n
        # Добавляем ключ только в ru, эмулируя неполный перевод
        i18n._translations.setdefault("ru", {})["test_only_ru"] = "Только русский"
        i18n._translations.setdefault("en", {}).pop("test_only_ru", None)
        i18n.set_language("en")
        self.assertEqual(i18n.t("test_only_ru"), "Только русский")

    def test_set_language_unknown_falls_back(self) -> None:
        import i18n
        i18n.set_language("zz")  # несуществующий
        self.assertEqual(i18n.get_language(), i18n.FALLBACK_LANGUAGE)

    def test_detect_system_language_returns_supported(self) -> None:
        import i18n
        lang = i18n.detect_system_language()
        # Результат должен быть либо поддерживаемым, либо fallback
        self.assertIn(lang, list(i18n.SUPPORTED_LANGUAGES.keys()) + [i18n.FALLBACK_LANGUAGE])

    def test_init_auto_returns_lang_code(self) -> None:
        import i18n
        lang = i18n.init(language="auto")
        self.assertIn(lang, i18n.SUPPORTED_LANGUAGES)


if __name__ == "__main__":
    unittest.main()
