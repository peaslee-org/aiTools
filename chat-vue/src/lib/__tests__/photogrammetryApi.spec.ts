import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/lib/axios", () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
}))
vi.mock("@/lib/transcribeApi", () => ({ uploadToS3: vi.fn() }))

import { apiClient } from "@/lib/axios"
import { createJob, fetchJobPhotos, fetchSamplePhotos, setJobVisibility } from "@/lib/photogrammetryApi"

const get = vi.mocked(apiClient.get)
const post = vi.mocked(apiClient.post)
const patch = vi.mocked(apiClient.patch)

const photo = { filename: "0001.jpg", url: "https://s3/full/0001.jpg", thumb_url: "https://s3/thumbs/0001.jpg" }

describe("photogrammetry api client — photos", () => {
  beforeEach(() => {
    get.mockReset()
    patch.mockReset()
  })

  it("fetchSamplePhotos GETs /samples and returns the body", async () => {
    get.mockResolvedValueOnce({ data: { name: "Sample scan", image_count: 1, photos: [photo] } })
    const res = await fetchSamplePhotos()
    expect(get).toHaveBeenCalledWith("/api/v1/photogrammetry/samples")
    expect(res).toEqual({ name: "Sample scan", image_count: 1, photos: [photo] })
  })

  it("fetchJobPhotos GETs /jobs/{id}/photos and returns photos with matched/total", async () => {
    get.mockResolvedValueOnce({ data: { photos: [{ ...photo, status: "registered" }], matched: 1, total: 1 } })
    const res = await fetchJobPhotos("job-1")
    expect(get).toHaveBeenCalledWith("/api/v1/photogrammetry/jobs/job-1/photos")
    expect(res).toEqual({ photos: [{ ...photo, status: "registered" }], matched: 1, total: 1 })
  })

  it("setJobVisibility PATCHes /jobs/{id} with is_public and returns PhotogrammetryJob", async () => {
    patch.mockResolvedValueOnce({ data: { job_id: "j1", name: "Scan", status: "complete", is_public: true } })
    const res = await setJobVisibility("j1", true)
    expect(patch).toHaveBeenCalledWith("/api/v1/photogrammetry/jobs/j1", { is_public: true })
    expect(res.is_public).toBe(true)
  })
})

describe("photogrammetry api client — createJob", () => {
  beforeEach(() => post.mockReset())

  it("sends remove_background, false by default", async () => {
    post.mockResolvedValueOnce({ data: { job_id: "j", uploads: [] } })
    await createJob("n", ["a.jpg"])
    expect(post).toHaveBeenLastCalledWith("/api/v1/photogrammetry/jobs", { name: "n", filenames: ["a.jpg"], remove_background: false })
    post.mockResolvedValueOnce({ data: { job_id: "j", uploads: [] } })
    await createJob("n", ["a.jpg"], true)
    expect(post).toHaveBeenLastCalledWith("/api/v1/photogrammetry/jobs", { name: "n", filenames: ["a.jpg"], remove_background: true })
  })
})
