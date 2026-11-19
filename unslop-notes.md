# AutoMap interface pass

## Approved direction

- Product: Generate maps and compose map videos
- Character: Precise, technical, composed
- World: Cartographic workbench, graphite surfaces and a restrained teal accent
- Moments: Generated maps and video output carry the rich color
- Signature: Shared flat folded-map mark

## Changes by pass

1. Spacing: Unified half-step control spacing, panel gaps and mobile padding around the 4px grid
2. Color: Replaced multicolor chrome with graphite surfaces, quiet borders and a teal selection accent; retained output color pickers
3. Icons: Kept the existing Font Awesome library, removed decorative emoji from labels and standardized common chrome icon boxes
4. Copy: Shortened generation and composition labels, removed inflated synthesis language and decorative coordinate claims
5. Type: IBM Plex Sans for the interface, JetBrains Mono for parameters; raised tiny utility labels to 12px and reduced heavy headings
6. Layout: Preserved section order and workflows; bounded the map work area to 720px on desktop and 560px on phones so the empty state stays visible
7. Details: Shared 8px control corners, consistent radio selection and keyboard focus, neutral command badges, off-white primary generation/render actions
8. Visuals: Removed ambient blobs, glowing logos and glass treatments; retained actual map and video previews with a quiet grid
9. Motion: Removed decorative pulses, bouncing checks and fake equalizer activity; retained loading spinners and actual video playback; added reduced-motion handling

No generated images or image prompts were needed.

## Validation

- Visually reviewed both workspaces at 1440px desktop and 390px phone widths
- No horizontal overflow or page script errors in those renders
- Existing map/video browser suite: 50 passed
- Two failures reproduced with the original, unchanged Video Studio template: portrait map replacement centering and oversized generated-map preparation
- No backend, request schema, generated-media settings or service integration changes intended

Method: Unslop by Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), https://heydamir.design/unslop

## Layout and palette revision

Approved palette: light gray and white with forest green actions (`#28623c`). The folded-map mark and existing type families remain.

- Spacing: 20px workspace gutters, 16px action panels and explicit spacing within settings groups
- Color: white panels on a pale neutral canvas, dark ink, forest green actions and selections; the authored video canvas stays dark
- Icons: retain the shared folded-map mark and quiet existing icons
- Copy: numbered map groups and mobile audio/compose/preview navigation
- Type: retain IBM Plex Sans and JetBrains Mono with dark text for light surfaces
- Layout: independently scrolling map controls with a separate action panel; desktop video workspace splits into audio, composition and persistent preview columns
- Details: native form associations keep relocated Generate and Render buttons connected to their original forms; appearance/model groups are collapsible and open by default
- Visuals: keep real map and video output as the primary visual; no generated image or prompt required
- Motion: preserve reduced-motion handling and restrained control feedback

Phone layouts stack the sections, add anchor navigation, and pin Generate/Re-Paint or Render within reach. Desktop layout primitives live in local CSS so controls remain reachable when the utility CDN is unavailable.

Revision validation: 50 existing browser tests pass with the action-location assertion updated for the new panel. The two previously confirmed baseline failures remain excluded. Both submit buttons retain native connections to their original forms. Final responsive checks cover 1440px, 1024px, 820px and 390px widths.

## Dark theme follow-up

Changed both workspaces to graphite backgrounds and dark panels, with light text, sage focus/selection indicators and forest-green primary actions. Kept the grouped controls, scrolling panels and persistent previews. Verified 1440px, 1024px, 820px and 390px widths: no horizontal overflow or page script errors; both relocated submit buttons remain associated with their original forms. This follow-up changes theme values only; the behavior suite was not rerun.

## Mobile interface audit

Checked both studios at 320px, 390px, 430px and 768px widths, including section links, the map style modal, title editing and an image upload into the video preview. Found and fixed overlapping map metadata/sample controls at 320px by putting the metadata below the canvas content. Enlarged the video empty-state text, adjusted mobile anchor offsets, removed empty action-panel frames and added safe-area clearance to pinned actions.

After these fixes: no horizontal overflow or page script errors across those sizes. Existing mobile video-frame, watermark upload/validation and map repaint interaction tests: 3 passed. Checks use desktop Chromium with resized viewports; physical-device keyboard and iOS Safari behavior were not tested.
