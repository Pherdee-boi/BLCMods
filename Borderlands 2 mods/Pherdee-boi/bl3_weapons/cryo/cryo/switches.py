"""Cryo in what BL2 switches by element (RESEARCH.md §6.43).

Some of BL2's projectiles pick what they do by the element they were thrown or fired with: a chain of compares on
their damage type, each match setting a switch of the projectile's body, and under each value of the switch that
element's damage areas and effects. BL2's chains know BL2's elements, so a cryo one matched nothing:
- the area-effect grenade's child (Vladof's grenades; `Grenade_AreaEffectChild`) set nothing: no cloud, no damage, and
  it went off once more when its time was up (the report, 2026-10-04);
- the Maliwan launchers' rocket leaves a damage area where it lands (`Projectile_Rocket_Maliwan_Child_Invisible`),
  and its switch starts at 4, slag: a cryo launcher left a slag area;
- the Bouncing Betty's shots (`Grenade_BouncingBetty_Jump`) stayed at 0, the non-elemental ones.

TPS has ice in each, where BL2 has slag or nothing, and each time it is another element's with the ice damage type:
corrosive's three sizes of cloud with TPS's own spray for the grenade, shock's area for the rocket, shock's two areas
for the Betty. Here cryo is one more element: a compare at the end of the chain, one more value of the switch, and
under it copies of that other element's templates with the cryo damage type (core/graphs.py does both).

The grenade's spray is TPS's `Part_Ice_AOESpray`, carried into her build's ice effects (tools/icefx.py) and drawn with
runtime materials (frozenfx.py names them). Until her files carry it the cloud deals its damage unseen. TPS also lays
a sheet of ice on the ground under it for seven seconds, a decal whose material isn't carried: left out.

Not here, because only a look is missing: the Singularity's rising effect, and the Fastball's glow and impact (their
damage and explosion come from the grenade's own element, which cryo has).
"""

from __future__ import annotations

from collections.abc import Callable

import unrealsdk
from unrealsdk.unreal import UObject, WrappedStruct

from ..core import graphs
from . import element, frozenfx

AREA_CHILD = "GD_GrenadeMods.Projectiles.Grenade_AreaEffectChild"
ROCKET_CHILD = "GD_Weap_Launchers.Projectiles.Projectile_Rocket_Maliwan_Child_Invisible"
BETTY = "GD_GrenadeMods.Projectiles.Grenade_BouncingBetty_Jump"
SHOCK = "GD_Shock.DamageType.DmgType_Shock_Impact"
SPRAY = frozenfx.FX + "Part_Ice_AOESpray"
SWITCH = "DamageTypeSwitch"
SET_SWITCH = "Behavior_ChangeInstanceDataSwitch"
ENABLE = "Behavior_ChangeRemoteBehaviorSequenceState"
CONDITIONAL = "Behavior_Conditional"

_said: set[str] = set()


def _log(msg: str) -> None:
    unrealsdk.logging.info(f"[Aurelia] cryo switches: {msg}")


def _find(cls: str, path: str) -> UObject | None:
    try:
        return unrealsdk.find_object(cls, path)
    except ValueError:
        return None


def _templates(definition: UObject, damage: UObject, system: UObject | None = None,
               spray: bool = False) -> Callable[[WrappedStruct, int], None]:
    """Gives each copied attachment its own templates: a damage area deals `damage`, and with `spray` a particle
    component plays `system` (nothing, while her files don't carry it)."""
    def edit(attachment: WrappedStruct, number: int) -> None:
        data = attachment.Data
        template = data.Object
        if template is not None:
            area = graphs.make(template, definition, f"{template.Class.Name}_Ice{number}")
            area.DamageTypeDefinition = damage
            data.Object = area
        component = data.ComponentData.Component
        if component is not None:
            mine = graphs.make(component, definition, f"{component.Class.Name}_Ice{number}")
            if spray and mine.Class.Name == "ParticleSystemComponent":
                mine.Template = system
            data.ComponentData.Component = mine
    return edit


def _matched(seq: graphs.Sequence, known: UObject, class_name: str) -> int:
    """The behaviour a chain runs when its compare with `known` matches, which must be of `class_name`."""
    address = known._get_address()
    for compare in seq.of_class(graphs.COMPARE):
        if seq.compared(compare) != address:
            continue
        for link_id, behavior, _delay in seq.outputs_of(compare):
            if link_id == graphs.EQUAL and seq.behaviors[behavior].Behavior.Class.Name == class_name:
                return behavior
    raise LookupError(f"{seq.bpd._path_name()}: {known.Name}'s match isn't a {class_name}")


def _switch_setter(seq: graphs.Sequence, switch: str = SWITCH) -> int:
    """The behaviour of a sequence that sets `switch`."""
    for i in seq.of_class(SET_SWITCH):
        if str(seq.behaviors[i].Behavior.SwitchName) == switch:
            return i
    raise LookupError(f"{seq.bpd._path_name()} [{seq.bpd.BehaviorSequences[seq.index].BehaviorSequenceName}]: "
                      f"nothing sets {switch}")


def _set_to(seq: graphs.Sequence, like: UObject, value: int) -> UObject:
    setter = graphs.make(like, seq.bpd, f"{SET_SWITCH}_Ice")
    setter.NewValue = value
    return setter


def _area_child(damage: UObject, shock: UObject) -> str:
    """The area-effect grenade: corrosive's cloud, in its three sizes, with cryo damage and TPS's spray."""
    definition = unrealsdk.find_object("ProjectileDefinition", AREA_CHILD)
    bpd = definition.BehaviorProviderDefinition
    seq = graphs.Sequence(bpd, "Default")
    if seq.compares(damage):
        return "has cryo"
    # BL2's elements each have a sequence of their own, switched on by the match; corrosive's sets the switch and
    # then the cloud's size from the grenade's damage radius. Cryo does both right in the chain.
    enabled = seq.behaviors[_matched(seq, shock, ENABLE)].Behavior
    source = graphs.Sequence(bpd, "corrosive")
    setter = _switch_setter(source)
    sizes = next(b for link_id, b, _d in source.outputs_of(setter) if link_id == graphs.THEN)
    if source.behaviors[sizes].Behavior.Class.Name != CONDITIONAL or str(enabled.SequenceName) != "shock":
        raise LookupError(f"{AREA_CHILD}: not the graph this was written for")
    system = _find("ParticleSystem", SPRAY)
    if system is None and "spray" not in _said:
        _said.add("spray")
        _log(f"no {SPRAY}: her files predate it (a rebuild adds it); the cryo cloud is unseen until then")
    value = graphs.add_switch_value(definition, SWITCH, int(source.behaviors[setter].Behavior.NewValue),
                                    _templates(definition, damage, system, spray=True))
    first = seq.add(_set_to(seq, source.behaviors[setter].Behavior, value))
    pick = seq.add(graphs.make(source.behaviors[sizes].Behavior, bpd, f"{CONDITIONAL}_Ice"))
    seq.link(first, [(graphs.THEN, pick)])
    seq.link(pick, [(link_id, seq.add(graphs.make(source.behaviors[b].Behavior, bpd, f"{SET_SWITCH}_IceSize{link_id}")),
                     delay) for link_id, b, delay in source.outputs_of(sizes)])
    seq.extend_chain(shock, damage, f"{graphs.COMPARE}_Ice", first)
    seq.write()
    return f"cryo is {SWITCH} {value}" + ("" if system is not None else ", unseen")


def _rocket_child(damage: UObject, shock: UObject) -> str:
    """The Maliwan launchers' lingering area: shock's, with cryo damage."""
    definition = unrealsdk.find_object("ProjectileDefinition", ROCKET_CHILD)
    seq = graphs.Sequence(definition.BehaviorProviderDefinition, "Default")
    if seq.compares(damage):
        return "has cryo"
    setter = seq.behaviors[_matched(seq, shock, SET_SWITCH)].Behavior
    value = graphs.add_switch_value(definition, SWITCH, int(setter.NewValue), _templates(definition, damage))
    seq.extend_chain(shock, damage, f"{graphs.COMPARE}_Ice", seq.add(_set_to(seq, setter, value)))
    seq.write()
    return f"cryo is {SWITCH} {value}"


def _betty(damage: UObject, shock: UObject) -> str:
    """The Bouncing Betty's shots: shock's two areas, with cryo damage. Its shots' colour stays the plain one's (TPS's
    is white): BL2's elements set theirs through a variable of their own sequence."""
    definition = unrealsdk.find_object("ProjectileDefinition", BETTY)
    bpd = definition.BehaviorProviderDefinition
    seq = graphs.Sequence(bpd, "Default")
    if seq.compares(damage):
        return "has cryo"
    enabled = seq.behaviors[_matched(seq, shock, ENABLE)].Behavior
    source = graphs.Sequence(bpd, str(enabled.SequenceName))
    setter = source.behaviors[_switch_setter(source)].Behavior
    value = graphs.add_switch_value(definition, SWITCH, int(setter.NewValue), _templates(definition, damage))
    seq.extend_chain(shock, damage, f"{graphs.COMPARE}_Ice", seq.add(_set_to(seq, setter, value)))
    seq.write()
    return f"cryo is {SWITCH} {value}"


TARGETS = (("area-effect grenades", _area_child), ("Maliwan rockets' areas", _rocket_child),
           ("Bouncing Betties", _betty))


def apply() -> list[str]:
    """Teach BL2's element switches cryo. Idempotent; one that isn't the graph this was written for is left as it
    is and said. The lines say what was done."""
    damage = element.ensure_objects()[element.IMPACT]
    shock = unrealsdk.find_object("WillowDamageTypeDefinition", SHOCK)
    lines = []
    for label, teach in TARGETS:
        try:
            lines.append(f"{label}: {teach(damage, shock)}")
        except Exception as ex:  # noqa: BLE001 - one projectile's graph is no reason to stop the others
            lines.append(f"{label}: left alone ({type(ex).__name__}: {ex})")
    _log("; ".join(lines))
    return lines
