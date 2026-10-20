# BL3 Weapons for Borderlands 2

Borderlands 3's gun mechanics in Borderlands 2, plus two elements BL2 never had: **cryo** (from The Pre-Sequel) and **radiation** (from BL3).
Single player and co-op (every player needs the mod).

## What it adds
- **Weapon Mode key (B):** Maliwan guns swap to a second element (cryo and radiation are in the pool), Vladof guns deploy an underbarrel, Torgue guns
  toggle impact/sticky rounds, Dahl guns cycle full-auto/burst/semi. Tediore guns roll a BL3 reload throw (turret, MIRV, chaser, bouncing). The key also
  explains the passive traits: Hyperion (aim down sights for a frontal shield), Jakobs (critical hits ricochet), Bandit (no reloads: the gun heats up and
  stops firing until it has cooled, even with the trigger held).
- **Weapon add-ons, one switch per manufacturer** (Maliwan, Vladof, Torgue, Dahl, Tediore, Hyperion, Jakobs, Bandit), at the top of Mods menu → BL3 Weapons.
  A manufacturer that is switched off behaves as in the base game (no mode key, no trait, no card lines). Everything else stays.
- **Vladof underbarrels:** assault rifles (rocket launcher, grenade launcher, shotgun, double barrel), pistols (zip rockets, taser, and a double barrel only on
  a Vladof barrel) and snipers (rocket launcher, shotgun, double barrel, bipod). Mods menu → BL3 Weapons → "Vladof Underbarrels That Can Roll" has a switch for
  each: one that is off is not rolled (a gun type with every one of its own off rolls all of its own).
- **Anointments** (off by default, Mods menu): rare and better guns can roll a BL3 anointment, shown on the item card. N shows the held gun's.
- **Relics** roll BL3 artifact perks; **snipers** can roll close-range sights from other gun types.
- **Loot beams** by rarity (off by default: switch them on in the Mods menu), and item cards that show anointments, trait lines and a Maliwan gun's second element.
- **HeartShatter:** the mod's own legendary, a slow, hard-hitting Vladof sniper with a Dahl pistol sight and a bipod, always cryo. Console: `heartshatter give`.
- **Radiation:** a new element after BL3. Damage over time; an irradiated enemy hurts the ones near it and explodes when it dies, leaving a cloud.
  Radiation guns, grenade mods and shields drop in the world (see below) and have their own look: yellow muzzle flashes and tracers, gas explosions
  with rays, a Geiger-counter blast sound. Console: `radiation give [type]`, `radiation apply` (makes the gun in your hands radioactive).
- **Cryo:** freezing and shattering, cryo guns, grenades, shields and relics, the frozen-enemy look. Details in [cryo/README.md](cryo/README.md).

## Requirements
- Borderlands 2 (Steam, PC) with the [Willow2 Mod Manager](https://github.com/bl-sdk/willow2-mod-manager) (v3.8 or newer).
- For **radiation's gas, bubbles and smoke** (the cloud on an irradiated enemy and the explosions): the [Willow Texture Loader (texloader)](https://github.com/juso40/Willow-Texture-Loader)
  by juso, installed as its own mod. Without it those are not drawn (radiation's damage, drops, yellow muzzle flashes and tracers, card icons and names all
  still work).
- For **cryo to work fully:** a copy of Borderlands: The Pre-Sequel installed on the same PC (see "Cryo and The Pre-Sequel" below). Everything else,
  radiation included, does not need it.
- Optional, for the **Geiger-counter sound** of radiation explosions: the [Willow Audio Loader (blaudio)](https://github.com/juso40/Willow-Audio-Loader)
  by juso, installed as its own mod. Without it everything works, the blast just has no Geiger sound.

**The Aurelia mod is not required.** This mod works without it and with it: when Aurelia (Grim Reader's The Pre-Sequel Baroness) is installed and on,
Aurelia supplies cryo, this mod's own cryo steps aside, and everything else, radiation included, works as usual.

**Works with Enforcer** (the Wilhelm character mod, which has a skill that freezes enemies with cryo): Enforcer looks for a "Cryo" mod, so BL3 Weapons keeps
a small `sdk_mods\settings\Cryo.json` (`"enabled": true/false`) in step with itself, and Wilhelm's Cold War freezes as long as this mod's cryo is on. Without
it (or without cryo's files) Enforcer falls back to slag.

## Install
1. Put `bl3_weapons.sdkmod` into the Borderlands 2 folder's `sdk_mods` folder (do not unpack it).
2. Start the game. The mod switches itself on the first time. If a message says cryo's files were built, restart the game once.
3. Options are in the Mods menu → BL3 Weapons. The Weapon Mode key can be changed under Keybinds.

On first start the mod copies its pictures, the Geiger sound and cryo's build scripts to `sdk_mods\settings\bl3_weapons_files`. To change a radiation
picture, put a PNG with the same name into the `art` folder there (see `ASSETS.md`); it is kept when the mod updates.

## Cryo and The Pre-Sequel
Cryo's ice effects, snowflake icon and translated texts are built from **your own copy** of Borderlands: The Pre-Sequel the first time you start the game
(nothing of it ships with this mod). For cryo to look and read as intended you need that copy installed.
- **How it is found:** the mod looks for the Pre-Sequel folder (the one holding `WillowGame\CookedPCConsole\Startup.upk`) in this order: a folder you
  gave it, the `CRYO_TPS` environment variable, your Steam libraries (all of them, any drive), the Epic Games launcher's installs, then the folders
  next to the Borderlands 2 folder. It remembers the folder it used.
- **If it is somewhere else** (for example a copy you moved by hand), the game shows a notice at start-up. Open the console and type
  `cryo build run "D:\Games\BorderlandsPreSequel"` with your own path (or set `CRYO_TPS` to that folder), then restart the game once.
  `cryo build` shows what is built and what is missing.
- **If you don't have it:** the game runs without errors and cryo guns still drop and work, but without the ice effects, snowflake icon and translated
  texts (what cryo then looks like has not been checked). A notice at start-up says so.
- **"Cryo's files can't be built: <file> ..."**: a file cryo needs is already in the game folder and the mod has no record of having written it (an earlier cryo or
  Aurelia, or the mod's settings were reset). If it is byte for byte what the build writes, the mod takes it over by itself. If not, the notice names it: press
  **Mods menu → BL3 Weapons → Cryo → "Replace files in the way and build"** (or type `cryo build adopt` in the console). The old file is moved to
  `sdk_mods\settings\cryo_replaced`, never deleted, and the new one is in place after one restart.

## Drops
Radiation rolls on guns as an element of its own, with chances set to sit in line with the other elements:
- plain guns (white to epic): **6%**, E-tech and Maliwan guns (which always have an element, so radiation takes an even share): **16%**, legendaries that roll an element: **8%** (sliders in the menu, 0 turns it off).
- Maliwan guns also get radiation as their second element (the one the mode key switches to), picked evenly with the other five.
- Legendaries that never have an element in BL2 (Maggie, Skullmasher, Striker, Hammerbuster, the Torgue ones) or have one fixed (Hornet,
  Logan's Gun, Pyrophobia, Slagga...) never roll radiation or cryo.
- Grenade mods and shields: **15%** each (sliders).
- Cryo rolls at shock's odds, on the same makers that roll the other elements (not Bandit, Jakobs or Torgue).

## Problems
Send `unrealsdk.log` (in `Borderlands 2\Binaries\Win32\Plugins`) and say what you did. Radioactive guns, grenades and shields are remembered in
`sdk_mods\settings\bl3_weapons.json` (keep it if you want them to stay radioactive after a save and reload). Cryo guns, grenades and shields are known
only while the mod runs: move them off a character before removing the mod.

## Credits
- **Pherdee**: design, direction, testing.
- **Claude** (Anthropic's AI, through Claude Code) wrote the code; this mod is AI-assisted work.
- **Grim Reader**: the Aurelia mod, whose cryo element is the base of the cryo part (used with permission), and the bl-sdk team's Willow2 Mod Manager.
- The Geiger-counter sound is Pherdee's own recording. The radiation gas, bubble and icon pictures were drawn by script for this mod.

Borderlands 2 and Borderlands: The Pre-Sequel are by Gearbox Software and 2K. This mod is not affiliated with them.
