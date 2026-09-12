import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/lib/axios", () => ({
  apiClient: { get: vi.fn(), patch: vi.fn() },
}))

import { apiClient } from "@/lib/axios"
import { getSamples, setJobVisibility, getJobAudioUrl, getSampleAudioUrl, uploadToS3 } from "@/lib/transcribeApi"

const get = vi.mocked(apiClient.get)
const patch = vi.mocked(apiClient.patch)

describe("transcribe api client — jobs", () => {
  beforeEach(() => patch.mockReset())

  it("setJobVisibility PATCHes /jobs/{id} with is_public and returns TranscriptionJob", async () => {
    patch.mockResolvedValueOnce({ data: { job_id: "t1", status: "complete", is_public: true } })
    const res = await setJobVisibility("t1", true)
    expect(patch).toHaveBeenCalledWith("/api/v1/transcribe/jobs/t1", { is_public: true })
    expect(res.is_public).toBe(true)
  })
})

describe("transcribe api client — samples", () => {
  beforeEach(() => get.mockReset())

  it("getSamples GETs /samples and returns the bundle", async () => {
    const body = {
      name: "Sample conversation",
      audio: { filename: "conversation", url: "https://dl/samples/conversation.wav" },
      speakers: [
        { speaker_name: "Barry", url: "https://dl/samples/speakers/barry.wav" },
        { speaker_name: "Jane", url: "https://dl/samples/speakers/jane.wav" },
      ],
    }
    get.mockResolvedValueOnce({ data: body })
    const res = await getSamples()
    expect(get).toHaveBeenCalledWith("/api/v1/transcribe/samples")
    expect(res).toEqual(body)
  })
})

describe("transcribe api client — audio", () => {
  beforeEach(() => get.mockReset())

  it("getJobAudioUrl GETs /jobs/{id}/audio and returns the presigned bundle", async () => {
    const body = {
      url: "https://dl/audio/u/j/source",
      download_url: "https://dl/audio/u/j/source?dl=job-audio",
      filename: "job-audio",
      expires_at: "2026-09-02T10:15:00Z",
    }
    get.mockResolvedValueOnce({ data: body })
    const res = await getJobAudioUrl("j1")
    expect(get).toHaveBeenCalledWith("/api/v1/transcribe/jobs/j1/audio")
    expect(res).toEqual(body)
  })

  it("getSampleAudioUrl GETs /speakers/{id}/samples/{id}/audio and returns the presigned bundle", async () => {
    const body = {
      url: "https://dl/audio/u/speakers/s/samples/sm",
      download_url: "https://dl/audio/u/speakers/s/samples/sm?dl=speaker-sample",
      filename: "speaker-sample",
      expires_at: "2026-09-02T10:15:00Z",
    }
    get.mockResolvedValueOnce({ data: body })
    const res = await getSampleAudioUrl("s1", "sm1")
    expect(get).toHaveBeenCalledWith("/api/v1/transcribe/speakers/s1/samples/sm1/audio")
    expect(res).toEqual(body)
  })
})

describe("uploadToS3", () => {
  const file = () => new File(["photo"], "0001.jpg", { type: "image/jpeg" })
  const ok = { ok: true, status: 200 } as Response
  const fail = (status: number) => ({ ok: false, status }) as Response

  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    vi.useFakeTimers()
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  /** Drives a pending upload past its backoff timers. */
  async function settle<T>(pending: Promise<T>): Promise<T> {
    await vi.runAllTimersAsync()
    return pending
  }

  it("retries after a dropped connection", async () => {
    // A 150-photo scan over cellular drops requests. Before retrying, one blip failed the
    // whole scan and left the job stuck pending.
    fetchMock.mockRejectedValueOnce(new TypeError("Load failed")).mockResolvedValueOnce(ok)

    await expect(settle(uploadToS3("https://s3/0001", file()))).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("retries when S3 asks us to slow down", async () => {
    fetchMock.mockResolvedValueOnce(fail(503)).mockResolvedValueOnce(ok)

    await expect(settle(uploadToS3("https://s3/0001", file()))).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("does not retry an expired presigned URL", async () => {
    // S3 answers 403 once the signature's window has passed; it will never start working,
    // so retrying just delays the failure the user needs to see.
    fetchMock.mockResolvedValue(fail(403))

    await expect(settle(uploadToS3("https://s3/0001", file()))).rejects.toThrow(/403/)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("gives up rather than retrying forever", async () => {
    fetchMock.mockRejectedValue(new TypeError("Load failed"))

    await expect(settle(uploadToS3("https://s3/0001", file()))).rejects.toThrow()
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })
})
