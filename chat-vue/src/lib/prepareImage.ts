/**
 * Turning a phone original into something worth uploading: smaller, JPEG, EXIF intact.
 *
 * A 12MP iPhone frame is ~3 MB, so a 150-photo scan is most of half a gigabyte — past what a
 * 15-minute presigned PUT window reliably survives on cellular, and slow through the worker's
 * image_undistorter. Re-encoding at a capped long edge fixes both, and carries the original's
 * EXIF across so COLMAP keeps its focal prior.
 */
import type { Size } from "@/lib/exifJpeg"
import {
  extractApp1,
  insertApp1,
  readFocalLength,
  readOrientation,
  readStoredSize,
  setOrientation,
} from "@/lib/exifJpeg"

/** Long-edge cap in pixels. A 12MP frame (4032×3024) lands at 2400×1800, under a megabyte. */
export const MAX_LONG_EDGE = 2400
const JPEG_QUALITY = 0.9

/**
 * `size` scaled so neither edge exceeds `maxLongEdge`, never upscaled.
 *
 * The scale factor depends only on the longer edge, so swapping width and height swaps the
 * result — which is what keeps a portrait frame an exact transpose of its landscape siblings.
 * photogrammetry-worker/pipeline/photos.py relies on that to rotate the odd frames instead of
 * skipping them.
 */
export function targetSize(width: number, height: number, maxLongEdge: number): Size {
  const longEdge = Math.max(width, height)
  if (longEdge <= maxLongEdge) return { width, height }
  const scale = maxLongEdge / longEdge
  return { width: Math.round(width * scale), height: Math.round(height * scale) }
}

/** `filename` with its extension replaced by `.jpg` — what the re-encode actually is. */
export function jpegName(filename: string): string {
  const dot = filename.lastIndexOf(".")
  return (dot > 0 ? filename.slice(0, dot) : filename) + ".jpg"
}

/** Degrees clockwise that each EXIF orientation is away from upright. Mirroring is ignored. */
const ROTATION_BY_ORIENTATION: Record<number, Rotation> = {
  1: 0, 2: 0, 3: 180, 4: 180, 5: 90, 6: 90, 7: 270, 8: 270,
}

export type Rotation = 0 | 90 | 180 | 270

/**
 * How far the decoded pixels still need turning to sit upright.
 *
 * Whether a browser applies EXIF orientation in `createImageBitmap` varies by engine and
 * version, so rather than trust either answer we compare the frame header's stored size with
 * what came back: transposed dimensions mean the decoder rotated it and there is nothing left
 * to do. Only quarter turns matter — they change the dimensions that COLMAP's
 * `--ImageReader.single_camera 1` insists all photos share. A leftover half turn is per-image
 * roll, which structure-from-motion solves as part of the pose (photos.py says as much), so
 * guessing wrong about 180° costs nothing.
 */
export function rotationFor(
  exifOrientation: number,
  stored: Size | null,
  decoded: Size,
): Rotation {
  if (!stored) return 0
  const decoderRotated = stored.width === decoded.height && stored.height === decoded.width
  if (decoderRotated) return 0
  return ROTATION_BY_ORIENTATION[exifOrientation] ?? 0
}

export interface DrawPlan {
  translateX: number
  translateY: number
  radians: number
  /** Extent to draw the source at, in the rotated frame — swapped for quarter turns. */
  drawWidth: number
  drawHeight: number
}

/**
 * The canvas transform that draws a source image rotated by `rotation` so it exactly fills a
 * `target`-sized canvas. Translation moves the origin to the corner the rotated frame starts
 * from; the drawn extent swaps for quarter turns because the source's width then runs down
 * the canvas rather than across it.
 */
export function drawPlan(rotation: Rotation, target: Size): DrawPlan {
  const quarter = rotation === 90 || rotation === 270
  const drawWidth = quarter ? target.height : target.width
  const drawHeight = quarter ? target.width : target.height
  const origin: Record<Rotation, [number, number]> = {
    0: [0, 0],
    90: [target.width, 0],
    180: [target.width, target.height],
    270: [0, target.height],
  }
  const [translateX, translateY] = origin[rotation]
  return { translateX, translateY, radians: (rotation * Math.PI) / 180, drawWidth, drawHeight }
}

/**
 * A phone original re-encoded as an upright, size-capped JPEG that keeps the original's EXIF.
 *
 * The orientation tag in the result is always 1, because the rotation is baked into the pixels
 * here — leaving the original value would make the worker rotate a second time. An input with
 * no readable EXIF (a HEIC the browser handed over unconverted) still re-encodes, but arrives
 * without a focal prior for COLMAP.
 *
 * Only the pure helpers above are unit-tested; jsdom has no canvas, so this wrapper is
 * deliberately thin and is verified on a real device.
 */
export async function prepareImage(file: File): Promise<File> {
  const original = new Uint8Array(await file.arrayBuffer())
  const app1 = extractApp1(original)
  const stored = readStoredSize(original)

  const bitmap = await createImageBitmap(file)
  const canvas = document.createElement("canvas")
  try {
    const decoded = { width: bitmap.width, height: bitmap.height }
    const rotation = rotationFor(app1 ? readOrientation(app1) : 1, stored, decoded)
    const quarterTurn = rotation === 90 || rotation === 270
    const upright = quarterTurn
      ? { width: decoded.height, height: decoded.width }
      : decoded

    const target = targetSize(upright.width, upright.height, MAX_LONG_EDGE)
    canvas.width = target.width
    canvas.height = target.height
    const ctx = canvas.getContext("2d")
    if (!ctx) throw new Error("Could not get a 2d canvas context")

    const plan = drawPlan(rotation, target)
    ctx.translate(plan.translateX, plan.translateY)
    ctx.rotate(plan.radians)
    ctx.drawImage(bitmap, 0, 0, plan.drawWidth, plan.drawHeight)

    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY),
    )
    if (!blob) throw new Error("Could not encode the resized photo")

    const encoded = new Uint8Array(await blob.arrayBuffer())
    const bytes = app1 ? insertApp1(encoded, setOrientation(app1, 1)) : encoded
    return new File([bytes], jpegName(file.name), {
      type: "image/jpeg",
      lastModified: file.lastModified,
    })
  } finally {
    bitmap.close?.()
    // iOS holds on to canvas backing stores; zeroing it releases ~48 MB per 12MP frame, which
    // matters when 150 of them go through in a row on an older phone.
    canvas.width = 0
    canvas.height = 0
  }
}


/**
 * How much of a file to read when only its metadata is wanted. EXIF sits just after SOI; 64 KB
 * clears even an APP1 carrying an embedded thumbnail.
 */
const EXIF_HEAD_BYTES = 65536

/**
 * The names of photos carrying no EXIF FocalLength, so the picker can say so.
 *
 * On iOS these are the ones captured through the browser rather than picked from the photo
 * library (docs/superpowers/probes/2026-09-12-ios-photo-picker.md); COLMAP falls back to a
 * guessed focal prior for them. Advisory only — a truncated or unusual APP1 reports as missing,
 * which is why this warns rather than rejects.
 */
export async function photosMissingFocalLength(files: File[]): Promise<string[]> {
  const missing: string[] = []
  for (const file of files) {
    const head = new Uint8Array(await file.slice(0, EXIF_HEAD_BYTES).arrayBuffer())
    const app1 = extractApp1(head)
    if (!app1 || readFocalLength(app1) === null) missing.push(file.name)
  }
  return missing
}
