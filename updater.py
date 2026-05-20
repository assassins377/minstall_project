from __future__ import annotations

import os
import struct
import sys
import json
import hashlib
import subprocess
import threading
import urllib.request
import logging
from typing import Callable

import config
import core


# --- GitHub Releases API ---
GITHUB_REPO = "assassins377/minstall_project"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

USER_AGENT = f"MInstAll/{config.APP_VERSION}"


def current_arch() -> str:
    """Возвращает 'x64' или 'x86' в зависимости от разрядности текущего процесса.

    Важно: смотрим именно на текущий Python/exe, а не на ОС — потому что 32-битный
    exe может бежать на 64-битной системе, и обновлять его надо тем же x86.
    """
    return "x64" if struct.calcsize("P") == 8 else "x86"


def exe_asset_name() -> str:
    """Имя .exe ассета в Release для текущей архитектуры."""
    return f"MInstAll_{current_arch()}.exe"


def sha256_asset_name() -> str:
    """Имя .sha256 ассета для текущей архитектуры."""
    return f"{exe_asset_name()}.sha256"


# ------------------------------------------------------------------
# Проверка обновлений через GitHub Releases API
# ------------------------------------------------------------------
def _fetch_json(url: str, timeout: int = 5) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_text(url: str, timeout: int = 5) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8").strip()


def check_for_updates(current_version: str | None = None) -> dict:
    """
    Запрашивает GitHub Releases API. Возвращает dict:
      {"has_update": bool, "latest": str, "url": str, "sha256": str | None,
       "size": int | None, "notes": str}
    или {"error": str} при сбое.

    Если в Release есть файл .sha256 — он скачивается для верификации.
    Если нет — SHA-256 верификация пропускается (с предупреждением в логе).
    """
    current = current_version or config.APP_VERSION

    try:
        release = _fetch_json(RELEASES_API_URL, timeout=5)
    except Exception as e:
        logging.warning(f"Не удалось получить релизы с GitHub: {e}")
        return {"error": str(e)}

    # Тег вида "v2.1.0" → "2.1.0"
    tag = str(release.get("tag_name", "")).lstrip("v")
    if not tag:
        return {"error": "Не удалось определить версию релиза (отсутствует tag_name)"}

    notes = release.get("body", "") or ""
    assets = release.get("assets", []) or []

    # Динамическое имя по архитектуре текущего процесса (x86 / x64)
    exe_name = exe_asset_name()
    sha_name = sha256_asset_name()

    # Ищем основной .exe
    exe_asset = next((a for a in assets if a.get("name") == exe_name), None)
    if not exe_asset:
        return {"error": f"В релизе v{tag} нет файла {exe_name}"}

    exe_url = exe_asset.get("browser_download_url")
    exe_size = exe_asset.get("size")
    if not exe_url:
        return {"error": f"У файла {exe_name} нет ссылки на скачивание"}

    # Опционально: .sha256 — отдельный файл с хешем
    sha256: str | None = None
    sha_asset = next((a for a in assets if a.get("name") == sha_name), None)
    if sha_asset and (sha_url := sha_asset.get("browser_download_url")):
        try:
            sha_text = _fetch_text(sha_url, timeout=5)
            # Формат может быть: "abc123..." или "abc123...  MInstAll_x86.exe"
            sha256 = sha_text.split()[0].lower()
        except Exception as e:
            logging.warning(f"Не удалось прочитать {sha_name}: {e}")

    if not sha256:
        logging.warning(
            f"В релизе v{tag} нет {sha_name} — обновление пройдёт без верификации SHA-256"
        )

    has_update = core.compare_versions(tag, current) > 0
    return {
        "has_update": has_update,
        "latest": tag,
        "url": exe_url,
        "sha256": sha256,
        "size": exe_size,
        "notes": notes,
    }


def check_for_updates_async(callback: Callable[[dict], None]) -> None:
    """
    Асинхронная обёртка. callback вызывается из фонового потока —
    GUI должен маршалить в UI-поток сам.
    """
    def _worker() -> None:
        callback(check_for_updates())
    threading.Thread(target=_worker, daemon=True).start()


# ------------------------------------------------------------------
# Скачивание и применение обновления
# ------------------------------------------------------------------
def _download_with_progress(
    url: str,
    dst_path: str,
    expected_size: int | None,
    callback: Callable[[dict], None],
) -> str:
    """Скачивает файл чанками, считает SHA-256 на лету. Возвращает SHA-256."""
    sha = hashlib.sha256()
    downloaded = 0

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=config.DOWNLOAD_TIMEOUT) as resp:
        total = expected_size
        try:
            cl = resp.headers.get("Content-Length")
            if cl:
                total = int(cl)
        except Exception:
            pass

        with open(dst_path, "wb") as out:
            while True:
                chunk = resp.read(config.DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                downloaded += len(chunk)
                if total:
                    callback({"type": "progress", "percent": int(downloaded * 100 / total)})

    return sha.hexdigest().lower()


def download_and_update(
    update_info: dict,
    callback: Callable[[dict], None] | None = None,
) -> bool:
    """
    Скачивает обновление, проверяет SHA-256 (если он есть в update_info),
    формирует BAT для атомарной замены, запускает его и завершает процесс.
    """
    def emit(msg: dict) -> None:
        if callback:
            try:
                callback(msg)
            except Exception:
                pass

    if not getattr(sys, "frozen", False):
        emit({"type": "error", "text": "Обновление работает только для собранного .exe"})
        return False

    current_exe = sys.executable
    exe_dir = os.path.dirname(current_exe)
    new_exe_path = current_exe + ".new"
    bak_exe_path = current_exe + ".bak"
    bat_path = os.path.join(os.environ.get("TEMP", exe_dir), "minstall_updater.bat")

    for stale in (new_exe_path, bak_exe_path):
        try:
            if os.path.exists(stale):
                os.remove(stale)
        except OSError:
            pass

    try:
        emit({"type": "status", "text": "Скачивание обновления..."})
        actual_sha = _download_with_progress(
            update_info["url"], new_exe_path, update_info.get("size"), emit
        )

        # Верификация SHA-256 — только если ожидаемый хеш известен
        expected_sha = update_info.get("sha256")
        if expected_sha:
            if actual_sha != expected_sha.lower():
                logging.error(
                    f"SHA-256 не совпадает. Ожидалось {expected_sha}, получено {actual_sha}"
                )
                try:
                    os.remove(new_exe_path)
                except OSError:
                    pass
                emit({"type": "error",
                      "text": "Контрольная сумма не совпадает. Файл повреждён или подменён."})
                return False
            logging.info(f"Обновление скачано и проверено: SHA-256 {actual_sha}")
        else:
            logging.warning(
                f"SHA-256 верификация пропущена (хеш не предоставлен). Загружено: {actual_sha}"
            )

        emit({"type": "status", "text": "Применение обновления..."})

        # PID текущего процесса — BAT будет ждать пока он не завершится,
        # вместо хрупкого ping-таймаута. Максимум — 60 секунд защиты от вечного цикла.
        current_pid = os.getpid()

        bat_content = f"""@echo off
chcp 866 > nul
echo Обновление MInstAll...

REM Ждём пока текущий процесс завершится (max 60 сек защита от deadlock)
set /a "waited=0"
:wait_loop
tasklist /FI "PID eq {current_pid}" /NH 2>nul | findstr /R /C:"^.* {current_pid} " >nul
if errorlevel 1 goto proceed
if %waited% GEQ 60 (
    echo Процесс {current_pid} не завершился за 60 секунд, прерываем
    del /Q "{new_exe_path}" >nul 2>&1
    pause
    exit /b 1
)
timeout /t 1 /nobreak >nul
set /a "waited+=1"
goto wait_loop

:proceed
move /Y "{current_exe}" "{bak_exe_path}" >nul 2>&1
if errorlevel 1 (
    echo Не удалось создать резервную копию.
    del /Q "{new_exe_path}" >nul 2>&1
    pause
    exit /b 1
)

move /Y "{new_exe_path}" "{current_exe}" >nul 2>&1
if errorlevel 1 (
    echo Не удалось применить обновление, откат.
    move /Y "{bak_exe_path}" "{current_exe}" >nul 2>&1
    pause
    exit /b 1
)

del /Q "{bak_exe_path}" >nul 2>&1
start "" "{current_exe}"
del "%~f0"
"""
        with open(bat_path, "w", encoding="cp866", errors="replace") as f:
            f.write(bat_content)

        subprocess.Popen(
            ["cmd", "/c", bat_path],
            creationflags=config.CREATE_NO_WINDOW,
            cwd=exe_dir,
        )

        emit({"type": "done"})
        logging.info("Передача управления updater.bat. Завершение работы.")
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as e:
        logging.exception(f"Ошибка при обновлении: {e}")
        try:
            if os.path.exists(new_exe_path):
                os.remove(new_exe_path)
        except OSError:
            pass
        emit({"type": "error", "text": f"Ошибка: {e}"})
        return False
