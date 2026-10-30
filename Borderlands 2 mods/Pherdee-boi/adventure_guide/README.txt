ADVENTURE GUIDE  v0.9.2 (beta)   for Borderlands 2 (Steam, PC)
=============================================================

A sixth tab in the status menu (after Badass Rank) that lists where the good loot comes from:
bosses by DLC and map, special enemies, mission rewards, vendors, chests and world drops, with every legendary,
pearlescent, seraph, rainbow, e-tech and unique item they drop. Select an item to read its details; press Enter on it to
open the game's own item card (3D model and stats) - nothing is spawned or added to your inventory.

REQUIREMENTS
  Borderlands 2 (Steam, PC) with the Willow2 Mod Manager installed (the Python SDK mod loader for Borderlands 2,
  github.com/bl-sdk/willow2-mod-manager). Without it the game never loads this mod.

INSTALL
  1. Put "adventure_guide.sdkmod" into  <Borderlands 2>\sdk_mods\
     (e.g. C:\Program Files (x86)\Steam\steamapps\common\Borderlands 2\sdk_mods)
  2. Start the game. The mod switches itself on the first time it runs. Open the status menu: the new tab is the sixth button.
     (If you ever turn it off in the mod menu, it stays off until you turn it on again there.)
  To remove it, delete the file.

USE
  Tab / shoulder buttons   cycle the tabs (Badass Rank -> Adventure Guide -> Mission Log)
  Up / Down / Page keys    move
  Enter                    on a DLC row: fold / unfold it;  on a map: show / hide its bosses;  on a boss: show / hide its drops;
                           on an item: inspect it  (Map sorting is a tree: each level sits a little further to the right)
  Q                        sort: Map, Boss Name, Source Type, Item Type, Rarity
  R                        search: type part of an item, boss, map, DLC, type or rarity name (every word must match)
                           Backspace deletes, Enter keeps the results, Escape clears the search
  Escape                   close the menu (clears the search first when one is open)
  The left-hand panel shows where an item drops. Items that only start dropping at a certain level say so
  ("DROPS FROM LEVEL 50"); items without that line can drop at any level.

NOTES
  * Everything is read from your own game files while you play. The status menu is patched in memory only;
    nothing of the game is copied or changed on disk.
  * Items from DLC you do not own are still listed, but cannot be inspected.
  * Item stats on the card use your current character level.
  * Some sources are placed by pattern (chest and vending-machine families); report anything that looks wrong.

PROBLEMS
  If the tab does not show up, or something breaks, send "adventure_guide.log" from  <Borderlands 2>\sdk_mods\settings\
  (it is rewritten every time the game starts) together with what you did.

CREDITS
  Made by Pherdee with Claude (Anthropic's AI, through Claude Code): AI-assisted work.
  Not affiliated with Gearbox or 2K. Borderlands is a trademark of Take-Two / Gearbox.
