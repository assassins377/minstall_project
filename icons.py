"""Асинхронная загрузка и кеширование иконок программ.

Загрузка bitmap из файла + Rescale до 16x16 блокирует UI на ~5-20 мс на иконку.
При 50+ программах это заметные тормоза при старте/фильтрации.

Этот модуль грузит иконки в фоновом потоке и через wx.CallAfter обновляет
ImageList, не блокируя UI.

Также умеет извлекать иконки из .exe файлов и кешировать их как PNG.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from typing import Callable

import wx

import config


ICON_CACHE_DIR = os.path.join(config.SCRIPT_DIR, "icons", "cache")


# ====================================================================
# Извлечение иконки из .exe
# ====================================================================

def _cache_path_for(exe_path: str) -> str:
    """Уникальный путь к кешу иконки для данного .exe (по хешу абс. пути)."""
    h = hashlib.sha1(exe_path.lower().encode("utf-8", errors="replace")).hexdigest()[:12]
    return os.path.join(ICON_CACHE_DIR, f"{h}.png")


def _extract_hicon_from_exe(exe_path: str) -> int | None:
    """
    Извлекает HICON через Windows API ExtractIconExW.

    Возвращает handle иконки (int) или None.
    Caller обязан освободить через DestroyIcon (но если передадим в wx — wx сам освободит).
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes, byref

        shell32 = ctypes.windll.shell32
        shell32.ExtractIconExW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int,
            ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON),
            wintypes.UINT,
        ]
        shell32.ExtractIconExW.restype = wintypes.UINT

        large = wintypes.HICON()
        count = shell32.ExtractIconExW(
            exe_path, 0,
            byref(large), None,
            1,
        )
        if count == 0 or not large.value:
            return None
        return int(large.value)
    except Exception as e:
        logging.debug(f"ExtractIconExW упал для {exe_path}: {e}")
        return None


def _hicon_to_bitmap(hicon: int, size: int = 32) -> wx.Bitmap | None:
    """Рендерит HICON в wx.Bitmap нужного размера через GDI."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32

        DI_NORMAL = 0x0003

        # Создаём временный DC и битмап в памяти
        screen_dc = user32.GetDC(0)
        try:
            mem_dc = gdi32.CreateCompatibleDC(screen_dc)
            try:
                hbmp = gdi32.CreateCompatibleBitmap(screen_dc, size, size)
                old = gdi32.SelectObject(mem_dc, hbmp)
                try:
                    # Заливаем фон прозрачным (заполняем чёрным — потом маска
                    # учтена через DrawIconEx с альфой)
                    rect = wintypes.RECT(0, 0, size, size)
                    bg_brush = gdi32.GetStockObject(5)  # BLACK_BRUSH
                    user32.FillRect(mem_dc, ctypes.byref(rect), bg_brush)
                    user32.DrawIconEx(mem_dc, 0, 0, hicon, size, size, 0, 0, DI_NORMAL)
                finally:
                    gdi32.SelectObject(mem_dc, old)

                # Превращаем HBITMAP в wx.Bitmap
                bmp = wx.Bitmap()
                bmp.SetHandle(hbmp)
                bmp.SetSize((size, size))
                bmp.SetDepth(32)
                return bmp if bmp.IsOk() else None
            finally:
                gdi32.DeleteDC(mem_dc)
        finally:
            user32.ReleaseDC(0, screen_dc)
    except Exception as e:
        logging.debug(f"HICON → bitmap упало: {e}")
        return None


def try_extract_exe_icon(exe_path: str) -> str | None:
    """
    Извлекает иконку из .exe файла, сохраняет как PNG в icons/cache/, возвращает путь.

    Если иконка уже в кеше — возвращает кешированный путь.
    Если что-то пошло не так (не .exe, нет иконки в exe, не Windows и т.п.) — None.
    """
    if not exe_path or not os.path.exists(exe_path):
        return None
    if not exe_path.lower().endswith(".exe"):
        return None

    cache_path = _cache_path_for(exe_path)

    # Иконка уже извлечена и .exe не обновлялся после кеширования
    if os.path.exists(cache_path):
        try:
            if os.path.getmtime(cache_path) >= os.path.getmtime(exe_path):
                return cache_path
        except OSError:
            return cache_path

    hicon = _extract_hicon_from_exe(exe_path)
    if not hicon:
        return None

    try:
        bmp = _hicon_to_bitmap(hicon, size=32)
    finally:
        # DestroyIcon — освобождаем HICON в любом случае
        try:
            import ctypes
            ctypes.windll.user32.DestroyIcon(hicon)
        except Exception:
            pass

    if bmp is None or not bmp.IsOk():
        return None

    try:
        os.makedirs(ICON_CACHE_DIR, exist_ok=True)
        img = bmp.ConvertToImage()
        if img.SaveFile(cache_path, wx.BITMAP_TYPE_PNG):
            return cache_path
    except Exception as e:
        logging.debug(f"Сохранение иконки {cache_path} упало: {e}")

    return None


def resolve_program_icon(program: dict, software_dir_resolver) -> str | None:
    """
    Возвращает лучший путь к иконке для программы.

    Логика:
      1. Если program["icon"] указывает на существующий файл — используем.
      2. Иначе пытаемся извлечь иконку из .exe в program["cmd"].
      3. Иначе — None (caller подставит system.png).

    software_dir_resolver — функция типа core.resolve_path для преобразования
    относительных путей в абсолютные.
    """
    # 1. Явный icon из programs.json (если файл существует)
    icon_path = program.get("icon", "")
    if icon_path:
        abs_path = software_dir_resolver(icon_path)
        # Системная заглушка system.png пропускаем — пробуем найти что-то лучше
        if os.path.exists(abs_path) and not abs_path.endswith("system.png"):
            return abs_path

    # 2. Извлечение из .exe
    cmd_str = program.get("cmd", "")
    if cmd_str:
        import shlex
        try:
            parts = shlex.split(cmd_str, posix=False)
            if parts:
                exe_abs = software_dir_resolver(parts[0])
                extracted = try_extract_exe_icon(exe_abs)
                if extracted:
                    return extracted
        except Exception:
            pass

    # 3. Возвращаем system.png если он указан в icon, иначе None
    if icon_path:
        abs_path = software_dir_resolver(icon_path)
        if os.path.exists(abs_path):
            return abs_path

    return None


class IconLoader:
    """Асинхронный загрузчик иконок с кешем."""

    def __init__(self, image_list: wx.ImageList, on_loaded: Callable[[str, int], None]) -> None:
        """
        image_list   — wx.ImageList куда добавляются загруженные иконки
        on_loaded(path, image_index) — колбэк, вызывается в UI-потоке когда иконка готова
        """
        self._image_list = image_list
        self._on_loaded = on_loaded
        self._cache: dict[str, int] = {}      # path -> index в ImageList
        self._loading: set[str] = set()       # пути в процессе загрузки
        self._lock = threading.Lock()
        self._queue: list[str] = []
        self._worker_thread: threading.Thread | None = None

    def get_or_load(self, icon_path: str) -> int | None:
        """
        Возвращает индекс иконки в ImageList если она уже загружена.
        Иначе ставит её в очередь на фоновую загрузку и возвращает None.
        Колбэк on_loaded будет вызван когда иконка будет готова.
        """
        if not icon_path or not os.path.exists(icon_path):
            return None

        with self._lock:
            if icon_path in self._cache:
                return self._cache[icon_path]
            if icon_path in self._loading:
                return None
            self._loading.add(icon_path)
            self._queue.append(icon_path)
            self._ensure_worker()

        return None

    def _ensure_worker(self) -> None:
        """Запускает воркер, если он ещё не запущен."""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """Фоновая обработка очереди — декодирует и масштабирует bitmap."""
        while True:
            with self._lock:
                if not self._queue:
                    return
                path = self._queue.pop(0)

            try:
                # Декодирование можно делать в фоне, но создание wx.Bitmap
                # требует GUI thread на некоторых платформах. Мы используем
                # wx.Image — он thread-safe для чтения файла.
                img = wx.Image(path, wx.BITMAP_TYPE_ANY)
                if not img.IsOk():
                    logging.warning(f"Не удалось декодировать иконку: {path}")
                    with self._lock:
                        self._loading.discard(path)
                    continue
                img.Rescale(16, 16, wx.IMAGE_QUALITY_HIGH)
            except Exception as e:
                logging.warning(f"Ошибка загрузки иконки {path}: {e}")
                with self._lock:
                    self._loading.discard(path)
                continue

            # Финальные шаги (Add в ImageList и вызов колбэка) — в UI-потоке,
            # т.к. они трогают wx-объекты которые на macOS/Windows требуют main thread
            wx.CallAfter(self._finalize, path, img)

    def _finalize(self, path: str, img: wx.Image) -> None:
        """Вызывается в UI-потоке: добавляет bitmap в ImageList и нотифицирует."""
        with self._lock:
            self._loading.discard(path)
            if path in self._cache:
                # Другой вызов уже добавил — выходим
                return
            try:
                index = self._image_list.Add(wx.Bitmap(img))
                self._cache[path] = index
            except Exception as e:
                logging.warning(f"Не удалось добавить иконку в ImageList {path}: {e}")
                return

        try:
            self._on_loaded(path, index)
        except Exception as e:
            logging.warning(f"Колбэк on_loaded упал для {path}: {e}")
