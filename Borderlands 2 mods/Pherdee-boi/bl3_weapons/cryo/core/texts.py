"""Texts in the game's language (0.6.0; RESEARCH.md §6.82).

Borderlands 2 runs in one of eight languages, and her own texts come in the game's language by themselves wherever the
Baroness Pack has them: the engine reads a package's texts from the file `<package>.<language>` in the game's
`Localization/<language>` folder when it loads the package, and the mod's build copies the pack's files there
(builder.py, `_text_targets`). English is in her packages themselves.

What the mod writes into the game from Python is English: a name typed in from TPS's English. For such a text this
asks the engine for the game's own (`Object.Localize`: a section, a key, a file's name; it answers from the same
files) and keeps the English where there is none: in an English game (no file holds English), in a language the pack
lacks (Traditional Chinese), and where a file isn't there (a Pre-Sequel without that language's files).

Cryo's words are TPS's base game's, in files BL2 has under the same names with its own texts. The build picks what is
cryo's out of them into one file of the mod's own, `GD_AureliaTexts.<language>` (the converter's cryotexts.py), each
section under the name `<TPS file, lower case>.<TPS's section>`: `picked` asks for a line by where it came from.
"""

from __future__ import annotations

from mods_base import ENGINE

ENGLISH = "INT"
PICKED = "GD_AureliaTexts"      # the build's file of texts picked out of TPS's base game (cryo's)


def language() -> str:
    """The game's language as the engine names it: INT, DEU, ESN, FRA, ITA, JPN, KOR, TWN (RUS in its own edition)."""
    try:
        return str(ENGINE.GetLanguage()).upper() or ENGLISH
    except Exception:  # noqa: BLE001 - nothing to ask yet: English
        return ENGLISH


def english() -> bool:
    return language() == ENGLISH


def localized(section: str, key: str, package: str, fallback: str) -> str:
    """The text under `key` in `section` of the file `<package>.<language>`, as the engine reads it, or `fallback`
    (the English) where the game has none. A miss comes back from the engine as "?<language>?<package>.<section>.<key>?"."""
    if english():
        return fallback
    try:
        got = str(ENGINE.Localize(section, key, package))
    except Exception:  # noqa: BLE001 - a text is never worth a failed setup
        return fallback
    return fallback if not got or got.startswith("?") else got


def picked(tps_file: str, section: str, key: str, fallback: str) -> str:
    """A text of TPS's base game the build has picked out (cryo's card lines and names): `key` in `section` of
    TPS's file `tps_file`, in the game's language, or `fallback`."""
    return localized(f"{tps_file.lower()}.{section}", key, PICKED, fallback)
