import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/lib/photogrammetryApi", () => ({
  fetchJobPhotos: vi.fn(),
  fetchSamplePhotos: vi.fn(),
  deleteJob: vi.fn().mockResolvedValue(undefined),
  listJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  confirmJob: vi.fn(),
  createSampleJob: vi.fn(),
  getMeshUrl: vi.fn(),
  uploadToS3: vi.fn(),
}))

vi.mock("@/lib/prepareImage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/prepareImage")>()),
  prepareImage: vi.fn(),
}))

vi.mock("@/lib/wakeLock", () => ({ holdScreenAwake: vi.fn() }))

import * as api from "@/lib/photogrammetryApi"
import * as wake from "@/lib/wakeLock"
import * as prepare from "@/lib/prepareImage"
import { jpegName } from "@/lib/prepareImage"
import { usePhotogrammetryStore } from "@/stores/photogrammetry"

const photo = { filename: "0001.jpg", url: "u", thumb_url: "t", status: null }
const body = { photos: [photo], matched: null, total: 1 }

describe("photogrammetry store — photos", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.fetchJobPhotos).mockReset().mockResolvedValue(body)
    vi.mocked(api.fetchSamplePhotos).mockReset().mockResolvedValue({ name: "Sample scan", image_count: 1, photos: [photo] })
  })

  it("fetchJobPhotos caches the full response per job id", async () => {
    const store = usePhotogrammetryStore()
    expect(await store.fetchJobPhotos("j1")).toEqual(body)
    expect(await store.fetchJobPhotos("j1")).toEqual(body)
    await store.fetchJobPhotos("j2")
    expect(api.fetchJobPhotos).toHaveBeenCalledTimes(2)
  })

  it("does not cache a photo list smaller than the job's image count", async () => {
    // The Photos pane can ask half a second after job creation, before the presigned PUTs have
    // landed — caching that near-empty answer froze the pane for the session (2026-08-31).
    const store = usePhotogrammetryStore()
    store.jobs.push({ job_id: "j1", image_count: 2 } as never)
    vi.mocked(api.fetchJobPhotos).mockResolvedValueOnce({ photos: [], matched: null, total: 0 })
    expect((await store.fetchJobPhotos("j1")).photos).toEqual([])
    expect((await store.fetchJobPhotos("j1")).photos).toEqual([photo])
    expect(api.fetchJobPhotos).toHaveBeenCalledTimes(2)
  })

  it("fetchJobPhotos with force refetches and replaces the cache", async () => {
    const store = usePhotogrammetryStore()
    await store.fetchJobPhotos("j1")
    const after = { photos: [{ ...photo, status: "unregistered" }], matched: 0, total: 1 }
    vi.mocked(api.fetchJobPhotos).mockResolvedValueOnce(after)
    expect(await store.fetchJobPhotos("j1", { force: true })).toEqual(after)
    expect(await store.fetchJobPhotos("j1")).toEqual(after)
    expect(api.fetchJobPhotos).toHaveBeenCalledTimes(2)
  })

  it("clearSelection drops the active job", () => {
    const store = usePhotogrammetryStore()
    store.selectJob("j1")
    expect(store.activeJobId).toBe("j1")
    store.clearSelection()
    expect(store.activeJobId).toBeNull()
  })

  it("deleting a job drops its cached photos", async () => {
    const store = usePhotogrammetryStore()
    await store.fetchJobPhotos("j1")
    await store.deleteJob("j1")
    await store.fetchJobPhotos("j1")
    expect(api.fetchJobPhotos).toHaveBeenCalledTimes(2)
  })

  it("fetchSamplePhotos passes the sample set through", async () => {
    const store = usePhotogrammetryStore()
    expect(await store.fetchSamplePhotos()).toEqual({ name: "Sample scan", image_count: 1, photos: [photo] })
  })
})

describe("photogrammetry store — submitScan", () => {
  const uploadsFor = (names: string[]) =>
    names.map((filename, i) => ({ filename, key: `k${i}`, url: `https://s3/${i}` }))

  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.createJob).mockReset()
    vi.mocked(api.confirmJob).mockReset().mockResolvedValue(undefined)
    vi.mocked(api.uploadToS3).mockReset().mockResolvedValue(undefined)
    vi.mocked(prepare.prepareImage).mockReset().mockImplementation(
      async (file: File) => new File(["prepared:" + file.name], jpegName(file.name)),
    )
    vi.mocked(wake.holdScreenAwake).mockReset().mockResolvedValue({
      held: true,
      release: vi.fn().mockResolvedValue(undefined),
    })
  })

  function heicFiles(count: number): File[] {
    return Array.from({ length: count }, (_, i) =>
      new File(["raw"], `IMG_000${i + 1}.HEIC`, { type: "image/heic" }),
    )
  }

  it("registers the job under the names the re-encode will actually produce", async () => {
    // The API validates on extension (chat-api/app/schemas/photogrammetry.py:29), so sending
    // the phone's .HEIC names would be rejected outright.
    vi.mocked(api.createJob).mockResolvedValue({
      job_id: "j1",
      uploads: uploadsFor(["IMG_0001.jpg", "IMG_0002.jpg"]),
    } as never)
    const store = usePhotogrammetryStore()

    await store.submitScan("Scan", heicFiles(2))

    expect(api.createJob).toHaveBeenCalledWith("Scan", ["IMG_0001.jpg", "IMG_0002.jpg"], false)
  })

  it("uploads the prepared photo rather than the phone original", async () => {
    const produced: File[] = []
    vi.mocked(prepare.prepareImage).mockImplementation(async (file: File) => {
      const out = new File(["prepared"], jpegName(file.name))
      produced.push(out)
      return out
    })
    vi.mocked(api.createJob).mockResolvedValue({
      job_id: "j1",
      uploads: uploadsFor(["IMG_0001.jpg"]),
    } as never)
    const store = usePhotogrammetryStore()

    await store.submitScan("Scan", heicFiles(1))

    const [, uploaded] = vi.mocked(api.uploadToS3).mock.calls[0]
    expect(uploaded).toBe(produced[0])
    expect((uploaded as File).name).toBe("IMG_0001.jpg")
  })

  it("decodes one photo at a time, however many uploads are in flight", async () => {
    // A 12MP frame is ~48 MB of RGBA once decoded. Four at once is enough to have an iPhone XS
    // discard the tab mid-scan, and parallel decoding buys nothing on a CPU-bound step.
    let inFlight = 0
    let peak = 0
    vi.mocked(prepare.prepareImage).mockImplementation(async (file: File) => {
      inFlight++
      peak = Math.max(peak, inFlight)
      await new Promise((resolve) => setTimeout(resolve, 0))
      inFlight--
      return new File(["prepared"], jpegName(file.name))
    })
    vi.mocked(api.createJob).mockResolvedValue({
      job_id: "j1",
      uploads: uploadsFor(["a.jpg", "b.jpg", "c.jpg", "d.jpg", "e.jpg", "f.jpg"]),
    } as never)
    const store = usePhotogrammetryStore()

    await store.submitScan("Scan", heicFiles(6))

    expect(peak).toBe(1)
    expect(prepare.prepareImage).toHaveBeenCalledTimes(6)
  })
})

describe("photogrammetry store — keeping the screen awake", () => {
  const uploadsFor = (names: string[]) =>
    names.map((filename, i) => ({ filename, key: `k${i}`, url: `https://s3/${i}` }))
  const file = () => [new File(["raw"], "IMG_0001.jpg", { type: "image/jpeg" })]

  let release: ReturnType<typeof vi.fn>

  beforeEach(() => {
    setActivePinia(createPinia())
    release = vi.fn().mockResolvedValue(undefined)
    vi.mocked(wake.holdScreenAwake).mockReset().mockResolvedValue({ held: true, release })
    vi.mocked(prepare.prepareImage).mockReset().mockImplementation(
      async (f: File) => new File(["prepared"], jpegName(f.name)),
    )
    vi.mocked(api.confirmJob).mockReset().mockResolvedValue(undefined)
    vi.mocked(api.uploadToS3).mockReset().mockResolvedValue(undefined)
    vi.mocked(api.createJob).mockReset().mockResolvedValue({
      job_id: "j1",
      uploads: uploadsFor(["IMG_0001.jpg"]),
    } as never)
  })

  it("holds the screen awake for the upload and lets go afterwards", async () => {
    // The phone locking mid-scan suspends the tab, losing the File handles and stranding the
    // job in `pending` — there is nothing to resume from.
    const store = usePhotogrammetryStore()

    await store.submitScan("Scan", file())

    expect(wake.holdScreenAwake).toHaveBeenCalled()
    expect(release).toHaveBeenCalled()
  })

  it("lets go of the lock even when the scan fails", async () => {
    vi.mocked(api.uploadToS3).mockRejectedValue(new Error("network gone"))
    const store = usePhotogrammetryStore()

    await expect(store.submitScan("Scan", file())).rejects.toThrow()

    expect(release).toHaveBeenCalled()
  })
})
