"""What the mod's features stand on. Nothing in here imports a feature (cryo, the Baroness): they import this.

- `setup`: what must be in place before the game reads a save, built once from every feature's steps, in order.
- `items`, `layout`: saveable items (sublibraries appended to the base game's asset libraries), and the keeper that
  puts each save's recorded order back before it is read. `saveedit`: the same items, for the save editor.
- `shaders`, `tpsports`, `particles`: materials compiled at runtime, TPS's own particle materials ported, and the queue
  that gives every carried particle system's emitters their materials. `shadercache`: what the compiler made of them,
  kept between launches.
- `actors`, `swf`: which actors are safe to call into; Scaleform movies written by hand.
- `texts`: the game's language, and the game's own text for a name the mod writes (English where it has none).
- `maps`: the two game events that say a map has come up.
- `later`: work that can wait a frame (a frozen enemy's looks), done inside a time budget each frame.
- `graphs`: BL2's behaviour graphs and the bodies they switch, added to at runtime.
- `netobjects`: objects of the mod's own that other players' games can name (out of a file, blank; or given a place
  once they are made). `placelist`: which ones.
"""

from . import (actors, graphs, items, later, layout, maps, netobjects, particles, placelist, saveedit, setup,
               shaders, swf, texts, tpsports)
from .setup import Step

HOOKS = (*layout.HOOKS, *particles.HOOKS, *later.HOOKS)
STEPS = (
    # First of all: the mod's run-time packages out of their identity file, before any module makes one (netobjects).
    Step("objects", "the mod's packages, for other players' games", lambda: netobjects.identify(placelist.PLACES)),
    Step("materials", "particle materials", particles.start),       # compiled over the next ticks
    # The item libraries first, and they must be there: every feature's items are appended after them, and the whole
    # setup is retried until they are (items must be registered before a save is read, S3).
    Step("items", "item libraries", items.register, guarded=False, needed=True),
    Step("records", "save editor data", lambda: saveedit.export("setup")),      # what bl2-saveedit reads
)

__all__ = ["HOOKS", "STEPS", "Step", "actors", "graphs", "items", "later", "layout", "maps", "netobjects",
           "particles", "placelist", "saveedit", "setup", "shaders", "swf", "texts", "tpsports"]
