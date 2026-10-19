"""When a map has come up: the two game events that say so (RESEARCH.md §6.55).

The game brings a map up in two ways, and no one script event covers both (measured 2026-10-05, hooks on a dozen
events across both):
- **A new world.** From the main menu into a game, back to the menu, and the console's `open`. The new world's
  `WorldInfo.NotifyMatchStarted` runs, a moment before the map's own levels are in it.
- **The game's own travel.** A fast travel station, a level's exit. The world stays (it is "Loader" all along) and
  swaps its levels (`CommitMapChange`, twice: to an empty one, then to the map). `NotifyMatchStarted` does not run,
  the world's clock runs on, and the player's controller and pawn come along.
Both end with the loading screen taken down, once, with the map's levels in:
`WillowPlayerController.WillowClientDisableLoadingMovie`.

Up to 0.4.9 every "new map" handler of the mod hung on `NotifyMatchStarted` alone. In play, none of them ran again
after a session's first map: lists and pools that come with a map (a DLC's, a level's uniques) got no cryo entries,
Marcus did not offer the cryo step in a Sanctuary reached by travel, a boss's map did not get her legendary's drop,
and what was kept about the last map's enemies stayed kept.

A handler hangs on both now (`@hook(maps.ARRIVED, Type.POST)` over `@hook(maps.NEW_WORLD, Type.POST)`). So it is
called twice for a map entered from the menu, and it may be called with the last map's actors still alive (whatever
takes the loading screen down without a change of map). Its rule: let go of what is alive, forget what is gone, and
add only what is not there yet.
"""

NEW_WORLD = "Engine.WorldInfo:NotifyMatchStarted"
ARRIVED = "WillowGame.WillowPlayerController:WillowClientDisableLoadingMovie"
