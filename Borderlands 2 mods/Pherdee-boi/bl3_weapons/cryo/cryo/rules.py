"""How each BL2 enemy takes cryo: TPS's per-enemy rules, mapped onto BL2's enemies by type (M3).

RESEARCH.md §6.12 item 12 and §6.13. TPS keeps its rules on the enemy classes, and the scans behind this
table are tools/enemyscan.py (both games) and tools/cryoscan.py (TPS pawn flags and freeze nodes):
- By default every TPS enemy can be frozen for the full time, and cryo does normal damage (only the
  damage type's shield multiplier differs).
- Cryo creatures (Frost Kraggons, Cryo Rodunks, the Cryo Hive, Cryo Putti, the Ice Marine) resist cryo
  damage (x0.05 to x0.3) and never take the cryo status (`BaseFreezeChanceResistanceModifier` 0).
- Fire creatures do NOT resist cryo in TPS; they resist fire. BL2's fire enemies keep normal cryo.
- Bosses, jets and camera drones can't be frozen (chance 0, or `bCanBeFrozen` off on the pawn).
- Giants (the Rockzilla/Lavazilla/Frostzilla Kraggons) and tentacles or worms can't be frozen.
- Dahl powersuits freeze for 0.2 of the time, the Proto-WarBot's legs for 0.1.
- Flying creatures fall when frozen; turrets freeze but stay put (`bCanFallWhenFrozen`).
Port decisions on top (2026-09-30 review): bosses are slowed instead of frozen, and so are the other
enemies TPS won't freeze but which still move (giants, jets). Enemies that don't move (tentacles, worms,
statues) just take the damage and the damage over time.

BL2 flags only 12 enemy classes as bosses (`AIClassDefinition.bBoss`), so the boss list below adds the
story, DLC, raid and Headhunter bosses by class. Named minibosses stay freezable, as most were in TPS.

BL2's own element resistances count too (`resistances`): an enemy that resists fire, shock and corrosive (Dragon
Keep's Crystal Skeletons and Skeleton King, Rock Golems, the Pumpkin Kingpin) resists cryo as much as it resists
the middle one of the three, so cryo can't slip past a design where only one element is meant to work. An enemy
that resists one element only (a fire skeleton, a fire skag) takes cryo in full, as TPS's fire creatures do. The
same goes for the status: an enemy that can't be burned, shocked or corroded can't be frozen or slowed either.
The stronger of this and the TPS rule wins; they never multiply.

The user's own calls (2026-10-03), on top: Mercenary Day's snowmen (Sir Tinder Snowflake, the Frost Nippers) are
immune; Dragon Keep's Badass Sorcerers resist 95% of cryo, its Sorcerers 90% once they turn white (Ice Body, which they
keep until they die) and its Paladins 80% while their white bubble (an enemy shield their Divine Shield fills) holds;
Commander Lilith's New Pandora (Dahl) troops resist more by rank (`RESIST_BY_PACKAGE`). Later the same day: Mercenary
Day's snow people resist 80% and more by type, the base game's snow psycho 75/95/75% by mode (its three names), snow
skags are immune, Badass Bullymongs in Windshear Waste and Three Horns resist 10%, and Wizards' arcane bubble counts
like the Paladins'. A resistance is a share of the cryo damage kept off; every resistant enemy is also immune to the
status (no freeze, no slow), except the New Pandora troops and those bullymongs, whose status chance, length and slow
are cut by the same share.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unrealsdk.unreal import UObject

FREEZE = "freeze"    # frozen for the status' duration
SLOW = "slow"        # slowed instead of frozen, for the status' duration
NONE = "none"        # the status and its damage over time land, but no freeze or slow
IMMUNE = "immune"    # the cryo status never lands


@dataclass(frozen=True)
class CryoProfile:
    kind: str                   # the rule's name, for logs and the RESEARCH table
    mode: str = FREEZE
    duration: float = 1.0       # freeze-time multiplier
    damage: float = 1.0         # cryo damage multiplier, on top of the flesh/armor/shield ones
    falls: bool = False         # a frozen flyer falls (TPS); not implemented yet


DEFAULT = CryoProfile("default")
FLYER = CryoProfile("flyer", falls=True)
TURRET = CryoProfile("turret")
BOSS = CryoProfile("boss", SLOW)
GIANT = CryoProfile("giant", SLOW)
JET = CryoProfile("jet", SLOW)
HEAVY_MECH = CryoProfile("heavy mech", duration=0.2)                  # TPS Dahl powersuits
STILL = CryoProfile("tentacle, worm or statue", NONE)
ICE_BEAST = CryoProfile("ice beast", IMMUNE, damage=0.1)              # TPS Frost Kraggons
SNOW =CryoProfile("made of snow", IMMUNE, damage=0.0)                # the user's call: no cryo damage at all
YETI_BRAT = CryoProfile("yeti brat", IMMUNE, damage=0.2)              # the user's call: Mercenary Day's snow 80%+
BADASS_YETI = CryoProfile("badass yeti", IMMUNE, damage=0.05)

_BOSS_PACKAGES = """
GD_PrimalBeast_KnuckleDragger GD_BoomBoom GD_Flynt GD_Willhelm GD_ConstructorWillhelm GD_BloodwingNew GD_Anemone_Talon
GD_HyperionBunkerBoss GD_ConstructorRoland GD_Jack GD_FinalBoss GD_LoaderUltimateBadass GD_LoaderUltimateBadass_Digi
GD_Skagzilla GD_Skagzilla_Digi GD_Thresher_Raid GD_BugMorphRaid GD_AngelBoss
GD_Orchid_PirateQueen_Combat GD_Orchid_BossWorm GD_Orchid_RakkHive GD_Orchid_LoaderBoss GD_Orchid_RaidEngineer
GD_Orchid_RaidEngineerAid GD_Orchid_RaidShaman
GD_Iris_Piston_Boss GD_Iris_BlimpBoss GD_Iris_MotorMama GD_Iris_MotorMamaTurret GD_Truckasaurus GD_Iris_Raid_PyroPete
GD_Iris_Pyro_Pete
GD_Sage_FinalBoss GD_Sage_DrNakayama GD_Sage_Raid_Beast GD_DrifterRaid
GD_JackWarlock GD_DragonBridgeBoss GD_GolemGold GD_DragonHeart_Raid GD_DragonBlue_Raid GD_DragonGreen_Raid
GD_DragonRed_Raid GD_DragonPurple_Raid
GD_Anemone_Hector GD_Anemone_Cassius GD_Anemone_UranusBOT GD_Anemone_SandWormBoss_1 GD_Anemone_Slagenstein1
GD_Anemone_Slagenstein2 GD_Anemone_Lt_Angvar GD_Anemone_Lt_Bolson GD_Anemone_Lt_Hoffman GD_Anemone_Lt_Tetra
GD_Anemone_A_Queen_Digi
GD_Pumpkinhead GD_PumpkinheadFlying GD_BigBird GD_GoliathBride GD_GoliathGroom GD_GoliathBossProxy GD_Nast_ThresherLove
GD_Crawmerax_Son GD_ThresherGluttonous GD_SpiderTank_Boss GD_SpiderTank_BossTop1 GD_SpiderTank_BossTop2
GD_SpiderTank_BossTop3
"""

_GIANT_PACKAGES = """
GD_Crystalisk GD_CrystaliskVolcanic GD_Orchid_CrystaliskAqua GD_SafeCrystalisk GD_Sage_SM_BigFeetData
GD_GolemRock GD_GolemBadass GD_Golem_SwordInStone GD_Golem_ClaptrapWand GD_Anemone_Infected_Golem
GD_Anemone_Infected_Golem_Badass GD_Drifter GD_DrifterNamed GD_Sage_Rare_Drifter GD_Treant GD_Treant_TreeHugger
GD_Skeleton_Giant GD_UndeadFirePsycho_Giant GD_Anemone_Gargantuan_Goliath GD_CraboidGiant
"""

_HEAVY_MECH_PACKAGES = """
GD_Constructor GD_ConstructorBadass GD_Constructor_Digi GD_Constructor_1340 GD_SpiderTank GD_SpiderTank_Baricade
GD_SpiderTank_Top GD_SpiderTank_TopRocket
"""

_STILL_PACKAGES = """
GD_ConstructorLaserStatue GD_TentacleA GD_TentacleFeeler GD_TentacleFire GD_TentacleGluttonous GD_TentacleHoldObject
GD_TentacleTurf GD_Tentacle_RaidA GD_Tentacle_RaidC GD_Tentacle_RaidD GD_Tentacle_RaidE GD_Tentacle_RaidF
GD_Tentacle_Slappy GD_Nast_TentacleLove GD_Nast_TentacleMoby GD_Anemone_Infected_TentacleA GD_Anemone_InfectedPodTendril
GD_Orchid_SandWorm GD_Orchid_SandWormQueen GD_Orchid_SandWormRaid GD_Anemone_SandWormQueen GD_Anemone_InfectedSandWorm
GD_Allium_GameShowCam
"""

_JET_PACKAGES = "GD_Gyrocopter GD_GyrocopterBadass GD_Gyrocopter-BunkerBoss GD_Iris_GyrocopterCargo"

_TURRET_PACKAGES = """
GD_Aster_RolandTurret GD_JackDeployableTurret GD_RolandDeployableTurret GD_Scorpio GD_Hyperion_AutoCannon
GD_Anemone_AutoCannon GD_Anemone_StolenTurret GD_BigLoaderTurret GD_BigLoaderTurret_Digi GD_ConstructorTurret
GD_Engineer_Foreman_Turret GD_HyperionSoldierTurret GD_Hyperion_BargeTurret GD_Hyperion_LargeTurret GD_Hyperion_TurretGun
GD_Hyperion_TurretGun_Digi GD_Hyperion_TurretSlag_Digi GD_Hyperion_VOGTurret GD_TurretGun
"""

# Mercenary Day's snowmen are made of snow: immune (TPS's ice beings take x0.05); its yetis match the Frost Kraggons.
_ICE = {
    "GD_Snowman": SNOW,
    "GD_SnowMinion": SNOW,
    "GD_PrimalBeast_Yeti": ICE_BEAST,
    "GD_Allium_BadassYeti": BADASS_YETI,
    "GD_Allium_YetiBaby": YETI_BRAT,
}

# Keyed by an enemy class's package (its path's first part), lower case: BL2 spells some in lower case.
BY_PACKAGE: dict[str, CryoProfile] = {}
for _names, _profile in ((_TURRET_PACKAGES, TURRET), (_JET_PACKAGES, JET), (_STILL_PACKAGES, STILL),
                         (_HEAVY_MECH_PACKAGES, HEAVY_MECH), (_GIANT_PACKAGES, GIANT), (_BOSS_PACKAGES, BOSS)):
    BY_PACKAGE.update(dict.fromkeys(_names.lower().split(), _profile))    # later groups win: bosses last
BY_PACKAGE.update({name.lower(): profile for name, profile in _ICE.items()})   # ice beats boss (Sir Tinder Snowflake)

# Whole families (the kill stat an enemy counts toward), where TPS's counterpart has one rule.
BY_FAMILY: dict[str, CryoProfile] = {
    "STAT_PLAYER_KILLS_RAKK": FLYER,                 # TPS Rathyds / Rodunks: freeze, fall
    "STAT_PLAYER_KILLS_SURVEYOR": FLYER,             # TPS repair and shield drones
    "STAT_ASTER_PLAYER_KILLS_FAIRY": FLYER,
    "STAT_PLAYER_KILLS_GYRO": JET,                   # buzzards: TPS jets can't be frozen
    "STAT_PLAYER_KILLS_CONSTRUCTOR": HEAVY_MECH,     # TPS Dahl powersuits
    "STAT_PLAYER_KILLS_AUTOMATED_TURRET": TURRET,
    "STAT_ORCHID_PLAYER_KILLS_WORM": STILL,          # burrowing worms (grubs are overridden below)
    "STAT_ANEMONE_PLAYER_KILLS_WORM": STILL,
}
BY_PACKAGE.update(dict.fromkeys(("gd_orchid_sandwormgrub", "gd_anemone_sandwormgrub"), DEFAULT))

def profile_for_class(class_path: str, family: str = "", boss: bool = False, flying: bool = False) -> CryoProfile:
    """The rule for an enemy class, by path (`GD_X.Character.CharClass_X`), kill stat and flags."""
    rule = BY_PACKAGE.get(class_path.split(".", 1)[0].lower())
    if rule is not None:
        return rule
    if boss:
        return BOSS
    rule = BY_FAMILY.get(family)
    if rule is not None:
        return rule
    return FLYER if flying else DEFAULT


_class_facts: dict[str, tuple[str, bool]] = {}      # an enemy class's path -> (its kill stat, it flies): as it loaded


def profile_for(pawn: UObject) -> CryoProfile:
    """The rule for a live enemy pawn. What its class says is read once for a class (by its path: a class that is
    loaded again says the same); whether this one is a boss is asked of it each time."""
    ai_class = getattr(pawn, "AIClass", None)
    if ai_class is None:
        return profile_for_class("", "", bool(pawn.IsBoss()), _enum_name(pawn.Physics) == "PHYS_Flying")
    path = ai_class._path_name()
    facts = _class_facts.get(path)
    if facts is None:
        facts = _class_facts[path] = (str(ai_class.KilledStatID), _enum_name(ai_class.Physics) == "PHYS_Flying")
    return profile_for_class(path, facts[0], bool(pawn.IsBoss()), facts[1])


def _enum_name(value: object) -> str:
    return str(getattr(value, "name", value))


# ------------------------------------------------------------------------------------- BL2's own resistances


@dataclass(frozen=True)
class Resistances:
    """What an enemy's BL2 element resistances mean for cryo, each 0..1 (1 = no resistance)."""
    damage: float = 1.0         # cryo hits
    status_damage: float = 1.0  # cryo damage over time
    chance: float = 1.0         # the cryo status landing
    duration: float = 1.0       # how long it lasts
    slow: float = 1.0           # BL2's slow chance resistance: 0 on a few bosses (Haderax, Dukino's Mom), never slowed
    slow_strength: float = 1.0  # how much of the slow it takes (the user's partial resistances cut it)


NO_RESISTANCE = Resistances()
_ELEMENTS = ("Incendiary", "Shock", "Corrosive")      # the pawn's damage modifiers
_STATUSES = ("Ignite", "Shock", "Corrosive")          # its status-effect component's resistances


def _middle(values: list[float]) -> float:
    """The median of three, clamped to 0..1: skills push these below zero (scale -9) or above one (weaknesses)."""
    return max(0.0, min(1.0, sorted(values)[1]))


# Asked at every cryo hit (0.6.2, RESEARCH.md §6.84). The damage hook asks for an enemy's rule and its resistances
# at each one, and a shotgun's pellets, a nova's hits and a status' ticks ask in the same frame. Asked by name, the
# values cost 0.12 ms an enemy (0.18 in a fight), a quarter of all the mod did for a pellet (2026-10-07). So:
# - the pawn's own values are read through their fields, found once (core/actors.py `Field`: 1 us each, not 10 to 25);
# - `recent` keeps an enemy's answers for RECENT seconds: a blast's pellets land in one frame, and ask once.
# `resistances` and `profile_for` themselves always read the game as it is.
RECENT = 0.05
_MOST_RECENT = 512
_recent: dict[Any, tuple[float, CryoProfile, Resistances]] = {}
_reads: list[Any] = []


def _pawn_reads() -> tuple[Any, tuple[Any, ...], tuple[Any, ...], Any]:
    """(the Pawn class, its three hit modifiers, its three damage-over-time modifiers, its balance), each a field
    found once. Made at the first ask: this module is read without the game too (the tables)."""
    if not _reads:
        import unrealsdk  # noqa: PLC0415 - kept out of the module's import so tools can read the tables

        from ..core import actors  # noqa: PLC0415
        _reads.append((unrealsdk.find_class("Pawn"),
                       tuple(actors.Field("Pawn", f"{e}ImpactDamageModifier") for e in _ELEMENTS),
                       tuple(actors.Field("Pawn", f"{e}StatusEffectDamageModifier") for e in _ELEMENTS),
                       actors.Field("Pawn", "BalanceDefinitionState")))
    return _reads[0]


def recent(pawn: UObject) -> tuple[CryoProfile, Resistances]:
    """An enemy's rule and its resistances as they were at most RECENT seconds ago: for what asks at every hit."""
    now = time.monotonic()
    known = _recent.get(pawn)
    if known is not None and 0.0 <= now - known[0] < RECENT:
        return known[1], known[2]
    if len(_recent) >= _MOST_RECENT:
        _recent.clear()                         # the dead and the gone: nothing here is worth keeping longer
    answer = (profile_for(pawn), resistances(pawn))
    _recent[pawn] = (now, *answer)
    return answer


def forget() -> None:
    """Nothing kept of what the enemies said (a new map; a test that changes an enemy and asks at once)."""
    _recent.clear()


def resistances(pawn: UObject) -> Resistances:
    """Read live, so a skill that changes a resistance mid-fight (a Sorcerer's Ice Body, a turtled Scaylion,
    Bloodwing's phases) counts at once. Players are left alone: this is for enemies. The user's calls (`user_resist`)
    cap the hit and its damage over time at the share kept, and the status at nothing (immune) or the same share."""
    rule = user_resist(pawn)
    keep = 1.0 - rule.share if rule is not None else 1.0
    status = 0.0 if rule is not None and rule.status == IMMUNE_STATUS else keep
    try:
        pawn_class, hits, ticks, _balance = _pawn_reads()
        if pawn.Class._inherits(pawn_class):
            damage = _middle([float(read(pawn)) for read in hits])
            status_damage = _middle([float(read(pawn)) for read in ticks])
        else:                                   # no pawn (nothing of the game's asks for one): by name, as before
            damage = _middle([float(getattr(pawn, f"{e}ImpactDamageModifier")) for e in _ELEMENTS])
            status_damage = _middle([float(getattr(pawn, f"{e}StatusEffectDamageModifier")) for e in _ELEMENTS])
        component = pawn.GetStatusEffectsComponent()
        if component is None:
            return Resistances(min(damage, keep), min(status_damage, keep), status, status, 1.0, status)
        chance = _middle([float(getattr(component, f"{s}ChanceResistanceModifier")) for s in _STATUSES])
        duration = _middle([float(getattr(component, f"{s}DurationResistanceModifier")) for s in _STATUSES])
        slow = max(0.0, min(1.0, float(component.SlowChanceResistanceModifier)))
    except (AttributeError, TypeError, ValueError):
        return Resistances(keep, keep, status, status, 1.0, status) if rule is not None else NO_RESISTANCE
    return Resistances(min(damage, keep), min(status_damage, keep), min(chance, status), min(duration, status), slow,
                       status)


# ------------------------------------------------------------------------------------- the user's calls (2026-10-03)
IMMUNE_STATUS = "immune"    # the cryo status never lands on it: no freeze, no slow
PARTIAL = "partial"         # the status' chance, its length and its slow are cut by the share resisted


@dataclass(frozen=True)
class Resist:
    share: float                # share of cryo damage resisted, 0..1
    status: str = IMMUNE_STATUS


# By enemy class package (lower case).
RESIST_BY_PACKAGE: dict[str, Resist] = {
    # Commander Lilith's New Pandora troops, by rank (the user: "increasing levels of frost resist", and "they should
    # also resist the slow effect according to their percentages"; the numbers are ours, the user agreed)
    **dict.fromkeys(("gd_anemone_np_sniperbase", "gd_anemone_np_enlisted"), Resist(0.10, PARTIAL)),  # Scout, Recruit
    "gd_anemone_np_enforcer": Resist(0.20, PARTIAL),                                               # Heavy
    **dict.fromkeys(("gd_dahlmedic", "gd_dahlinfecto"), Resist(0.30, PARTIAL)),                     # Medic, Infectologist
    **dict.fromkeys(("gd_anemone_np_flamer", "gd_dahlsniper"), Resist(0.40, PARTIAL)),             # Pyro, Sniper
    **dict.fromkeys(("gd_dahlsergeant", "gd_dahlsniper_badass"), Resist(0.60, PARTIAL)),           # Commander, Badass Sniper
    **dict.fromkeys(("gd_anemone_lt_angvar", "gd_anemone_lt_bolson", "gd_anemone_lt_hoffman",
                     "gd_anemone_lt_tetra"), Resist(0.75, PARTIAL)),                                # the lieutenants
    # Mercenary Day's snow people ("80% or above, depending on the type": the split is ours)
    **dict.fromkeys(("gd_allium_maraudersnow", "gd_allium_psychosnow_midget"), Resist(0.80)),       # Snow Bandit, Midget
    "gd_allium_psychosnow": Resist(0.85),                                                          # Snow Psycho
    **dict.fromkeys(("gd_allium_badass_snowmarauder", "gd_allium_goliathsnow"), Resist(0.90)),      # Badass Snow Bandit, Goliath
}
# By balance (an enemy that shares its class with others)
SNOW_SKAGS = ("GD_Population_Skag.Balance.PawnBalance_SkagBoll", "GD_Population_Skag.Balance.PawnBalance_SkagBoll2",
              "GD_Population_Skag.Balance.PawnBalance_SkagBoll3")      # Snow, Spitter Snow, Alpha Snow Skag
RESIST_BY_BALANCE: dict[str, Resist] = {
    "GD_Aster_Pop_Wizards.Balance.PawnBalance_Sorcerer_Badass": Resist(0.95),
    **dict.fromkeys(SNOW_SKAGS, Resist(1.0)),                               # immune
}
# The base game's snow psycho, one enemy with a name per mode: Freezing Psycho (Normal), Arctic Maniac (True Vault
# Hunter), Frosty Lunatic (Ultimate). The user gave each name its own share.
PSYCHO_SNOW = "GD_Population_Psycho.Balance.PawnBalance_PsychoSnow"
PSYCHO_SNOW_BY_PLAYTHROUGH = (Resist(0.75), Resist(0.95), Resist(0.75))
# Badass Bullymongs, in Windshear Waste and Three Horns only (Divide and Valley); still frozen and slowed, a tenth less
BADASS_BULLYMONG = "GD_Population_PrimalBeast.Balance.PawnBalance_PrimalBeast_Badass"
BULLYMONG_MAPS = frozenset({"glacial_p", "ice_p", "frost_p"})
BADASS_BULLYMONG_RESIST = Resist(0.10, PARTIAL)
SORCERER = "gd_sorcerer"
ICE_BODY = "GD_WizardShared.Skills.Skill_IceBody"       # a Sorcerer's white form, until it dies
ICE_BODY_RESIST = Resist(0.90)
BUBBLED = frozenset({"gd_knight_paladin", "gd_wizard"})  # Paladins' Divine Shield and Wizards' arcane bubble
BUBBLE_RESIST = Resist(0.80)                           # while it holds (an enemy shield above 0)


def user_resist(pawn: UObject) -> Resist | None:
    """The user's call for this enemy, if any (the strongest that applies)."""
    try:
        ai_class = getattr(pawn, "AIClass", None)
        package = ai_class._path_name().split(".", 1)[0].lower() if ai_class is not None else ""
        found = [RESIST_BY_PACKAGE.get(package)]
        pawn_class, _hits, _ticks, balance_state = _pawn_reads()
        state = balance_state(pawn) if pawn.Class._inherits(pawn_class) else getattr(pawn, "BalanceDefinitionState", None)
        balance = state.BalanceDefinition if state is not None else None
        path = balance._path_name() if balance is not None else ""
        found.append(RESIST_BY_BALANCE.get(path))
        if path == PSYCHO_SNOW:
            found.append(PSYCHO_SNOW_BY_PLAYTHROUGH[min(max(_playthrough(), 0), 2)])
        elif path == BADASS_BULLYMONG and _map_name(pawn) in BULLYMONG_MAPS:
            found.append(BADASS_BULLYMONG_RESIST)
        if package == SORCERER and _in_ice_body(pawn):
            found.append(ICE_BODY_RESIST)
        if package in BUBBLED and float(pawn.GetShieldStrength()) > 0.0:
            found.append(BUBBLE_RESIST)
    except (AttributeError, TypeError, ValueError):
        return None
    found = [r for r in found if r is not None]
    return max(found, key=lambda r: (r.share, r.status == IMMUNE_STATUS)) if found else None


def _playthrough() -> int:
    from mods_base import get_pc  # noqa: PLC0415 - kept out of the module's import so tools can read the tables
    pc = get_pc()
    return int(pc.GetCurrentPlaythrough()) if pc is not None else 0


def _map_name(pawn: UObject) -> str:
    from ..core import actors  # noqa: PLC0415 - kept out of the module's import so tools can read the tables
    world = actors.running_world(pawn)             # the running world: asked of the engine, not looked up on the pawn
    return str(world.GetMapName(False)).lower() if world is not None else ""


def _in_ice_body(pawn: UObject) -> bool:
    import unrealsdk  # noqa: PLC0415 - kept out of the module's import so tools can read the tables
    from mods_base import get_pc  # noqa: PLC0415
    try:                                    # found each time: map content, freed when the map changes
        skill = unrealsdk.find_object("SkillDefinition", ICE_BODY)
    except ValueError:
        return False                        # Dragon Keep's content isn't loaded
    pc = get_pc()
    mind = pawn.Controller
    return pc is not None and mind is not None and bool(pc.GetSkillManager().IsSkillActive(mind, skill))
