# Pictures and sound you can replace

Drop a file with the same name into this folder and restart the game. They are read when the game starts.

| File | Where it shows | Size |
| --- | --- | --- |
| `radiation_icon.png` | the title icon of a radiation gun, grenade mod or shield on its item card, and the HUD ammo area | 128x128 or 256x256, transparent; it is drawn about 37 px wide, so make it bold and simple |
| `radiation_icon_row.png` | the small icon before the card's stat rows ("Radiation chance"...) | 64x64, transparent; drawn about 21 px wide |
| `radiation_gas.png` | the gas puffs of radiation explosions, hits and clouds | soft round opacity picture, 0 at the edge |
| `radiation_bubble.png` | the rising bubbles of an explosion | 256x256 ring with a highlight, transparent |
| `radiation_smoke.png` | the wisps on an irradiated enemy | opacity picture |
| `mortar_ring.png` | the mortar underbarrel's marking on the ground (the reticle) | square, any size (512x512 is good): **white or coloured shapes on black**, since the ground adds it to its own colours (black shows nothing, a soft glow is fine). Needs the Willow Texture Loader (texloader); without it, or without the file, the game's own red rings are used |
| `mortar_ring_inner.png` | an optional second, smaller marking inside it (0.42 of its size) | same as above; leave it out to have only the one |
| `mortar_ring_brackets.png` | the other mortar reticle (option "Mortar Reticle": Rocket Brackets), used on its own | same as above |
| `explosion_geiger.wav` | the Geiger counter heard over a radiation death blast | WAV, 44.1 kHz, 16 bit |

PNG: 8 bits per channel, not interlaced. The pictures that came with the mod were drawn by script for it; the sound is the author's own recording.
