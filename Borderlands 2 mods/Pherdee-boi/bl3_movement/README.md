# BL3 Movement for Borderlands 2

Borderlands 3's slide and ground slam in Borderlands 2.

- **Slide:** sprint, then crouch. You get a burst of speed that fades (slower downhill, faster uphill). Jump out of a slide to keep the momentum.
  When it ends you stand and go straight back to sprinting, like BL3. The first-person arms tilt and drop, with a slight camera tilt and a field-of-view kick.
- **Ground slam:** crouch in mid-air. You plunge, and land with an explosion that damages enemies around you, with a camera shake.

Options (Mods menu → BL3 Movement): Slide Speed, Slide Length, Sprint After Slide, Slam Damage, Camera Effects, Slide FOV Kick, Slide Camera Tilt.

## Requirements and install
Borderlands 2 (Steam, PC) with the [Willow2 Mod Manager](https://github.com/bl-sdk/willow2-mod-manager). Put `bl3_movement.sdkmod` into the
Borderlands 2 folder's `sdk_mods` folder, start the game and switch the mod on in the Mods menu.

## Credits
**Pherdee** (design, testing) and **Claude** (Anthropic's AI, through Claude Code; this is AI-assisted work). The slide uses the technique of Juso's
"Sliding" mod (bl2sdk-mods): speed through `WillowPawn.CrouchedPct` while crouched. This is a fresh implementation.
Borderlands 2 is by Gearbox Software and 2K; this mod is not affiliated with them.
