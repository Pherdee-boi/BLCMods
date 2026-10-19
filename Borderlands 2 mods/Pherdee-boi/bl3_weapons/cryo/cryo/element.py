"""Cryo for BL2 (M3): TPS's cryo damage, status and freeze, rebuilt at runtime (RESEARCH.md §6.6, §6.8, §6.13).

TPS keeps cryo in `Startup.upk` as three damage types, one status and one skill (`GD_Ice.*`). BL2 has none
of it, so the objects are built here from BL2's shock ones, under TPS's own paths so that her converted
skills' imports can find them. What BL2's engine can't do is scripted:
- **Freeze.** TPS freezes natively when `Status_Ice` lands. Here a hook on
  `StatusEffectsComponent.ApplyStatusEffect` freezes (freeze.py's AI hold), slows or skips per enemy
  (rules.py), for the status' duration, and ice-tints the enemy's material.
- **Frozen bonuses.** TPS's `Skill_IceEffects` on a frozen enemy: received melee +250%, explosive +200%,
  crit bonus +200%. Applied in `WillowDamagePipeline.AdjustDamage`, with the slag x frozen cap (3x in
  Normal and TVHM, a setting, on by default).
- **Per-enemy cryo damage** (ice beings take x0.05 to x0.3; enemies that resist fire, shock and corrosive resist
  cryo too, rules.resistances), in the same hook. The status' chance and duration follow the same rules.
- **Shatter.** An enemy that dies frozen bursts (BL2's full-body gib, which replicates) and its shatter freezes
  enemies within SHATTER_RADIUS: TPS's `OnShatter` explodes `Explosion_IceMaster_ShieldNova` with no impact
  damage, a 100% cryo status chance and status damage at a tenth of the ice nova value.
Frozen players are data, as in BL2's own statuses: the status switches a skill on and off
(`Behavior_ActivateSkill` / `Behavior_DeactivateSkill`), and the skill carries TPS's players-only FootSpeed
x(1 - 1.25), which stops them walking. Visible numbers are cyan through the damage type's HUDDamageColor.
- **Looks.** A frozen enemy takes the ice tint (`LOOK`). The status is BL2's slow type (BL2 has no freeze), whose own
  particles (a green swirl with yellow rings) are switched off (`_mute_slow_look`). A cryo gun's card ends with TPS's
  "Freezes enemies solid." (`_card_note`; its stat rows are guns.py's).
- **Only pawns.** An interactive object can carry statuses too (a radio, a piano); cryo's is a freeze, and an object has
  no brain to hold: the status is kept off anything that isn't a pawn (`_before_status`).

Clocks: a thaw is due at a time on the map's clock (`WorldInfo.TimeSeconds`). That clock starts again when the world
does (back to the main menu and in again), and keeps running through the game's own travel, which swaps the map inside
one world. So nothing here may wait for a time read earlier than "a moment ago" (`_thaw_tick`).

Enemies are kept by weak pointer (`_chilled`; freeze.py's `_held`): the game's own travel frees a map's actors without
a word, and asking a freed one anything ("are you still there?" included) reads freed memory. That took the dev copy
down at the first travel with frozen enemies left behind (2026-10-05).
"""

from __future__ import annotations

import random
import time
from typing import Any

import unrealsdk
from mods_base import BoolOption, get_pc, hook
from unrealsdk.hooks import Block, Type
from unrealsdk.unreal import BoundFunction, UObject, WeakPointer, WrappedStruct

from ..core import actors, later, maps, texts
from . import attributes, freeze, rules

PACKAGE = "GD_Ice"
IMPACT = f"{PACKAGE}.DamageType.DmgType_Ice_Impact"
IMPACT_NO_FREEZE = f"{PACKAGE}.DamageType.DmgType_Ice_Impact_NoFreeze"
STATUS_DAMAGE = f"{PACKAGE}.DamageType.DmgType_Ice_Status"
STATUS = f"{PACKAGE}.StatusEffect.Status_Ice"
SKILL = f"{PACKAGE}.Skills.Skill_IceEffects"
LOOK = f"{PACKAGE}.Effects.CE_Ice_Frozen"

# BL2 templates, all in Startup.upk (always loaded).
_SHOCK_IMPACT = "GD_Shock.DamageType.DmgType_Shock_Impact"
_SHOCK_STATUS_DAMAGE = "GD_Shock.DamageType.DmgType_Shock_Status"
_SHOCK_STATUS = "GD_Shock.StatusEffect.Status_Shock"
_SHOCK_SKILL = "GD_Shock.Skills.Skill_ShockEffects"
_AMP_SKILL = "GD_Amp.Skills.Skill_AmplifyDamage"       # a SkillEffectData to copy the enum values from
_FOOT_SPEED = "D_Attributes.GameplayAttributes.FootSpeed"
_SLAG_LOOK = "GD_CoordinatedEffects.Shared.Status.Slag_Status"
_NORMAL_RESIST = "GD_Balance_HealthAndDamage.DamageMultipliers.Init_NormalElementResistMultiplier_ByPlaythrough"
_HIGH_RESIST = "GD_Balance_HealthAndDamage.DamageMultipliers.Init_HighElementResistMultiplier_ByPlaythrough"

# TPS's values (GD_Ice, decoded 2026-09-30/10-01).
HUD_COLOR = (39, 255, 199)                  # DmgType_Ice_Impact.HUDDamageColor (stored B, G, R, A)
TRAIL_COLOR = (0.0, 1.0, 0.7185, 2.0)       # DmgType_Ice_Impact.DamageColor
BASE_DURATION = 5.0                         # Status_Ice.BaseDuration
# Status_Ice.DamageSurfaceChanceModifiers: surface -> (chance %, spread chance %, spread decay)
SURFACE_CHANCES = {"Generic": (15.0, 0.0, 0.0), "Flesh": (15.0, 20.0, 0.5),
                   "Armor": (15.0, 10.0, 0.5), "Shield": (5.0, 0.0, 0.5)}
NO_FREEZE_SCALES = (0.8, 0.7, 0.5)          # DmgType_Ice_Impact_NoFreeze: flesh, armor, shield
# DmgType_Ice_Impact's line on a gun's card (where shock's says "Highly effective vs Shields.") and its colour, R G B
CARD_NOTE = ("Freezes enemies solid.", (0, 210, 255))
# ... in the game's other languages: TPS's own line on its ice damage type (core/texts.py, `picked`)
CARD_NOTE_TEXT = ("gd_ice", "DamageType.DmgType_Ice_Impact:AttributePresentationDefinition_5 AttributePresentationDefinition")
FROZEN_FOOT_SPEED = -1.25                   # Skill_IceEffects: FootSpeed MT_Scale, players only

# Skill_IceEffects: what a frozen enemy takes (flat, RESEARCH.md §6.8 "Frozen bonuses" setting default).
FROZEN_MELEE = 3.5          # ReceivedMeleeDamageModifier, MT_Scale +2.5
FROZEN_EXPLOSIVE = 3.0      # ExplosiveImpactDamageModifier, MT_Scale +2.0
FROZEN_CRIT_BONUS = 2.0     # PlayerCriticalHitBonus, MT_Scale +2.0: the crit's extra over a normal hit x3
CRIT_MULTIPLIER = 2.0       # BL2's base crit, only if a crit hit's summary has no crit extra (see frozen_bonus)
BONUS_CAP = 3.0             # slag x frozen, Normal and TVHM (RESEARCH.md §6.8)
SLOW_FACTOR = 0.5           # bosses, giants, jets (RESEARCH.md §6.13 open item)
SHATTER_RADIUS = 192.0      # Explosion_IceMaster_ShieldNova's first band (TPS sets no Behavior_Explode radius)
ICE_TINT = (1.2, 2.4, 3.6)  # p_ElementalDeathColor while frozen; BL2's slag is (2, 0, 2). Tune in game.
# The status particles' instance parameter a slow status turns on (the cryo status is one: _mute_slow_look)
SLOW_LOOK_PARAMETER = "On_Slow_Character"

cap_option = BoolOption(
    "Cap slag + cryo bonus (Normal/TVHM)",      # the identifier its saved value is kept under
    True,
    display_name="Slag + cryo bonus cap",       # short enough for BL2's options menu (0.3.1)
    description=("On: in Normal and True Vault Hunter Mode a slagged, frozen enemy takes at most 3x from the two "
                 "bonuses together. Ultimate Vault Hunter Mode is never capped."),
)

_RF_ROOTSET = 0x4000
_UVHM = 2                   # WillowPlayerController.GetCurrentPlaythrough(): 0 Normal, 1 TVHM, 2 UVHM

_objects: dict[str, UObject] = {}
_ours: list[UObject] = []           # our damage types, for the damage hook
_chilled: dict[str, dict[str, Any]] = {}  # enemy path -> {"pawn" (a weak pointer), "mode", "until", "kind"}
_next_check = [0.0]
CHECK_EVERY = 0.1                   # seconds of the map's clock between two looks at who is due to thaw
# status particle system path -> where its first slow emitter level is (emitter, level), or None: it has none
_muted: dict[str, tuple[int, int] | None] = {}
_pawn_class: list[UObject | None] = [None]
# A thaw is 1 ms (the hold, the tint, the coat, the icicles). A crowd that froze together thaws together: 57 at once
# were one 57 ms call (Spawn Multiplier at 25x, 2026-10-04). So a tick thaws for this long, and the rest wait a frame.
THAW_BUDGET = 0.002
_coordinator: list[WeakPointer | None] = [None]
_melee: list[UObject | None] = [None]
_shattering: dict[str, UObject | None] = {}     # enemy path -> killer, between Died PRE and POST
_last_cryo_kill = [-1.0]            # game time of the last death by a cryo hit (frozenfx.py's Bloodsplosion)
_cryo_hits: dict[Any, float] = {}   # enemy -> when a cryo hit last landed on it (time.monotonic), for the line above
_MOST_CRYO_HITS = 256
CRYO_KILL_WITHIN = 0.25             # seconds from a cryo hit to the enemy's death for the death to count as its
_write_back: list[bool | None] = [None]         # must a changed summary be written back whole? Asked once (below)
_said: set[str] = set()
AFTER_DAMAGE: list[Any] = []        # callables(hit: Hit) run after this module's own damage changes
ON_FREEZE: list[Any] = []           # callables(pawn, instigator controller): an enemy just froze (the host's game only)
# ... and its looks: callables(pawn, instigator or None), run before ON_FREEZE's, and in a joined game too (proxies.py)
ON_FREEZE_LOOK: list[Any] = []
ON_FROZEN_KILL: list[Any] = []      # callables(pawn, killer controller, damage source class): a frozen enemy died
IMMUNE: list[Any] = []              # callables(pawn) -> bool: it never takes the cryo status (gear.py's shields)
ON_PLAYER_FROZEN: list[Any] = []    # callables(player pawn, instigator controller): the cryo status landed on a player
ON_THAW: list[Any] = []             # callables(pawn, alive): an enemy's freeze ended (thawed, or it died frozen)
ON_SHATTER: list[Any] = []          # callables(pawn, gibbed): a frozen enemy died and burst (gibbed: BL2 gibbed it)
WHILE_CHILLED: list[Any] = []       # callables(): ten times a second while any enemy is frozen or slowed


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] cryo: {msg}")


def _once(key: str, msg: str) -> None:
    if key not in _said:
        _said.add(key)
        _log(msg)


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _root(obj: UObject) -> UObject:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET
    return obj


def _package(path: str) -> UObject:
    """A runtime package (and its outers), e.g. GD_Ice.DamageType."""
    pkg = _find("Package", path)
    if pkg is None:
        outer_path, _, name = path.rpartition(".")
        outer = _package(outer_path) if outer_path else None
        pkg = unrealsdk.construct_object("Package", outer, name)
    return _root(pkg)


def _make(cls: str, path: str, template_path: str) -> UObject:
    obj = _find(cls, path)
    if obj is None:
        outer_path, _, name = path.rpartition(".")
        template = unrealsdk.find_object(cls, template_path)
        obj = unrealsdk.construct_object(cls, _package(outer_path), name, 0, template)
    return _root(obj)


def _init(constant: float = 1.0, definition: UObject | None = None, scale: float = 1.0) -> WrappedStruct:
    return unrealsdk.make_struct("AttributeInitializationData", BaseValueConstant=constant,
                                 BaseValueAttribute=None, InitializationDefinition=definition,
                                 BaseValueScaleConstant=scale)


def _impact_type(obj: UObject, status: UObject | None, scales: tuple[float, float, float],
                 taken: UObject | None = None) -> None:
    normal = unrealsdk.find_object("AttributeInitializationDefinition", _NORMAL_RESIST)
    high = unrealsdk.find_object("AttributeInitializationDefinition", _HIGH_RESIST)
    obj.FleshDamageModifier = _init(1.0, normal, scales[0])
    obj.ArmorDamageModifier = _init(1.0, normal, scales[1])
    obj.ShieldDamageModifier = _init(1.0, high, scales[2])
    obj.StatusEffect = status
    _paint(obj)
    # The damaged pawn's cryo-damage-taken attribute (attributes.py: a noun at 1.0 unless a skill such as Cold Money's
    # enemy debuff raises it), which BL2 multiplies in natively, as it does ShockImpactDamageModifier for shock.
    obj.CharacterDamageTypeModifierAttribute = taken


def _paint(obj: UObject) -> None:
    r, g, b = HUD_COLOR
    obj.HUDDamageColor = unrealsdk.make_struct("Color", R=r, G=g, B=b, A=255)
    tr, tg, tb, ta = TRAIL_COLOR
    obj.DamageColor = unrealsdk.make_struct("LinearColor", R=tr, G=tg, B=tb, A=ta)
    # BL2 has no DAMAGE_TYPE_Ice. Impact is the neutral value (no shock deaths or shock resistances); per-enemy
    # cryo resistance is rules.py's job, and the damage-taken attribute is _impact_type's.
    obj.DamageType = unrealsdk.find_enum("EDamageType").DAMAGE_TYPE_Impact
    obj.CharacterDamageTypeModifierAttribute = None
    obj.WeaponCardPresentations = []


def _card_note(impact: UObject) -> None:
    """TPS's line on a cryo gun's card, its own copy of shock's line (which `_paint` took off), under TPS's path."""
    shock = unrealsdk.find_object("WillowDamageTypeDefinition", _SHOCK_IMPACT).WeaponCardPresentations[0]
    note = None
    for separator in (":", "."):                # a subobject's path name takes ':' after its outer
        note = note or _find("AttributePresentationDefinition", f"{impact._path_name()}{separator}{shock.Name}")
    if note is None:
        note = unrealsdk.construct_object("AttributePresentationDefinition", impact, shock.Name, 0, shock)
    text, (r, g, b) = CARD_NOTE
    note.Description = texts.picked(*CARD_NOTE_TEXT, "Description", text)
    note.TextColor = unrealsdk.make_struct("Color", R=r, G=g, B=b, A=255)
    impact.WeaponCardPresentations = [_root(note)]


def _status(obj: UObject, damage_type: UObject) -> None:
    obj.StatusEffectType = unrealsdk.find_enum("EStatusEffectType").STATUS_EFFECT_Slow
    obj.BaseDuration = _init(BASE_DURATION)
    obj.BaseSpreadTimeInterval = _init(1.0)
    obj.BaseSpreadDistanceFromSource = _init(48.0)
    # TPS scales chance and duration by TPS-only freeze resistances; rules.py does that here, so the
    # shock ones the template carries must not apply.
    obj.TargetStatusEffectChanceModifier = _init(1.0)
    obj.TargetStatusEffectDurationModifier = _init(1.0)
    obj.TargetStatusEffectDamageModifier = _init(1.0)
    obj.DamageTypeDefinition = damage_type
    obj.OnApplication = []          # the template's are shock's: its skill, sound and dialog
    obj.OnDurationBegin = []
    obj.OnDurationEnd = []
    obj.AppliedEffectStatId = "None"
    screen = obj.ScreenParticleInit      # shock's screen crackle; TPS's frost is a TPS-only particle
    screen.Template = None
    obj.ScreenParticleInit = screen
    modifiers = []
    for old in obj.DamageSurfaceChanceModifiers:
        surface = str(getattr(old.SurfaceType, "name", old.SurfaceType)).removeprefix("DMGSURFACE_")
        chance, spread, decay = SURFACE_CHANCES.get(surface, SURFACE_CHANCES["Generic"])
        modifiers.append(unrealsdk.make_struct(
            "DamageSurfaceTypeModifier", SurfaceType=old.SurfaceType, BaseChance=_init(chance),
            BaseSpreadChance=_init(spread), SpreadChanceDecayMultiplier=_init(decay)))
    obj.DamageSurfaceChanceModifiers = modifiers


def _skill(obj: UObject) -> None:
    """TPS's Skill_IceEffects minus what is scripted here (the frozen bonuses, the enemy freeze)."""
    obj.SkillName = "Ice status"
    obj.BehaviorProviderDefinition = None       # the template's plays shock's look
    shape = unrealsdk.find_object("SkillDefinition", _AMP_SKILL).SkillEffectDefinitions[0]
    obj.SkillEffectDefinitions = [unrealsdk.make_struct(
        "SkillEffectData", AttributeToModify=unrealsdk.find_object("AttributeDefinition", _FOOT_SPEED),
        bIncludeDuelingTargets=False, bIncludeSelfAsTarget=False, bOnlyEffectTargetsInRange=False,
        bExcludeNonPlayerCharacters=True, EffectTarget=shape.EffectTarget, TargetInstanceDataName="None",
        TargetCriteria=shape.TargetCriteria, ModifierType=shape.ModifierType, BaseModifierValue=_init(FROZEN_FOOT_SPEED),
        GradeToStartApplyingEffect=shape.GradeToStartApplyingEffect, PerGradeUpgradeInterval=1,
        PerGradeUpgrade=_init(0.0), BonusUpgradeList=[])]


def _switch(status: UObject, skill: UObject) -> None:
    """The status turns the skill on when it lands and off when it ends, like BL2's shock status."""
    shock = unrealsdk.find_object("StatusEffectDefinition", _SHOCK_STATUS)
    on_template = next(b for b in shock.OnApplication if b is not None and b.Class.Name == "Behavior_ActivateSkill")
    off_template = next(b for b in shock.OnDurationEnd if b is not None and b.Class.Name == "Behavior_DeactivateSkill")
    on = _find("Behavior_ActivateSkill", f"{STATUS}.Behavior_ActivateSkill_0") or unrealsdk.construct_object(
        "Behavior_ActivateSkill", status, "Behavior_ActivateSkill_0", 0, on_template)
    off = _find("Behavior_DeactivateSkill", f"{STATUS}.Behavior_DeactivateSkill_0") or unrealsdk.construct_object(
        "Behavior_DeactivateSkill", status, "Behavior_DeactivateSkill_0", 0, off_template)
    on.SkillToActivate = skill
    off.SkillToDeactivate = skill
    status.OnApplication = [_root(on)]
    status.OnDurationEnd = [_root(off)]


def _curve_vector(value: tuple[float, float, float]) -> WrappedStruct:
    zero = unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0)
    out = unrealsdk.make_struct("Vector", X=value[0], Y=value[1], Z=value[2])
    points = [unrealsdk.make_struct("InterpCurvePointVector", InVal=t, OutVal=out, ArriveTangent=zero,
                                    LeaveTangent=zero, InterpMode=0) for t in (0.0, 1.0)]  # CIM_Linear
    return unrealsdk.make_struct("InterpCurveVector", Points=points, InterpMethod=0)


def _look(obj: UObject) -> None:
    """BL2's creature material has TPS's P_DeathsValue but not its ice texture parameter, so the
    frozen look is a tint, the way BL2's slag and corrosive looks are (Athena's bleed notes)."""
    obj.MaterialVectorParameters = [unrealsdk.make_struct(
        "CoordinatedVectorParameter", ParamName="p_ElementalDeathColor", ParamValueOverTime=_curve_vector(ICE_TINT))]
    obj.MutexName = "IceStatus"


def ensure_objects() -> dict[str, UObject]:
    """Build (once) and return the cryo objects. Needs Startup.upk, so call it from the main menu on."""
    if _objects and all(o is not None for o in _objects.values()):
        return _objects
    ice = attributes.ensure_attributes()
    status_damage = _make("WillowDamageTypeDefinition", STATUS_DAMAGE, _SHOCK_STATUS_DAMAGE)
    _impact_type(status_damage, None, (1.0, 1.0, 1.0), ice[attributes.TAKEN_STATUS])
    status_damage.bNoBlood = True
    status_damage.bNoPlayerFeedback = True
    status = _make("StatusEffectDefinition", STATUS, _SHOCK_STATUS)
    _status(status, status_damage)
    skill = _make("SkillDefinition", SKILL, _SHOCK_SKILL)
    _skill(skill)
    _switch(status, skill)
    impact = _make("WillowDamageTypeDefinition", IMPACT, _SHOCK_IMPACT)
    _impact_type(impact, status, (1.0, 1.0, 1.0), ice[attributes.TAKEN_IMPACT])
    _card_note(impact)
    impact.bNoBlood = True
    no_freeze = _make("WillowDamageTypeDefinition", IMPACT_NO_FREEZE, _SHOCK_IMPACT)
    _impact_type(no_freeze, None, NO_FREEZE_SCALES, ice[attributes.TAKEN_IMPACT])
    look = _make("CoordinatedEffectDefinition", LOOK, _SLAG_LOOK)
    _look(look)
    _objects.update({IMPACT: impact, IMPACT_NO_FREEZE: no_freeze, STATUS_DAMAGE: status_damage,
                     STATUS: status, SKILL: skill, LOOK: look})
    _ours[:] = [impact, no_freeze, status_damage]
    _log(f"built {len(_objects)} cryo objects under {PACKAGE}")
    return _objects


# ---------------------------------------------------------------------------------------------- freeze


def _now(_actor: UObject | None = None) -> float:
    """The map's clock. Asked of the engine (core/actors.py `now`): until 0.6.1 it was `actor.WorldInfo.TimeSeconds`,
    the slowest read there is on a controller, at every frame while anything was frozen (0.13 ms in a fight)."""
    return actors.now()


def _effect_coordinator() -> UObject | None:
    ref = _coordinator[0]
    coordinator = ref() if ref is not None else None
    if coordinator is None or not actors.is_live_actor(coordinator):
        willow = unrealsdk.find_class("WillowGlobals").ClassDefaultObject.GetWillowGlobals()
        coordinator = willow.GetEffectCoordinator() if willow is not None else None
        _coordinator[0] = WeakPointer(coordinator) if coordinator is not None else None
    return coordinator


def _tint(pawn: UObject, on: bool) -> None:
    look = _objects.get(LOOK)
    coordinator = _effect_coordinator()
    if look is None or coordinator is None:
        return
    if on:
        if not coordinator.HasEffect(Target=pawn, TheDefinition=look, EffectOwner=None):
            coordinator.PushEffect(Target=pawn, TheDefinition=look, EffectOwner=None, StartingPercent=0.0,
                                   IgnoreInstanceData="None")
    else:
        coordinator.PopEffect(Target=pawn, TheDefinition=look, EffectOwner=None, IncludeChainedEffects=True)


def chill(pawn: UObject, seconds: float, profile: rules.CryoProfile | None = None,
          resists: rules.Resistances = rules.NO_RESISTANCE) -> str:
    """Freeze, slow or skip one enemy for `seconds`, per its cryo rule and its BL2 status duration resistance
    (the shorter of the two applies). Refreshing extends, never shortens."""
    profile = profile or rules.profile_for(pawn)
    mode = profile.mode
    if mode in (rules.NONE, rules.IMMUNE):
        return mode
    seconds *= min(profile.duration if mode == rules.FREEZE else 1.0, resists.duration)
    key = pawn._path_name()
    state = _chilled.get(key)
    if state is not None and state["pawn"]() != pawn:
        # Another enemy had this name and left without dying or thawing (the player travelled on, or it was taken out
        # of the map): what is kept under the name is not this one's.
        _chilled.pop(key, None)
        freeze.forget(key)
        state = None
    until = _now(pawn) + seconds
    if state is not None and state["mode"] == mode:
        state["until"] = max(state["until"], until)
        return mode
    if mode == rules.FREEZE:
        if not freeze.freeze(pawn):
            return "failed"
        _tint(pawn, True)
    else:       # a partial resistance (the user's calls) takes that share off the slow too
        freeze.slow(pawn, 1.0 - (1.0 - SLOW_FACTOR) * resists.slow_strength)
    _chilled[key] = {"pawn": WeakPointer(pawn), "mode": mode, "until": until, "kind": profile.kind}
    return mode


def thaw(pawn: UObject) -> bool:
    state = _chilled.pop(pawn._path_name(), None)
    if state is None:
        return False
    freeze.release(pawn)
    if state["mode"] == rules.FREEZE:
        live = actors.is_live_actor(pawn)
        if live:
            _tint(pawn, False)
        _notify(ON_THAW, pawn, live and float(pawn.GetHealth()) > 0.0)
    return True


def release_all() -> int:
    """Every enemy cryo holds frozen or slowed is let go at once. For when the mod is switched off in the MODS menu:
    the tick that would thaw them stops with it, and they would stay as they are. Returns how many."""
    count = 0
    for key, held in list(_chilled.items()):
        pawn = held["pawn"]()
        if pawn is not None and actors.is_live_actor(pawn):
            count += thaw(pawn)
        else:
            _chilled.pop(key, None)
            freeze.forget(key)
    _shattering.clear()
    return count


def is_frozen(pawn: UObject | None) -> bool:
    if pawn is None or not _chilled:
        return False
    state = _chilled.get(pawn._path_name())
    return state is not None and state["mode"] == rules.FREEZE


def state() -> list[str]:
    return [f"{pawn.Name if (pawn := s['pawn']()) is not None else 'gone'}: {s['mode']} ({s['kind']}) until {s['until']:.1f}"
            for s in _chilled.values()]


def is_cryo_damage(definition: UObject | None) -> bool:
    """Is this one of cryo's damage types (a hit's, the hit's that doesn't freeze, the status' tick's)?"""
    return definition is not None and any(definition == ours for ours in _ours)


def _is_our_status(definition: UObject | None) -> bool:
    status = _objects.get(STATUS)
    return definition is not None and status is not None and definition == status


def _status_seconds(component: UObject, definition: UObject) -> float:
    for effect in component.ActiveStatusEffects:
        if effect.StatusEffectDefinition == definition and float(effect.Duration) > 0.0:
            return float(effect.Duration)
    return BASE_DURATION


def _is_enemy(pawn: UObject | None) -> bool:
    return pawn is not None and pawn.Class.Name != "WillowPlayerPawn"


def _is_pawn(actor: UObject) -> bool:
    cls = _pawn_class[0]
    if cls is None:
        cls = _pawn_class[0] = unrealsdk.find_class("Pawn")
    return actor.Class._inherits(cls)


@hook("WillowGame.StatusEffectsComponent:ApplyStatusEffect")
def _before_status(obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> Any:
    """Who never takes the cryo status: anyone wearing a freeze-immunity shield (IMMUNE), ice beings (TPS: their freeze
    chance is 0), and enemies BL2 keeps free of statuses (rules.resistances: no burning, shocking or corroding, or
    no slowing for a freeze or slow). Partial status resistance is a second roll, since the status' own chance doesn't
    read the enemy's resistances."""
    if not _is_our_status(args.StatusEffectDefinition):
        return None
    pawn = obj.Owner
    if pawn is not None and not _is_pawn(pawn):
        # An interactive object (the user's game, 2026-10-04: "'WillowInteractiveObject' object has no attribute
        # 'Controller'" at every such hit). Nothing of it can freeze, and the slow status' own look would show on it.
        return Block
    _mute_slow_look(pawn, args.HitRegion)
    if pawn is not None and _immune(pawn):
        return Block
    if not _is_enemy(pawn):
        return None
    profile, resists = rules.recent(pawn)       # kept a moment: `_after_status` asks again, and so does each hit
    if profile.mode == rules.IMMUNE:
        return Block
    if resists.chance <= 0.0 or (profile.mode in (rules.FREEZE, rules.SLOW) and resists.slow <= 0.0):
        return Block
    if resists.chance < 1.0 and random.random() >= resists.chance:
        return Block
    return None


def _mute_slow_look(pawn: UObject | None, region: UObject | None = None) -> int:
    """A status' particles come from one system: the hit region's own (`BodyHitRegionDefinition.ParticleSystemTemplate`)
    or the pawn's (`WillowPawn.GetDefaultStatusEffectsParticleSystemTemplate`); BL2's is FX_CHAR_Damage_Matrix's, cooked
    into each map. It holds every status' emitters, each switched on by its own instance parameter. The cryo status is
    a slow one (BL2 has no freeze), and slow draws "Dazed": a green swirl with yellow rings, which stayed on the body
    after death. No BL2 status is a slow one (Startup's four are fire, shock, corrosive and slag), so those emitters go
    off, before the status attaches its particles. Returns how many."""
    templates = []
    try:
        if region is not None:
            templates.append(region.ParticleSystemTemplate)
        if pawn is not None:
            templates.append(pawn.GetDefaultStatusEffectsParticleSystemTemplate())
    except Exception:  # noqa: BLE001 - nothing of the kind on this one: nothing more to mute
        pass
    muted = 0
    seen = None
    for template in templates:
        if template is not None and template != seen:
            seen = template
            muted += _mute(template)
    return muted


def _mute(template: UObject) -> int:
    """The slow emitters of one status particle system, off. A system is gone through once (that cost 0.6 ms at
    every cryo status, 0.4.6); after that one read says whether its first slow level is still off. It is on again
    when the game has loaded the system anew at the same path, which no event tells of. Until 0.4.9 the path alone was
    remembered, so such a system would have kept its rings (the user still saw them on Sanctuary's target dummy,
    2026-10-05; in the dev copy the system stayed the same object through every travel tried, and they never showed)."""
    key = template._path_name()
    if key in _muted:
        first = _muted[key]
        if first is None:
            return 0
        try:
            if not template.Emitters[first[0]].LODLevels[first[1]].bEnabled:
                return 0
        except (IndexError, AttributeError, TypeError):
            pass                                    # another system than the one looked at: gone through below
    first = None
    muted = 0
    for e_index, emitter in enumerate(template.Emitters):
        for l_index, lod in enumerate(emitter.LODLevels if emitter is not None else ()):
            if lod is None or lod.SpawnModule is None:
                continue
            rate = lod.SpawnModule.Rate.Distribution
            if (rate is not None and rate.Class.Name == "DistributionFloatParticleParameter"
                    and str(rate.ParameterName) == SLOW_LOOK_PARAMETER):
                if first is None:
                    first = (e_index, l_index)
                if lod.bEnabled:
                    lod.bEnabled = False
                    muted += 1
    _muted[key] = first
    if muted:
        _once(f"slow-look:{template.Name}", f"{template.Name}: the slow status' {muted} emitter levels off")
    return muted


def _immune(pawn: UObject) -> bool:
    for check in IMMUNE:
        try:
            if check(pawn):
                return True
        except Exception as ex:  # noqa: BLE001 - a check's fault never blocks the status
            _once(f"immune:{type(ex).__name__}", f"immunity check failed: {type(ex).__name__}: {ex}")
    return False


@hook("WillowGame.StatusEffectsComponent:ApplyStatusEffect", Type.POST)
def _after_status(obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    definition = args.StatusEffectDefinition
    if not _is_our_status(definition):
        return
    pawn = obj.Owner
    if pawn is None or not actors.is_live_actor(pawn) or not _is_pawn(pawn):
        return
    if pawn.Class.Name == "WillowPlayerPawn":
        _notify(ON_PLAYER_FROZEN, pawn, args.InstigatedBy)
        return          # players: the status' skill stops them walking (data, see the module docstring)
    was_frozen = is_frozen(pawn)
    profile, resists = rules.recent(pawn)       # as `_before_status` read them, a moment ago
    mode = chill(pawn, _status_seconds(obj, definition), profile, resists)
    if f"chill:{mode}" not in _said:
        _once(f"chill:{mode}", f"first cryo status -> {mode} on {pawn.Name}")
    if mode == rules.FREEZE and not was_frozen:
        _notify(ON_FREEZE_LOOK, pawn, args.InstigatedBy)
        _notify(ON_FREEZE, pawn, args.InstigatedBy)


@hook("WillowGame.WillowPlayerController:PlayerTick", Type.POST)
def _thaw_tick(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    if not _chilled:
        return
    now = actors.now()
    # Ten times a second of the map's clock. A wait longer than that is a time of another world's clock, left from
    # before the player went back to the main menu: up to 0.4.9 it was waited out, and for as long as the last stay
    # had lasted nothing thawed (the user, 2026-10-05: "occasionally some enemies stay frozen forever").
    if now < _next_check[0] <= now + CHECK_EVERY:
        return
    _next_check[0] = now + CHECK_EVERY
    begun = time.perf_counter()
    for key, s in list(_chilled.items()):
        pawn = s["pawn"]()
        if pawn is None or not actors.is_live_actor(pawn):
            _chilled.pop(key, None)
            freeze.forget(key)                   # it left without dying: its hold went with it
        elif now >= s["until"]:
            thaw(pawn)
            if time.perf_counter() - begun >= THAW_BUDGET:
                _next_check[0] = now             # the others next frame (THAW_BUDGET)
                break
    for check in WHILE_CHILLED:
        try:
            check()
        except Exception as ex:  # noqa: BLE001 - looks; never the thaw's business
            _once(f"while:{type(ex).__name__}", f"a check while frozen failed: {type(ex).__name__}: {ex}")


@hook("WillowGame.WillowAIPawn:Died")
def _on_death(obj: UObject, args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    if _cryo_hits:
        landed = _cryo_hits.pop(obj, None)
        if landed is not None and 0.0 <= time.monotonic() - landed <= CRYO_KILL_WITHIN:
            _last_cryo_kill[0] = actors.now()       # a cryo hit and then its death: a cryo kill
    if not _chilled:
        return                          # nobody is frozen or slowed: nothing more of this death is cryo's
    if is_frozen(obj):
        _shattering[obj._path_name()] = args.Killer
        _notify(ON_FROZEN_KILL, obj, args.Killer, args.DamageType)
    thaw(obj)


def _notify(listeners: list[Any], *args: Any) -> None:
    for listener in listeners:
        try:
            listener(*args)
        except Exception as ex:  # noqa: BLE001 - a listener's fault never breaks the freeze
            _once(f"listener:{type(ex).__name__}", f"cryo listener failed: {type(ex).__name__}: {ex}")


@hook("WillowGame.WillowAIPawn:Died", Type.POST)
def _after_death(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    if not _shattering:
        return
    key = obj._path_name()
    if key in _shattering:
        shatter(obj, _shattering.pop(key))


def shatter(pawn: UObject, killer: UObject | None) -> None:
    """A frozen enemy died: burst it, and freeze its allies within SHATTER_RADIUS (TPS's OnShatter)."""
    impact = _objects.get(IMPACT)
    zero = unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0)
    place = actors.LOCATION_OF(pawn)
    x, y, z = float(place.X), float(place.Y), float(place.Z)        # copied: the gib can destroy the pawn
    where = unrealsdk.make_struct("Vector", X=x, Y=y, Z=z)
    # The nova catches the killer's enemies, asked of each living neighbour: asking the dead one (GetOpinion on a pawn
    # whose controller is gone) crashed the game when a shatter had neighbours (2026-10-02).
    target = actors.pawn_of(killer)
    me = pawn._path_name()                  # pawns of the same name can sit in different levels: compare paths
    nearby = [o for o in (actors.live_ai_pawns_within(pawn, x, y, z, SHATTER_RADIUS) if target is not None else ())
              if o._path_name() != me and o.GetHealth() > 0 and _hostile_to(o, target)]
    gibbed = False
    try:
        gibbed = bool(pawn.TryFullBodyGib(Damage=float(pawn.GetMaxHealth()), InstigatedBy=killer,
                                          DamageType=unrealsdk.find_class("WillowDmgSource_Skill"),
                                          DamageTypeDefinition=impact, HitLocation=where, Momentum=zero))
    except Exception as ex:  # noqa: BLE001 - the burst is looks; the nova below still runs
        _once("gib-error", f"shatter gib failed: {type(ex).__name__}: {ex}")
    _notify(ON_SHATTER, pawn, gibbed)
    # The nova's freeze reaches each of them in one of the next frames, ahead of any looks that wait (core/later.py):
    # a status put on an enemy is a third of a millisecond and more when it freezes, and a blast that kills six
    # frozen enemies with three neighbours each put eighteen of them into the frame of the kills (0.6.1).
    if _objects.get(STATUS) is not None:
        slayer = WeakPointer(killer) if killer is not None else None
        for other in nearby:
            later.first(_nova_freeze, WeakPointer(other), slayer)
    if "shatter" not in _said:
        _once("shatter", f"first shatter ({len(nearby)} caught in the nova)")


def _nova_freeze(ref: WeakPointer, slayer: WeakPointer | None) -> None:
    """A shatter's nova reaches one enemy that stood near it (queued by `shatter`): the cryo status, in the killer's
    name. Whoever has died or gone since is left alone: nothing is asked of a pawn without its controller."""
    other = ref()
    status = _objects.get(STATUS)
    if other is None or status is None or not actors.is_live_actor(other) or actors.CONTROLLER_OF(other) is None:
        return
    killer = slayer() if slayer is not None else None
    other.GetStatusEffectsComponent().ApplyStatusEffect(
        StatusEffectDefinition=status, InstigatedBy=killer, DamageCauser=None, DamageType=None,
        HitRegion=None, bIsFromSpreading=False)


def recent_cryo_kill(window: float) -> bool:
    """Did a cryo hit kill an enemy within the last `window` seconds of game time?"""
    if _last_cryo_kill[0] < 0.0:
        return False
    return 0.0 <= actors.now() - _last_cryo_kill[0] <= window


def _hostile_to(other: UObject, killer_pawn: UObject) -> bool:
    """Is `other` (alive) an enemy of the killer's pawn (the nova only hits the killer's enemies)?"""
    return str(getattr(other.GetOpinion(killer_pawn), "name", "")) == "OPINION_Enemy"


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    release_all()                       # whoever is still there is let go; the old map's own are gone with it
    _coordinator[0] = None
    _muted.clear()                      # a map brings its own status particle systems
    _cryo_hits.clear()
    rules.forget()                      # what was kept of the old map's enemies, and of where they stood
    attributes.forget()
    actors.forget_placed()
    _next_check[0] = 0.0                # a new world's clock starts again


# ---------------------------------------------------------------------------------------------- damage


def crit_share(summary: WrappedStruct) -> float:
    """The part of a crit hit's damage that the crit added, 0..1.

    BL2 books a crit's extra as a negative HitRegionReduction (measured 2026-10-01: a sniper head shot, initial
    425.9, HitRegionReduction -1533.2, final 1959.1, so x4.6 with sniper and skill crit bonuses). Without one the
    hit is taken as BL2's base x2."""
    initial = float(summary.InitialDamage)
    extra = -float(summary.HitRegionReduction)
    if initial > 0.0 and extra > 0.0:
        return extra / (initial + extra)
    return (CRIT_MULTIPLIER - 1.0) / CRIT_MULTIPLIER


def frozen_bonus(pipeline: UObject, crit: bool, summary: WrappedStruct | None = None) -> float:
    """TPS's Skill_IceEffects on a frozen target, for this hit (1.0 = none). The bonuses multiply. `summary`: the
    hit's, when the caller has read it already."""
    bonus = 1.0
    source = pipeline.DamageSource
    melee = _melee[0] or unrealsdk.find_class("WillowDmgSource_Melee")
    _melee[0] = melee
    if source is not None and (source == melee or source._inherits(melee)):
        bonus *= FROZEN_MELEE
    definition = pipeline.WillowDamageTypeDef
    if definition is not None and str(getattr(definition.DamageType, "name", "")) == "DAMAGE_TYPE_Explosive":
        bonus *= FROZEN_EXPLOSIVE
    if crit:
        # the crit's extra over a normal hit, x(1 + FROZEN_CRIT_BONUS): the hit grows by that share x the bonus
        bonus *= 1.0 + FROZEN_CRIT_BONUS * crit_share(summary if summary is not None else pipeline.DamageSummary)
    return bonus


def slag_factor(summary: WrappedStruct) -> float:
    """Slag's multiplier on this hit: (initial + amplify extra) / initial.

    The extra is in the hit's pre-armor units while the final damage already carries armor's x0.8 (measured
    2026-10-01 on an Auto Cannon: initial 40.7, extra 40.7, final 65.2), so it is compared with the initial damage,
    never with the final one (that made slag look like x2.66 on armor and clamped the cap to 2.25x)."""
    initial = float(summary.InitialDamage)
    extra = float(summary.ExtraDamageDealtDueToAmplify)
    return (initial + extra) / initial if initial > 0.0 and extra > 0.0 else 1.0


def _capped(final: float, slag: float, bonus: float) -> float:
    """Apply `bonus` to a hit that slag multiplied by `slag`, capping slag x bonus at BONUS_CAP outside UVHM
    (RESEARCH.md §6.8). The damage hook runs on the host, whose playthrough is the game's."""
    if not cap_option.value or slag * bonus <= BONUS_CAP:
        return final * bonus
    pc = get_pc()
    if pc is not None and int(pc.GetCurrentPlaythrough()) >= _UVHM:
        return final * bonus
    return final / slag * BONUS_CAP


def _share_counted(summary: WrappedStruct) -> bool:
    """Did this hit land on a body part that lets only a fixed share of its owner's health through
    (`BodyHitRegionDefinition.PercentOfMaxHealthLimit`: a Crystalisk's three leg crystals at 34% each, the Rakk
    Hive's eyes, the Leviathan's parts)? The game books a hit against that share inside `AdjustDamage`, before this
    hook: by then the part is used up by what the game worked out, and breaks at its last bit. A hit made smaller
    after that leaves the enemy the difference with no part left to shoot: a Crystalisk, which resists the three
    elements by half and so cryo, stood with half its health and every crystal gone (a Nexus report,
    2026-10-06; RESEARCH.md §6.80). So on such a part the mod never makes a hit smaller. (Putting the booking right
    afterwards isn't enough: the part breaks on what the game booked.)"""
    region = summary.HitRegion
    return region is not None and float(region.PercentOfMaxHealthLimit) > 0.0


_UNREAD: Any = object()
_final_field: list[Any] = [None]    # the summary's FinalDamage as a field, found once (False: by name, as before)


def _set_final(summary: WrappedStruct, value: float) -> None:
    """Write a hit's final damage into its summary: through the field (1 us; by name the write is 10)."""
    field = _final_field[0]
    if field is None:
        try:
            field = summary._type._find("FinalDamage")
        except Exception:  # noqa: BLE001 - not found that way: by name
            field = False
        _final_field[0] = field
    if field:
        summary._set_field(field, value)
    else:
        summary.FinalDamage = value


class Hit:
    """One hit, as the damage hook sees it: what `AFTER_DAMAGE`'s listeners are handed. Each thing is read from the
    game's pipeline once, when the first one asks for it, and kept for the others.

    Up to 0.6.1 every listener read the pipeline anew (her Frigid Touch, Custom Loads, the note of her critical hits,
    each with its own copy of the hit's summary), and a pellet cost 0.45 ms of this mod's time: a shotgun's blast
    was 10 ms of one frame (RESEARCH.md §6.84)."""

    __slots__ = ("pipeline", "_definition", "_target", "_instigator", "_summary", "_cryo")

    def __init__(self, pipeline: UObject) -> None:
        self.pipeline = pipeline
        self._definition = self._target = self._instigator = self._summary = self._cryo = _UNREAD

    @property
    def definition(self) -> UObject | None:
        """The hit's damage type."""
        if self._definition is _UNREAD:
            self._definition = self.pipeline.WillowDamageTypeDef
        return self._definition

    @property
    def cryo(self) -> bool:
        """Is it one of cryo's damage types?"""
        if self._cryo is _UNREAD:
            self._cryo = is_cryo_damage(self.definition)
        return self._cryo

    @property
    def target(self) -> UObject | None:
        """The pawn that is hit."""
        if self._target is _UNREAD:
            self._target = self.pipeline.DamagedPawn
        return self._target

    @property
    def instigator(self) -> UObject | None:
        """The player whose hit it is (her controller); None for an enemy's."""
        if self._instigator is _UNREAD:
            self._instigator = self.pipeline.DamageInstigatorWPC
        return self._instigator

    @property
    def summary(self) -> WrappedStruct:
        """The game's account of the hit (`DamageSummary`), with what cryo has changed in it."""
        if self._summary is _UNREAD:
            self._summary = self.pipeline.DamageSummary
        return self._summary

    @property
    def final(self) -> float:
        """What the hit deals, cryo's own changes included."""
        return float(self.summary.FinalDamage)

    @property
    def crit(self) -> bool:
        return bool(self.summary.bWasCrit)


@hook("WillowGame.WillowDamagePipeline:AdjustDamage", Type.POST)
def _adjust_damage(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    # Runs for every hit in the game: a fault is logged once instead of on every shot.
    hit = Hit(obj)
    try:
        _scale_hit(hit)
    except Exception as ex:  # noqa: BLE001
        _once(f"adjust-error:{type(ex).__name__}", f"damage hook failed: {type(ex).__name__}: {ex}")
    for after in AFTER_DAMAGE:          # what must see the hit's final damage, cryo's included (Custom Loads)
        try:
            after(hit)
        except Exception as ex:  # noqa: BLE001
            _once(f"after-error:{type(ex).__name__}", f"damage filter failed: {type(ex).__name__}: {ex}")


def _scale_hit(hit: Hit) -> None:
    ours = hit.cryo
    if not ours and not _chilled:
        return
    target = hit.target
    frozen = is_frozen(target)
    if not ours and not frozen:
        return
    summary = hit.summary
    final = float(summary.FinalDamage)
    if final <= 0.0:
        return
    scale = 1.0
    enemy = ours and _is_enemy(target)
    if ours:
        status_tick = hit.definition == _objects.get(STATUS_DAMAGE)
        if enemy:
            # the enemy's cryo rule against its BL2 element resistances: the stronger one, not both. As read a moment
            # ago at most (rules.py `recent`): a blast's pellets ask together.
            profile, resists = rules.recent(target)
            scale = min(profile.damage, resists.status_damage if status_tick else resists.damage)
        # her cryo damage attribute (attributes.py; Avalanche_Active raises it), status ticks and hits apart
        path = attributes.INSTIGATED_STATUS_DAMAGE if status_tick else attributes.INSTIGATED_DAMAGE
        scale *= attributes.recent_value(path, hit.instigator)
    new = final * scale
    if enemy:
        # For `recent_cryo_kill`: the enemy's death, if it comes in a moment, is a cryo kill (`_on_death`). Up to
        # 0.6.1 each cryo hit asked the enemy for its health and its shield, to tell beforehand: two calls into the
        # game at every pellet, for a question only Bloodsplosion asks.
        if len(_cryo_hits) >= _MOST_CRYO_HITS:
            _cryo_hits.clear()
        _cryo_hits[target] = time.monotonic()
    if frozen:
        bonus = frozen_bonus(hit.pipeline, bool(summary.bWasCrit), summary)
        if bonus != 1.0:
            new = _capped(new, slag_factor(summary), bonus)
    if new < final and _share_counted(summary):
        new = final
    if new == final:
        return
    _set_final(summary, new)
    # The summary read from the pipeline is the pipeline's own memory, and the write above lands in it (measured
    # 2026-10-07; the SDK could as well hand out a copy, so the game is asked once in a session). Writing the whole
    # summary back, as up to 0.6.1 at every changed hit, cost twice the write itself.
    if _write_back[0] is None:
        landed = float(hit.pipeline.DamageSummary.FinalDamage)
        _write_back[0] = abs(landed - new) > max(1e-3, abs(new) * 1e-4)
        if _write_back[0]:
            _log("a hit's summary is read as a copy here: every changed hit is written back")
    if _write_back[0]:
        hit.pipeline.DamageSummary = summary
    if "adjust" not in _said:
        _once("adjust", f"first adjusted cryo hit: {final:.0f} -> {new:.0f} on {target.Name if target else '?'}")


# ---------------------------------------------------------------------------------------------- dev


def apply_status(pawn: UObject, instigator: UObject | None = None) -> bool:
    """Dev: put the cryo status on an enemy, as a proc would (the hooks above then freeze it)."""
    ensure_objects()
    component = pawn.GetStatusEffectsComponent()
    if component is None:
        return False
    component.ApplyStatusEffect(StatusEffectDefinition=_objects[STATUS], InstigatedBy=instigator,
                                DamageCauser=None, DamageType=None, HitRegion=None, bIsFromSpreading=False)
    return True


def hit(pawn: UObject, instigator: UObject, amount: float, definition: str = IMPACT,
        source: str = "WillowDmgSource_Skill") -> None:
    """Dev: deal cryo damage through a pipeline carrying our damage type (Athena's bleed route)."""
    ensure_objects()
    pawn, amount = actors.hit_for(pawn, amount)      # a turret riding on a robot: the robot takes its share
    if pawn is None:
        return
    pipe = pawn.GetDamagePipeline(InDamageTypeDef=_objects[definition], InImpactDef=None,
                                  inCanHitFriendly=False, InHitObject=None)
    zero = unrealsdk.make_struct("Vector", X=0.0, Y=0.0, Z=0.0)
    try:
        pawn.TakeDamage(Damage=float(amount), InstigatedBy=instigator, HitLocation=pawn.Location, Momentum=zero,
                        DamageType=unrealsdk.find_class(source), HitInfo=unrealsdk.make_struct("TraceHitInfo"),
                        DamageCauser=None, Pipeline=pipe)
    finally:
        if pipe is not None:
            pawn.ReleaseDamagePipeline(pipe)


HOOKS = (_before_status, _after_status, _thaw_tick, _on_death, _after_death, _on_new_map, _adjust_damage)
OPTIONS = (cap_option,)
