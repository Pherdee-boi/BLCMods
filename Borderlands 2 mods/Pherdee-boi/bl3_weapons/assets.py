"""Where the mod's pictures, sound and cryo's converter scripts are read from.

Run from a folder (development) that is the mod's own folder. Inside a .sdkmod they cannot be opened by path, and the game's texture and audio loaders and
cryo's converter want real files, so they are copied out into sdk_mods/settings/bl3_weapons_files: the converter whenever it differs (it must match the
code), the pictures and the sound only when missing, so a picture the player put there in place of ours stays.
"""

import zipfile
from pathlib import Path

from mods_base import SETTINGS_DIR

HERE = Path(__file__).parent
ARCHIVE = next((p for p in HERE.parents if p.is_file() and p.suffix == ".sdkmod"), None)
DIR = HERE if ARCHIVE is None else SETTINGS_DIR / "bl3_weapons_files"
KEPT = ("art/",)  # copied once; whatever is there already stays
ALWAYS = ("cryo/convert/",)


def _extract() -> None:
    with zipfile.ZipFile(ARCHIVE) as z:
        for info in z.infolist():
            _, _, rel = info.filename.partition("/")  # the archive's one root folder
            if info.is_dir() or not rel.startswith(KEPT + ALWAYS):
                continue
            target = DIR / rel
            if target.exists() and (rel.startswith(KEPT) or target.read_bytes() == z.read(info)):
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))


if ARCHIVE is not None:
    _extract()
