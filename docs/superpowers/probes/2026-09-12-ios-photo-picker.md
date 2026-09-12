# What an iPhone hands a web page from the photo picker

Measured 2026-09-12 on an iPhone XS and an iPhone 11, mobile Safari, against a throwaway page
served over the LAN. It settles the assumptions `chat-vue/src/lib/prepareImage.ts` rests on; if
that file's rotation detection is ever reworked, re-run something like this first.

## Findings

**HEIC never reaches the page.** Every file arrived as JPEG with an `Exif` APP1 segment, whether
the input declared `accept="image/jpeg"` or `accept="image/*"`. The `accept` attribute does *not*
drive the conversion — iOS transcodes either way. So the planned fallback for parsing EXIF out of
HEIF boxes was never needed.

**The decoder auto-rotates, but the tag stays stale.** For a portrait shot:

| | stored (SOF) | decoded (`createImageBitmap`) | EXIF Orientation |
|---|---|---|---|
| Landscape | 4032x3024 | 4032x3024 | 1 |
| Portrait | 4032x3024 | 3024x4032 | 6 |

Storage stays sensor-native landscape; the decoder returns upright pixels; the Orientation tag is
left at 6. **Acting on that tag would rotate the image a second time.** This is why
`rotationFor()` decides by comparing stored against decoded dimensions rather than reading the
tag, and why the prepared file's Orientation is stamped to 1 — the pixels written really are
upright, and `photogrammetry-worker/pipeline/photos.py` would otherwise `exif_transpose` them
again.

The resulting prepared sizes, 3000x2250 and 2250x3000, are exact transposes, which is what lets
`normalise()` rotate the minority orientation instead of skipping it under
`--ImageReader.single_camera 1`.

**In-browser camera captures lose FocalLength.** Photos taken by tapping "Take Photo" in the
picker sheet arrived with an APP1 and a readable Orientation but **no FocalLength** (`f4.25` on
library photos, absent on captures). COLMAP then falls back to its `1.2 x max-dimension` focal
prior for those frames.

*Practical consequence:* shoot in the Camera app and pick from the library. Capturing through the
web page costs the focal prior.

## Not covered

- Only the wide lens (4.25 mm) was exercised — no ultra-wide, telephoto, or Portrait-mode shots.
- No screenshots or crops, whose differing aspect ratios the worker skips with a warning.
- Android was not tested; this feature targets these two phones (Web Share Target, the other
  route to "push from my phone", is unimplemented in iOS Safari, so a PWA buys nothing here).
