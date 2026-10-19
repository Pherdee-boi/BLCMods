"""Behaviour graphs and the bodies they switch, edited at runtime (RESEARCH.md §6.43).

A BehaviorProviderDefinition is BL2's script for a gun part, a grenade, a projectile: per sequence, flat lists of
events, behaviours, variables and the links between them. BL2's own graphs know BL2's elements; these let a feature
teach one a new element without replacing it.

How a sequence is laid out (read from the game's own, 2026-10-04):
- `BehaviorData2`: the behaviours, each with a run of the output links (`OutputLinks`) and a run of the variable
  links (`LinkedVariables`). A run is one number: where it starts in its list, times 65536, plus how many.
- `ConsolidatedOutputLinkData`: the output links. `LinkIdAndLinkedBehavior` is the behaviour it leads to, with the
  link's id in the top byte: -1 for "then", 0 and 1 for a compare's equal and different, a condition's id for a
  `Behavior_Conditional`.
- `ConsolidatedVariableLinkData`: a behaviour's property fed from variables, with a run of
  `ConsolidatedLinkedVariables`, which holds the variables' places in `VariableData`.
- `VariableData`: name, type and value. The value isn't a tagged property: the package keeps it after them, and in
  memory it is one machine word (`Value.Data.Dummy`): an object's address, a float's bits, or for the larger kinds a
  pointer into a block the provider owns. So an object variable can be made here; the larger kinds are only ever
  referred to where they are.

Nothing here adds an event or a sequence, and every list only grows at its end: what the game may have looked up by
position stays where it was. A provider is edited before anything that runs it exists (the setup, at the main menu).
An event (`EventData2`: its name in `UserData`, and a run of output links like a behaviour's) can be given other
outputs (`link_event`): what it used to start stays in the lists, unused by it.

A projectile's body (`BodyCompositionData`) is a list of attachments: components, actor templates and switches. An
attachment names the switch it hangs under by position (`AttachmentBaseInstanceDataIndex`) and the value it is for
(`SwitchIdentifier`); `add_switch_value` copies what hangs under one value to a new one, at the list's end.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import unrealsdk
from unrealsdk.unreal import UObject, WrappedStruct

THEN = -1                       # an output link's id: the behaviour that simply comes next
EQUAL, DIFFERENT = 0, 1         # Behavior_CompareObject's outputs
COMPARE = "Behavior_CompareObject"
_BVAR_OBJECT = 5                # EBehaviorVariableType.BVAR_Object
_RF_ROOTSET = 0x4000


def _root(obj: UObject) -> UObject:
    flags = int(obj.ObjectFlags)
    if not flags & _RF_ROOTSET:
        obj.ObjectFlags = flags | _RF_ROOTSET
    return obj


def make(template: UObject, outer: UObject, name: str) -> UObject:
    """A new copy of `template` named `name` in `outer`, with the template's own flags (a template stays one).
    Callers look for an earlier copy first, through what holds it: constructing over an existing object would
    replace it in place. Components the template owns are copied with it (the engine instances them)."""
    return _root(unrealsdk.construct_object(template.Class, outer, name, int(template.ObjectFlags), template))


def clone(bpd: UObject | None, outer: UObject, current: UObject | None) -> UObject | None:
    """A copy of a behaviour provider and of its behaviours (a template copy alone shares them), under `outer`.
    `current` is what `outer` holds now: returned as it is when it is our copy already."""
    if current is not None and current.Outer == outer:
        return current
    if bpd is None:
        return None
    new = _root(unrealsdk.construct_object("BehaviorProviderDefinition", outer, bpd.Name, 0, bpd))
    sequences = []
    for sequence in new.BehaviorSequences:
        sequence = copy.copy(sequence)
        entries = []
        for entry in sequence.BehaviorData2:
            entry = copy.copy(entry)
            old = entry.Behavior
            if old is not None and old.Outer == bpd:
                entry.Behavior = _root(unrealsdk.construct_object(old.Class, new, old.Name, 0, old))
            entries.append(entry)
        sequence.BehaviorData2 = entries
        sequences.append(sequence)
    new.BehaviorSequences = sequences
    return new


def behaviors(bpd: UObject | None, class_name: str) -> list[UObject]:
    if bpd is None:
        return []
    return [entry.Behavior for sequence in bpd.BehaviorSequences for entry in sequence.BehaviorData2
            if entry.Behavior is not None and entry.Behavior.Class.Name == class_name]


def _run(packed: int) -> range:
    return range(packed >> 16, (packed >> 16) + (packed & 0xFFFF))


def _packed(start: int, count: int) -> int:
    return (start << 16) | count


def _word(value: int) -> int:
    """An address as the signed 32-bit number the variable's value is read as."""
    return value - (1 << 32) if value >= 1 << 31 else value


class Sequence:
    """One sequence of a provider, read into lists that can be added to and written back in place."""

    def __init__(self, bpd: UObject, name: str) -> None:
        self.bpd = bpd
        self.index = next(i for i, s in enumerate(bpd.BehaviorSequences) if str(s.BehaviorSequenceName) == name)
        data = bpd.BehaviorSequences[self.index]
        self.behaviors = [copy.copy(e) for e in data.BehaviorData2]
        self.variables = [copy.copy(v) for v in data.VariableData]
        self.outputs = [copy.copy(o) for o in data.ConsolidatedOutputLinkData]
        self.links = [copy.copy(link) for link in data.ConsolidatedVariableLinkData]
        self.linked = [int(v) for v in data.ConsolidatedLinkedVariables]
        self._event_runs: dict[int, int] = {}       # an event's place -> its new run of outputs, written by `write`

    # ------------------------------------------------------------------------------------------ reading

    def event(self, name: str) -> int | None:
        """The place of this sequence's event of that name (`UserData.EventName`), or None."""
        events = self.bpd.BehaviorSequences[self.index].EventData2
        return next((i for i, e in enumerate(events) if str(e.UserData.EventName) == name), None)


    def of_class(self, class_name: str) -> list[int]:
        return [i for i, e in enumerate(self.behaviors)
                if e.Behavior is not None and e.Behavior.Class.Name == class_name]

    def outputs_of(self, behavior: int) -> list[tuple[int, int, float]]:
        """A behaviour's output links: (link id, the behaviour it leads to, its delay)."""
        out = []
        for i in _run(int(self.behaviors[behavior].OutputLinks.ArrayIndexAndLength)):
            packed = int(self.outputs[i].LinkIdAndLinkedBehavior)
            out.append((packed >> 24, packed & 0xFFFFFF, float(self.outputs[i].ActivateDelay)))
        return out

    def variables_of(self, behavior: int) -> dict[str, list[int]]:
        """A behaviour's properties that are fed from variables: property -> the variables' places."""
        out = {}
        for i in _run(int(self.behaviors[behavior].LinkedVariables.ArrayIndexAndLength)):
            link = self.links[i]
            out[str(link.PropertyName)] = [self.linked[j] for j in _run(int(link.LinkedVariables.ArrayIndexAndLength))]
        return out

    def object_at(self, variable: int) -> int:
        """The address an object variable holds (0: none, or not an object variable)."""
        var = self.variables[variable]
        return int(var.Value.Data.Dummy) & 0xFFFFFFFF if int(var.Type) == _BVAR_OBJECT else 0

    def compared(self, behavior: int) -> int:
        """The address a compare's ObjectB is (0 when it isn't a constant)."""
        places = self.variables_of(behavior).get("ObjectB", [])
        return self.object_at(places[0]) if len(places) == 1 else 0

    # ------------------------------------------------------------------------------------------ adding

    def add_object(self, obj: UObject) -> int:
        """A new object variable that holds `obj` (which must stay alive: root it). Returns its place."""
        like = next(v for v in self.variables if int(v.Type) == _BVAR_OBJECT)
        var = copy.copy(like)
        var.Value.Data.Dummy = _word(obj._get_address())
        self.variables.append(var)
        return len(self.variables) - 1

    def add(self, behavior: UObject, like: int | None = None, variables: dict[str, int] | None = None) -> int:
        """Append `behavior` with no outputs. With `like`, it gets that behaviour's variable links, `variables`
        naming another variable for some of its properties. Returns its place."""
        entry = copy.copy(self.behaviors[0 if like is None else like])
        entry.Behavior = behavior
        entry.OutputLinks.ArrayIndexAndLength = 0
        entry.LinkedVariables.ArrayIndexAndLength = 0
        if like is not None:
            first = len(self.links)
            run = _run(int(self.behaviors[like].LinkedVariables.ArrayIndexAndLength))
            for i in run:
                link = copy.copy(self.links[i])
                name = str(link.PropertyName)
                places = ([variables[name]] if variables and name in variables
                          else [self.linked[j] for j in _run(int(link.LinkedVariables.ArrayIndexAndLength))])
                link.LinkedVariables.ArrayIndexAndLength = _packed(len(self.linked), len(places))
                self.linked.extend(places)
                self.links.append(link)
            entry.LinkedVariables.ArrayIndexAndLength = _packed(first, len(run))
        self.behaviors.append(entry)
        return len(self.behaviors) - 1

    def link(self, behavior: int, outputs: list[tuple[int, int]] | list[tuple[int, int, float]]) -> None:
        """Give a behaviour these outputs, in place of what it had: (link id, behaviour[, delay]). They are a new
        run at the end of the list; the old run stays where it was, unused."""
        first = len(self.outputs)
        for link_id, target, *delay in outputs:
            out = copy.copy(self.outputs[0])
            packed = ((link_id & 0xFF) << 24) | (target & 0xFFFFFF)
            out.LinkIdAndLinkedBehavior = _word(packed)
            out.ActivateDelay = delay[0] if delay else 0.0
            self.outputs.append(out)
        self.behaviors[behavior].OutputLinks.ArrayIndexAndLength = _packed(first, len(outputs))

    def link_event(self, event: int, outputs: list[tuple[int, int]] | list[tuple[int, int, float]]) -> None:
        """Give an event these outputs, in place of what it had: (link id, behaviour[, delay]), an event's own link
        id being 0. A new run at the end of the list, like `link`'s; the event takes it when `write` is called."""
        first = len(self.outputs)
        for link_id, target, *delay in outputs:
            out = copy.copy(self.outputs[0])
            out.LinkIdAndLinkedBehavior = _word(((link_id & 0xFF) << 24) | (target & 0xFFFFFF))
            out.ActivateDelay = delay[0] if delay else 0.0
            self.outputs.append(out)
        self._event_runs[event] = _packed(first, len(outputs))

    def chain(self, behaviors: list[UObject]) -> int:
        """Append behaviours that run one after another; returns the first one's place."""
        places = [self.add(b) for b in behaviors]
        for here, after in zip(places, places[1:]):
            self.link(here, [(THEN, after)])
        return places[0]

    def write(self) -> None:
        data = self.bpd.BehaviorSequences[self.index]        # the array element is the provider's own memory
        data.VariableData = self.variables
        data.ConsolidatedLinkedVariables = self.linked
        data.ConsolidatedVariableLinkData = self.links
        data.ConsolidatedOutputLinkData = self.outputs
        data.BehaviorData2 = self.behaviors
        for event, packed in self._event_runs.items():       # last: the runs they name are in the lists now
            data.EventData2[event].OutputLinks.ArrayIndexAndLength = packed

    # ------------------------------------------------------------------------------------------ compare chains

    def chain_end(self, known: UObject) -> int | None:
        """BL2 picks by element with a chain of compares: each either matches or hands on to the next. The last
        compare of the chain that asks for `known` (an element's damage type) somewhere, or None."""
        compares = {i: self.compared(i) for i in self.of_class(COMPARE)}
        address = known._get_address()
        at = next((i for i, a in compares.items() if a == address), None)
        seen: set[int] = set()
        while at is not None and at not in seen:
            seen.add(at)
            onward = [b for link_id, b, _d in self.outputs_of(at) if link_id == DIFFERENT and b in compares]
            if not onward:
                return at
            at = onward[0]
        return None

    def compares(self, obj: UObject) -> bool:
        """Does a compare of this sequence ask for `obj` already?"""
        address = obj._get_address()
        return any(self.compared(i) == address for i in self.of_class(COMPARE))

    def extend_chain(self, known: UObject, obj: UObject, name: str, then: int) -> int:
        """One more compare at the end of `known`'s chain, against `obj`: a match runs the behaviour at `then`,
        anything else goes where the chain's end sent it. Returns the new compare's place."""
        end = self.chain_end(known)
        if end is None:
            raise LookupError(f"{self.bpd._path_name()}: no compare chain with {known.Name}")
        rest = [(link_id, b, d) for link_id, b, d in self.outputs_of(end) if link_id == DIFFERENT]
        kept = [(link_id, b, d) for link_id, b, d in self.outputs_of(end) if link_id != DIFFERENT]
        compare = make(self.behaviors[end].Behavior, self.bpd, name)
        place = self.add(compare, like=end, variables={"ObjectB": self.add_object(_root(obj))})
        self.link(place, [(EQUAL, then, 0.0), *rest])
        self.link(end, [*kept, (DIFFERENT, place, 0.0)])
        return place


# ---------------------------------------------------------------------------------------------- bodies

SWITCH = "IDT_Switch"


def _is(value: Any, name: str) -> bool:
    return str(getattr(value, "name", value)) == name


def add_switch_value(definition: UObject, switch: str, source: int,
                     edit: Callable[[WrappedStruct, int], None]) -> int:
    """A new value for a body's top-level switch: everything that hangs under its value `source` is copied to the
    list's end, under the next value. `edit(attachment, number)` gives each copy its own objects (the copies still
    name the source's templates); `number` counts the copies. Returns the new value."""
    body = definition.BodyComposition
    attachments = [copy.copy(a) for a in body.Attachments]
    at = next(i for i, a in enumerate(attachments)
              if str(a.Data.Name) == switch and _is(a.Data.Type, SWITCH) and int(a.AttachmentBaseInstanceDataIndex) < 0)
    value = int(attachments[at].Data.MaxSwitchValue) + 1
    moved: dict[int, int] = {}                      # a source attachment's place -> its copy's
    for i, a in enumerate(list(attachments)):
        base = int(a.AttachmentBaseInstanceDataIndex)
        if not ((base == at and int(a.SwitchIdentifier) == source) or base in moved):
            continue
        new = copy.copy(a)
        place = len(attachments)
        if base == at:
            new.SwitchIdentifier = value
        else:
            new.AttachmentBaseInstanceDataIndex = moved[base]
        new.Data.BodyCompositionIndex = place + 1   # as the game's own: one past its place
        edit(new, len(moved))
        moved[i] = place
        attachments.append(new)
    if not moved:
        raise LookupError(f"{definition._path_name()}: nothing under {switch} = {source}")
    switched = attachments[at]
    switched.Data.MaxSwitchValue = value
    switched.Data.BitsRequiredForMaxSwitchValue = max(int(switched.Data.BitsRequiredForMaxSwitchValue),
                                                     value.bit_length())
    body.Attachments = attachments
    definition.BodyComposition = body               # write back, in case the read was a copy
    return value
