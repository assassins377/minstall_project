"""Встроенная панель просмотра install.log с live-обновлением.

Используется по запросу пользователя через меню «Справка → Показать лог».
Поллинг файла раз в LOG_POLL_INTERVAL_MS — никаких inotify/ReadDirectoryChanges,
для маленьких логов этого достаточно.
"""
from __future__ import annotations

import os

import wx

import config


LOG_POLL_INTERVAL_MS = 500  # как часто проверять файл (мс)
MAX_INITIAL_LOAD_BYTES = 256 * 1024  # при открытии показываем последние 256 КБ


class LogPanel(wx.Panel):
    """Панель просмотра лога с поллингом и автоскроллом."""

    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent)
        self.SetBackgroundColour(wx.Colour(245, 245, 245))

        # Курсор в файле — сколько байт уже прочитали (для инкрементного чтения)
        self._read_offset: int = 0
        self._last_known_size: int = 0

        sizer = wx.BoxSizer(wx.VERTICAL)

        # Заголовок
        header_sizer = wx.BoxSizer(wx.HORIZONTAL)
        title = wx.StaticText(self, label=f"Лог: {config.LOG_FILE}")
        title.SetForegroundColour(wx.Colour(100, 100, 100))
        font = wx.Font(8, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        title.SetFont(font)
        header_sizer.Add(title, 1, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)

        btn_clear = wx.Button(self, label="Очистить", size=(80, 22))
        btn_clear.Bind(wx.EVT_BUTTON, self._on_clear)
        header_sizer.Add(btn_clear, 0, wx.RIGHT, 5)

        btn_open_ext = wx.Button(self, label="Внешний редактор", size=(130, 22))
        btn_open_ext.Bind(wx.EVT_BUTTON, self._on_open_external)
        header_sizer.Add(btn_open_ext, 0, wx.RIGHT, 5)

        sizer.Add(header_sizer, 0, wx.EXPAND | wx.BOTTOM, 3)

        # Текстовое поле с логом
        self.text_ctrl = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP | wx.HSCROLL,
        )
        self.text_ctrl.SetFont(
            wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        )
        self.text_ctrl.SetBackgroundColour(wx.Colour(255, 255, 255))
        sizer.Add(self.text_ctrl, 1, wx.EXPAND)

        self.SetSizer(sizer)

        # Таймер опроса файла
        self._timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_timer, self._timer)

    def start(self) -> None:
        """Запустить опрос — вызывается когда панель показывается."""
        self._initial_load()
        self._timer.Start(LOG_POLL_INTERVAL_MS)

    def stop(self) -> None:
        """Остановить опрос — вызывается когда панель скрывается."""
        self._timer.Stop()

    def _initial_load(self) -> None:
        """Первичная загрузка — показываем хвост файла (последние MAX_INITIAL_LOAD_BYTES)."""
        if not os.path.exists(config.LOG_FILE):
            self.text_ctrl.SetValue("(лог-файл ещё не создан)\n")
            self._read_offset = 0
            self._last_known_size = 0
            return

        try:
            size = os.path.getsize(config.LOG_FILE)
            with open(config.LOG_FILE, "rb") as f:
                if size > MAX_INITIAL_LOAD_BYTES:
                    f.seek(size - MAX_INITIAL_LOAD_BYTES)
                    # Пропускаем первую строку — может быть обрезана
                    f.readline()
                content = f.read()
            text = content.decode("utf-8", errors="replace")
            if size > MAX_INITIAL_LOAD_BYTES:
                text = f"... (показаны последние {MAX_INITIAL_LOAD_BYTES // 1024} КБ из {size // 1024} КБ) ...\n" + text
            self.text_ctrl.SetValue(text)
            self._read_offset = size
            self._last_known_size = size
            self._scroll_to_end()
        except Exception as e:
            self.text_ctrl.SetValue(f"Не удалось прочитать лог: {e}\n")

    def _on_timer(self, event: wx.TimerEvent) -> None:
        """Поллинг файла — если вырос, дочитываем хвост."""
        if not os.path.exists(config.LOG_FILE):
            return
        try:
            size = os.path.getsize(config.LOG_FILE)
        except OSError:
            return

        if size == self._last_known_size:
            return  # нет изменений

        if size < self._last_known_size:
            # Файл был обрезан/пересоздан — перечитываем с начала
            self._initial_load()
            return

        # Файл вырос — читаем только новое
        try:
            with open(config.LOG_FILE, "rb") as f:
                f.seek(self._read_offset)
                new_bytes = f.read()
            text = new_bytes.decode("utf-8", errors="replace")
            self.text_ctrl.AppendText(text)
            self._read_offset = size
            self._last_known_size = size
            self._scroll_to_end()
        except Exception:
            pass

    def _scroll_to_end(self) -> None:
        """Прокручивает к концу — чтобы видны были последние строки."""
        self.text_ctrl.ShowPosition(self.text_ctrl.GetLastPosition())

    def _on_clear(self, event: wx.CommandEvent) -> None:
        """Очищает отображение (не сам файл)."""
        self.text_ctrl.Clear()

    def _on_open_external(self, event: wx.CommandEvent) -> None:
        """Открывает лог в системном редакторе."""
        if os.path.exists(config.LOG_FILE):
            try:
                os.startfile(config.LOG_FILE)
            except Exception as e:
                wx.MessageBox(f"Не удалось открыть лог:\n{e}",
                              "Ошибка", wx.OK | wx.ICON_WARNING)
