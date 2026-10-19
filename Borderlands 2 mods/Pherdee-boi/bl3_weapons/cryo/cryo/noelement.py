"""Cryo is an element to BL2's skills for guns without one (RESEARCH.md §6.76).

BL2 asks a gun's element one element at a time (`D_Attributes.WeaponDamageType.WeaponDamage_Is_Shock` and its four
siblings), and a skill that is for guns without an element lists every one of them: Axton's Duty Calls pauses itself
while the gun in hand is corrosive, explosive, incendiary, shock or slag. Cryo is on no such list, so a cryo gun
passed for a gun without an element, and Duty Calls gave it its gun damage and its fire rate (measured on 0.5.1: an
SMG's hit 135 to 163, 7.4 to 9.1 hits a second; a shock SMG's the same with the skill and without).

So a skill that tests the gun for "not shock" and for "not slag" gets the same test for cryo: a copy of its shock test
that asks `WeaponDamage_Is_Ice` (attributes.py). Slag is the mark of a list that is BL2's own and means every
element: The Pre-Sequel has no slag, and what a skill of hers leaves out, it means to leave out (Next to Cleanliness
asks about three).

A skill comes with its class's package and goes with it, so the skills are looked at where they are held: in a
player's skill tree, when the tree is set up (a class's own skills, and a skill mod's) and at each map start. The
test is not rooted: it lives and goes with its skill, and a skill loaded afresh gets a new one.
"""

from __future__ import annotations

import copy
from typing import Any

import unrealsdk
from mods_base import get_pc, hook
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

from ..core import maps
from . import attributes

_IS = "WeaponDamage_Is_"
_SHOCK, _SLAG, _ICE = _IS + "Shock", _IS + "Amp", _IS + "Ice"
EVALUATOR = "AttributeExpressionEvaluator_Ice"       # the test's object, inside its skill
_said: set[str] = set()


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


def element_tests(skill: UObject) -> dict[str, Any]:
    """The skill's constraints that ask about the element of the gun in hand: the attribute's name -> the constraint."""
    found: dict[str, Any] = {}
    for constraint in skill.SkillConstraints:
        evaluator = constraint.Evaluator
        if evaluator is None or evaluator.Class.Name != "AttributeExpressionEvaluator":
            continue
        asked = evaluator.Expression.AttributeOperand1
        if asked is not None and asked.Name.startswith(_IS):
            found[asked.Name] = constraint
    return found


def join(skill: UObject) -> bool:
    """A skill for guns without an element (it tests for "not shock" and "not slag") gets the test for cryo. True
    when it got one now."""
    tests = element_tests(skill)
    if _SHOCK not in tests or _SLAG not in tests or _ICE in tests:
        return False
    shock = tests[_SHOCK]
    template = shock.Evaluator
    path = skill._path_name()
    evaluator = None
    for separator in (":", "."):                    # a subobject's path name takes ':' after its outer
        evaluator = evaluator or _find(template.Class.Name, f"{path}{separator}{EVALUATOR}")
    if evaluator is None:
        evaluator = unrealsdk.construct_object(template.Class, skill, EVALUATOR, 0, template)
    expression = copy.copy(template.Expression)
    expression.AttributeOperand1 = attributes.ensure_attributes()[attributes.WEAPON_IS_ICE]
    evaluator.Expression = expression
    constraint = copy.copy(shock)
    constraint.Evaluator = evaluator
    skill.SkillConstraints = [*[copy.copy(c) for c in skill.SkillConstraints], constraint]
    _log(f"{skill.Name} is for guns without an element: cryo joins the {len(tests)} it tests for")
    return True


def patch_tree(tree: UObject | None) -> int:
    """Every skill a player's tree holds is looked at. Returns how many got the test now."""
    if tree is None:
        return 0
    count = 0
    for row in tree.Skills:
        skill = row.Definition
        if skill is not None and len(skill.SkillConstraints) and join(skill):
            count += 1
    return count


def _try(tree: UObject | None) -> None:
    try:
        patch_tree(tree)
    except Exception as ex:  # noqa: BLE001 - another class's data, or a skill mod's: never break a character's load
        _once(f"error:{type(ex).__name__}", f"skills for guns without an element not looked at: {type(ex).__name__}: {ex}")


@hook("WillowGame.PlayerSkillTree:Initialize", Type.POST)
def _tree_ready(obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    _try(obj)


@hook(maps.ARRIVED, Type.POST)
@hook(maps.NEW_WORLD, Type.POST)
def _on_new_map(_obj: UObject, _args: WrappedStruct, _ret: Any, _func: BoundFunction) -> None:
    pc = get_pc()
    _try(pc.PlayerSkillTree if pc is not None else None)     # a tree set up before the mod was switched on


HOOKS = (_tree_ready, _on_new_map)
