from __future__ import annotations

import os
import sys
import subprocess

import wx
import wx.lib.agw.customtreectrl as CT
import wx.adv

import config
import core
import i18n
import icons
import log_panel
import profiles
import scanner
import state
import stats

# Сокращение для t() — чтобы не пухло в коде
_ = i18n.t


SEVERITY_COLORS: dict[str, wx.Colour] = {
    "info":     wx.Colour(100, 100, 100),
    "progress": wx.Colour(0, 0, 255),
    "warn":     wx.Colour(255, 140, 0),
    "error":    wx.Colour(255, 0, 0),
    "success":  wx.Colour(0, 128, 0),
}


class MInstAllFrame(wx.Frame):
    DEFAULT_SIZE = (820, 650)
    MIN_SIZE = (600, 450)

    def __init__(self) -> None:
        self._state = state.load_state()
        win = self._state.get("window", {})
        size = (win.get("width", self.DEFAULT_SIZE[0]),
                win.get("height", self.DEFAULT_SIZE[1]))

        super().__init__(None, title=_("app.title"), size=size)
        self.SetMinSize(self.MIN_SIZE)

        # Восстановление позиции (если есть и попадает в видимый экран)
        if "x" in win and "y" in win:
            pos = wx.Point(win["x"], win["y"])
            if self._is_position_visible(pos, size):
                self.SetPosition(pos)
            else:
                self.Centre()
        else:
            self.Centre()

        # Восстановление развёрнутого состояния
        if win.get("maximized"):
            self.Maximize(True)

        if os.path.exists(config.ICON_FILE):
            try:
                icon = wx.Icon()
                icon.CopyFromBitmap(wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY))
                self.SetIcon(icon)
            except Exception:
                pass

        self.programs_db: dict[str, list[dict]] = core.load_programs_from_json()

        prefs_early = self._state.get("prefs", {})
        self._installed_cache_enabled = prefs_early.get("installed_cache", True)
        # По умолчанию: генерируем каталог из содержимого software/.
        # programs.json используется только как источник метаданных (depends_on,
        # retry, icon, desc, pre_cmd, ...) для файлов которые реально есть на диске.
        self._gen_from_scan = prefs_early.get("gen_from_scan", True)
        self._autoscan_enabled = prefs_early.get("autoscan", True)
        self._hide_missing = prefs_early.get("hide_missing", False)
        self._last_scan_new: list[dict] = []
        self._catalog_dirty = False

        if self._gen_from_scan:
            # Режим "только software/" — полная регенерация каталога.
            # Используем existing_db чтобы сохранить depends_on, retry, icons и т.д.
            self.programs_db = scanner.build_catalog_from_scan(
                existing_db=self.programs_db,
            )
            self._catalog_dirty = True
        elif self._autoscan_enabled:
            # Режим "добавлять новое" — программы из JSON остаются, новые из папки добавляются
            self.programs_db, self._last_scan_new = scanner.scan_and_merge(self.programs_db)
            if self._last_scan_new:
                self._catalog_dirty = True

        self.installed_names: list[tuple[str, str]] = core.get_installed_programs(
            state_dict=self._state, use_cache=self._installed_cache_enabled,
        )
        self.status_cache: dict[str, tuple[str, str]] = core.build_status_cache(
            self.programs_db, self.installed_names
        )
        self.worker: core.InstallWorker | None = None
        self.tree_data: dict = {}
        self._closing = False

        # Единственный таймер debounce поиска — переиспользуется при каждом keystroke
        self._search_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_search_timer, self._search_timer)

        # Системная статус-строка снизу окна — wxPython автоматически
        # выводит туда help-текст пунктов меню при наведении
        self.CreateStatusBar(1)
        self.SetStatusText("")

        self.create_menu()
        self.init_ui()
        self.populate_tree()
        self._restore_session()
        self.Bind(wx.EVT_CLOSE, self.on_closing)

        # Watcher для software/: периодически проверяет изменения.
        # Интервал хранится в state.json в миллисекундах (0 = выключен).
        self._watcher_interval_ms = prefs_early.get(
            "watcher_interval_ms",
            config.WATCHER_POLL_INTERVAL_MS if config.WATCHER_ENABLED else 0,
        )
        self._dir_snapshot = scanner.directory_snapshot()
        self._watcher_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self._on_watcher_tick, self._watcher_timer)
        if self._watcher_interval_ms > 0:
            self._watcher_timer.Start(self._watcher_interval_ms)

    # --------------------------------------------------------------
    # Меню
    # --------------------------------------------------------------
    def create_menu(self) -> None:
        menubar = wx.MenuBar()

        # --- Меню "Настройки" ---
        settings_menu = wx.Menu()
        self._parallel_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.parallel"), _("menu.parallel.tooltip")
        )
        prefs = self._state.get("prefs", {})
        self._parallel_enabled = prefs.get("parallel_install", config.PARALLEL_INSTALL_ENABLED)
        self._parallel_menu_item.Check(self._parallel_enabled)
        self.Bind(wx.EVT_MENU, self._on_toggle_parallel, self._parallel_menu_item)

        settings_menu.AppendSeparator()

        # Подменю "Язык"
        lang_menu = wx.Menu()
        current_lang_pref = prefs.get("language", "auto")
        self._lang_radio_items: dict[str, wx.MenuItem] = {}

        # "Автоматически"
        auto_item = lang_menu.AppendRadioItem(wx.ID_ANY, _("menu.language.auto"))
        self._lang_radio_items["auto"] = auto_item
        self.Bind(wx.EVT_MENU, lambda e: self._on_set_language("auto"), auto_item)

        lang_menu.AppendSeparator()

        # По одному пункту на каждый поддерживаемый язык
        for code, name in i18n.SUPPORTED_LANGUAGES.items():
            item = lang_menu.AppendRadioItem(wx.ID_ANY, name)
            self._lang_radio_items[code] = item
            self.Bind(wx.EVT_MENU, lambda e, c=code: self._on_set_language(c), item)

        # Отмечаем текущий выбор
        if current_lang_pref in self._lang_radio_items:
            self._lang_radio_items[current_lang_pref].Check(True)

        # Автосканирование (добавлять новое из software/ к programs.json)
        self._autoscan_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.autoscan"), _("menu.autoscan.tooltip")
        )
        self._autoscan_menu_item.Check(self._autoscan_enabled)
        self.Bind(wx.EVT_MENU, self._on_toggle_autoscan, self._autoscan_menu_item)

        # Полная регенерация каталога из software/ (режим "доверяем папке")
        self._gen_from_scan_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.gen_from_scan"), _("menu.gen_from_scan.tooltip")
        )
        self._gen_from_scan_menu_item.Check(self._gen_from_scan)
        self.Bind(wx.EVT_MENU, self._on_toggle_gen_from_scan, self._gen_from_scan_menu_item)

        # Скрывать программы с отсутствующим файлом инсталлятора
        self._hide_missing_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.hide_missing"), _("menu.hide_missing.tooltip")
        )
        self._hide_missing_menu_item.Check(self._hide_missing)
        self.Bind(wx.EVT_MENU, self._on_toggle_hide_missing, self._hide_missing_menu_item)

        # Подменю "Слежение за software/" с радио-кнопками интервалов
        watcher_menu = wx.Menu()
        self._watcher_radio_items: dict[int, wx.MenuItem] = {}
        for interval_ms in config.WATCHER_INTERVALS_MS:
            if interval_ms == 0:
                label = _("menu.watcher.off")
            else:
                label = _("menu.watcher.interval_sec", n=interval_ms // 1000)
            item = watcher_menu.AppendRadioItem(wx.ID_ANY, label)
            self._watcher_radio_items[interval_ms] = item
            self.Bind(wx.EVT_MENU,
                      lambda e, ms=interval_ms: self._on_set_watcher_interval(ms),
                      item)
        # Отмечаем текущий выбор
        if self._watcher_interval_ms in self._watcher_radio_items:
            self._watcher_radio_items[self._watcher_interval_ms].Check(True)
        settings_menu.AppendSubMenu(watcher_menu, _("menu.watcher"))

        # Кеш установленных программ
        self._installed_cache_menu_item = settings_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.installed_cache"), _("menu.installed_cache.tooltip")
        )
        self._installed_cache_menu_item.Check(self._installed_cache_enabled)
        self.Bind(wx.EVT_MENU, self._on_toggle_installed_cache,
                  self._installed_cache_menu_item)

        settings_menu.AppendSeparator()

        rescan_item = settings_menu.Append(wx.ID_ANY, _("menu.rescan"), _("menu.rescan.tooltip"))
        self.Bind(wx.EVT_MENU, self._on_rescan, rescan_item)

        save_catalog_item = settings_menu.Append(
            wx.ID_ANY, _("menu.save_catalog"), _("menu.save_catalog.tooltip")
        )
        self.Bind(wx.EVT_MENU, self._on_save_catalog, save_catalog_item)

        settings_menu.AppendSubMenu(lang_menu, _("menu.language"))
        menubar.Append(settings_menu, _("menu.settings"))

        # --- Меню "Профили" ---
        profiles_menu = wx.Menu()
        loaded_profiles = profiles.list_profiles()
        if not loaded_profiles:
            empty = profiles_menu.Append(wx.ID_ANY, _("menu.profile.empty"))
            empty.Enable(False)
        else:
            for profile in loaded_profiles:
                # Считаем сколько программ из профиля реально доступно
                found, _missing = profiles.resolve_profile_programs(
                    profile, self.programs_db,
                )
                total = len(profile.get("programs", []))
                avail = len(found)

                # Имя в меню — с количеством "Developer (5)" или "Developer (3/5)"
                if avail < total:
                    label = _("profile.menu_label_with_avail",
                              name=profile["name"], avail=avail, count=total)
                else:
                    label = _("profile.menu_label",
                              name=profile["name"], count=total)

                # Help (показывается в статус-строке при наведении в меню):
                # описание + список программ
                desc = profile.get("description", "")
                programs_preview = ", ".join(profile.get("programs", [])[:5])
                if len(profile.get("programs", [])) > 5:
                    programs_preview += "…"
                help_text = f"{desc} — {programs_preview}" if desc else programs_preview

                item = profiles_menu.Append(wx.ID_ANY, label, help_text)
                self.Bind(wx.EVT_MENU,
                          lambda e, p=profile: self._on_apply_profile(p), item)
        menubar.Append(profiles_menu, _("menu.profiles"))

        # --- Меню "Справка" ---
        help_menu = wx.Menu()
        update_item = help_menu.Append(wx.ID_ANY, _("menu.check_updates"),
                                       _("menu.check_updates.tooltip"))
        self.Bind(wx.EVT_MENU, self.on_check_update, update_item)

        self._log_menu_item = help_menu.AppendCheckItem(
            wx.ID_ANY, _("menu.log"), _("menu.log.tooltip"),
        )
        self.Bind(wx.EVT_MENU, self._on_toggle_log, self._log_menu_item)

        help_menu.AppendSeparator()
        about_item = help_menu.Append(wx.ID_ABOUT, _("menu.about"),
                                      _("menu.about.tooltip"))
        self.Bind(wx.EVT_MENU, self.on_about, about_item)
        menubar.Append(help_menu, _("menu.help"))

        self.SetMenuBar(menubar)

    def _on_toggle_parallel(self, event: wx.CommandEvent) -> None:
        self._parallel_enabled = self._parallel_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["parallel_install"] = self._parallel_enabled
        state.save_state(self._state)
        mode = _("status.mode_parallel") if self._parallel_enabled else _("status.mode_sequential")
        self._set_status(_("status.parallel_mode", mode=mode), "info")

    def _on_toggle_autoscan(self, event: wx.CommandEvent) -> None:
        self._autoscan_enabled = self._autoscan_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["autoscan"] = self._autoscan_enabled
        state.save_state(self._state)

    def _on_toggle_hide_missing(self, event: wx.CommandEvent) -> None:
        """Переключает фильтр скрытия программ без файлов."""
        self._hide_missing = self._hide_missing_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["hide_missing"] = self._hide_missing
        state.save_state(self._state)
        self.populate_tree(self.search_ctrl.GetValue())

    def _on_set_watcher_interval(self, interval_ms: int) -> None:
        """Меняет интервал поллинга watcher'а (или выключает)."""
        self._watcher_interval_ms = interval_ms
        prefs = self._state.setdefault("prefs", {})
        prefs["watcher_interval_ms"] = interval_ms
        state.save_state(self._state)
        if self._watcher_timer.IsRunning():
            self._watcher_timer.Stop()
        if interval_ms > 0:
            self._watcher_timer.Start(interval_ms)

    def _on_toggle_installed_cache(self, event: wx.CommandEvent) -> None:
        """Переключает кеш списка установленных программ."""
        self._installed_cache_enabled = self._installed_cache_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["installed_cache"] = self._installed_cache_enabled
        # Если выключили — сразу сбросим существующий кеш
        if not self._installed_cache_enabled:
            core.invalidate_installed_cache(self._state)
        state.save_state(self._state)

    def _on_toggle_gen_from_scan(self, event: wx.CommandEvent) -> None:
        """Полная регенерация каталога из software/ при следующем запуске."""
        self._gen_from_scan = self._gen_from_scan_menu_item.IsChecked()
        prefs = self._state.setdefault("prefs", {})
        prefs["gen_from_scan"] = self._gen_from_scan
        state.save_state(self._state)

        # Применить сразу — пересобрать каталог
        if self._gen_from_scan:
            self.programs_db = scanner.build_catalog_from_scan(
                existing_db=self.programs_db,
            )
            self.status_cache = core.build_status_cache(self.programs_db, self.installed_names)
            self.populate_tree(self.search_ctrl.GetValue())
            self._catalog_dirty = True
            total = sum(len(v) for v in self.programs_db.values())
            self._set_status(_("scan.saved", count=total).replace("сохранён", "перегенерирован"),
                              "success")

    def _on_watcher_tick(self, event: wx.TimerEvent) -> None:
        """Раз в WATCHER_POLL_INTERVAL_MS проверяем не изменилось ли содержимое software/."""
        if self.worker and self.worker.is_alive():
            # Не сканируем во время установки — лишний шум в UI
            return

        current = scanner.directory_snapshot()
        if current == self._dir_snapshot:
            return
        self._dir_snapshot = current

        # Содержимое изменилось — пересобираем каталог
        if self._gen_from_scan:
            self.programs_db = scanner.build_catalog_from_scan(
                existing_db=core.load_programs_from_json(),
            )
        else:
            self.programs_db, _ = scanner.scan_and_merge(
                core.load_programs_from_json(),
            )

        self.status_cache = core.build_status_cache(self.programs_db, self.installed_names)
        self.populate_tree(self.search_ctrl.GetValue())
        self._update_selection_counter()

    def _on_rescan(self, event: wx.CommandEvent) -> None:
        """Запуск сканирования по нажатию пункта меню."""
        self.programs_db, new_entries = scanner.scan_and_merge(self.programs_db)
        self._last_scan_new = new_entries

        if not new_entries:
            self._set_status(_("scan.no_new"), "info")
            return

        # Перестраиваем кеш статусов и дерево
        self.status_cache = core.build_status_cache(self.programs_db, self.installed_names)
        self._catalog_dirty = True
        self.populate_tree(self.search_ctrl.GetValue())

        # Показываем что нашли
        self._set_status(_("scan.new_found", count=len(new_entries)), "success")
        names_by_cat: dict[str, list[str]] = {}
        for entry in new_entries:
            names_by_cat.setdefault(entry["_category"], []).append(entry["name"])
        lines = [f"[{cat}]\n  • " + "\n  • ".join(names) for cat, names in names_by_cat.items()]
        wx.MessageBox(
            "\n\n".join(lines),
            _("scan.new_list_title"),
            wx.OK | wx.ICON_INFORMATION,
        )

    def _on_save_catalog(self, event: wx.CommandEvent) -> None:
        """Сохраняет текущий programs_db в programs.json."""
        if scanner.save_merged_to_disk(self.programs_db):
            total = sum(len(v) for v in self.programs_db.values())
            self._set_status(_("scan.saved", count=total), "success")
            self._catalog_dirty = False
        else:
            self._set_status(_("scan.save_failed"), "error")
            wx.MessageBox(_("scan.save_failed"), "Error", wx.OK | wx.ICON_ERROR)

    def _on_toggle_log(self, event: wx.CommandEvent) -> None:
        """Показывает/скрывает лог-панель в нижней части окна."""
        show = self._log_menu_item.IsChecked()
        if show:
            # Сплит снизу, занимая 35% высоты
            total_h = self._splitter.GetSize().height
            sash = max(150, int(total_h * 0.65))
            self._log_panel.Show()
            self._splitter.SplitHorizontally(self.tree, self._log_panel, sash)
            self._log_panel.start()
        else:
            self._log_panel.stop()
            self._splitter.Unsplit(self._log_panel)
            self._log_panel.Hide()

    def _on_apply_profile(self, profile: dict) -> None:
        """Применяет профиль: отмечает все программы из его списка."""
        found, missing = profiles.resolve_profile_programs(profile, self.programs_db)
        found_names = {p["name"] for p in found}

        # Снимаем все галочки и ставим на программы из профиля
        for item, data in self.tree_data.items():
            self.tree.CheckItem(item, data["name"] in found_names)

        self._set_status(
            _("profile.applied", name=profile["name"], count=len(found_names)),
            "info",
        )
        self._update_selection_counter()
        if missing:
            wx.MessageBox(
                _("profile.missing", name=profile["name"], names=", ".join(missing)),
                _("menu.profiles"), wx.OK | wx.ICON_WARNING,
            )

    def _on_set_language(self, lang_code: str) -> None:
        """Сохраняет выбор языка. Применится после перезапуска."""
        prefs = self._state.setdefault("prefs", {})
        prefs["language"] = lang_code
        state.save_state(self._state)
        self._set_status(_("status.lang_changed"), "warn")
        wx.MessageBox(_("status.lang_changed"),
                      _("menu.language"), wx.OK | wx.ICON_INFORMATION)

    def on_about(self, event: wx.CommandEvent) -> None:
        info = wx.adv.AboutDialogInfo()
        info.SetName("MInstAll")
        info.SetVersion(f"v{config.APP_VERSION}")
        info.SetDescription(_("about.description"))
        info.SetCopyright(_("about.copyright"))
        info.SetWebSite("https://github.com/assassins377/minstall_project", "GitHub")

        if os.path.exists(config.ICON_FILE):
            try:
                bmp = wx.Bitmap(config.ICON_FILE, wx.BITMAP_TYPE_ANY)
                img = bmp.ConvertToImage()
                img.Rescale(64, 64, wx.IMAGE_QUALITY_HIGH)
                info.SetIcon(wx.Icon(wx.Bitmap(img)))
            except Exception:
                pass

        wx.adv.AboutBox(info)

    def on_check_update(self, event: wx.CommandEvent) -> None:
        import updater
        self._set_status("Проверка обновлений...", "progress")

        def on_check_done(result: dict) -> None:
            wx.CallAfter(self._handle_update_check_result, result)

        updater.check_for_updates_async(on_check_done)

    def _handle_update_check_result(self, result: dict) -> None:
        import updater

        if "error" in result:
            self._set_status("Не удалось проверить обновления.", "warn")
            wx.MessageBox(f"Не удалось проверить обновления:\n{result['error']}",
                          "Обновление", wx.OK | wx.ICON_WARNING)
            return

        if not result["has_update"]:
            self._set_status("У вас установлена последняя версия.", "success")
            wx.MessageBox("У вас установлена самая актуальная версия.",
                          "Инфо", wx.OK | wx.ICON_INFORMATION)
            return

        msg = f"Доступна новая версия MInstAll (v{result['latest']}).\n"
        if result.get("notes"):
            msg += f"\n{result['notes']}\n"
        msg += "\nСкачать и установить сейчас?"

        dlg = wx.MessageDialog(self, msg, "Обновление", wx.YES_NO | wx.ICON_INFORMATION)
        try:
            choice = dlg.ShowModal()
        finally:
            dlg.Destroy()

        if choice != wx.ID_YES:
            return

        self.btn_install.Disable()
        self.progress_bar.SetValue(0)

        def update_cb(msg: dict) -> None:
            wx.CallAfter(self._on_update_message, msg)

        import threading
        threading.Thread(
            target=updater.download_and_update,
            args=(result, update_cb),
            daemon=True,
        ).start()

    def _on_update_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "status":
            self._set_status(msg["text"], "progress")
        elif t == "progress":
            self.progress_bar.SetValue(msg["percent"])
        elif t == "error":
            self._set_status(msg["text"], "error")
            self.btn_install.Enable()
            wx.MessageBox(msg["text"], "Ошибка обновления", wx.OK | wx.ICON_ERROR)
        elif t == "done":
            self._set_status("Перезапуск...", "progress")

    # --------------------------------------------------------------
    # UI
    # --------------------------------------------------------------
    def init_ui(self) -> None:
        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        if os.name == "nt" and not core.is_admin():
            admin_panel = wx.Panel(panel)
            admin_panel.SetBackgroundColour(wx.Colour(255, 243, 205))
            admin_sizer = wx.BoxSizer(wx.HORIZONTAL)
            warn_text = wx.StaticText(admin_panel, label=_("admin.warning"))
            warn_text.SetForegroundColour(wx.Colour(133, 100, 4))
            btn_restart = wx.Button(admin_panel, label=_("btn.restart"))
            btn_restart.Bind(wx.EVT_BUTTON, self._elevate)
            admin_sizer.Add(warn_text, 1, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 10)
            admin_sizer.Add(btn_restart, 0, wx.ALIGN_CENTER_VERTICAL | wx.ALL, 5)
            admin_panel.SetSizer(admin_sizer)
            main_sizer.Add(admin_panel, 0, wx.EXPAND | wx.BOTTOM, 10)

        # Поиск
        search_sizer = wx.BoxSizer(wx.HORIZONTAL)
        search_sizer.Add(wx.StaticText(panel, label=_("toolbar.search")),
                         0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.search_ctrl = wx.TextCtrl(panel)
        self.search_ctrl.SetHint(_("toolbar.search.hint"))
        self.search_ctrl.Bind(wx.EVT_TEXT, self._on_search_input)
        search_sizer.Add(self.search_ctrl, 1, wx.EXPAND)
        main_sizer.Add(search_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Кнопки выбора
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_sel_all = wx.Button(panel, label=_("toolbar.select_missing"))
        btn_sel_all.Bind(wx.EVT_BUTTON, self.select_all)
        btn_desel_all = wx.Button(panel, label=_("toolbar.deselect_all"))
        btn_desel_all.Bind(wx.EVT_BUTTON, self.deselect_all)
        btn_sizer.Add(btn_sel_all, 0, wx.RIGHT, 5)
        btn_sizer.Add(btn_desel_all, 0)

        main_sizer.Add(btn_sizer, 0, wx.LEFT | wx.BOTTOM, 10)

        # Splitter: сверху дерево, снизу (опционально) лог-панель
        self._splitter = wx.SplitterWindow(panel, style=wx.SP_LIVE_UPDATE | wx.SP_3DSASH)
        self._splitter.SetMinimumPaneSize(80)

        self.tree = CT.CustomTreeCtrl(
            self._splitter,
            agwStyle=wx.TR_DEFAULT_STYLE | wx.TR_HIDE_ROOT
                     | wx.TR_FULL_ROW_HIGHLIGHT | wx.TR_HAS_VARIABLE_ROW_HEIGHT
        )
        self.root_item = self.tree.AddRoot("Root")
        self.tree.Bind(wx.EVT_TREE_SEL_CHANGED, self.on_tree_select)
        self.tree.Bind(wx.EVT_TREE_ITEM_RIGHT_CLICK, self._on_tree_right_click)
        # CustomTreeCtrl кидает EVT_TREE_ITEM_CHECKED при изменении галочки
        self.tree.Bind(CT.EVT_TREE_ITEM_CHECKED, self._on_tree_item_check)
        # Tooltip при наведении на программу
        self.tree.Bind(wx.EVT_MOTION, self._on_tree_motion)
        self._last_tooltip_item = None

        # Лог-панель — создаётся но не показывается, пока пользователь не включит
        self._log_panel = log_panel.LogPanel(self._splitter)
        self._log_panel.Hide()
        self._splitter.Initialize(self.tree)

        main_sizer.Add(self._splitter, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        self.il = wx.ImageList(16, 16)
        self.tree.AssignImageList(self.il)
        # Асинхронный загрузчик иконок: иконки декодируются в фоне
        self._icon_loader = icons.IconLoader(self.il, self._on_icon_loaded)
        # path -> список item_id'шек ждущих эту иконку
        self._icon_pending: dict[str, list] = {}

        # Описание
        self.desc_label = wx.StaticText(panel, label=_("desc.hint"))
        self.desc_label.Wrap(740)
        desc_box = wx.StaticBoxSizer(wx.VERTICAL, panel, _("desc.title"))
        desc_box.Add(self.desc_label, 1, wx.EXPAND | wx.ALL, 5)
        main_sizer.Add(desc_box, 0, wx.EXPAND | wx.ALL, 10)

        # Нижняя панель
        bottom_sizer = wx.BoxSizer(wx.HORIZONTAL)
        status_prog_sizer = wx.BoxSizer(wx.VERTICAL)
        self.status_label = wx.StaticText(panel, label=self._initial_status_text())
        self.status_label.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        self.status_label.SetForegroundColour(SEVERITY_COLORS["info"])

        # Метка с подсчётом выбранных программ (≈ размер, ≈ время)
        self.selection_label = wx.StaticText(panel, label=_("selection.none"))
        self.selection_label.SetFont(wx.Font(8, wx.FONTFAMILY_DEFAULT,
                                              wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        self.selection_label.SetForegroundColour(wx.Colour(100, 100, 100))

        self.progress_bar = wx.Gauge(panel, range=100)
        status_prog_sizer.Add(self.status_label, 0, wx.EXPAND | wx.BOTTOM, 2)
        status_prog_sizer.Add(self.selection_label, 0, wx.EXPAND | wx.BOTTOM, 2)
        status_prog_sizer.Add(self.progress_bar, 0, wx.EXPAND)
        bottom_sizer.Add(status_prog_sizer, 1, wx.EXPAND | wx.RIGHT, 15)

        self.btn_cancel = wx.Button(panel, label=_("btn.cancel"), size=(-1, 40))
        self.btn_cancel.Bind(wx.EVT_BUTTON, self.cancel_install)
        self.btn_cancel.Disable()

        self.btn_install = wx.Button(panel, label=_("btn.install"), size=(-1, 40))
        self.btn_install.Bind(wx.EVT_BUTTON, self.start_install)

        bottom_sizer.Add(self.btn_cancel, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        bottom_sizer.Add(self.btn_install, 0, wx.ALIGN_CENTER_VERTICAL)
        main_sizer.Add(bottom_sizer, 0, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(main_sizer)

    def _elevate(self, event: wx.CommandEvent) -> None:
        if core.relaunch_as_admin():
            self.Destroy()
            sys.exit(0)
        else:
            wx.MessageBox("Не удалось перезапуститься с правами администратора.",
                          "Ошибка", wx.OK | wx.ICON_WARNING)

    @staticmethod
    def _is_position_visible(pos: wx.Point, size: tuple[int, int]) -> bool:
        """Проверяет, что окно с такой позицией/размером попадает хотя бы на один монитор.
        Защищает от ситуации, когда пользователь отключил внешний монитор и сохранённая
        позиция оказалась за пределами доступного пространства."""
        for i in range(wx.Display.GetCount()):
            screen = wx.Display(i).GetGeometry()
            # Достаточно чтобы хотя бы 100×100 пикселей окна попало на экран
            if (pos.x + 100 > screen.x and pos.x < screen.x + screen.width - 100 and
                    pos.y + 30 > screen.y and pos.y < screen.y + screen.height - 30):
                return True
        return False

    def _save_window_state(self) -> None:
        """Сохраняет размер и позицию окна в state.json."""
        if self.IsMaximized():
            # Сохраняем флаг + размер из restore-режима
            self.Restore()
            size = self.GetSize()
            pos = self.GetPosition()
            self._state["window"] = {
                "width": size.width, "height": size.height,
                "x": pos.x, "y": pos.y,
                "maximized": True,
            }
        else:
            size = self.GetSize()
            pos = self.GetPosition()
            self._state["window"] = {
                "width": size.width, "height": size.height,
                "x": pos.x, "y": pos.y,
                "maximized": False,
            }
        state.save_state(self._state)

    # --------------------------------------------------------------
    # Помощники
    # --------------------------------------------------------------
    def _set_status(self, text: str, severity: str = "info") -> None:
        self.status_label.SetLabel(text)
        self.status_label.SetForegroundColour(SEVERITY_COLORS.get(severity, SEVERITY_COLORS["info"]))

    def _initial_status_text(self) -> str:
        installed = outdated = runnable = installable_total = 0
        for progs in self.programs_db.values():
            for p in progs:
                status, _v = self.status_cache.get(p["name"], ("missing", ""))
                if status == "runnable":
                    runnable += 1
                    continue
                installable_total += 1
                if status == "ok":
                    installed += 1
                elif status == "outdated":
                    outdated += 1
        parts = [_("status.installed_count", installed=installed, total=installable_total)]
        if outdated:
            parts.append(_("status.outdated_count", outdated=outdated))
        if runnable:
            parts.append(_("status.runnable_count", runnable=runnable))
        return ". ".join(parts) + ". " + _("app.ready")

    # --------------------------------------------------------------
    # Поиск с debounce
    # --------------------------------------------------------------
    def _on_search_input(self, event: wx.CommandEvent) -> None:
        self._search_timer.Stop()
        self._search_timer.StartOnce(config.SEARCH_DEBOUNCE_MS)

    def _on_search_timer(self, event: wx.TimerEvent) -> None:
        self.populate_tree(self.search_ctrl.GetValue())

    # --------------------------------------------------------------
    # Дерево
    # --------------------------------------------------------------
    def _get_or_create_category_path(self, category_name: str) -> wx.TreeItemId:
        """
        Превращает имя категории вида "INTERFACE / THEMES" в путь tree-узлов.
        Создаёт промежуточные категории если их ещё нет.

        Возвращает item самой глубокой категории (куда добавлять программы).
        """
        import scanner as _scanner
        parts = [p.strip() for p in category_name.split(_scanner.CATEGORY_SEPARATOR)]

        parent = self.root_item
        for part in parts:
            # Ищем дочерний item с таким же label
            child, cookie = self.tree.GetFirstChild(parent)
            found = None
            while child is not None and child.IsOk():
                if self.tree.GetItemText(child) == part:
                    found = child
                    break
                child, cookie = self.tree.GetNextChild(parent, cookie)

            if found is None:
                found = self.tree.AppendItem(parent, part, ct_type=0)
                self.tree.SetItemFont(
                    found,
                    wx.Font(10, wx.FONTFAMILY_DEFAULT,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD),
                )
                self.tree.SetItemTextColour(found, wx.Colour(0, 51, 102))
            parent = found

        return parent

    def populate_tree(self, filter_text: str = "") -> None:
        # Запоминаем какие программы были отмечены до пересоздания дерева
        checked_names = set(self._get_checked_program_names())

        self.tree.DeleteChildren(self.root_item)
        self.tree_data.clear()
        # Очищаем pending — старые item_id уже невалидны
        self._icon_pending.clear()
        filter_lower = filter_text.strip().lower()

        for category, programs in self.programs_db.items():
            visible = []
            for p in programs:
                # Фильтр поиска
                if filter_lower and not (
                    filter_lower in p["name"].lower()
                    or filter_lower in p.get("desc", "").lower()
                ):
                    continue
                # Фильтр скрытия недоступных
                if self._hide_missing and not core.is_installer_available(p):
                    continue
                visible.append(p)
            if not visible:
                continue

            # Создаём (возможно вложенную) категорию
            cat_item = self._get_or_create_category_path(category)

            for prog in visible:
                status, found_ver = self.status_cache.get(prog["name"], ("missing", ""))
                min_ver = (prog.get("detect") or {}).get("min_version")
                available = core.is_installer_available(prog)

                if status == "ok":
                    label = (_("tree.installed_ver", name=prog["name"], ver=found_ver)
                             if found_ver else _("tree.installed", name=prog["name"]))
                elif status == "outdated":
                    label = _("tree.outdated", name=prog["name"], ver=min_ver)
                elif status == "runnable":
                    label = _("tree.runnable", name=prog["name"])
                else:
                    label = prog["name"]

                # Программы без файла инсталлятора — серый цвет + значок ⚠
                if not available and status != "ok":
                    label += _("tree.file_missing")

                prog_item = self.tree.AppendItem(cat_item, label, ct_type=1)

                if not available and status != "ok":
                    # Серый — программа неустановима
                    self.tree.SetItemTextColour(prog_item, wx.Colour(160, 160, 160))
                elif status == "ok":
                    self.tree.SetItemTextColour(prog_item, wx.Colour(34, 139, 34))
                elif status == "outdated":
                    self.tree.SetItemTextColour(prog_item, wx.Colour(217, 119, 6))
                elif status == "runnable":
                    self.tree.SetItemTextColour(prog_item, wx.Colour(106, 27, 154))

                # Иконка: 1) явная из json, 2) извлечённая из exe, 3) system.png
                icon_path = icons.resolve_program_icon(prog, core.resolve_path)
                if not icon_path:
                    fallback = core.resolve_path(prog.get("icon") or "icons/system.png")
                    if os.path.exists(fallback):
                        icon_path = fallback

                if icon_path:
                    index = self._icon_loader.get_or_load(icon_path)
                    if index is not None:
                        self.tree.SetItemImage(prog_item, index)
                    else:
                        self._icon_pending.setdefault(icon_path, []).append(prog_item)

                prog_meta = dict(prog)
                prog_meta["_status"] = status
                prog_meta["_item_id"] = prog_item
                self.tree_data[prog_item] = prog_meta

                # Восстанавливаем галочки после пересоздания дерева
                if prog["name"] in checked_names:
                    self.tree.CheckItem(prog_item, True)

        if filter_lower:
            # При поиске — разворачиваем всё, чтобы видны были совпадения
            self.tree.ExpandAll()
        else:
            # По умолчанию — разворачиваем только верхний уровень категорий
            child, cookie = self.tree.GetFirstChild(self.root_item)
            while child is not None and child.IsOk():
                self.tree.Expand(child)
                child, cookie = self.tree.GetNextChild(self.root_item, cookie)

    def select_all(self, event: wx.CommandEvent) -> None:
        for item, data in self.tree_data.items():
            # Не выделяем уже установленные и недоступные (без файла) программы
            if data["_status"] == "ok":
                continue
            if not core.is_installer_available(data):
                continue
            self.tree.CheckItem(item, True)
        self._update_selection_counter()

    def deselect_all(self, event: wx.CommandEvent) -> None:
        for item in self.tree_data.keys():
            self.tree.CheckItem(item, False)
        self._update_selection_counter()

    def _get_checked_program_names(self) -> list[str]:
        """Возвращает имена отмеченных программ (для сохранения сессии)."""
        names = []
        for item, data in self.tree_data.items():
            try:
                if self.tree.IsItemChecked(item):
                    names.append(data["name"])
            except Exception:
                # Item может быть удалён во время вызова
                pass
        return names

    def _restore_session(self) -> None:
        """Восстанавливает галочки и фильтр поиска из state.json."""
        session = self._state.get("session", {})

        # Восстанавливаем фильтр (до populate_tree, чтобы он сразу применился)
        last_filter = session.get("filter", "")
        if last_filter:
            self.search_ctrl.ChangeValue(last_filter)

        # Восстанавливаем галочки
        checked_names = set(session.get("checked", []))
        if checked_names:
            for item, data in self.tree_data.items():
                if data["name"] in checked_names:
                    self.tree.CheckItem(item, True)

        self._update_selection_counter()

    def _save_session(self) -> None:
        """Сохраняет отмеченные программы и фильтр поиска в state.json."""
        self._state["session"] = {
            "checked": self._get_checked_program_names(),
            "filter": self.search_ctrl.GetValue(),
        }
        state.save_state(self._state)

    def _on_icon_loaded(self, icon_path: str, image_index: int) -> None:
        """Колбэк IconLoader — вызывается в UI-потоке когда иконка готова."""
        items = self._icon_pending.pop(icon_path, [])
        for item in items:
            try:
                # Tree item мог быть удалён за время загрузки (populate_tree пересоздаёт)
                self.tree.SetItemImage(item, image_index)
            except Exception:
                pass

    def on_tree_select(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        data = self.tree_data.get(item)
        if data and "desc" in data:
            self.desc_label.SetLabel(data["desc"])
            self.desc_label.Wrap(740)

    def _on_tree_item_check(self, event) -> None:
        """Реакция на изменение галочки — обновляем счётчик."""
        self._update_selection_counter()
        event.Skip()

    def _on_tree_motion(self, event: wx.MouseEvent) -> None:
        """Показывает tooltip с инфой о программе при наведении мыши."""
        event.Skip()
        pos = event.GetPosition()
        hit_item, flags = self.tree.HitTest(pos)
        if hit_item is None or not hit_item.IsOk():
            self._last_tooltip_item = None
            self.tree.SetToolTip(None)
            return

        # Не обновляем tooltip если мышь всё ещё на том же элементе —
        # иначе wx будет постоянно пересоздавать его и подсказка не покажется
        if hit_item == self._last_tooltip_item:
            return
        self._last_tooltip_item = hit_item

        data = self.tree_data.get(hit_item)
        if not data:
            self.tree.SetToolTip(None)
            return

        # Собираем содержимое подсказки
        lines: list[str] = [data["name"]]

        desc = data.get("desc", "").strip()
        if desc:
            # Переносим длинные описания
            if len(desc) > 80:
                desc = desc[:77] + "..."
            lines.append("")
            lines.append(desc)

        status = data.get("_status", "missing")
        status_text = {
            "ok": "✓ Установлено",
            "outdated": "↑ Требуется обновление",
            "runnable": "→ Действие/твик",
            "missing": "○ Не установлено",
        }.get(status, "")
        if status_text:
            lines.append("")
            lines.append(status_text)

        # Метаданные: зависимости, retry, размер
        meta_lines: list[str] = []
        if deps := data.get("depends_on"):
            meta_lines.append(f"Зависит от: {', '.join(deps)}")
        if (retry := data.get("retry", 0)) > 0:
            meta_lines.append(f"Повторов при ошибке: {retry}")
        if not core.is_installer_available(data):
            meta_lines.append("⚠ Файл инсталлятора отсутствует")

        if meta_lines:
            lines.append("")
            lines.extend(meta_lines)

        tooltip = wx.ToolTip("\n".join(lines))
        tooltip.SetDelay(500)  # 0.5 сек задержка перед показом
        self.tree.SetToolTip(tooltip)

    def _update_selection_counter(self) -> None:
        """Обновляет label с подсчётом выбранных программ."""
        selected = [data for item, data in self.tree_data.items()
                    if self.tree.IsItemChecked(item)]
        count, total_size, total_time = stats.selection_summary(
            selected, self._state, t=_,
        )

        if count == 0:
            self.selection_label.SetLabel(_("selection.none"))
            return

        size_str = stats.format_size(total_size, t=_) if total_size > 0 else ""
        time_str = stats.format_duration(total_time, t=_) if total_time is not None else ""

        if size_str and time_str:
            text = _("selection.summary", count=count, size=size_str, time=time_str)
        elif size_str:
            text = _("selection.summary_no_time", count=count, size=size_str)
        else:
            text = _("selection.summary_count_only", count=count)

        self.selection_label.SetLabel(text)

    # --------------------------------------------------------------
    # Контекстное меню (правый клик в дереве)
    # --------------------------------------------------------------
    def _on_tree_right_click(self, event: wx.TreeEvent) -> None:
        item = event.GetItem()
        data = self.tree_data.get(item)
        if not data:
            return  # клик по категории или пустому месту

        # Выделяем элемент чтобы пользователь видел на чём контекст
        self.tree.SelectItem(item)

        menu = wx.Menu()

        only_item = menu.Append(wx.ID_ANY, _("ctx.only_this"))
        self.Bind(wx.EVT_MENU,
                  lambda e, it=item: self._ctx_only_this(it), only_item)

        menu.AppendSeparator()

        open_folder = menu.Append(wx.ID_ANY, _("ctx.open_folder"))
        self.Bind(wx.EVT_MENU,
                  lambda e, d=data: self._ctx_open_folder(d), open_folder)

        copy_name = menu.Append(wx.ID_ANY, _("ctx.copy_name"))
        self.Bind(wx.EVT_MENU,
                  lambda e, d=data: self._ctx_copy(d["name"]), copy_name)

        copy_cmd = menu.Append(wx.ID_ANY, _("ctx.copy_cmd"))
        self.Bind(wx.EVT_MENU,
                  lambda e, d=data: self._ctx_copy(d.get("cmd", "")), copy_cmd)

        menu.AppendSeparator()

        open_log = menu.Append(wx.ID_ANY, _("ctx.open_log"))
        self.Bind(wx.EVT_MENU, lambda e: self._ctx_open_log(), open_log)

        self.PopupMenu(menu)
        menu.Destroy()

    def _ctx_only_this(self, item) -> None:
        """Снимает все галочки и ставит только на выбранной программе."""
        for it in self.tree_data.keys():
            self.tree.CheckItem(it, False)
        self.tree.CheckItem(item, True)
        self._set_status("Выбрана только одна программа", "info")

    def _ctx_open_folder(self, data: dict) -> None:
        """Открывает Проводник в папке инсталлятора."""
        cmd = data.get("cmd", "")
        if not cmd:
            return
        try:
            import shlex
            parts = shlex.split(cmd, posix=False)
            if not parts:
                return
            script_path = core.resolve_path(parts[0])
            folder = os.path.dirname(script_path)
            if os.path.isdir(folder):
                # Выделяем файл если он есть, иначе открываем папку
                if os.path.exists(script_path):
                    subprocess.Popen(["explorer", "/select,", script_path])
                else:
                    subprocess.Popen(["explorer", folder])
            else:
                wx.MessageBox(f"Папка не существует:\n{folder}",
                              "Ошибка", wx.OK | wx.ICON_WARNING)
        except Exception as e:
            wx.MessageBox(f"Не удалось открыть папку:\n{e}",
                          "Ошибка", wx.OK | wx.ICON_WARNING)

    def _ctx_copy(self, text: str) -> None:
        """Копирует текст в буфер обмена."""
        if not text:
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                self._set_status(f"Скопировано: {text[:50]}", "info")
            finally:
                wx.TheClipboard.Close()

    def _ctx_open_log(self) -> None:
        """Открывает install.log в системном редакторе."""
        if os.path.exists(config.LOG_FILE):
            try:
                os.startfile(config.LOG_FILE)
            except Exception as e:
                wx.MessageBox(f"Не удалось открыть лог:\n{e}",
                              "Ошибка", wx.OK | wx.ICON_WARNING)
        else:
            wx.MessageBox("Лог-файл ещё не создан.",
                          "Инфо", wx.OK | wx.ICON_INFORMATION)

    # --------------------------------------------------------------
    # Установка
    # --------------------------------------------------------------
    def start_install(self, event: wx.CommandEvent | None) -> None:
        if self.worker and self.worker.is_alive():
            return

        tasks = [data for item, data in self.tree_data.items()
                 if self.tree.IsItemChecked(item)]
        if not tasks:
            self._set_status("Вы ничего не выбрали!", "error")
            return

        tasks = core.resolve_dependencies(tasks, self.programs_db)

        # Запоминаем для записи таймингов в finish_install
        import time as _time
        self._install_start_ts = _time.time()
        self._install_task_names = [t["name"] for t in tasks]

        self.btn_install.Disable()
        self.btn_cancel.Enable()
        self.progress_bar.SetValue(0)

        def dispatch(msg: dict) -> None:
            if not self._closing:
                wx.CallAfter(self.on_worker_message, msg)

        self.worker = core.InstallWorker(
            tasks,
            dispatch,
            parallel=self._parallel_enabled,
            all_programs=self.programs_db,
        )
        self.worker.start()

    def on_worker_message(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "progress":
            self._set_status(msg["text"], msg.get("severity", "info"))
        elif msg_type == "value":
            self.progress_bar.SetValue(msg["percent"])
        elif msg_type == "scroll_to":
            if item := msg.get("item_id"):
                self.tree.ScrollTo(item)
        elif msg_type == "finished":
            self.finish_install(msg)

    def finish_install(self, msg: dict) -> None:
        reboot_needed = msg.get("reboot", False)
        success = msg.get("success", 0)
        fails = msg.get("fails", 0)
        results = msg.get("results", {})

        # Записываем медианное время на программу — для будущих оценок
        import time as _time
        elapsed = _time.time() - getattr(self, "_install_start_ts", _time.time())
        successful_count = success
        if successful_count > 0 and elapsed > 0:
            per_program = elapsed / successful_count
            for name in getattr(self, "_install_task_names", []):
                # Записываем только для тех, что реально успешно поставились
                stats.record_install_time(self._state, name, per_program)
            state.save_state(self._state)

        self.worker = None
        self.btn_install.Enable()
        self.btn_cancel.Disable()

        cancelled = sum(1 for r in results.values() if r == "cancelled")

        lines = []
        if success:
            lines.append(f"Успешно: {success}")
        if fails:
            lines.append(f"Ошибок: {fails}")
        if cancelled:
            lines.append(f"Отменено: {cancelled}")
        if reboot_needed:
            lines.append("Требуется перезагрузка")

        if fails > 0:
            severity = "warn"
        elif cancelled > 0:
            severity = "warn"
        else:
            severity = "success"
        self._set_status(". ".join(lines) + "." if lines else "Готово.", severity)

        core.invalidate_caches()
        # После установки реестр изменился — сбрасываем кеш установленных
        core.invalidate_installed_cache(self._state)
        self.installed_names = core.get_installed_programs(
            state_dict=self._state, use_cache=self._installed_cache_enabled,
        )
        self.status_cache = core.build_status_cache(self.programs_db, self.installed_names)
        self.populate_tree(self.search_ctrl.GetValue())

        rollbacks = msg.get("rollbacks", {})
        rolled_back = [n for n, r in rollbacks.items() if r == "rolled_back"]
        rollback_failed = [n for n, r in rollbacks.items() if r == "rollback_failed"]

        summary_msg = f"Установка завершена.\n\nУспешно: {success}\nОшибок: {fails}"
        if cancelled:
            summary_msg += f"\nОтменено: {cancelled}"
        if rolled_back:
            summary_msg += f"\n\nОткачено ({len(rolled_back)}):\n"
            summary_msg += "\n".join(f"  - {n}" for n in rolled_back)
        if rollback_failed:
            summary_msg += f"\n\nОткат не удался ({len(rollback_failed)}):\n"
            summary_msg += "\n".join(f"  - {n}" for n in rollback_failed)
        if reboot_needed:
            summary_msg += "\n\nТребуется перезагрузка."
        wx.MessageBox(summary_msg, "Результат", wx.OK | wx.ICON_INFORMATION)

        if reboot_needed:
            dlg = wx.MessageDialog(
                self,
                "Установщики требуют перезагрузки компьютера.\n\nПерезагрузить сейчас?",
                "Перезагрузка", wx.YES_NO | wx.ICON_QUESTION
            )
            try:
                choice = dlg.ShowModal()
            finally:
                dlg.Destroy()

            if choice == wx.ID_YES:
                try:
                    subprocess.run(
                        ["shutdown", "/r", "/t", "10", "/c",
                         "Мастер установки: перезагрузка"],
                        check=False
                    )
                except Exception:
                    pass

    def cancel_install(self, event: wx.CommandEvent) -> None:
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self._set_status("Отмена установки...", "warn")
            self.btn_cancel.Disable()

    def on_closing(self, event: wx.CloseEvent) -> None:
        if self.worker and self.worker.is_alive():
            dlg = wx.MessageDialog(self, "Установка выполняется. Прервать и выйти?",
                                   "Выход", wx.YES_NO | wx.ICON_WARNING)
            try:
                choice = dlg.ShowModal()
            finally:
                dlg.Destroy()

            if choice == wx.ID_YES:
                self._closing = True
                self.worker.stop()
                self.worker.join(timeout=5.0)
                self._save_window_state()
                self._save_session()
                event.Skip()
            else:
                event.Veto()
        else:
            self._save_window_state()
            self._save_session()
            event.Skip()
