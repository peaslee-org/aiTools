<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue"
import { usePhotogrammetryStore } from "@/stores/photogrammetry"
import type { PhotoItem } from "@/types"
import ScanStatusBadge from "./ScanStatusBadge.vue"
import StageStrip from "./StageStrip.vue"
import MeshViewer from "./MeshViewer.vue"
import NewScanForm from "./NewScanForm.vue"
import PhotoGrid from "./PhotoGrid.vue"
import PublicToggle from "@/components/PublicToggle.vue"
import type { NewScanMode } from "./newScanMode"

type Pane = "3d" | "photos"

const props = defineProps<{ formMode: NewScanMode }>()
const emit = defineEmits<{ "close-new-job-form": []; "use-own-photos": [] }>()

const store = usePhotogrammetryStore()
const job = computed(() => store.activeJob)
const meshUrl = ref<string | null>(null)
const hasPreviewDownload = ref(false)
const meshError = ref<string | null>(null)

// ── 3D | Photos toggle, remembered per job for the session ──
const paneByJob = new Map<string, Pane>()
const pane = ref<Pane>("photos")
const isComplete = computed(() => job.value?.status === "complete")

watch([() => job.value?.job_id, isComplete], ([jobId, complete]) => {
  if (!jobId) return
  pane.value = paneByJob.get(jobId) ?? (complete ? "3d" : "photos")
}, { immediate: true })

function setPane(p: Pane) {
  if (p === "3d" && !isComplete.value) return
  pane.value = p
  if (job.value) paneByJob.set(job.value.job_id, p)
}

// ── photos pane, fetched the first time it is shown for a job; refetched when SfM has run ──
const photos = ref<PhotoItem[]>([])
const photosMatched = ref<number | null>(null)
const photosLoading = ref(false)
const photosError = ref<string | null>(null)
const TERMINAL = new Set(["complete", "failed"])

// The API generates thumbnails in the background (kicked at confirm); a photo whose thumbnail
// isn't ready yet comes back with thumb_url null. Poll with force until they've all arrived,
// bounded so a permanently undecodable photo doesn't poll forever (cap ≈ 5 min, comfortably
// past the ~2.5 min a 150-photo set takes to thumbnail).
const THUMB_POLL_MS = 5000
const MAX_THUMB_POLLS = 60
let thumbTimer: number | null = null
let thumbPolls = 0

function cancelThumbPoll() {
  if (thumbTimer !== null) window.clearTimeout(thumbTimer)
  thumbTimer = null
  thumbPolls = 0
}

async function loadPhotos(jobId: string, force = false): Promise<void> {
  photosError.value = null
  photosLoading.value = photos.value.length === 0
  try {
    const res = await store.fetchJobPhotos(jobId, { force })
    if (job.value?.job_id !== jobId) return
    photos.value = res.photos
    photosMatched.value = res.matched
    if (res.photos.some(p => !p.thumb_url) && pane.value === "photos" && thumbPolls < MAX_THUMB_POLLS) {
      thumbTimer = window.setTimeout(() => {
        thumbTimer = null
        thumbPolls += 1
        void loadPhotos(jobId, true)
      }, THUMB_POLL_MS)
    }
  } catch {
    if (job.value?.job_id !== jobId) return
    photosError.value = "Could not load the photos"
  } finally {
    photosLoading.value = false
  }
}

watch([() => job.value?.job_id, pane], ([jobId, p]) => {
  cancelThumbPoll()
  photos.value = []
  photosMatched.value = null
  photosError.value = null
  if (!jobId || p !== "photos") return
  void loadPhotos(jobId)
}, { immediate: true })

onUnmounted(cancelThumbPoll)

// Per-photo status (which photos SfM matched) lands on the row when the job ends — refetch then.
watch(() => job.value?.status, (status, prev) => {
  const jobId = job.value?.job_id
  if (!jobId || !status || !TERMINAL.has(status) || (prev && TERMINAL.has(prev))) return
  if (pane.value === "photos") void loadPhotos(jobId, true)
  else void store.fetchJobPhotos(jobId, { force: true }).catch(() => undefined)
})

// ── closing the scan: ✕ or Escape (unless a photo overlay owns the key) ──
function closeScan(): void { store.clearSelection() }
function onDocKeydown(e: KeyboardEvent): void {
  if (e.key !== "Escape" || e.defaultPrevented) return
  if (props.formMode !== "closed" || !job.value) return
  if (document.querySelector("[data-photo-overlay]")) return
  closeScan()
}
onMounted(() => document.addEventListener("keydown", onDocKeydown))
onUnmounted(() => document.removeEventListener("keydown", onDocKeydown))

watch(
  [() => job.value?.job_id, () => job.value?.status],
  async ([jobId, status]) => {
    meshUrl.value = null
    hasPreviewDownload.value = false
    meshError.value = null
    if (jobId && status === "complete") {
      try {
        const urls = await store.fetchMeshUrls(jobId)
        if (job.value?.job_id !== jobId) return
        meshUrl.value = urls.url
        hasPreviewDownload.value = urls.previewDownloadUrl !== null
      } catch {
        if (job.value?.job_id !== jobId) return
        meshError.value = "Could not load the mesh URL"
      }
    }
  },
  { immediate: true },
)

/**
 * Re-resolve the URL at click time (the store refreshes it within 30 s of expiry) so a tab
 * left open past the 15-minute presign never hands S3 a stale link. The URL is presigned
 * with Content-Disposition: attachment, so assigning it saves the file and leaves the page.
 */
async function download(which: "mesh" | "preview"): Promise<void> {
  const jobId = job.value?.job_id
  if (!jobId) return
  try {
    const urls = await store.fetchMeshUrls(jobId)
    const url = which === "mesh" ? urls.downloadUrl : urls.previewDownloadUrl
    if (url) window.location.assign(url)
  } catch {
    meshError.value = "Could not get a download link"
  }
}

const visibilityBusy = ref(false)
async function setVisibility(next: boolean) {
  if (!job.value) return
  visibilityBusy.value = true
  try {
    await store.setVisibility(job.value.job_id, next)
  } catch {
    store.pushToast("Couldn't update visibility — try again")
  } finally {
    visibilityBusy.value = false
  }
}

const segment = "px-3 py-1 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40"
</script>

<template>
  <section class="flex flex-col bg-gray-50 text-gray-900">
    <div v-if="props.formMode !== 'closed'" class="p-6 overflow-y-auto">
      <NewScanForm
        :sample="props.formMode === 'sample'"
        @submitted="emit('close-new-job-form')"
        @use-own-photos="emit('use-own-photos')"
      />
    </div>

    <div v-else-if="!job" class="flex flex-1 items-center justify-center text-sm text-gray-500">
      Select a scan or start a new one
    </div>

    <div v-else class="flex flex-1 flex-col overflow-hidden">
      <header class="flex items-center gap-3 border-b border-gray-200 bg-white px-6 py-3">
        <h2 class="truncate text-base font-semibold">{{ job.name }}</h2>
        <ScanStatusBadge :status="job.status" :stage="job.stage" :worker-state="job.worker_state" :estimated-wait-seconds="job.estimated_wait_seconds" />
        <span class="text-xs text-gray-500">{{ job.image_count }} photos</span>
        <span v-if="job.remove_background" class="text-xs text-gray-500" title="Backdrop masked out of the dense stage">· background removed</span>
        <span v-if="job.gpu_notice" class="text-xs text-amber-700">{{ job.gpu_notice }}</span>

        <div class="ml-auto flex shrink-0 items-center gap-2">
          <PublicToggle
            :is-public="job.is_public ?? false"
            :busy="visibilityBusy"
            @toggle="(next) => setVisibility(next)"
          />
          <div class="inline-flex overflow-hidden rounded border border-gray-300 bg-white" role="tablist" aria-label="View">
            <button
              type="button"
              role="tab"
              :aria-selected="pane === '3d'"
              :class="[segment, pane === '3d' ? 'bg-indigo-600 text-white' : 'text-gray-700 hover:bg-gray-100']"
              :disabled="!isComplete"
              :title="isComplete ? 'Reconstructed mesh' : 'Available when the scan completes'"
              @click="setPane('3d')"
            >3D</button>
            <button
              type="button"
              role="tab"
              :aria-selected="pane === 'photos'"
              :class="[segment, 'border-l border-gray-300', pane === 'photos' ? 'bg-indigo-600 text-white' : 'text-gray-700 hover:bg-gray-100']"
              title="Input photos"
              @click="setPane('photos')"
            >Photos</button>
          </div>
          <template v-if="isComplete">
            <button
              type="button"
              class="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100 disabled:opacity-50"
              :disabled="!meshUrl"
              @click="download('mesh')"
            >Download GLB</button>
            <button
              v-if="hasPreviewDownload"
              type="button"
              class="rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-100"
              @click="download('preview')"
            >Download preview</button>
          </template>
          <button
            type="button"
            class="ml-1 rounded px-2 py-1 text-base leading-none text-gray-400 hover:bg-gray-100 hover:text-gray-700"
            title="Close scan"
            aria-label="Close scan"
            @click="closeScan"
          >✕</button>
        </div>
      </header>

      <div class="flex-1 overflow-auto p-6">
        <ul v-if="job.warnings?.length" class="mb-4 space-y-1 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
          <li v-for="w in job.warnings ?? []" :key="w">⚠ {{ w }}</li>
        </ul>

        <p v-if="job.status === 'failed'" class="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {{ job.error_message ?? "Reconstruction failed" }}
        </p>

        <template v-if="pane === '3d' && isComplete">
          <p v-if="meshError" class="text-sm text-red-600">{{ meshError }}</p>
          <MeshViewer v-else :src="meshUrl" :pending="!meshUrl" :poster="job.preview_url" :mock="job.mock" />
        </template>

        <template v-else>
          <div v-if="job.status !== 'complete' && job.status !== 'failed'" class="mb-4 space-y-4">
            <StageStrip :status="job.status" :stage="job.stage" />
            <p class="text-sm text-gray-600">
              <span v-if="job.status === 'pending'">Waiting for uploads to finish…</span>
              <span v-else-if="job.status === 'queued'">Queued — waiting for a GPU worker.</span>
              <span v-else>Reconstructing…</span>
            </p>
            <img v-if="job.preview_url" :src="job.preview_url" alt="" class="max-h-64 rounded border border-gray-200" />
          </div>
          <PhotoGrid :photos="photos" :loading="photosLoading" :error="photosError" :matched="photosMatched" />
        </template>
      </div>
    </div>
  </section>
</template>
