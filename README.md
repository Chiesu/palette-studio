# Palette Studio 0.3.0

A Blender 4.5+ extension for scene-wide art-direction looks: independently named colors per palette, multiple material/light/node targets per color, and one-click switching between palettes. No subscriptions, accounts, or external services.

## What changed in 0.3.0

### 1. Fixed: Base Color node-link crash on Blender 5.2

Previous versions attempted to store an embedded `Material.node_tree` directly inside a `PointerProperty`. Blender disallows that and raised:

```
RuntimeError: RNA_property_pointer_set: cannot assign an embedded ID to an IDProperty
```

Palette Studio now saves the owning **Material, Light, World, Scene, or Texture** and resolves its node tree at application time. Independent node groups can still be referenced directly. This works for a Principled BSDF Base Color, additional node sockets, and ColorRamp stops. No shader connections are disconnected.

### 2. Quick Link: edit and link on the same row

Open the **Palette** sidebar in the 3D View or Shader Editor:

1. Select the active palette and one named swatch in **01 COLORS**.
2. Expand **02 LINK COLORS**. Relevant Blender colors appear as live color chips next to their labels.
3. Click the **chain** icon next to a color target to bind it to your selected swatch. The target immediately receives the swatch's color.
4. Click the **eyedropper** icon to **sample** the target's current value into the selected swatch instead. Sampling is not a link.
5. Change the swatch to recolor its linked targets (when **Live Color Edit** is on).

The 3D View shows Shader Base Color (when the active material has an unambiguous Principled node), Material Viewport, Object Display, Light Color (for a selected light), and World Background. **04 BATCH LINK** contains bulk assignments.

### 3. Right-click a Blender color property to link it

You can now right-click supported color fields elsewhere in Blender, such as a Principled BSDF Base Color input, a separate Emission Color input, a Light color, or Material Viewport color.

The button context menu adds:

- **Link this color to selected swatch**: bind the exact RNA color property to the selected swatch, and apply that swatch color.
- **Sample this color into swatch**: copy the current RGB/RGBA values into the swatch without binding.

Supported right-click fields: Object Color, Material Viewport Color, Light Color, World Color, editable RGBA node sockets, and ColorRamp stops in the Shader/Node Editor. The right-click entries are intentionally not shown for arbitrary unrecognized properties.

**Why not a screen eyedropper for binding?** Blender's conventional color eyedropper reads a displayed pixel/color, not which data-block, material, node, and socket owns it. A pixel sampler would silently attach colors to the wrong source. The native right-click color-field menu identifies the actual property and is more reliable.

### 4. Selected node color pickers

In the Shader Editor, select one or more nodes. **03 SELECTED NODE COLORS** displays editable RGBA inputs and ColorRamp stops as color chips, each with a chain and eyedropper. You can give one Principled material a `Base`, `Emission`, and `Coat` palette color independently. A socket already connected by a node link is shown as locked and is not overwritten.

### 5. More compact defaults

The Palette sidebar now prioritizes the selected palette, colors, and direct links. Palette duplication, variation generation, bulk operations, assignments inspection, and JSON import/export remain available in collapsed secondary sections.

## Install / update on Blender 5.2 (Mac)

1. Save a **backup copy of your .blend** while Palette Studio 0.2 is still available. Existing palettes and assignments are saved inside the .blend.
2. Go to **Edit > Preferences > Get Extensions > top-right menu > Install from Disk**.
3. Select `palette_studio-0.3.0.zip` **without extracting**.
4. Replace/update the previous `palette_studio` extension. Do not enable two installations of the same add-on. If Blender still shows the old UI, restart Blender.
5. Open your saved project and find **3D View / Shader Editor > N sidebar > Palette**.

The existing version 0.2 scene-local palette data is preserved. There is no palette reset, and no re-linking is required for supported assignments. **Previously failed Base Color links were never saved by v0.2**, so link those again using the new chain icon or right-click menu.

Projects last saved with v0.1 will still be migrated to palette-local colors/assignments when opened (the legacy fields are kept for compatibility). Back up the file before first save after upgrading.

## Recommended workflow: Day / Night look

1. Select `Day`, select or create `Warm Key`, select a light and click **Link** next to `Light Color`.
2. Select `Warm Wall`, select the wall material, then click **Link** next to `Shader Base`. You can also right-click the actual Base Color field and choose **Link this color to selected swatch**.
3. Duplicate `Day` using **05 PALETTE MANAGEMENT > Duplicate**. This copies the color list and all assignments into a new, independent palette.
4. Rename the new palette to `Night`, and change its copied color swatches to cold colors (rename those colors freely).
5. Switch between `Day` and `Night` at the top of the panel. Both sets of linked values are applied to the same scene targets in one click.

**One color can control many targets**. The same swatch can be assigned to many materials, sockets, ColorRamp stops, and lights. **One target can have one color at a time within each palette**, and different colors across different palettes. Different sockets of the same material may have different swatches in a single palette.

## Existing behaviors retained from 0.2

- Add, duplicate, rename, delete, and switch independent palettes; derive Warm, Cool, Muted, Darker, or Lighter variants.
- Create named swatches, change sRGB HEX or Blender linear RGB/RGBA values, copy/paste HEX, reorder or delete swatches.
- Copy assignments from another palette; filter assignments by selected color; inspect, disable, change target color, unlink or restore originals.
- Live editing and optional automatic palette application on selection. Manual **Apply Scene Palette** remains available.
- If a target is absent from the next palette: **Restore Original** or **Keep Last Color**.
- Export/import independent palette colors as JSON, export the active palette as GIMP `.gpl`, and import swatches from Color Helper when installed. Exports are colors/names; scene target assignments remain in `.blend`.
- Works with Material Viewport colors, Object Display colors, light datablocks, World colors, Material/Light/World node input sockets, node groups, and ColorRamp stops.

## Safety and known constraints

- A shared Blender Material or Light datablock affects all objects that use it. Use **single-user** datablocks for object-specific looks.
- Palette Studio changes existing color values only: it does not create new shader nodes, change node links, overwrite image textures, or create animation keyframes.
- Connected shader inputs are protected. An image texture, Mix node or other linked source will continue controlling that input.
- ColorRamp stop assignments use stop indexes. If you insert or delete stops, inspect and re-link the affected stop.
- Properties animated by keyframes/drivers may override palette values during playback or rendering. There is no automatic frame-by-frame handler.
- `Material Viewport` and `Shader Base` are separate Blender properties. Changes to the viewport swatch do not necessarily change the rendered material.
- Blender stores shader colors in scene-linear values; HEX uses sRGB conversion. AgX/exposure/world lighting still affect the apparent render color.
- **Restore Original** reuses the first captured target value. It is not a substitute for Undo or file-version backups.
- Only supported color RNA inputs can be directly right-click-linked. A screen-pixel eyedropper cannot reliably discover data targets, and arbitrary custom node-group UI inputs might require selecting their underlying node socket.

## Verification

The 0.3 update passes 18 focused simulated-Blender behavioral tests, including an explicit reproduction of Blender's embedded-ID pointer restriction, palette switching on shared material/light targets, original color restoration, standalone node groups, ColorRamp stops, direct button-context linking, and protections for connected inputs. Python source compilation and extension ZIP integrity are checked separately.

**A live Blender 5.2 UI test has not been run in the build environment.** Use a duplicate .blend for the first update test; report any traceback and the field/operator clicked so that Blender-specific context discrepancies can be corrected.

Source is included in the ZIP. License: GPL-3.0-or-later. Independently implemented using the Blender Python API after reviewing Color Helper 0.8.0 (GPL-3.0-or-later).

## Public GitHub distribution

Source: https://github.com/Chiesu/palette-studio

Download the installable Blender Extension ZIP from the latest [GitHub Release](https://github.com/Chiesu/magnetic-palette/releases/latest), not the source-code ZIP shown by GitHub's green Code menu. Blender: **Edit > Preferences > Get Extensions > Install from Disk**.

The GitHub distribution is an independent experimental project. Changes to external provider APIs, or installed add-on versions, may require follow-up updates. See [PUBLIC-RELEASE.md](PUBLIC-RELEASE.md) for upgrade/licensing notes.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE). External downloaded assets have their own provider licenses and are not included in this repository.
