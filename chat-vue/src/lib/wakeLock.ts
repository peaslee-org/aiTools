/**
 * Keeping the screen awake while a long upload runs.
 *
 * A 150-photo scan takes minutes, and the phone locking mid-way suspends the tab — which loses
 * the in-memory File handles and strands the job in `pending` with nothing to resume from. A
 * screen wake lock removes that failure at its cause, which is considerably cheaper than
 * building resumable uploads.
 *
 * Needs a secure context and Safari 16.4+ (Chrome 84+). Where it is unavailable or refused this
 * degrades to a no-op: an upload must never fail because the screen could have slept.
 */

interface WakeLockSentinel {
  release(): Promise<void>
}

interface WakeLockCapableNavigator {
  wakeLock?: { request(type: "screen"): Promise<WakeLockSentinel> }
}

export interface HeldWakeLock {
  /** False when the platform has no wake lock, or refused one. */
  held: boolean
  release(): Promise<void>
}

const NOT_HELD: HeldWakeLock = { held: false, release: async () => undefined }

/**
 * Holds a screen wake lock until the returned handle is released.
 *
 * The platform drops the lock every time the page is hidden, so this re-acquires it when the
 * page comes back into view — without that, switching away once leaves the rest of a long
 * upload unprotected.
 */
export async function holdScreenAwake(): Promise<HeldWakeLock> {
  const api = (navigator as WakeLockCapableNavigator).wakeLock
  if (!api) return NOT_HELD

  const acquire = async (): Promise<WakeLockSentinel | null> => {
    try {
      return await api.request("screen")
    } catch {
      return null // refused: low battery, a user setting, a background tab
    }
  }

  const first = await acquire()
  if (!first) return NOT_HELD

  let sentinel: WakeLockSentinel | null = first
  let released = false

  const reacquire = (): void => {
    if (released || document.visibilityState !== "visible") return
    void acquire().then((next) => {
      if (released) void next?.release().catch(() => undefined)
      else if (next) sentinel = next
    })
  }
  document.addEventListener("visibilitychange", reacquire)

  return {
    held: true,
    async release() {
      released = true
      document.removeEventListener("visibilitychange", reacquire)
      try {
        await sentinel?.release()
      } catch {
        // Already gone — the page was hidden, or the lock lapsed on its own.
      }
      sentinel = null
    },
  }
}
