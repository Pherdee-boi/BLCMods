"""A frost layer on frozen enemies (RESEARCH.md §6.32): TPS's coat of ice, built for BL2.

TPS ices a frozen enemy over through its creature material: its freeze sets the material's death-texture layer
(P_DeathsValue, p_DeathTextureAdd) and TPS's layer is ice. BL2's creature material has those parameters, but its layer
is the slag goo (element.py's tint recolours it, which is all it can do). So a frozen enemy gets a shell instead: a second
skeletal mesh component with the enemy's own mesh, posed by the enemy's (`ParentAnimComponent`: it copies the bones,
so it holds the frozen pose), drawn with a translucent frost material compiled at runtime (`FROST`, shaders.py): pale
ice, brighter where it faces up, TPS's frost and a cracked-ice pattern projected from three sides (a frozen enemy
stands still), and a bright rim where the surface turns away from the camera. The shell has the body's vertices, so it
lies exactly on the body. What the enemy wears on its bones (a bullymong's spikes, a helmet: static meshes attached to
the body) gets a frost copy at the same bone and offset; its gun (another actor's) doesn't. A thing that comes off
while it is frozen (a Goliath's helmet, shot off) takes its frost with it: the copy alone stayed on, a helmet of frost
(`_shed`, twice a second).

What it can't follow: a body its own material swells (a Raging Goliath's chest and arms, `p_MuscleFat` on BL2's
creature material: the vertices are pushed out when drawn). The shell has the unswollen shape and lies inside there;
that part shows the tint alone (RESEARCH.md §6.55).

It goes on when an enemy freezes (element.ON_FREEZE) and comes off when the freeze ends, thawed or dead (element.ON_THAW); a
shattered body is hidden, shell and all (frozenfx.py). The material compiles at the main menu with the particles'
(core/particles.py EXTRA_SPECS); until it has, frozen enemies get the tint alone.
"""

from __future__ import annotations

import copy
import time
from typing import Any

import unrealsdk
from unrealsdk.unreal import UObject, WeakPointer

from ..core import actors, later, particles, shaders, tpsports
from . import element

FROST = shaders.Shader(
    name="FrostLayer", blend=shaders.TRANSLUCENT, usage=("bUsedWithSkeletalMesh",),
    world_normal=True, world_position=True,
    textures=(("Frost", tpsports.carried("FX_CO_Gladiator.Textures.T_FX_GladShield_Ice")),
              ("Cracks", tpsports.carried("Prop_Glacier.Textures.FrozenLake"))),
    scalars=(("Tile", 72.0), ("Amount", 1.0), ("Brightness", 1.0)),
    code="""float3 N = normalize(WorldNormal);
float facing = saturate(abs(normalize(Parameters.TangentCameraVector).z));
float rim = (1.0 - facing) * (1.0 - facing);
float3 w = N * N;
w *= w;
w /= max(w.x + w.y + w.z, 0.0001);
float3 p = WorldPos / Tile;
float frost = dot(tex2D(TEX(0), p.yz).rgb, 0.333) * w.x + dot(tex2D(TEX(0), p.xz).rgb, 0.333) * w.y
            + dot(tex2D(TEX(0), p.xy).rgb, 0.333) * w.z;
float3 q = p * 2.3;
float cracks = dot(tex2D(TEX(1), q.yz).rgb, 0.333) * w.x + dot(tex2D(TEX(1), q.xz).rgb, 0.333) * w.y
             + dot(tex2D(TEX(1), q.xy).rgb, 0.333) * w.z;
float up = saturate(N.z * 0.5 + 0.5);
float3 ice = float3(0.6, 0.8, 1.0) * (0.55 + 0.45 * up) * (0.55 + 0.9 * frost);
float3 rgb = ice + float3(0.85, 0.95, 1.0) * (1.2 * rim + 0.6 * pow(saturate(cracks), 3.0));
float a = saturate(0.35 + 0.45 * frost + 0.55 * rim) * Amount;
return float4(min(rgb * Brightness, 4.0), a);
""",
)
_QUIET = ("CastShadow", "bCastDynamicShadow", "CollideActors", "BlockActors", "BlockZeroExtent", "BlockNonZeroExtent",
          "BlockRigidBody", "bAcceptsStaticDecals", "bAcceptsDynamicDecals")
_PLACEMENT = ("Translation", "Rotation", "Scale", "Scale3D", "AbsoluteTranslation", "AbsoluteRotation", "AbsoluteScale")
# enemy path -> (cover, what holds it, what it is the frost of: None for the body's shell) WeakPointers, while it's frozen
_shells: dict[str, list[tuple[Any, Any, Any]]] = {}
SHED_EVERY = 0.5                    # seconds between two looks at whether what an enemy wore is still on it
_next_shed = [0.0]
_ATTACHED = actors.Field("ActorComponent", "bAttached")     # read for every cover of every frozen enemy: by its field
_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] frost layer: {msg}")


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        _log(msg)


def _material() -> UObject | None:
    """The frost material, once compiled (never compiled here: a freeze mid-fight is no time for a hitch)."""
    return shaders.material(FROST) if FROST.name in shaders.compiled_names() else None


def _shell(pawn: UObject, material: UObject) -> UObject | None:
    """A new shell for `pawn`: its mesh, posed by its mesh, every section frost, no shadow, no collision."""
    mesh = pawn.Mesh
    skeleton = mesh.SkeletalMesh if mesh is not None else None
    if skeleton is None:
        return None
    shell = unrealsdk.construct_object("SkeletalMeshComponent", pawn)
    shell.SetSkeletalMesh(skeleton)
    shell.SetParentAnimComponent(mesh)
    # Its bones come placed as the body's, the body's offset and scale included: the shell itself keeps no transform
    # (copying the body's drew it at 0.7 x 0.7 the size, at a bullymong's feet: bl2-test, 2026-10-03).
    for prop in ("ForcedLodModel", "MinLodModel"):
        setattr(shell, prop, getattr(mesh, prop))
    for prop in _QUIET:
        setattr(shell, prop, False)
    for index in range(int(mesh.GetNumElements())):
        shell.SetMaterial(index, material)
    return shell


def _covers(pawn: UObject, material: UObject) -> list[tuple[UObject, UObject]]:
    """A frost copy of each static mesh the enemy wears on its body's bones, attached the same way; returns each
    with what it is the copy of."""
    mesh = pawn.Mesh
    covers = []
    # Copies of the entries, taken before anything is attached. Attaching a cover adds to this array, the engine moves
    # it, and the array's own entries read after that are freed memory: `list(mesh.Attachments)` walked those, and a
    # bandit with a helmet, a shoulder plate and two guns took the user's game down at its third (0.4.10, RESEARCH.md
    # §6.57).
    for attachment in [copy.copy(entry) for entry in mesh.Attachments]:
        part = attachment.Component
        if (part is None or part.Class.Name != "StaticMeshComponent" or part.StaticMesh is None
                or part.Outer != pawn or part.HiddenGame):
            continue                              # not its own (a gun is another actor's), or not shown
        cover = unrealsdk.construct_object("StaticMeshComponent", pawn)
        cover.SetStaticMesh(part.StaticMesh)
        for prop in _PLACEMENT:
            setattr(cover, prop, getattr(part, prop))
        for prop in _QUIET:
            setattr(cover, prop, False)
        for index in range(int(part.GetNumElements())):
            cover.SetMaterial(index, material)
        mesh.AttachComponent(cover, attachment.BoneName, attachment.RelativeLocation, attachment.RelativeRotation,
                             attachment.RelativeScale)
        covers.append((cover, part))
    return covers


def coat(pawn: UObject) -> bool:
    """Put the frost layer on a (frozen) enemy. True if it has one now."""
    if not actors.is_live_actor(pawn):
        return False
    key = pawn._path_name()
    if any(cover() is not None for cover, _holder, _part in _shells.get(key, ())):
        return True
    material = _material()
    if material is None:
        _once("no-material", "frost material not compiled yet: frozen enemies get the tint alone")
        return False
    shell = _shell(pawn, material)
    if shell is None:
        return False
    pawn.AttachComponent(shell)
    held = [(WeakPointer(shell), WeakPointer(pawn), None)]
    try:
        held += [(WeakPointer(cover), WeakPointer(pawn.Mesh), WeakPointer(part)) for cover, part in _covers(pawn, material)]
    except Exception as ex:  # noqa: BLE001 - the body's coat is the most of it
        _once(f"covers:{type(ex).__name__}", f"what it wears not coated: {type(ex).__name__}: {ex}")
    _shells[key] = held
    _once("first", f"first frost layer, on {pawn.Name} (and {len(held) - 1} things it wears)")
    return True


def uncoat(pawn: UObject) -> None:
    """Take the frost layer off."""
    for cover, holder, _part in _shells.pop(pawn._path_name(), ()):
        component, owner = cover(), holder()
        if component is not None and owner is not None:
            owner.DetachComponent(component)


def _shed() -> None:
    """What an enemy wore and has lost since it froze loses its frost copy (element.WHILE_CHILLED)."""
    now = time.monotonic()
    if now < _next_shed[0]:
        return
    _next_shed[0] = now + SHED_EVERY
    for held in _shells.values():
        for index in range(len(held) - 1, 0, -1):         # the body's shell is the first, and stays
            cover, holder, part = held[index]
            worn = part() if part is not None else None
            if worn is not None and _ATTACHED(worn):
                continue
            component, owner = cover(), holder()
            if component is not None and owner is not None:
                owner.DetachComponent(component)
            del held[index]


def _on_freeze(pawn: UObject, _instigator: Any) -> None:
    # The coat is looks, and 1.2 ms an enemy: it waits its turn (core/later.py), a frame or a few after the freeze.
    later.do(_coat_frozen, WeakPointer(pawn))


def _coat_frozen(ref: WeakPointer) -> None:
    """Its turn has come: the coat, if the enemy is still there and still frozen."""
    if not later.waiting():                       # the last of a batch: forget the coats of enemies that are gone
        for key in [k for k, held in _shells.items() if all(cover() is None for cover, _holder, _part in held)]:
            del _shells[key]
    pawn = ref()
    if pawn is not None and element.is_frozen(pawn):
        coat(pawn)


def _on_thaw(pawn: UObject, _alive: bool) -> None:
    uncoat(pawn)


element.ON_FREEZE_LOOK.append(_on_freeze)
element.ON_THAW.append(_on_thaw)
element.WHILE_CHILLED.append(_shed)
if FROST not in particles.EXTRA_SPECS:
    particles.EXTRA_SPECS.append(FROST)      # compiled at the main menu, with the particles' materials
