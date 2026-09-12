import { apiClient } from "@/lib/axios"
import type {
  SpeakerProfile, SpeakerListResponse, SampleUploadInitResponse, SpeakerSample,
  TranscriptionJob, JobListResponse, JobCreateRequest, JobCreateResponse,
  TranscriptResponse, TurnDistancesResponse, SamplePreview, AudioUrlResponse,
  CompileSettings,
} from "@/types"

// ── Speakers ──────────────────────────────────────────────────────────────

export async function createSpeaker(name?: string): Promise<SpeakerProfile> {
  const res = await apiClient.post("/api/v1/transcribe/speakers", { speaker_name: name || null })
  return res.data
}

export async function listSpeakers(cursor?: string): Promise<SpeakerListResponse> {
  const res = await apiClient.get("/api/v1/transcribe/speakers", {
    params: { cursor, limit: 50 },
  })
  return res.data
}

export async function getSpeaker(speakerId: string): Promise<SpeakerProfile> {
  const res = await apiClient.get(`/api/v1/transcribe/speakers/${speakerId}`)
  return res.data
}

export async function renameSpeaker(speakerId: string, name: string): Promise<SpeakerProfile> {
  const res = await apiClient.patch(`/api/v1/transcribe/speakers/${speakerId}`, { speaker_name: name })
  return res.data
}

export async function deleteSpeaker(speakerId: string): Promise<void> {
  await apiClient.delete(`/api/v1/transcribe/speakers/${speakerId}`)
}

export async function initSampleUpload(speakerId: string): Promise<SampleUploadInitResponse> {
  const res = await apiClient.post(
    `/api/v1/transcribe/speakers/${speakerId}/samples`
  )
  return res.data
}

export async function confirmSampleUpload(
  speakerId: string,
  sampleId: string,
): Promise<SpeakerSample> {
  const res = await apiClient.post(
    `/api/v1/transcribe/speakers/${speakerId}/samples/${sampleId}/confirm`
  )
  return res.data
}

export async function deleteSample(speakerId: string, sampleId: string): Promise<void> {
  await apiClient.delete(
    `/api/v1/transcribe/speakers/${speakerId}/samples/${sampleId}`
  )
}

export async function getSampleAudioUrl(speakerId: string, sampleId: string): Promise<AudioUrlResponse> {
  const res = await apiClient.get(
    `/api/v1/transcribe/speakers/${speakerId}/samples/${sampleId}/audio`
  )
  return res.data
}

// ── Jobs ──────────────────────────────────────────────────────────────────

export interface SampleJobResponse {
  job_id: string
  speaker_ids: string[]
}

export async function createSampleJob(): Promise<SampleJobResponse> {
  const res = await apiClient.post("/api/v1/transcribe/jobs/sample")
  return res.data
}

export async function getSamples(): Promise<SamplePreview> {
  const res = await apiClient.get("/api/v1/transcribe/samples")
  return res.data
}

export async function createJob(params: JobCreateRequest): Promise<JobCreateResponse> {
  const res = await apiClient.post("/api/v1/transcribe/jobs", params)
  return res.data
}

export async function confirmJobUpload(jobId: string): Promise<void> {
  await apiClient.post(`/api/v1/transcribe/jobs/${jobId}/confirm`)
}

export async function rerunJob(jobId: string): Promise<TranscriptionJob> {
  const res = await apiClient.post(`/api/v1/transcribe/jobs/${jobId}/rerun`)
  return res.data
}

export async function listJobs(cursor?: string): Promise<JobListResponse> {
  const res = await apiClient.get("/api/v1/transcribe/jobs", {
    params: { cursor, limit: 20 },
  })
  return res.data
}

export async function getJobStatus(jobId: string): Promise<TranscriptionJob> {
  const res = await apiClient.get(`/api/v1/transcribe/jobs/${jobId}`)
  return res.data
}

export async function getTranscript(jobId: string): Promise<TranscriptResponse> {
  const res = await apiClient.get(`/api/v1/transcribe/jobs/${jobId}/transcript`)
  return res.data
}

export async function compileTranscript(jobId: string, settings: CompileSettings): Promise<TranscriptResponse> {
  const res = await apiClient.post(`/api/v1/transcribe/jobs/${jobId}/compile`, settings)
  return res.data
}

export async function deleteJob(jobId: string): Promise<void> {
  await apiClient.delete(`/api/v1/transcribe/jobs/${jobId}`)
}

export async function fetchTurnDistances(jobId: string): Promise<TurnDistancesResponse> {
  const res = await apiClient.get(`/api/v1/transcribe/jobs/${jobId}/turn-distances`)
  return res.data
}

export async function setJobVisibility(jobId: string, isPublic: boolean): Promise<TranscriptionJob> {
  return (await apiClient.patch(`/api/v1/transcribe/jobs/${jobId}`, { is_public: isPublic })).data
}

export async function getJobAudioUrl(jobId: string): Promise<AudioUrlResponse> {
  const res = await apiClient.get(`/api/v1/transcribe/jobs/${jobId}/audio`)
  return res.data
}

// ── Helpers ───────────────────────────────────────────────────────────────

/**
 * Upload a file directly to S3 using a pre-signed PUT URL.
 * Must NOT use apiClient — no Authorization header should be sent to S3.
 */
/** Backoff before each retry; its length sets how many extra attempts an upload gets. */
const UPLOAD_RETRY_DELAYS_MS = [400, 1200]

/** Throttling and S3's own transient failures. A 403 is an expired signature — permanent. */
const RETRYABLE_STATUSES = new Set([429, 500, 502, 503, 504])

interface UploadFailure {
  error: Error
  retryable: boolean
}

async function attemptUpload(uploadUrl: string, file: File): Promise<UploadFailure | null> {
  try {
    const res = await fetch(uploadUrl, { method: "PUT", body: file })
    if (res.ok) return null
    return {
      error: new Error(`S3 upload failed: ${res.status}`),
      retryable: RETRYABLE_STATUSES.has(res.status),
    }
  } catch (err) {
    // fetch only rejects on network-level failure, which is exactly what a phone on cellular
    // does mid-scan. Always worth another go.
    return { error: err instanceof Error ? err : new Error(String(err)), retryable: true }
  }
}

/**
 * PUTs a file to a presigned URL, retrying transient failures.
 *
 * Presigned PUTs carry no auth header, so this deliberately uses plain `fetch` rather than
 * `apiClient`. A single dropped request used to fail an entire scan and leave the job stuck
 * in `pending` with no way to resume, which is untenable over a mobile connection.
 */
export async function uploadToS3(uploadUrl: string, file: File): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    const failure = await attemptUpload(uploadUrl, file)
    if (!failure) return
    if (!failure.retryable || attempt >= UPLOAD_RETRY_DELAYS_MS.length) throw failure.error
    await new Promise((resolve) => setTimeout(resolve, UPLOAD_RETRY_DELAYS_MS[attempt]))
  }
}
