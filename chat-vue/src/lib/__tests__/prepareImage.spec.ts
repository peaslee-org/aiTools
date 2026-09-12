import { describe, expect, it } from "vitest"
import { MAX_LONG_EDGE, drawPlan, jpegName, rotationFor, targetSize } from "@/lib/prepareImage"
import type { Rotation } from "@/lib/prepareImage"

describe("targetSize", () => {
  it("caps the long edge of a 12MP landscape frame", () => {
    expect(targetSize(4032, 3024, 3000)).toEqual({ width: 3000, height: 2250 })
  })

  it("gives portrait and landscape results that are exact transposes", () => {
    // photogrammetry-worker/pipeline/photos.py keeps a mixed-orientation set only when the odd
    // frames are the majority size transposed; anything else is skipped with a warning. Rounding
    // that broke this symmetry would silently drop every portrait shot in a landscape set.
    const landscape = targetSize(4032, 3024, MAX_LONG_EDGE)
    const portrait = targetSize(3024, 4032, MAX_LONG_EDGE)

    expect(portrait).toEqual({ width: landscape.height, height: landscape.width })
  })

  it("never upscales a frame that is already under the cap", () => {
    expect(targetSize(1600, 1200, 3000)).toEqual({ width: 1600, height: 1200 })
  })

  it("handles a square frame", () => {
    expect(targetSize(4000, 4000, 3000)).toEqual({ width: 3000, height: 3000 })
  })
})

describe("jpegName", () => {
  it("renames a HEIC original so the API's extension check accepts it", () => {
    expect(jpegName("IMG_0001.HEIC")).toBe("IMG_0001.jpg")
  })

  it("leaves an already-correct name alone", () => {
    expect(jpegName("IMG_0001.jpg")).toBe("IMG_0001.jpg")
  })

  it("normalises .jpeg to .jpg", () => {
    expect(jpegName("photo.jpeg")).toBe("photo.jpg")
  })

  it("replaces only the final extension", () => {
    expect(jpegName("my.photo.heic")).toBe("my.photo.jpg")
  })

  it("appends an extension when the original has none", () => {
    expect(jpegName("image")).toBe("image.jpg")
  })
})

const LANDSCAPE = { width: 4032, height: 3024 }
const PORTRAIT = { width: 3024, height: 4032 }

describe("rotationFor", () => {
  it("applies nothing when the decoder already rotated the pixels", () => {
    // Safari's createImageBitmap may or may not honour EXIF depending on version, so the
    // decoded dimensions are the only trustworthy signal. Transposed means it rotated.
    expect(rotationFor(6, LANDSCAPE, PORTRAIT)).toBe(0)
  })

  it("rotates a quarter turn when the decoder left a sideways frame sideways", () => {
    expect(rotationFor(6, LANDSCAPE, LANDSCAPE)).toBe(90)
  })

  it("rotates the other way for orientation 8", () => {
    expect(rotationFor(8, LANDSCAPE, LANDSCAPE)).toBe(270)
  })

  it("applies nothing to a frame already marked upright", () => {
    expect(rotationFor(1, LANDSCAPE, LANDSCAPE)).toBe(0)
  })

  it("turns a 180-flagged frame over", () => {
    expect(rotationFor(3, LANDSCAPE, LANDSCAPE)).toBe(180)
  })

  it("applies nothing when the stored size is unknown", () => {
    // A HEIC that reached us unconverted has no JPEG frame header to read.
    expect(rotationFor(6, null, PORTRAIT)).toBe(0)
  })

  it("applies nothing to a square frame, where transposition is undetectable and harmless", () => {
    // Only a quarter turn changes the dimensions COLMAP's single-camera model insists on
    // matching, and a square frame cannot mismatch. Leftover roll is per-image pose, which
    // structure-from-motion solves anyway (photos.py says as much).
    const square = { width: 3000, height: 3000 }
    expect(rotationFor(6, square, square)).toBe(0)
  })
})

describe("drawPlan", () => {
  /** Where a point in the source rect lands on the canvas, per the plan's transform. */
  function project(plan: ReturnType<typeof drawPlan>, x: number, y: number) {
    const cos = Math.cos(plan.radians)
    const sin = Math.sin(plan.radians)
    return {
      x: Math.round(plan.translateX + x * cos - y * sin),
      y: Math.round(plan.translateY + x * sin + y * cos),
    }
  }

  const key = (p: { x: number; y: number }) => `${p.x},${p.y}`

  it.each<Rotation>([0, 90, 180, 270])(
    "maps the drawn rect exactly onto the canvas at %i degrees",
    (rotation) => {
      // The real defect this guards is a sign error in the rotation: the image silently ends
      // up off-canvas, cropped, or mirrored. Checking that the source corners land on the
      // canvas corners pins the transform without restating its constants.
      const target = { width: 3000, height: 2250 }
      const plan = drawPlan(rotation, target)

      const corners = [
        project(plan, 0, 0),
        project(plan, plan.drawWidth, 0),
        project(plan, plan.drawWidth, plan.drawHeight),
        project(plan, 0, plan.drawHeight),
      ]

      expect(new Set(corners.map(key))).toEqual(
        new Set([
          key({ x: 0, y: 0 }),
          key({ x: target.width, y: 0 }),
          key({ x: target.width, y: target.height }),
          key({ x: 0, y: target.height }),
        ]),
      )
    },
  )

  it("keeps corner order unmirrored, so the image is not flipped", () => {
    // A mirrored set reconstructs as a mirrored model. Walking the source rect clockwise must
    // still walk the canvas clockwise, which a reflection would reverse.
    const target = { width: 3000, height: 2250 }
    for (const rotation of [0, 90, 180, 270] as Rotation[]) {
      const plan = drawPlan(rotation, target)
      const a = project(plan, 0, 0)
      const b = project(plan, plan.drawWidth, 0)
      const c = project(plan, plan.drawWidth, plan.drawHeight)
      // Cross product of AB × BC stays positive for a clockwise walk in screen coordinates.
      const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x)
      expect(cross, `rotation ${rotation}`).toBeGreaterThan(0)
    }
  })

  it("swaps the drawn extent for quarter turns", () => {
    const target = { width: 3000, height: 2250 }

    expect(drawPlan(0, target)).toMatchObject({ drawWidth: 3000, drawHeight: 2250 })
    expect(drawPlan(90, target)).toMatchObject({ drawWidth: 2250, drawHeight: 3000 })
  })
})

describe("what an iPhone actually hands over", () => {
  // Measured on an iPhone XS/11 picking from the photo library in mobile Safari; see
  // docs/superpowers/probes/2026-09-12-ios-photo-picker.md. These pass by construction —
  // they exist to pin observed device behaviour, so a later refactor of the rotation
  // detection cannot quietly break the one configuration this feature was built for.
  const SENSOR = { width: 4032, height: 3024 }

  it("leaves a landscape frame alone", () => {
    expect(rotationFor(1, SENSOR, { width: 4032, height: 3024 })).toBe(0)
  })

  it("adds no rotation to a portrait frame Safari already turned upright", () => {
    // Stored stays sensor-native landscape while the decoder returns upright pixels, and the
    // Orientation tag is left at 6. Trusting that tag would rotate the image a second time.
    expect(rotationFor(6, SENSOR, { width: 3024, height: 4032 })).toBe(0)
  })

  it("produces transposed sizes for a mixed set, which the worker can reconcile", () => {
    const landscape = targetSize(4032, 3024, MAX_LONG_EDGE)
    const portrait = targetSize(3024, 4032, MAX_LONG_EDGE)

    expect(landscape).toEqual({ width: 2400, height: 1800 })
    expect(portrait).toEqual({ width: 1800, height: 2400 })
  })
})
