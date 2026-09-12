import { describe, expect, it } from "vitest"
import type { Bytes } from "@/lib/exifJpeg"
import { extractApp1, insertApp1, readOrientation, readStoredSize, setOrientation } from "@/lib/exifJpeg"

const ORIENTATION = 0x0112
const FOCAL = 0x920a

const bytes = (values: number[]): Bytes => new Uint8Array(values)

const le16 = (n: number) => [n & 0xff, (n >> 8) & 0xff]
const le32 = (n: number) => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >> 24) & 0xff]
const be16 = (n: number) => [(n >> 8) & 0xff, n & 0xff]

/**
 * A little-endian TIFF block: IFD0 with an optional SHORT Orientation and an optional
 * RATIONAL FocalLength whose value sits past the end of the IFD.
 */
function buildTiff(tags: { orientation?: number; focal?: [number, number] }): number[] {
  const shorts: Array<[number, number]> = []
  if (tags.orientation !== undefined) shorts.push([ORIENTATION, tags.orientation])
  const hasFocal = tags.focal !== undefined
  const count = shorts.length + (hasFocal ? 1 : 0)
  const rationalOffset = 8 + 2 + count * 12 + 4

  const out: number[] = [0x49, 0x49, ...le16(42), ...le32(8), ...le16(count)]
  for (const [tag, value] of shorts) {
    out.push(...le16(tag), ...le16(3), ...le32(1), ...le16(value), 0, 0)
  }
  if (hasFocal) out.push(...le16(FOCAL), ...le16(5), ...le32(1), ...le32(rationalOffset))
  out.push(...le32(0)) // no IFD1
  if (hasFocal) out.push(...le32(tags.focal![0]), ...le32(tags.focal![1]))
  return out
}

const EXIF_SIG = [0x45, 0x78, 0x69, 0x66, 0, 0] // "Exif\0\0"

function app1Segment(tags: { orientation?: number; focal?: [number, number] }): number[] {
  const payload = [...EXIF_SIG, ...buildTiff(tags)]
  return [0xff, 0xe1, ...be16(payload.length + 2), ...payload]
}

const APP0_JFIF = [
  0xff, 0xe0, ...be16(16),
  0x4a, 0x46, 0x49, 0x46, 0x00, // "JFIF\0"
  1, 1, 0, 0, 1, 0, 1, 0, 0,
]

const SCAN = [0xff, 0xda, ...be16(8), 1, 1, 0, 0, 0x3f, 0x00, 0x7a, 0x7a, 0xff, 0xd9]

/** A JPEG shaped like a phone original: SOI, APP0, APP1(Exif), scan. */
function originalJpeg(tags: { orientation?: number; focal?: [number, number] }): Bytes {
  return bytes([0xff, 0xd8, ...APP0_JFIF, ...app1Segment(tags), ...SCAN])
}

/** A JPEG shaped like canvas.toBlob output: SOI, APP0, scan. No Exif. */
function canvasJpeg(): Bytes {
  return bytes([0xff, 0xd8, ...APP0_JFIF, ...SCAN])
}

describe("extractApp1", () => {
  it("returns the whole APP1 segment, marker and length included", () => {
    const expected = app1Segment({ orientation: 6, focal: [425, 100] })

    const found = extractApp1(originalJpeg({ orientation: 6, focal: [425, 100] }))

    expect(found).not.toBeNull()
    expect(Array.from(found!)).toEqual(expected)
  })
})

describe("extractApp1 boundaries", () => {
  it("returns null for a canvas re-encode, which carries no Exif", () => {
    expect(extractApp1(canvasJpeg())).toBeNull()
  })

  it("does not mistake an 0xFFE1 byte pair inside scan data for a segment", () => {
    // Entropy-coded data is not markers. Walking past SOS reads garbage lengths and,
    // worse, can return a bogus "segment" that gets spliced into the upload.
    const scanWithFakeMarker = [
      0xff, 0xda, ...be16(8), 1, 1, 0, 0, 0x3f, 0x00,
      0xff, 0xe1, ...be16(8), 0x45, 0x78, 0x69, 0x66, 0x00, 0x00,
      0xff, 0xd9,
    ]
    const jpeg = bytes([0xff, 0xd8, ...APP0_JFIF, ...scanWithFakeMarker])

    expect(extractApp1(jpeg)).toBeNull()
  })
})

describe("setOrientation", () => {
  it("rewrites the Orientation tag and changes nothing else", () => {
    // Canvas drawing bakes rotation into the pixels, so a surviving 6 would make the worker
    // rotate a second time — the same trap photos.py:46 avoids server-side.
    const original = bytes(app1Segment({ orientation: 6, focal: [425, 100] }))
    const asIfShotUpright = bytes(app1Segment({ orientation: 1, focal: [425, 100] }))

    const patched = setOrientation(original, 1)

    expect(Array.from(patched)).toEqual(Array.from(asIfShotUpright))
  })

  it("leaves a segment without an Orientation tag alone, since absent already means upright", () => {
    const noOrientation = bytes(app1Segment({ focal: [425, 100] }))

    const patched = setOrientation(noOrientation, 1)

    expect(Array.from(patched)).toEqual(Array.from(noOrientation))
  })
})

describe("insertApp1", () => {
  it("places the segment after APP0, where a JFIF reader expects to find it", () => {
    const app1 = bytes(app1Segment({ orientation: 1, focal: [425, 100] }))

    const merged = insertApp1(canvasJpeg(), app1)

    expect(Array.from(merged)).toEqual([
      0xff, 0xd8, ...APP0_JFIF, ...Array.from(app1), ...SCAN,
    ])
  })

  it("places the segment straight after SOI when there is no APP0", () => {
    const bare = bytes([0xff, 0xd8, ...SCAN])
    const app1 = bytes(app1Segment({ orientation: 1, focal: [425, 100] }))

    const merged = insertApp1(bare, app1)

    expect(Array.from(merged)).toEqual([0xff, 0xd8, ...Array.from(app1), ...SCAN])
  })

  it("round-trips: the spliced file reads back the focal length it was given", () => {
    const original = originalJpeg({ orientation: 6, focal: [425, 100] })

    const carried = setOrientation(extractApp1(original)!, 1)
    const merged = insertApp1(canvasJpeg(), carried)

    const recovered = extractApp1(merged)
    expect(recovered).not.toBeNull()
    expect(Array.from(recovered!)).toEqual(Array.from(carried))
    expect(Array.from(recovered!)).toEqual(app1Segment({ orientation: 1, focal: [425, 100] }))
  })
})

/** A JPEG carrying a frame header, so stored (pre-rotation) dimensions can be read back. */
function jpegWithSof(marker: number, width: number, height: number): Bytes {
  const sof = [0xff, marker, ...be16(17), 8, ...be16(height), ...be16(width), 3,
               1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1]
  return bytes([0xff, 0xd8, ...APP0_JFIF, ...sof, ...SCAN])
}

describe("readOrientation", () => {
  it("reads the Orientation tag", () => {
    expect(readOrientation(bytes(app1Segment({ orientation: 6 })))).toBe(6)
  })

  it("reports upright when the tag is absent, as EXIF specifies", () => {
    expect(readOrientation(bytes(app1Segment({ focal: [425, 100] })))).toBe(1)
  })
})

describe("readStoredSize", () => {
  it("reads the dimensions the pixels are actually stored at", () => {
    expect(readStoredSize(jpegWithSof(0xc0, 4032, 3024))).toEqual({ width: 4032, height: 3024 })
  })

  it("reads a progressive frame header too", () => {
    expect(readStoredSize(jpegWithSof(0xc2, 4032, 3024))).toEqual({ width: 4032, height: 3024 })
  })

  it("does not mistake a Huffman table for a frame header", () => {
    // DHT is 0xFFC4 — inside the SOF marker range but not an SOF. Reading its payload as
    // dimensions would yield nonsense and misjudge whether the decoder rotated the image.
    const dht = [0xff, 0xc4, ...be16(6), 0x00, 0x01, 0x02, 0x03]
    const jpeg = bytes([0xff, 0xd8, ...APP0_JFIF, ...dht, ...SCAN])

    expect(readStoredSize(jpeg)).toBeNull()
  })
})
