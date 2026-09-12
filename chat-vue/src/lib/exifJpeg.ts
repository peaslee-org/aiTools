/**
 * Moving EXIF from a phone original onto a canvas re-encode, as raw JPEG bytes.
 *
 * `canvas.toBlob()` throws metadata away, but COLMAP's focal prior reads EXIF FocalLength
 * (see photogrammetry-worker/pipeline/photos.py), so the segment has to be carried across.
 * Pure byte work on purpose: jsdom has no canvas, so keeping this DOM-free is what makes it
 * testable. Nothing here decodes pixels.
 */

const TAG_ORIENTATION = 0x0112
/** FFE1 marker, 2-byte length, then "Exif\0\0" — the TIFF header starts after all of that. */
const TIFF_OFFSET_IN_APP1 = 10

const MARKER_SOI = 0xffd8
const MARKER_APP0 = 0xffe0
const MARKER_APP1 = 0xffe1
const MARKER_SOS = 0xffda
const EXIF_SIG = "Exif\0\0"

/** Frame headers. The 0xC0–0xCF run also holds DHT (C4), JPG (C8) and DAC (CC), which are not. */
const SOF_MARKERS = new Set([
  0xffc0, 0xffc1, 0xffc2, 0xffc3, 0xffc5, 0xffc6, 0xffc7,
  0xffc9, 0xffca, 0xffcb, 0xffcd, 0xffce, 0xffcf,
])

/**
 * A byte view backed by a plain ArrayBuffer. Spelled out because the bare `Uint8Array` alias
 * admits SharedArrayBuffer, which `BlobPart` (and so `new File([...])`) rejects.
 */
export type Bytes = Uint8Array<ArrayBuffer>

export interface Size {
  width: number
  height: number
}

const u16 = (bytes: Bytes, at: number) => (bytes[at] << 8) | bytes[at + 1]

function readSig(bytes: Bytes, at: number, length: number): string {
  let out = ""
  for (let i = 0; i < length; i++) out += String.fromCharCode(bytes[at + i])
  return out
}

/**
 * Walks the JPEG's marker segments, calling `visit` for each until it returns true. Stops at
 * the start of scan: entropy-coded data is not markers, and a stray 0xFFE1 in there must not
 * be mistaken for one.
 */
function eachSegment(
  jpeg: Bytes,
  visit: (marker: number, at: number, length: number) => boolean | void,
): void {
  if (u16(jpeg, 0) !== MARKER_SOI) return
  let at = 2
  while (at + 4 <= jpeg.length) {
    const marker = u16(jpeg, at)
    if ((marker & 0xff00) !== 0xff00 || marker === MARKER_SOS) return
    const length = u16(jpeg, at + 2)
    if (visit(marker, at, length) === true) return
    at += 2 + length
  }
}

/**
 * The complete `FFE1` Exif segment (marker and length word included) of a JPEG, or null when
 * it has none.
 */
export function extractApp1(jpeg: Bytes): Bytes | null {
  let found: Bytes | null = null
  eachSegment(jpeg, (marker, at, length) => {
    if (marker === MARKER_APP1 && readSig(jpeg, at + 4, 6) === EXIF_SIG) {
      found = jpeg.slice(at, at + 2 + length)
      return true
    }
  })
  return found
}

/**
 * The dimensions the pixels are stored at, read from the frame header — before any EXIF
 * rotation is applied. Comparing these with what the decoder handed back is how prepareImage
 * tells whether the browser already rotated the image for us.
 */
export function readStoredSize(jpeg: Bytes): Size | null {
  let size: Size | null = null
  eachSegment(jpeg, (marker, at) => {
    if (!SOF_MARKERS.has(marker)) return
    size = { width: u16(jpeg, at + 7), height: u16(jpeg, at + 5) }
    return true
  })
  return size
}

interface OrientationSite {
  littleEndian: boolean
  /** Offset of the Orientation IFD entry, or null when the tag is absent. */
  entryAt: number | null
}

/** Finds the Orientation entry inside an APP1 segment's IFD0, without reading its value. */
function locateOrientation(app1: Bytes): OrientationSite | null {
  const tiff = TIFF_OFFSET_IN_APP1
  if (tiff + 8 > app1.length) return null

  const littleEndian = app1[tiff] === 0x49 && app1[tiff + 1] === 0x49
  if (!littleEndian && !(app1[tiff] === 0x4d && app1[tiff + 1] === 0x4d)) return null

  const read16 = (at: number) =>
    littleEndian ? app1[at] | (app1[at + 1] << 8) : (app1[at] << 8) | app1[at + 1]
  const read32 = (at: number) =>
    (littleEndian
      ? app1[at] | (app1[at + 1] << 8) | (app1[at + 2] << 16) | (app1[at + 3] << 24)
      : (app1[at] << 24) | (app1[at + 1] << 16) | (app1[at + 2] << 8) | app1[at + 3]) >>> 0

  const ifd0 = tiff + read32(tiff + 4)
  if (ifd0 + 2 > app1.length) return null
  const entries = read16(ifd0)
  for (let i = 0; i < entries; i++) {
    const entry = ifd0 + 2 + i * 12
    if (entry + 12 > app1.length) break
    if (read16(entry) === TAG_ORIENTATION) return { littleEndian, entryAt: entry }
  }
  return { littleEndian, entryAt: null }
}

/** The APP1 segment's Orientation value; 1 (upright) when the tag is absent, per EXIF. */
export function readOrientation(app1: Bytes): number {
  const site = locateOrientation(app1)
  if (!site || site.entryAt === null) return 1
  const at = site.entryAt + 8
  return site.littleEndian ? app1[at] | (app1[at + 1] << 8) : (app1[at] << 8) | app1[at + 1]
}

/**
 * A copy of an APP1 segment with its Orientation tag set to `value`, every other byte intact.
 *
 * Absence is a no-op: EXIF treats a missing Orientation as 1, and adding an IFD entry would
 * mean shifting every offset that follows it. Only the inline SHORT value is touched.
 */
export function setOrientation(app1: Bytes, value: number): Bytes {
  const out = app1.slice()
  const site = locateOrientation(out)
  if (!site || site.entryAt === null) return out
  const at = site.entryAt + 8
  out[at] = site.littleEndian ? value & 0xff : (value >> 8) & 0xff
  out[at + 1] = site.littleEndian ? (value >> 8) & 0xff : value & 0xff
  return out
}

/**
 * `jpeg` with `app1` spliced in: after APP0 when there is one, otherwise straight after SOI.
 *
 * JFIF wants APP0 first and APP1 second, and the segment carries its own length word, so
 * nothing needs recomputing — this is a pure byte splice.
 */
export function insertApp1(jpeg: Bytes, app1: Bytes): Bytes {
  let at = 2
  if (u16(jpeg, 0) === MARKER_SOI && u16(jpeg, at) === MARKER_APP0) {
    at += 2 + u16(jpeg, at + 2)
  }
  const out = new Uint8Array(jpeg.length + app1.length)
  out.set(jpeg.subarray(0, at), 0)
  out.set(app1, at)
  out.set(jpeg.subarray(at), at + app1.length)
  return out
}
