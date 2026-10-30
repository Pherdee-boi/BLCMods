"""BL3-style anointments on shields and grenade mods (v0.35.0).

Items roll like guns (seeded by DefinitionData.UniqueId, saved, chance by rarity via
WillowItem.StaticCalculateItemRarityLevel). Only the EQUIPPED shield / grenade mod counts: they are found
in WillowPawn.EquippedItems by class (WillowShield, WillowGrenadeMod).

  Shield:  sh_break - On Shield Break: the shield reaching 0 (polled; OnShieldDepleted never fired) -> +100%
           sh_full  - While shield is full: GetShieldStrength() >= GetMaxShieldStrength() -> +25%
           sh_kill  - On Kill: restore 25% shield (SetShieldStrength)
  Grenade: gr_damage - grenade hits (WillowDmgSource_Grenade) +50%
           gr_after  - After throwing a grenade: +30% weapon damage for 8 s
           gr_shield - On throw: restore 20% shield
  Relic:   BL3 artifact-style perks (relics.py: effects; RELIC here: names) (v0.37.0)
Damage bonuses are read by anoint.scale_damage (the mod's single damage hook); the events come from
anoint's grenade / kill hooks.
"""

import time

from mods_base import DropdownOption, get_pc, hook
from ui_utils import show_hud_message
from unrealsdk.unreal import UObject

from .core import log, now as game_now, remembered

SHIELD = {
    "sh_break": ("On Shield Break: +100% weapon damage for 6s", 1.00),
    "sh_full": ("While shield is full: +25% weapon damage", 0.25),
    "sh_kill": ("On Kill: restore 25% of your shield", 0.25),
}
GRENADE = {
    "gr_damage": ("Grenades deal +50% damage", 0.50),
    "gr_after": ("After throwing a grenade: +30% weapon damage for 8s", 0.30),
    "gr_shield": ("On grenade thrown: restore 20% of your shield", 0.20),
}
RELIC = {
    "rl_nova": ("Slide Nova: sliding releases a blast around you", 2.0),
    "rl_slam": ("Shockwave: ground slams hit twice as hard and 50% wider", 1.0),
    "rl_air": ("Airborne: +25% weapon damage while in the air", 0.25),
    "rl_moon": ("Moon Boots: jump 30% higher; aim in mid-air to hover", 1.3),
    "rl_rush": ("Victory Rush: +30% move speed for 6s after a kill", 0.30),
}
ALL = {**SHIELD, **GRENADE, **RELIC}
TABLES = {"shield": SHIELD, "grenade": GRENADE, "relic": RELIC}
BREAK_TIME, AFTER_TIME = 6.0, 8.0
CHANCE = {3: 0.20, 4: 0.50}  # by item rarity level, like guns; legendary-class always
LEGENDARY = {5, 7, 8, 9, 10}

test_shield = DropdownOption("Test Shield Anointment", "Off", ["Off", *(v[0] for v in SHIELD.values())],
                             description="Testing: the equipped shield gets this anointment.")
test_grenade = DropdownOption("Test Grenade Anointment", "Off", ["Off", *(v[0] for v in GRENADE.values())],
                              description="Testing: the equipped grenade mod gets this anointment.")
test_relic = DropdownOption("Test Relic Perk", "Off", ["Off", *(v[0] for v in RELIC.values())],
                            description="Testing: the equipped relic gets this perk.")
TESTS = {"shield": test_shield, "grenade": test_grenade, "relic": test_relic}
OPTIONS = [test_shield, test_grenade, test_relic]

_break_at = -1e9
_after_at = -1e9


def item_kind(item: UObject) -> str | None:
    name = item.Class.Name
    if "Shield" in name:
        return "shield"
    if "GrenadeMod" in name:
        return "grenade"
    if "Artifact" in name:  # BL2 relics are WillowArtifact
        return "relic"
    return None


def anointment_of(item: UObject) -> str | None:
    """Roll once per shield / grenade mod; None if not anointed (or not one of those)."""
    from . import anoint

    kind = item_kind(item)
    if kind is None or not anoint.enabled.value:
        return None
    test = TESTS[kind].value
    table = TABLES[kind]
    if test != "Off":
        return next(k for k, v in table.items() if v[0] == test)

    def make(rng):
        try:
            ret = item.StaticCalculateItemRarityLevel(item.DefinitionData)
            level = int(ret[0] if isinstance(ret, tuple) else ret)
        except Exception:  # noqa: BLE001
            level = 0
        chance = 1.0 if level in LEGENDARY or level >= 500 else CHANCE.get(4 if level == 6 else level, 0.0)
        if rng.random() >= chance:
            return []
        return [rng.choice(sorted(table))]

    saved = remembered(f"g{kind}", item, make)
    return saved[0] if saved else None


def _equipped(kind: str) -> UObject | None:
    pc = get_pc(possibly_loading=True)
    pawn = pc.Pawn if pc is not None else None
    if pawn is None:
        return None
    try:
        items = list(pawn.EquippedItems)
    except Exception:  # noqa: BLE001
        return None
    for item in items:
        if item is not None and item_kind(item) == kind:
            return item
    return None


_active_seen: dict[str, tuple[float, str | None]] = {}  # kind -> (when, anointment): a swapped item is seen within a quarter second
ACTIVE_TTL = 0.25


def _active(kind: str) -> str | None:
    t = time.monotonic()
    hit = _active_seen.get(kind)
    if hit is not None and t - hit[0] < ACTIVE_TTL:
        return hit[1]
    item = _equipped(kind)
    found = anointment_of(item) if item is not None else None
    _active_seen[kind] = (t, found)
    return found


def _pawn() -> UObject | None:
    pc = get_pc(possibly_loading=True)
    return pc.Pawn if pc is not None else None


def weapon_bonus() -> float:
    """Extra gun damage from the equipped shield / grenade anointments."""
    now = game_now()
    bonus = 0.0
    shield = _active("shield")
    if shield == "sh_break" and now - _break_at < BREAK_TIME:
        bonus += SHIELD[shield][1]
    if shield == "sh_full":
        pawn = _pawn()
        if pawn is not None and pawn.GetMaxShieldStrength() > 0 and pawn.GetShieldStrength() >= pawn.GetMaxShieldStrength() - 0.5:
            bonus += SHIELD[shield][1]
    if _active("grenade") == "gr_after" and now - _after_at < AFTER_TIME:
        bonus += GRENADE["gr_after"][1]
    if _active("relic") == "rl_air":
        pawn = _pawn()
        if pawn is not None and pawn.Physics == 2:  # PHYS_Falling
            bonus += RELIC["rl_air"][1]
    return bonus


def grenade_bonus() -> float:
    return GRENADE["gr_damage"][1] if _active("grenade") == "gr_damage" else 0.0


def _restore_shield(fraction: float) -> None:
    pawn = _pawn()
    if pawn is None or pawn.GetMaxShieldStrength() <= 0:
        return
    pawn.SetShieldStrength(min(pawn.GetMaxShieldStrength(), pawn.GetShieldStrength() + pawn.GetMaxShieldStrength() * fraction))


def on_grenade() -> None:
    """Called by anoint's grenade hooks (once per throw)."""
    global _after_at
    from . import radgear  # noqa: PLC0415

    radgear.on_throw()
    kind = _active("grenade")
    if kind == "gr_after":
        _after_at = game_now()
        show_hud_message("Grenade Anointment", "+30% weapon damage for 8s", 2)
    elif kind == "gr_shield":
        _restore_shield(GRENADE[kind][1])
        show_hud_message("Grenade Anointment", "Shield restored", 1.5)


def on_kill() -> None:
    """Called by anoint's kill hook."""
    if _active("shield") == "sh_kill":
        _restore_shield(SHIELD["sh_kill"][1])
    from . import relics

    relics.on_kill()


_last_shield = -1.0
_shield_at = 0.0


@hook("WillowGame.WillowGameViewportClient:Tick", hook_identifier="bl3w_gear_tick")
def watch_shield(*_: object) -> None:
    """Shield break = the shield hitting 0 from above. (v0.35.0 hooked WillowPawn.OnShieldDepleted, which
    never fired for the player from Python; polling the strength works.)"""
    global _break_at, _last_shield, _shield_at
    t = time.monotonic()
    if t - _shield_at < 0.05:  # twenty looks a second catch a shield break as well as sixty
        return
    _shield_at = t
    pawn = _pawn()
    if pawn is None or pawn.GetMaxShieldStrength() <= 0:
        _last_shield = -1.0
        return
    strength = pawn.GetShieldStrength()
    broke = _last_shield > 0.5 and strength <= 0.5
    _last_shield = strength
    if broke:
        from . import radgear  # noqa: PLC0415

        radgear.on_shield_break()
    if broke and _active("shield") == "sh_break":
        _break_at = game_now()
        show_hud_message("Shield Anointment", "+100% weapon damage for 6s", 2)
        log("shield anointment: shield broke")


def card_line(item: UObject) -> str | None:
    kind = anointment_of(item)
    if not kind:
        return None
    return f"Relic perk: {ALL[kind][0]}" if kind in RELIC else f"Anointed: {ALL[kind][0]}"


HOOKS = [watch_shield]
