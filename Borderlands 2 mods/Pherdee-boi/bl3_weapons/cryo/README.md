# Cryo (part of BL3 Weapons)

The Pre-Sequel's cryo element in Borderlands 2: freezing and shattering, cryo guns that drop like any other element, cryo
grenades, shields and relics, the frozen-enemy look, the snowflake icon on item cards, and the texts in the game's languages.

**This is the cryo part of the Aurelia mod, built into BL3 Weapons.** Aurelia (Borderlands: The Pre-Sequel's Baroness as a playable character) was
built in layers, and cryo was one of them; Grim Reader gave permission to use it as the base for the cryo of the BL3 Weapons mod. It is the same code
with the character, her skills, class mods, voice, slam and Contract left out.

**Nothing from The Pre-Sequel ships with the mod.** Cryo's files (TPS's ice effects, explosions, the cryo icon and texts) are built on your PC, from
your own copy of The Pre-Sequel, the first time you start Borderlands 2 with the mod. The Baroness Pack is not needed. Without The Pre-Sequel nothing breaks (tested: no errors, cryo guns still drop), but cryo has no ice effects, snowflake icon or
translated texts; what it looks like then has not been checked. Everything else in BL3 Weapons (radiation included) is unaffected.

## Together with Aurelia

Both make the same cryo objects and hook the same functions, so they cannot both run. When the Aurelia mod is installed and not switched off in the
MODS menu, the cryo part **stands aside** (builds nothing, hooks nothing, says so in `unrealsdk.log`) and Aurelia supplies cryo. The rest of BL3
Weapons, radiation included, keeps working: radiation's effects run on Aurelia's own core and are built from Borderlands 2's files (a first start
builds them and asks for one restart). To use this cryo instead: switch Aurelia off (or remove her folder).

Cryo items already in your saves keep working: the packages and sublibraries keep Aurelia's names, so a cryo gun, grenade or shield saved with
Aurelia loads here. Items that are Aurelia's own (her class mods and legendaries) are not known to this mod and are dropped the next time that
character saves: take them out of the characters you want to keep them on first.

## What you need

- Borderlands: The Pre-Sequel installed on the same PC, for cryo to work fully. It is looked for in your Steam libraries, the Epic Games launcher's
  installs and the folders beside Borderlands 2; anywhere else: `cryo build run "D:\Games\BorderlandsPreSequel"` in the console (or set `CRYO_TPS`).
- The first start builds cryo's files in the background (a few seconds); a message says when it is done and whether to restart Borderlands 2.

## Cryo guns

Cryo rolls like the other elements, at shock's odds, on every manufacturer that rolls elements, except Bandit (The
Pre-Sequel's cryo guns are Scav's, not Bandit's), Jakobs (no elements) and Torgue (explosive only).

Their cards show The Pre-Sequel's cryo lines: Freeze Damage / sec., Freeze Chance and "Freezes enemies solid.", and its
cryo icon, a snowflake, where the other elements show theirs: beside those lines and beside the maker's name.

What a cryo gun fires is cryo too: a launcher's rockets, an E-tech gun's darts, spikes, blobs and plasma, a Maliwan
gun's splash. They explode with The Pre-Sequel's ice explosion, and a Maliwan launcher's rocket leaves a patch of cryo
damage where it lands, as it leaves one of its own element for the others.

Cryo guns shoot with The Pre-Sequel's cryo effects too: its muzzle flashes (their sprites pulled in towards the barrel, which BL2's guns have shorter),
tracers, rocket trails, the barrel's glow and the ice bullet impact. They are cut from your own TPS copy with the other effects (a build, then one restart; nothing
of TPS ships). The separate Cryo Shot FX mod that did this before is not needed any more: if it is still installed this stands aside for it, so remove it.

A cryo gun's glow is The Pre-Sequel's plain white. A cryo E-tech gun, whose shell and sights are all glow, has a
bright icy white there (since 0.5.5), the white of its shots' trails; up to 0.5.4 those parts were a dull grey. If
another mod gives such a gun a glow of its own, that one is kept.

An amp shield works with a cryo gun as with any other: a shot fired with the shield full deals the shield's Amp
Damage more, split over the gun's pellets, as cryo, and takes the Amp Shot Drain off the shield (measured in 0.6.2:
the same gain a pellet as the same gun in shock).

## With the other vault hunters

Cryo guns are any class's to use. What the classes' skills do with them was tried with each class at full ranks
(0.5.2): they do with cryo what they do with shock.
- Krieg's Bloodsplosion: a cryo kill's blast is cryo (see Frozen enemies), and a chain of them stays cryo.
- Zer0's B0re: a cryo bullet that goes through an enemy hits the one behind it as cryo, for twice the damage.
- Maya's Chain Reaction and Gaige's The Nth Degree: the bullets that go on to another enemy are cryo.
- Axton's Duty Calls is a bonus for guns without an element, and cryo is an element: since 0.5.2 a cryo gun gets none
  of it (up to 0.5.1 it did: 5% gun damage and 3% fire rate a rank).
- Salvador's Gunzerk with two cryo guns, and each class's action skill beside cryo: nothing unusual.

## Frozen enemies

A frozen enemy looks as in The Pre-Sequel: coated in ice (a frost layer built for Borderlands 2, whose enemy materials
have none), icicles on its body and frost around it, the ice breaking off when it thaws. An enemy that dies frozen
shatters in a burst of ice with the Cryo Nova's blast, and its body goes.

Cryo explosions are The Pre-Sequel's ice explosions (three sizes), and a Cryo Nova shield's blast is its ice nova. A cryo
kill sets off Bloodsplosion (Krieg's skill, or a skill mod's): The Pre-Sequel's shatter in place of red gore, then its
ice explosion, dealing cryo damage.

Some enemies resist cryo. "Can't be frozen" means no freeze and no slow; the others' chance to be frozen or slowed, and
how long and how much, go down by their share.
- Mercenary Day: the snowmen (Sir Tinder Snowflake, the Frost Nippers) are immune; Snow Bandits and Snow Psycho Midgets
  resist 80%, Snow Psychos 85%, Badass Snow Bandits and the Snow Goliath 90%, and none of them can be frozen; the yetis
  resist most of it.
- The Freezing Psycho resists 75% (Normal Mode), the Arctic Maniac 95% (True Vault Hunter Mode), the Frosty Lunatic 75%
  (Ultimate Vault Hunter Mode); it can't be frozen. Snow skags are immune.
- In Windshear Waste and Three Horns, Badass Bullymongs resist 10%.
- In Tiny Tina's Assault on Dragon Keep, Badass Sorcerers resist 95%, Sorcerers 90% once they turn white (until they
  die), and Paladins and Wizards 80% while their bubble holds; none of these can be frozen meanwhile.
- Commander Lilith's New Pandora troops resist more by rank: 10% (Scouts, Recruits), 20% (Heavies), 30% (Medics,
  Infectologists), 40% (Pyros, Snipers), 60% (Commanders, Badass Snipers), 75% (the lieutenants).
- Enemies that resist fire, shock and corrosive alike resist cryo as much; fire enemies don't resist it.
- A part of an enemy that lets only a fixed share of its health through takes cryo in full, whatever the enemy
  resists (since 0.5.4): a Crystalisk's three leg crystals, a third of its health each; the Rakk Hive's eyes; the
  Leviathan's parts. The game counts a hit against such a part before the mod can make it smaller.

## Cryo gear

The Pre-Sequel's cryo gear, rolling wherever shock's does, at shock's odds:
- **Grenades:** every grenade type that rolls an element can be cryo, with The Pre-Sequel's names (Cryo MIRV, Snow Ball,
  Frigid Sheila...) and its card line, "Cryo Damage / sec.". An area-effect grenade (Vladof's) leaves The Pre-Sequel's
  cloud of cryo damage, in an icy spray; a Bouncing Betty's shots are cryo.
- **Shields:** Cryo Nova and Cryo Spike shields, and Maliwan's **Warming** shields: immune to being frozen, like
  Grounded shields are to shock.
- **Relics:** Elemental relics can raise cryo damage, Resistance relics cryo resistance.

Cryo grenades, Cryo Nova and Cryo Spike shields and cryo damage relics show the cryo icon on their cards too.

## Options (Mods menu → BL3 Weapons → Cryo)

A **Cryo** section and two buttons: **Build cryo's files now** and **Remove cryo's files**. In co-op the host's settings apply.

**Cryo**
- **Cryo Item Drops** (Yes): cryo guns, grenades, shields and relics drop like the other elements, in loot, in shops
  and on enemies. No: cryo is taken out of the game's loot lists and pools, and nothing of it is added: they are
  Borderlands 2's own again, for use beside a mod that changes them. The cryo items you own stay and work as before,
  and so does the element. It takes effect at once, both ways.
- **Slag + cryo bonus cap** (On): in Normal and True Vault Hunter Mode a slagged, frozen enemy takes at most 3x from the
  two bonuses together. Ultimate Vault Hunter Mode is never capped.
- **Enemies use cryo guns** (On): enemies roll cryo guns like any other element and can freeze you. Off: cryo guns are
  player loot only. (With Cryo Item Drops on No there are none to roll.)


Console: `cryo` (status), `cryo build` (the files' state), `cryo build run|adopt|uninstall`, `cryo layout`, `cryo cryo` (frozen enemies).

## Switching the mod off / removing it

MODS menu, BL3 Weapons, disable: from that moment none of the mod's code runs and enemies it held frozen are let go. What it built stays in the game until
you restart it. Cryo guns, grenades and shields are known only while the mod runs: **move them out of every character you want to keep them on before
you remove the mod** (Borderlands 2 drops what it cannot read the next time that character saves; cryo relics are Borderlands 2's own relics and stay,
without their cryo effect). Then Mods → BL3 Weapons → **Remove cryo's files**, and delete `sdk_mods/bl3_weapons`.

## Credits

The cryo element, the converter and the build were made by **Grim Reader** and **Claude** (Anthropic's AI) for the Aurelia mod: Grim Reader directed it,
made the calls and play-tested it; Claude did the research and wrote the converter and the mod. Used here as the base for the cryo of the BL3 Weapons
mod with Grim Reader's permission; building it into BL3 Weapons was done by Pherdee and Claude. Built on the bl-sdk team's willow2-mod-manager.

Borderlands 2 and Borderlands: The Pre-Sequel are by Gearbox Software and 2K. This mod is not affiliated with them.
