"""Cryo's texts in the game's other languages, picked out of TPS's base game (0.6.1; RESEARCH.md §6.83).

TPS has cryo's lines in every language it speaks: a cryo gun's card ("Freeze Chance", "Freezes enemies solid."), a
cryo shield's, a relic's, the grenades' names. They are in its base game's localization files
(`WillowGame/Localization/<language>/`), which are named after TPS's packages, and BL2 has packages and files of
the same names with its own texts in them (`gd_AttributePresentation`, `GD_Shields` ...): the files can't be copied
as the Baroness Pack's are. So the sections that are cryo's are picked out of them into one file of the mod's own,
`GD_AureliaTexts.<language>`, in the engine's own format (UTF-16, `[section]`, `Key="text"`), and the mod asks the
engine for a line by the place it came from: the section `<TPS file, lower case>.<TPS's section>`, TPS's key.

A section is taken whole, its lines as they are (their quoting is the engine's). A language whose files have none
of it gives no file.

    python tools/cli.py cryotexts <TPS root> <language> [<out file>]      (without an out file: what would be picked)
"""

from __future__ import annotations

import re
from pathlib import Path

FOLDER = Path("WillowGame/Localization")
NAME = "GD_AureliaTexts"
# TPS file (lower case, without its language) -> which of its sections are cryo's
SOURCES = {
    "gd_attributepresentation": r"Freeze|Ice",
    "gd_ice": r".",
    "gd_cork_shields": r"Ice",
    "gd_grenademods": r"Ice",
    "gd_cork_weap_pistol": r"Prefix_Elemental_Ice",
    "gd_cork_weap_smg": r"Prefix_Elemental_Ice",
    "gd_cork_weap_shotgun": r"Prefix_Elemental_Ice",
    "gd_cork_weap_assaultrifle": r"Prefix_Elemental_Ice",
    "gd_cork_weap_sniperrifles": r"Prefix_Elemental_Ice",
    "gd_cork_weap_launchers": r"Prefix_Elemental_Ice",
}
_SECTION = re.compile(r"^\[(.+)\]\s*$")


def section_name(stem: str, section: str) -> str:
    """The name a TPS section has in the mod's file."""
    return f"{stem.lower()}.{section}"


def read(path: Path) -> str:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def sources(tps: Path, language: str) -> list[Path]:
    """TPS's files of that language that have something of cryo's, as far as their names say."""
    folder = tps / FOLDER / language
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and path.stem.lower() in SOURCES
                  and path.suffix.lower() == f".{language.lower()}")


def pick(tps: Path, language: str) -> tuple[list[str], int]:
    """The mod's file for that language, as lines, and how many texts it holds."""
    lines: list[str] = []
    texts = 0
    for path in sources(tps, language):
        wanted = re.compile(SOURCES[path.stem.lower()])
        taking = False
        for line in read(path).splitlines():
            line = line.rstrip()
            header = _SECTION.match(line)
            if header:
                taking = bool(wanted.search(header.group(1)))
                if taking:
                    lines += ["", f"[{section_name(path.stem, header.group(1))}]"]
            elif taking and "=" in line:
                lines.append(line)
                texts += 1
    return lines[1:], texts


def build(tps: Path, language: str, out: Path) -> str:
    lines, texts = pick(tps, language)
    if not texts:
        raise ValueError(f"TPS has none of cryo's texts in {language}")
    out.write_bytes(b"\xff\xfe" + ("\r\n".join(lines) + "\r\n").encode("utf-16-le"))
    return f"{texts} texts"
