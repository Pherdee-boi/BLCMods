"""Close-range sights on sniper rifles (v0.39.0).

BL3 snipers can wear red dots and short sights. Here every sight part of the shotgun, assault rifle, pistol and
SMG families is added to the sight list of every sniper part list, so they roll on dropped snipers like any scope
(same weight as the scope the list already has). Sights are plain WeaponPartDefinitions (zoom FOV upgrade, spread
bonus while aiming, reticle); each gun type draws its parts from one merged "gestalt" mesh, so a sight from another
gun type has no mesh in the sniper's: see draw() for how it is put on the gun.

Lists come with maps (DLC, uniques), so they are patched again on every map arrival; entries are built fresh
(like aurelia does) and each list's original entries are kept so turning the option off puts them back.

Dev (only with a dev.flag file here): a file named exec.now.py dropped in this folder runs once in the game (globals: sights, say, HERE, held_weapon).
"""

import contextlib
import copy
import random
import re
import time
import traceback
from pathlib import Path

import unrealsdk
from mods_base import BoolOption, SliderOption, get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import UObject, WeakPointer

from .core import held_frame, held_weapon, log, mesh_guards, rebuild

HERE = Path(__file__).parent
EXEC = HERE / "exec.now.py"
DEV_FLAG = HERE / "dev.flag"  # the trigger below only runs where this file exists (a release ships without it)

SOURCE_PACKAGES = ("GD_Weap_Shotgun", "GD_Weap_AssaultRifle", "GD_Weap_Pistol", "GD_Weap_SMG")
# the merged mesh definition each gun type draws its parts from
GESTALT_DEFS = {
    "GD_Weap_Shotgun": "Weap_Shotguns.GestaltDef_Shotgun",
    "GD_Weap_AssaultRifle": "Weap_AssaultRifles.GestaltDef_AssaultRifle",
    "GD_Weap_Pistol": "Weap_Pistol.GestaltDef_Pistol",
    "GD_Weap_SMG": "Weap_SMG.GestaltDef_SMG",
}
SNIPER_SIGHTS = "GD_Weap_SniperRifles.Sight."
# Sights taken out after the in-game review (2026-10-11): scope-type sights whose aimed view is just the sniper scope overlay.
EXCLUDED = {"AR_Sight_Bandit", "SMG_Sight_Bandit", "SG_Sight_Hyperion",
            # the other scope-type sights (bScoped): aiming through them shows the sniper's full-screen scope overlay (removed 2026-10-11)
            "AR_Sight_Dahl", "AR_Sight_Jakobs", "Pistol_Sight_Bandit", "Pistol_Sight_Hyperion", "Pistol_Sight_Tediore",
            "Pistol_Sight_Vladof", "SMG_Sight_Tedior", "SG_Sight_Bandit", "SG_Sight_Jakobs", "SG_Sight_Tediore"}

def _changed(_option: object, value: bool) -> None:
    try:
        patch_lists() if value else unpatch_lists()
    except Exception:  # noqa: BLE001
        unrealsdk.logging.error("[BL3Weapons] sights: " + traceback.format_exc())


enabled = BoolOption("Close-Range Sniper Sights", True, "Yes", "No", on_change=_changed,
                     description="Shotgun, assault rifle, pistol and SMG sights can roll on sniper rifles (new drops).")
chance = SliderOption("Close-Range Sight Chance", 30, 5, 80, 5, True,
                      description="Percent of new snipers that roll a close-range sight instead of a scope.")
height = SliderOption("Close-Range Sight Height", 0, -10, 10, 0.5, False,
                      description="Moves the sights up (+) or down (-) on the gun when you are not aiming, in game units. Aiming always uses the calibrated aim line.")
forward = SliderOption("Close-Range Sight Position", 0, -20, 20, 0.5, False,
                       description="Moves the sights toward the muzzle (+) or toward you (-) when you are not aiming, in game units. Aiming always uses the calibrated position.")
zoom_fov = SliderOption("Close-Range Sight Zoom (FOV)", 45, 20, 70, 1, True,
                        description="Field of view while aiming through a close-range sight on a sniper: a higher number zooms less. Scopes keep the sniper's own zoom "
                                    "(about 20). 45 is an assault rifle's, 60 a shotgun's.")
OPTIONS = [enabled, chance, zoom_fov, height, forward]

# Where a sight's centre goes on a sniper, in the gun mesh's own units (X sideways, Y along the barrel with the
# muzzle at -Y, Z up). Every gun type's parts are modelled in one shared frame, so this is the same for all: the
# sniper's aim line runs from the eye (rear socket Y=0, Z=15.25; eye socket Y=52) to the front sight (Y=-87, Z=17.25),
# which is Z~16 at the sight, and the sniper's own mounts hold things at Y~0. Measured in game 2026-10-11.
# A sight is lined up by its own eye socket ("<part>_EyeSocket1": where the eye sits on its own gun, e.g. the Torgue
# shotgun sight's is (0, 22, 19.75)): that point goes on the aim line, so the opening is centred on the crosshair.
TARGET = (0.0, 0.0, 16.5)  # centre of a sight whose own eye socket is unknown (fallback)
AIM_Z = 15.2  # fallback height of the sniper's aim line at the sights: where a sight's eye point goes (X=0)
ADS_DROP = 2.5  # while aiming through a sight: the gun body goes this far down and the sight this far up inside it
#                 (net: the sight stays on the aim line). Tried 2.0 and 3.0 on the Dahl body 2026-10-11: the receiver that
#                 sat on the crosshair clears at 2+; the eye-socket height itself can't be changed (the camera ignores it).
AIM_DROP = -0.13  # the reticle is centred on the crosshair when a sight's eye point is 0.13 ABOVE the body's EyeSocket2
#                 (measured 2026-10-11 by finding the reticle in screenshots: zero at +0.52 +- 0.03 from a 0.4 drop on the
#                 Jakobs, Hyperion and Maliwan bodies; a fixed height instead of the socket was 0.3-1.1 units off)

_original: dict[str, tuple[WeakPointer, list]] = {}  # list path -> (list, its sight entries before patching)
_sights: list[UObject] | None = None


def _find_all(cls: str) -> list[UObject]:
    return [o for o in unrealsdk.find_all(cls, exact=False) if not o.Name.startswith("Default__")]


def close_sights() -> list[UObject]:
    """Every sight part of the source gun types (not the 'None' one: it is a bare barrel with no mesh)."""
    global _sights
    if _sights is None or any(s is None for s in _sights):
        found = []
        for part in _find_all("WeaponPartDefinition"):
            path = part._path_name()
            head, _, rest = path.partition(".Sight.")
            if rest and head in SOURCE_PACKAGES and "_None" not in rest and "." not in rest and rest not in EXCLUDED:
                found.append(part)
        _sights = sorted(found, key=lambda p: p._path_name())
        log(f"sights: {len(_sights)} close-range sights found")
    return _sights


def _entry_like(src, part: UObject):
    """A new weighted-part entry like `src` (weight, game stage, manufacturers) for `part`, built fresh."""
    fields = {"Part": part, "Manufacturers": list(src.Manufacturers)}
    for name in ("MinGameStageIndex", "MaxGameStageIndex", "DefaultWeightIndex", "MinGameStage", "MaxGameStage",
                 "DefaultWeight"):
        if hasattr(src, name):
            fields[name] = getattr(src, name)
    return unrealsdk.make_struct(src._type.Name, **fields)


def patch_lists() -> int:
    """Add the close-range sights to each sniper part list (one that has a sniper sight). Idempotent. Returns lists
    changed. The game picks a part evenly among a list's distinct parts (every weight in these lists is 0, and
    repeating an entry changes nothing: measured 2026-10-11), so the lists hold them all and reroll() brings the
    share of new rolls down to the chance slider."""
    if not enabled.value:
        return 0
    sights = close_sights()
    if not sights:
        return 0
    changed = 0
    for coll in _find_all("WeaponPartListCollectionDefinition"):
        data = coll.SightPartData
        if data is None or not data.bEnabled:
            continue
        path = coll._path_name()
        entries = [_entry_like(e, e.Part) for e in data.WeightedParts]  # fresh copies: the array may move
        if path in _original:
            entries = _original[path][1]  # already patched once: start from what the list held
        own = [e for e in entries if e.Part is not None and e.Part._path_name().startswith(SNIPER_SIGHTS)]
        if not own:
            continue
        have = {e.Part._path_name() for e in entries if e.Part is not None}
        new = [s for s in sights if s._path_name() not in have]
        if not new:
            continue
        template = own[0]
        fresh = [_entry_like(e, e.Part) for e in entries]
        fresh += [_entry_like(template, s) for s in new]
        coll.SightPartData = unrealsdk.make_struct(data._type.Name, bEnabled=True, WeightedParts=fresh)
        _original[path] = (WeakPointer(coll), entries)
        changed += 1
    if changed:
        log(f"sights: patched {changed} sniper part lists, {len(_original)} in all")
    return changed


def unpatch_lists() -> int:
    """Put every patched list back as it was (option off / mod disabled)."""
    changed = 0
    for path, (ref, entries) in list(_original.items()):
        coll = ref()
        if coll is None:
            continue
        data = coll.SightPartData
        coll.SightPartData = unrealsdk.make_struct(
            data._type.Name, bEnabled=data.bEnabled, WeightedParts=[_entry_like(e, e.Part) for e in entries])
        changed += 1
    _original.clear()
    return changed


def reset() -> None:
    unpatch_lists()
    drop_drawn()


# --------------------------------------------------------------------------- drawing
# A gun type draws its parts from one merged mesh and a sight of another type has no fragment in it, so the part
# would be invisible. The fix (found in game 2026-10-11): a second SkeletalMeshComponent on the other type's merged
# mesh, filled with just that sight's fragment (GestaltSkeletalMeshDefinition.InitGestaltMeshDataFromParts), attached
# to the gun's first-person mesh. Needs bInWorldForeground (the viewmodel draws in its own foreground projection:
# without it the sight floats in the world), the gun's material (else it is untextured blue) and the gun's light
# environment. It uses the shared skeleton's root, so it sits where the same part sits on its own gun type.
_drawn: dict[int, dict] = {}  # weapon address -> {"weapon", "comp", "sight", "material"}


def _foreign_sight(weapon: UObject) -> tuple[UObject, str, list[str]] | None:
    """(sight part, gestalt definition path, fragment names) if the gun wears a close-range sight of another type."""
    part = weapon.DefinitionData.SightPartDefinition
    if part is None or not enabled.value:
        return None
    head = part._path_name().partition(".Sight.")[0]
    definition = GESTALT_DEFS.get(head)
    if definition is None:
        return None
    fp = weapon.FirstPersonMesh
    gestalt = unrealsdk.find_object("GestaltSkeletalMeshDefinition", definition)
    if fp is None or fp.SkeletalMesh is None or fp.SkeletalMesh._get_address() == gestalt.GestaltSkeletalMesh._get_address():
        return None  # the gun's own mesh already has it
    names = [str(part.GestaltModeSkeletalMeshName), *(str(n) for n in part.AdditionalGestaltModeSkeletalMeshNames)]
    return part, definition, [n for n in names if n and n != "None"]


_bounds_cache: dict[tuple, tuple[tuple[float, float, float], tuple[float, float, float]]] = {}

# Resting height of a sight when NOT aiming: its lowest point goes on the sniper's rail, where its own scopes' bottoms
# sit (SR_Scope_Dahl 12.3, Vladof 12.0, Maliwan 12.3, Jakobs 12.1, Hyperion 13.6 in the mesh's units). While aiming a
# sight is placed by its eye point instead (see place()).
HIP_BASE_Z = 12.3
# Hand fixes per sight, by the sight's mesh name, (X, Y, Z) added when NOT aiming (never while aiming). Empty until
# the in-game review asks for one.
HIP_ADJUST: dict[str, tuple[float, float, float]] = {
    "SMG_Scope_Hyperion": (0.0, 0.0, 1.0),  # the red dot sat half buried in the rail (user, 2026-10-11)
}


def fragment_bounds(definition: str, names: list[str]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """(low corner, high corner) of the box around the named parts of a gun type's merged mesh (GestaltPartBounds)."""
    key = (definition, tuple(names))
    if key not in _bounds_cache:
        gestalt = unrealsdk.find_object("GestaltSkeletalMeshDefinition", definition)
        lo, hi = [1e9] * 3, [-1e9] * 3
        for entry in gestalt.GestaltPartBounds:
            if str(entry.SkeletalMeshFragmentName) in names:
                b = entry.ReferencePoseBounds
                for i, (c, e) in enumerate(((b.Origin.X, b.BoxExtent.X), (b.Origin.Y, b.BoxExtent.Y), (b.Origin.Z, b.BoxExtent.Z))):
                    lo[i], hi[i] = min(lo[i], c - e), max(hi[i], c + e)
        if lo[0] > 1e8:  # no bounds recorded: a box around the fallback centre
            lo, hi = [TARGET[0] - 1, TARGET[1] - 1, TARGET[2] - 1], [TARGET[0] + 1, TARGET[1] + 1, TARGET[2] + 1]
        _bounds_cache[key] = (tuple(lo), tuple(hi))
    return _bounds_cache[key]


def _eye(comp: UObject, names: list[str]) -> tuple[float, float, float] | None:
    """Where the eye sits for this sight on its own gun (its EyeSocket1, in the merged mesh's units)."""
    try:
        socket = comp.GetSocketByName(f"{names[0]}_EyeSocket1")
    except Exception:  # noqa: BLE001
        return None
    if socket is None:
        return None
    loc = socket.RelativeLocation
    return loc.X, loc.Y, loc.Z


def aim_z(weapon: UObject) -> float:
    """Height of this gun's aim line at the sights: its body's EyeSocket2 (the ADS camera's height; it differs per
    body: Dahl 14.0, Hyperion 15.07, Maliwan / Vladof 15.0, Jakobs 15.25) minus AIM_DROP (AIM_DROP is negative: a hair above)."""
    try:
        fp = weapon.FirstPersonMesh
        for m in fp.GestaltData.SocketMappings:
            if str(m.OriginalSocketName) == "EyeSocket2":
                socket = fp.GetSocketByName(m.MangledSocketName)
                if socket is not None:
                    return socket.RelativeLocation.Z - AIM_DROP
    except Exception:  # noqa: BLE001
        pass
    return AIM_Z


def _want(weapon: UObject, aimed: bool) -> tuple[float, float, float, float]:
    """(height, position, aim height, lift) a sight should be placed with. The sliders are for how the sight sits on
    the gun; while aiming it always goes to the calibrated aim line (sliders 0) and the body drops (lift)."""
    return ((0.0, 0.0) if aimed else (float(height.value), float(forward.value))) + (round(aim_z(weapon), 2), ADS_DROP if aimed else 0.0)


def place(weapon: UObject, comp: UObject, definition: str, names: list[str], aimed: bool = False) -> tuple[float, float, float, float]:
    """Aiming: the sight's eye point goes on the gun's aim line (centre X, aim_z), lifted by ADS_DROP to make up for the
    body dropping, sliders and HIP_ADJUST ignored. Not aiming: its underside rests on the rail (HIP_BASE_Z), plus the
    height and position sliders and the sight's HIP_ADJUST. Both centre it over the rear mounts (Y = TARGET Y).
    Returns what it was placed for."""
    lo, hi = fragment_bounds(definition, names)
    cx, cy, cz = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2
    eye = _eye(comp, names)
    h, f, aim, lift = _want(weapon, aimed)
    dx = -eye[0] if eye else TARGET[0] - cx
    if aimed:
        dz = aim - eye[2] if eye else aim + (TARGET[2] - AIM_Z) - cz
        ax = ay = az = 0.0
    else:
        dz = HIP_BASE_Z - lo[2]  # at rest: the sight's underside on the rail, wherever its eye point is
        ax, ay, az = HIP_ADJUST.get(names[0], (0.0, 0.0, 0.0))
    comp.SetTranslation(unrealsdk.make_struct("Vector", X=dx + ax, Y=TARGET[1] - cy - f + ay, Z=dz + h + lift + az))
    return h, f, aim, lift


def _remove(addr: int) -> None:
    entry = _drawn.pop(addr, None)
    if entry is None:
        return
    comp, weapon = entry["comp"](), entry["weapon"]()
    psc = entry["reticle"]() if entry.get("reticle") is not None else None
    if psc is not None:
        psc.SetHidden(True)
        try:
            comp.DetachComponent(psc)
        except Exception:  # noqa: BLE001
            pass
    if entry.get("fov0") is not None and weapon is not None:
        weapon.ZoomedEndFOV = weapon.ZoomedEndFOVBaseValue = weapon.ReplicatedZoomedEndFOVBaseValue = entry["fov0"]
    if entry.get("fp_z") is not None and weapon is not None and weapon.FirstPersonMesh is not None:
        fp = weapon.FirstPersonMesh
        fp.SetTranslation(unrealsdk.make_struct("Vector", X=fp.Translation.X, Y=fp.Translation.Y, Z=entry["fp_z"]))
    if comp is not None:
        comp.SetHidden(True)
        try:
            if weapon is not None and weapon.FirstPersonMesh is not None:
                weapon.FirstPersonMesh.DetachComponent(comp)
        except Exception:  # noqa: BLE001
            pass


def _build(weapon: UObject, definition: str, names: list[str]) -> UObject | None:
    fp = weapon.FirstPersonMesh
    gestalt = unrealsdk.find_object("GestaltSkeletalMeshDefinition", definition)
    comp = unrealsdk.construct_object("SkeletalMeshComponent", weapon)
    comp.SetSkeletalMesh(gestalt.GestaltSkeletalMesh, False)
    gestalt.InitGestaltMeshDataFromParts(comp, names)
    comp.SetActorCollision(False, False, False)
    comp.CastShadow = fp.CastShadow
    comp.bInWorldForeground = fp.bInWorldForeground
    comp.bOverrideAttachmentOwnerVisibility = fp.bOverrideAttachmentOwnerVisibility
    comp.SetDepthPriorityGroup(fp.DepthPriorityGroup)
    if fp.LightEnvironment is not None:
        comp.SetLightEnvironment(fp.LightEnvironment)
    fp.AttachComponent(comp, fp.GetBoneName(0))
    place(weapon, comp, definition, names)
    return comp


def _reticle(weapon: UObject, part: UObject) -> UObject | None:
    """The reticle particle the game built for this sight (it never attaches: the sniper has no SightFX socket)."""
    template = part.FirstPersonParticle.Template if part.FirstPersonParticle is not None else None
    if template is None:
        return None
    best, best_n = None, -1
    for c in unrealsdk.find_all("ParticleSystemComponent", exact=False):
        if c.Template is not None and c.Template._get_address() == template._get_address()                 and c.Outer is not None and c.Outer._get_address() == weapon._get_address():
            n = int(c.Name.rsplit("_", 1)[-1]) if c.Name.rsplit("_", 1)[-1].isdigit() else 0
            if n > best_n:
                best, best_n = c, n
    return best


def _attach_reticle(weapon: UObject, comp: UObject, part: UObject, names: list[str]) -> UObject | None:
    psc = _reticle(weapon, part)
    if psc is None:
        return None
    socket = f"{names[0]}_{part.FirstPersonParticleSocketName}"
    if comp.GetSocketByName(socket) is None:
        return None
    try:
        if weapon.FirstPersonMesh is not None:
            weapon.FirstPersonMesh.DetachComponent(psc)
    except Exception:  # noqa: BLE001
        pass
    comp.AttachComponentToSocket(psc, socket)
    psc.SetDepthPriorityGroup(weapon.FirstPersonMesh.DepthPriorityGroup)
    psc.SetHidden(True)
    log(f"sights: reticle {psc.Template.Name} on {socket}")
    return psc


ZOOMED = ("ZST_ZoomingIn", "ZST_Zoomed")


def _drop_body(weapon: UObject, entry: dict, comp: UObject, zoomed: bool) -> None:
    """Aiming through a close-range sight: lower the gun body by ADS_DROP and raise the sight by the same inside it
    (it is the body's child), so the sight stays on the aim line but the receiver stops covering the crosshair."""
    fp = weapon.FirstPersonMesh
    if fp is None:
        return
    if zoomed:
        if entry["fp_z"] is None:
            entry["fp_z"] = fp.Translation.Z
        fp.SetTranslation(unrealsdk.make_struct("Vector", X=fp.Translation.X, Y=fp.Translation.Y, Z=entry["fp_z"] - ADS_DROP))
    elif entry["fp_z"] is not None:
        fp.SetTranslation(unrealsdk.make_struct("Vector", X=fp.Translation.X, Y=fp.Translation.Y, Z=entry["fp_z"]))
        entry["fp_z"] = None
    entry["placed"] = place(weapon, comp, entry["definition"], entry["names"], zoomed)


def follow(weapon: UObject) -> None:
    """Every frame: a scope's model hides while aiming (its overlay shows instead), a red dot's reticle shows only
    while aiming."""
    entry = _drawn.get(weapon._get_address())
    if entry is None:
        return
    zoomed = weapon.ZoomState.name in ZOOMED
    if entry.get("zoomed") == zoomed:
        return
    entry["zoomed"] = zoomed
    comp = entry["comp"]()
    if comp is not None and entry["scoped"]:
        comp.SetHidden(zoomed)
    elif comp is not None:
        _drop_body(weapon, entry, comp, zoomed)
    psc = entry["reticle"]() if entry["reticle"] is not None else None
    if psc is not None:
        psc.SetHidden(not zoomed)
        if zoomed:
            psc.ActivateSystem(True)


_mount_tries: dict[int, int] = {}  # weapon address -> rebuilds in a row that were meant to hide the scope mount


def _mount_shown(weapon: UObject) -> bool:
    """Is a scope-mount bracket (fragment Acc_Scope_Mount*) in the gun's mesh?"""
    return any(str(n).startswith("Acc_Scope_Mount") for n in weapon.FirstPersonMesh.GestaltData.PartMeshNames)


# A scope-mount accessory (Sniper_Accessory_Mount1/2/3) draws a big bracket that a sniper scope swallows, but a small
# close-range sight leaves it sticking out above the rail like a floating wedge (seen 2026-10-11). Every rebuild of a
# gun with such a sight (core.rebuild: the Maliwan element swap, ...) blanks the mount part's mesh name while the mesh
# is built, so the bracket is never drawn and the accessory (its stats) stays.
@contextlib.contextmanager
def _mount_guard(weapon: UObject):
    saved = []
    try:
        if _foreign_sight(weapon) is not None:
            data = weapon.DefinitionData
            saved = [(p, p.GestaltModeSkeletalMeshName) for p in (data.Accessory1PartDefinition, data.Accessory2PartDefinition)
                     if p is not None and str(p.GestaltModeSkeletalMeshName).startswith("Acc_Scope_Mount")]
            for p, _ in saved:
                p.GestaltModeSkeletalMeshName = "None"
    except Exception:  # noqa: BLE001
        unrealsdk.logging.error("[BL3Weapons] sights mount guard: " + traceback.format_exc())
    try:
        yield
    finally:
        for p, name in saved:
            p.GestaltModeSkeletalMeshName = name


if not any(g.__name__ == "_mount_guard" for g in mesh_guards):
    mesh_guards.append(_mount_guard)


def _hide_mount(weapon: UObject) -> bool:
    """Rebuild the gun (the guard hides the mount) if its mesh shows a mount: for guns loaded with the mount already
    in (saved guns, map changes). Returns True if it rebuilt the gun."""
    if not _mount_shown(weapon):
        return False
    rebuild(weapon)
    log(f"sights: scope mount hidden on {weapon.DefinitionData.BalanceDefinition.Name}")
    return True


# A sight's mesh is textured for its own gun type's material (every type has its own texture sheets per maker and
# rarity), so on the sniper's material it showed another part of the sheet: noise (seen 2026-10-20). It gets the source
# type's plain material of the sniper's maker and rarity instead (Hyperion 3 sniper -> the pistol's Mat_Hyperion_3).
_PLAIN_MATERIAL = re.compile(r"Mat(?:erial)?_([A-Za-z]+)_(\d)")
_source_materials: dict[tuple[str, str, int], str] = {}  # (type package, maker, rarity 1-4) -> material part path


def _maker_key(name: str) -> str:
    return {"banditmade": "bandit", "tedior": "tediore"}.get(name.lower(), name.lower())  # BL2's own spellings


def _source_material(weapon: UObject, definition: str) -> UObject | None:
    """The material to draw the foreign sight with (None: keep the sniper's)."""
    if not _source_materials:
        for part in unrealsdk.find_all("WeaponPartDefinition", exact=False):
            package, _, name = part._path_name().partition(".ManufacturerMaterials.")
            found = _PLAIN_MATERIAL.fullmatch(name)
            if found and package in GESTALT_DEFS:
                _source_materials[(package, _maker_key(found[1]), int(found[2]))] = part._path_name()
    package = next((p for p, d in GESTALT_DEFS.items() if d == definition), None)
    data = weapon.DefinitionData
    own = _PLAIN_MATERIAL.match(data.MaterialPartDefinition.Name) if data.MaterialPartDefinition is not None else None
    tier = min(int(own[2]), 4) if own else 3
    makers = [_maker_key(own[1])] if own else []
    makers.append(_maker_key(data.SightPartDefinition.Name.rpartition("_")[2]))  # (a type without the sniper's maker: the sight's own)
    path = next((_source_materials[k] for m in makers if (k := (package, m, tier)) in _source_materials), None)
    if path is None:
        path = next((v for k, v in _source_materials.items() if k[0] == package and k[2] == tier), None)
    return unrealsdk.find_object("WeaponPartDefinition", path).Material if path else None


def _zoom(weapon: UObject, entry: dict) -> None:
    """A sniper with a close-range sight zooms to the slider's field of view, not the sniper's own (about 20: the zoom of a scope).
    The game recomputes the zoom when a gun is equipped, so this is checked with every draw check."""
    fov = float(zoom_fov.value)
    if abs(weapon.ZoomedEndFOV - fov) < 0.01:
        return
    entry.setdefault("fov0", weapon.ZoomedEndFOV)
    weapon.ZoomedEndFOV = weapon.ZoomedEndFOVBaseValue = weapon.ReplicatedZoomedEndFOVBaseValue = fov


def draw(weapon: UObject) -> None:
    """Keep the held gun's foreign sight drawn (build once per gun and sight, follow material changes)."""
    addr = weapon._get_address()
    wanted = _foreign_sight(weapon)
    entry = _drawn.get(addr)
    if wanted is None or weapon.FirstPersonMesh is None:
        if entry is not None:
            _remove(addr)
        return
    part, definition, names = wanted
    sight = part._path_name()
    if _mount_tries.get(addr, 0) < 3:  # (3 failed tries in a row at most: the rebuild must not loop if it leaves the mount in)
        if _hide_mount(weapon):  # the gun's mesh was rebuilt: drop the old sight component, build it on the next check
            _mount_tries[addr] = _mount_tries.get(addr, 0) + 1 if _mount_shown(weapon) else 0
            if entry is not None:
                _remove(addr)
            return
        _mount_tries[addr] = 0  # no mount in the mesh
    if entry is not None and (entry["comp"]() is None or entry["sight"] != sight):
        _remove(addr)
        entry = None
    if entry is None:
        if not names:
            return
        comp = _build(weapon, definition, names)
        if comp is None:
            return
        scoped = bool(part.bScoped)
        psc = None if scoped else _attach_reticle(weapon, comp, part, names)
        entry = _drawn[addr] = {"weapon": WeakPointer(weapon), "comp": WeakPointer(comp), "sight": sight, "material": None,
                                "placed": _want(weapon, False), "scoped": scoped,
                                "definition": definition, "names": names, "fp_z": None,
                                "reticle": WeakPointer(psc) if psc is not None else None, "zoomed": None}
        log(f"sights: drawing {sight} ({'+'.join(names)}) on {weapon.DefinitionData.BalanceDefinition.Name}")
    _zoom(weapon, entry)
    aimed = bool(entry.get("zoomed")) and not entry["scoped"]
    if entry["placed"] != _want(weapon, aimed):  # sliders moved / body changed
        entry["placed"] = place(weapon, entry["comp"](), definition, names, aimed)
    material = weapon.FirstPersonMesh.GetMaterial(0)
    key = material._get_address() if material is not None else 0
    if material is not None and entry["material"] != key:
        comp = entry["comp"]()
        try:
            source = _source_material(weapon, definition)
        except Exception:  # noqa: BLE001
            unrealsdk.logging.error("[BL3Weapons] sights material: " + traceback.format_exc())
            source = None
        for i in range(max(comp.GetNumElements(), 1)):
            comp.SetMaterial(i, source or material)
        entry["material"] = key


def drop_drawn() -> None:
    for addr in list(_drawn):
        _remove(addr)
    _drawn.clear()
    _mount_tries.clear()


SCOPES = ("Dahl", "Vladof", "Maliwan", "Jakobs", "Hyperion")


SWAP_ACCESSORIES = ("Bayonet1", "Bipod1_Accuracy", "Bipod2_Critical", "Foregrip_Stability")  # sniper accessories with no bracket


def drop_mounts(weapon: UObject) -> None:
    """A new sniper that keeps a close-range sight swaps its scope-mount accessories for another accessory (so it keeps a
    bonus): the bracket floats above the gun in the inspect and shop views, which never draw the sight (see _mount_guard
    for the held gun)."""
    data = weapon.DefinitionData
    mounts = [f for f in ("Accessory1PartDefinition", "Accessory2PartDefinition")
              if getattr(data, f) is not None and str(getattr(data, f).GestaltModeSkeletalMeshName).startswith("Acc_Scope_Mount")]
    swaps = [p for p in (find_part_by_path(f"GD_Weap_SniperRifles.Accessory.Sniper_Accessory_{a}") for a in SWAP_ACCESSORIES) if p is not None]
    if not mounts or len(swaps) < len(mounts):
        return
    fresh = copy.copy(data)
    for field, part in zip(mounts, random.sample(swaps, len(mounts))):
        setattr(fresh, field, part)
    weapon.DefinitionData = fresh
    log(f"sights: new sniper's scope mount swapped for {', '.join(getattr(fresh, f).Name for f in mounts)}")


def reroll(weapon: UObject) -> None:
    """A new sniper that rolled a close-range sight keeps it with the probability that makes close-range sights
    `chance` percent of new snipers, else gets one of the five manufacturer scopes. (Called after the game rolls the
    parts: ChooseRandomParts only runs for new guns, never for a saved one loading.)"""
    if not enabled.value:
        return
    from . import heartshatter

    if heartshatter.has_fingerprint(weapon):  # the HeartShatter's Dahl pistol sight is the point of the gun: never reroll it
        return
    data = weapon.DefinitionData
    part = data.SightPartDefinition
    wtype = data.WeaponTypeDefinition
    if part is None or wtype is None or not wtype._path_name().startswith("GD_Weap_SniperRifles."):
        return
    if part._path_name().partition(".Sight.")[0] not in SOURCE_PACKAGES:
        return
    foreign = len(close_sights())
    share = foreign / (foreign + len(SCOPES))  # what the even pick gives close-range sights
    keep = min(1.0, chance.value / 100 / share)
    if random.random() < keep:
        drop_mounts(weapon)
        return
    scope = find_part_by_path(f"GD_Weap_SniperRifles.Sight.Sniper_Sight_{random.choice(SCOPES)}")
    if scope is None:
        return
    fresh = copy.copy(data)
    fresh.SightPartDefinition = scope
    weapon.DefinitionData = fresh
    log(f"sights: new sniper rerolled {part.Name} -> {scope.Name}")


def find_part_by_path(path: str) -> UObject | None:
    try:
        return unrealsdk.find_object("WeaponPartDefinition", path)
    except ValueError:
        return None


@hook("WillowGame.WillowWeapon:ChooseRandomParts", Type.POST, hook_identifier="bl3w_sights_roll")
def on_roll(obj: UObject, *_: object) -> None:
    try:
        reroll(obj)
    except Exception:  # noqa: BLE001
        unrealsdk.logging.error("[BL3Weapons] sights reroll: " + traceback.format_exc())


def _patch_safe() -> None:
    try:
        patch_lists()
    except Exception:  # noqa: BLE001
        unrealsdk.logging.error("[BL3Weapons] sights: " + traceback.format_exc())


@hook("WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie", Type.POST, hook_identifier="bl3w_sights_arrived")
def on_map(*_: object) -> None:
    _patch_safe()


@hook("Engine.WorldInfo:NotifyMatchStarted", Type.POST, hook_identifier="bl3w_sights_world")
def on_world(*_: object) -> None:
    _patch_safe()


_checked = 0.0
_dev_checked = 0.0
_dev_on = False
_draw_failures = 0  # a failing draw must not build a new component every check


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_sights_tick")
def tick(*_: object) -> None:
    global _checked, _draw_failures, _dev_checked, _dev_on
    t = time.monotonic()
    if _drawn and _draw_failures < 3:
        try:
            held = held_frame()
            if held is not None:
                follow(held)
        except Exception:  # noqa: BLE001
            _draw_failures += 1
            unrealsdk.logging.error(f"[BL3Weapons] sights follow ({_draw_failures}/3): " + traceback.format_exc())
    if t - _checked > 0.25 and _draw_failures < 3:  # a held gun's sight changes rarely: 4 checks a second are plenty
        _checked = t
        try:
            for addr, entry in list(_drawn.items()):
                if entry["weapon"]() is None:
                    _drawn.pop(addr, None)
            weapon = held_frame()
            if weapon is not None:
                draw(weapon)
        except Exception:  # noqa: BLE001
            _draw_failures += 1
            unrealsdk.logging.error(f"[BL3Weapons] sights draw ({_draw_failures}/3): " + traceback.format_exc())
    if t - _dev_checked > 1.0:
        _dev_checked, _dev_on = t, DEV_FLAG.exists()
    if not _dev_on or not EXEC.exists():
        return
    try:
        code = EXEC.read_text(encoding="utf-8")
        EXEC.unlink()
        import sys

        exec(compile(code, str(EXEC), "exec"),
             {"sights": sys.modules[__name__], "say": log, "HERE": HERE, "held_weapon": held_weapon,
              "unrealsdk": unrealsdk, "get_pc": get_pc})
    except Exception:  # noqa: BLE001
        unrealsdk.logging.error("[BL3Weapons] sights exec failed:\n" + traceback.format_exc())


HOOKS = [on_map, on_world, on_roll, tick]
