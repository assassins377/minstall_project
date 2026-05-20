"""Лёгкая система интернационализации без gettext.

Переводы хранятся в JSON-файлах в папке i18n/ — по одному на язык.
Использование:

    import i18n
    i18n.init(language="auto")
    print(i18n.t("app.title"))
    print(i18n.t("status.installed_count", n=5, total=10))

Ключи без перевода возвращаются как есть (или с подстановкой) — это
позволяет постепенно покрывать строки переводами не ломая приложение.
"""
from __future__ import annotations

import json
import locale
import logging
import os
from typing import Any

import config


# --- Список поддерживаемых языков ---
SUPPORTED_LANGUAGES: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
}
DEFAULT_LANGUAGE = "ru"
FALLBACK_LANGUAGE = "ru"

# Папка с json-файлами переводов
I18N_DIR = os.path.join(config.SCRIPT_DIR, "i18n")

# Активные переводы — _translations[lang][key] = "перевод"
_translations: dict[str, dict[str, str]] = {}
_current_lang: str = DEFAULT_LANGUAGE


# ------------------------------------------------------------------
# Загрузка переводов
# ------------------------------------------------------------------
def _load_translations_file(lang: str) -> dict[str, str]:
    """Читает i18n/<lang>.json. При ошибке — пустой dict."""
    path = os.path.join(I18N_DIR, f"{lang}.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logging.warning(f"i18n/{lang}.json: ожидается объект, получен {type(data).__name__}")
            return {}
        return data
    except Exception as e:
        logging.warning(f"Не удалось прочитать i18n/{lang}.json: {e}")
        return {}


def load_translations() -> None:
    """Загружает все поддерживаемые языки в память."""
    global _translations
    _translations = {}
    for lang in SUPPORTED_LANGUAGES:
        _translations[lang] = _load_translations_file(lang)


# ------------------------------------------------------------------
# Автодетекция системного языка
# ------------------------------------------------------------------
def detect_system_language() -> str:
    """
    Определяет язык системы. Возвращает один из SUPPORTED_LANGUAGES
    или FALLBACK_LANGUAGE если язык не поддерживается.
    """
    loc: str = ""

    # Попытка 1: locale.getlocale() — современный API
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        pass

    # Попытка 2: deprecated getdefaultlocale (но работает на голой системе без setlocale)
    if not loc:
        try:
            loc = locale.getdefaultlocale()[0] or ""  # type: ignore[attr-defined]
        except Exception:
            pass

    # Попытка 3: Windows API напрямую (для случаев когда locale ничего не даёт)
    if not loc and os.name == "nt":
        try:
            import ctypes
            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            # Primary language ID = младшие 10 бит
            primary = lcid & 0x3FF
            # https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-lcid/
            primary_to_code = {
                0x09: "en",  # English
                0x19: "ru",  # Russian
                0x22: "uk",  # Ukrainian
                0x07: "de",  # German
                0x0C: "fr",  # French
                0x0A: "es",  # Spanish
            }
            if primary in primary_to_code:
                return primary_to_code[primary] if primary_to_code[primary] in SUPPORTED_LANGUAGES else FALLBACK_LANGUAGE
        except Exception:
            pass

    loc = loc.lower()

    # Маппинг по префиксу / содержанию строки локали
    if loc.startswith("ru") or "russian" in loc:
        return "ru" if "ru" in SUPPORTED_LANGUAGES else FALLBACK_LANGUAGE
    if loc.startswith("en") or "english" in loc:
        return "en" if "en" in SUPPORTED_LANGUAGES else FALLBACK_LANGUAGE

    return FALLBACK_LANGUAGE


# ------------------------------------------------------------------
# Инициализация и переключение
# ------------------------------------------------------------------
def init(language: str = "auto") -> str:
    """
    Инициализирует i18n. Возвращает реально выбранный язык.

    language='auto' → определить автоматически.
    """
    load_translations()
    if language == "auto":
        language = detect_system_language()
    set_language(language)
    return _current_lang


def set_language(lang: str) -> None:
    """Устанавливает текущий язык. Неизвестный — сбрасывает на FALLBACK_LANGUAGE."""
    global _current_lang
    if lang in SUPPORTED_LANGUAGES:
        _current_lang = lang
    else:
        logging.warning(f"Неподдерживаемый язык '{lang}', откат к {FALLBACK_LANGUAGE}")
        _current_lang = FALLBACK_LANGUAGE


def get_language() -> str:
    """Текущий код языка."""
    return _current_lang


# ------------------------------------------------------------------
# Получение перевода
# ------------------------------------------------------------------
def t(key: str, **kwargs: Any) -> str:
    """
    Возвращает перевод по ключу.

    Порядок поиска:
      1. _translations[_current_lang][key]
      2. _translations[FALLBACK_LANGUAGE][key]
      3. key (как fallback — программисту видно непереведённое)

    kwargs передаются в str.format() — например:
      t("foo", n=5)  → "Найдено: 5" если в json есть "foo": "Найдено: {n}"
    """
    text = _translations.get(_current_lang, {}).get(key)
    if text is None and _current_lang != FALLBACK_LANGUAGE:
        text = _translations.get(FALLBACK_LANGUAGE, {}).get(key)
    if text is None:
        text = key  # видно непереведённое — это фича для отладки

    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text
