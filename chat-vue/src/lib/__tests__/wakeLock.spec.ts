import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { holdScreenAwake } from "@/lib/wakeLock"

function fakeSentinel() {
  return { release: vi.fn().mockResolvedValue(undefined) }
}

function withWakeLock(request: unknown) {
  Object.defineProperty(navigator, "wakeLock", {
    value: request === undefined ? undefined : { request },
    configurable: true,
    writable: true,
  })
}

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true })
  document.dispatchEvent(new Event("visibilitychange"))
}

describe("holdScreenAwake", () => {
  beforeEach(() => setVisibility("visible"))
  afterEach(() => withWakeLock(undefined))

  it("takes a screen lock so the phone does not sleep mid-upload", async () => {
    const request = vi.fn().mockResolvedValue(fakeSentinel())
    withWakeLock(request)

    const held = await holdScreenAwake()

    expect(request).toHaveBeenCalledWith("screen")
    await held.release()
  })

  it("releases the lock when asked", async () => {
    const sentinel = fakeSentinel()
    withWakeLock(vi.fn().mockResolvedValue(sentinel))

    const held = await holdScreenAwake()
    await held.release()

    expect(sentinel.release).toHaveBeenCalled()
  })

  it("takes the lock again when the page comes back into view", async () => {
    // The platform drops a wake lock whenever the page is hidden. Without re-acquiring, one
    // glance at another app leaves the rest of a long upload unprotected.
    const request = vi.fn().mockResolvedValue(fakeSentinel())
    withWakeLock(request)
    const held = await holdScreenAwake()

    setVisibility("hidden")
    setVisibility("visible")
    await Promise.resolve()

    expect(request).toHaveBeenCalledTimes(2)
    await held.release()
  })

  it("stops re-acquiring once released", async () => {
    const request = vi.fn().mockResolvedValue(fakeSentinel())
    withWakeLock(request)
    const held = await holdScreenAwake()

    await held.release()
    setVisibility("visible")
    await Promise.resolve()

    expect(request).toHaveBeenCalledTimes(1)
  })

  it("is a no-op where the API does not exist", async () => {
    // Anything before Safari 16.4, and any insecure context. Must never fail an upload.
    withWakeLock(undefined)

    const held = await holdScreenAwake()

    expect(held.held).toBe(false)
    await expect(held.release()).resolves.toBeUndefined()
  })

  it("is a no-op when the request is refused", async () => {
    // Low battery, or a user setting. Not a reason to abandon a scan.
    withWakeLock(vi.fn().mockRejectedValue(new Error("NotAllowedError")))

    const held = await holdScreenAwake()

    expect(held.held).toBe(false)
    await expect(held.release()).resolves.toBeUndefined()
  })
})
