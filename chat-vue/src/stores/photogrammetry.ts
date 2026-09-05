import { defineStore } from "pinia"
import { computed, reactive, ref } from "vue"
import axios from "axios"
import * as api from "@/lib/photogrammetryApi"
import type { JobPhotosResponse, MeshUrls, PhotogrammetryJob, SamplePhotos } from "@/types"

export interface Toast {
  id: number
  message: string
}

export interface UploadProgress {
  done: number
  total: number
}

const ACTIVE = new Set(["pending", "queued", "processing"])
const POLL_INTERVAL_MS = Number(import.meta.env.VITE_PHOTOGRAMMETRY_POLL_INTERVAL_MS) || 3_000
const POLL_INTERVAL_PAUSED_MS = 60_000
const UPLOAD_CONCURRENCY = 4
let nextToastId = 0

export const usePhotogrammetryStore = defineStore("photogrammetry", () => {
  // ── State ─────────────────────────────────────────────────────────────
  const jobs = ref<PhotogrammetryJob[]>([])
  const nextCursor = ref<string | null>(null)
  const activeJobId = ref<string | null>(null)
  const uploadProgress = ref<UploadProgress | null>(null)
  const meshUrls = ref<Record<string, MeshUrls>>({})
  const photosByJob = new Map<string, JobPhotosResponse>()
  const toasts = ref<Toast[]>([])
  const pollingActive = reactive(new Set<string>())
  const pollTimers = new Map<string, ReturnType<typeof setTimeout>>()

  const activeJob = computed(() => jobs.value.find(j => j.job_id === activeJobId.value) ?? null)

  // ── Toasts ────────────────────────────────────────────────────────────
  function dismissToast(id: number): void {
    toasts.value = toasts.value.filter(t => t.id !== id)
  }

  function pushToast(message: string): void {
    const id = nextToastId++
    toasts.value.push({ id, message })
    setTimeout(() => dismissToast(id), 8000)
  }

  // ── Jobs ──────────────────────────────────────────────────────────────
  async function loadJobs(reset = false): Promise<void> {
    if (reset) { jobs.value = []; nextCursor.value = null }
    const res = await api.listJobs(nextCursor.value ?? undefined)
    jobs.value.push(...res.items)
    nextCursor.value = res.next_cursor
  }

  function upsert(job: PhotogrammetryJob): void {
    const idx = jobs.value.findIndex(j => j.job_id === job.job_id)
    if (idx === -1) jobs.value.unshift(job)
    else jobs.value[idx] = job
  }

  function placeholder(job_id: string, name: string, image_count: number, status: PhotogrammetryJob["status"]): PhotogrammetryJob {
    const now = new Date().toISOString()
    return {
      job_id, name, status, stage: null, image_count, preview_url: null, error_message: null,
      warnings: [],
      mock: false, created_at: now, updated_at: now, completed_at: null,
    }
  }

  /** Create → upload every file (4 at a time) → confirm → poll. Returns the job id. */
  async function submitScan(name: string, files: File[], removeBackground = false): Promise<string> {
    let job_id: string
    try {
      const created = await api.createJob(name || null, files.map(f => f.name), removeBackground)
      job_id = created.job_id
      const { uploads } = created
      upsert(placeholder(job_id, name, files.length, "pending"))
      activeJobId.value = job_id
      uploadProgress.value = { done: 0, total: uploads.length }
      let next = 0
      async function worker(): Promise<void> {
        while (next < uploads.length) {
          const i = next++
          await api.uploadToS3(uploads[i].url, files[i])
          if (uploadProgress.value) uploadProgress.value.done++
        }
      }
      await Promise.all(Array.from({ length: Math.min(UPLOAD_CONCURRENCY, uploads.length) }, worker))
      await api.confirmJob(job_id)
      const idx = jobs.value.findIndex(j => j.job_id === job_id)
      if (idx !== -1) jobs.value[idx] = { ...jobs.value[idx], status: "queued" }
      startPolling(job_id)
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined
      pushToast(detail ? `Scan failed: ${detail}` : "Scan failed — could not create or upload the scan")
      throw err
    } finally {
      uploadProgress.value = null
    }
    return job_id
  }

  async function submitSampleJob(): Promise<string> {
    try {
      const { job_id } = await api.createSampleJob()
      upsert(placeholder(job_id, "Sample scan", 0, "queued"))
      activeJobId.value = job_id
      startPolling(job_id)
      return job_id
    } catch (err) {
      const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined
      pushToast(detail ? `Sample scan failed: ${detail}` : "Sample scan failed")
      throw err
    }
  }

  function selectJob(jobId: string): void {
    activeJobId.value = jobId
  }

  /** Close the open scan — back to "select a scan"; polling and caches are untouched. */
  function clearSelection(): void {
    activeJobId.value = null
  }

  async function deleteJob(jobId: string): Promise<void> {
    stopPolling(jobId)
    await api.deleteJob(jobId)
    jobs.value = jobs.value.filter(j => j.job_id !== jobId)
    delete meshUrls.value[jobId]
    photosByJob.delete(jobId)
    if (activeJobId.value === jobId) activeJobId.value = null
  }

  /**
   * A job's input photos (thumb + full-size presigned URLs, per-photo SfM status, matched count),
   * cached per job for the session. `force` refetches — the status is written when SfM ends.
   */
  async function fetchJobPhotos(jobId: string, opts: { force?: boolean } = {}): Promise<JobPhotosResponse> {
    const cached = photosByJob.get(jobId)
    if (cached && !opts.force) return cached
    const res = await api.fetchJobPhotos(jobId)
    // A listing taken while the presigned PUTs are still landing is missing photos — serving it
    // from cache would freeze the Photos pane on a near-empty grid for the session (2026-08-31).
    const expected = jobs.value.find(j => j.job_id === jobId)?.image_count
    if (expected === undefined || res.photos.length >= expected) photosByJob.set(jobId, res)
    return res
  }

  function fetchSamplePhotos(): Promise<SamplePhotos> {
    return api.fetchSamplePhotos()
  }

  /** Presigned viewer + download URLs for a complete job, cached until 30 s before they expire. */
  async function fetchMeshUrls(jobId: string): Promise<MeshUrls> {
    const cached = meshUrls.value[jobId]
    if (cached && cached.expiresAt - Date.now() > 30_000) return cached
    const res = await api.getMeshUrl(jobId)
    const entry: MeshUrls = {
      url: res.url,
      downloadUrl: res.download_url,
      previewDownloadUrl: res.preview_download_url,
      expiresAt: new Date(res.expires_at).getTime(),
    }
    meshUrls.value[jobId] = entry
    return entry
  }

  // ── Polling ───────────────────────────────────────────────────────────
  function startPolling(jobId: string): void {
    if (pollTimers.has(jobId)) return

    async function tick(): Promise<void> {
      pollingActive.add(jobId)
      let updated: PhotogrammetryJob | null = null
      try {
        updated = await api.getJob(jobId)
      } catch (err) {
        if (axios.isAxiosError(err) && err.response?.status === 404) {
          stopPolling(jobId)
          jobs.value = jobs.value.filter(j => j.job_id !== jobId)
          if (activeJobId.value === jobId) activeJobId.value = null
          return
        }
      } finally {
        pollingActive.delete(jobId)
      }
      if (updated) {
        const before = jobs.value.find(j => j.job_id === updated!.job_id)?.warnings?.length ?? 0
        ;(updated.warnings ?? []).slice(before).forEach(w => pushToast(`"${updated!.name}": ${w}`))
        upsert(updated)
        if (!ACTIVE.has(updated.status)) {
          stopPolling(jobId)
          if (updated.status === "failed") pushToast(`"${updated.name}" failed${updated.error_message ? `: ${updated.error_message}` : ""}`)
          return
        }
      }
      if (pollTimers.has(jobId)) {
        const interval = updated?.worker_state === "off" ? POLL_INTERVAL_PAUSED_MS : POLL_INTERVAL_MS
        pollTimers.set(jobId, setTimeout(tick, interval))
      }
    }

    pollTimers.set(jobId, setTimeout(tick, POLL_INTERVAL_MS))
  }

  function stopPolling(jobId: string): void {
    const t = pollTimers.get(jobId)
    if (t !== undefined) { clearTimeout(t); pollTimers.delete(jobId) }
  }

  function resumePollingForActiveJobs(): void {
    jobs.value.filter(j => ACTIVE.has(j.status)).forEach(j => startPolling(j.job_id))
  }

  async function setVisibility(jobId: string, isPublic: boolean) {
    const updated = await api.setJobVisibility(jobId, isPublic)
    const idx = jobs.value.findIndex((j) => j.job_id === updated.job_id)
    if (idx >= 0) jobs.value[idx] = { ...jobs.value[idx], ...updated }
  }

  return {
    jobs, nextCursor, activeJobId, activeJob, uploadProgress, pollingActive, toasts, meshUrls,
    loadJobs, submitScan, submitSampleJob, selectJob, clearSelection, deleteJob, fetchMeshUrls, fetchJobPhotos, fetchSamplePhotos,
    resumePollingForActiveJobs, dismissToast, pushToast, setVisibility,
  }
})
