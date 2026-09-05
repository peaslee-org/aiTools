<script setup lang="ts">
import { computed, ref, watch } from "vue"
import axios from "axios"
import { usePhotogrammetryStore } from "@/stores/photogrammetry"
import type { PhotoItem } from "@/types"
import ImageDropzone from "./ImageDropzone.vue"
import PhotoGrid from "./PhotoGrid.vue"

const props = withDefaults(defineProps<{ sample?: boolean }>(), { sample: false })
const emit = defineEmits<{ submitted: [jobId: string]; "use-own-photos": [] }>()

const store = usePhotogrammetryStore()

function defaultName(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, "0")
  return `Scan ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const name = ref(defaultName())
const files = ref<File[]>([])
const removeBackground = ref(false)
const submitting = ref(false)

// ── sample mode: the bundled set, shown read-only, submitted server-side ──
const samplePhotos = ref<PhotoItem[]>([])
const sampleCount = ref(0)
const sampleLoading = ref(false)
const sampleError = ref<string | null>(null)

async function loadSample() {
  sampleLoading.value = true
  sampleError.value = null
  try {
    const set = await store.fetchSamplePhotos()
    name.value = set.name
    samplePhotos.value = set.photos
    sampleCount.value = set.image_count
  } catch (err) {
    const detail = axios.isAxiosError(err) ? err.response?.data?.detail : undefined
    sampleError.value = detail ?? "Could not load the sample photo set"
  } finally {
    sampleLoading.value = false
  }
}

watch(() => props.sample, (on) => {
  if (on) loadSample()
  else { name.value = defaultName(); samplePhotos.value = []; sampleError.value = null; removeBackground.value = false }
}, { immediate: true })

const canSubmit = computed(() => {
  if (submitting.value) return false
  if (props.sample) return !sampleLoading.value && !sampleError.value && samplePhotos.value.length > 0
  return files.value.length > 0
})

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const jobId = props.sample
      ? await store.submitSampleJob()
      : await store.submitScan(name.value.trim(), files.value, removeBackground.value)
    emit("submitted", jobId)
  } catch {
    // the store already raised a toast
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <form class="mx-auto max-w-3xl space-y-4" @submit.prevent="submit">
    <h2 class="text-base font-semibold">{{ props.sample ? "New scan — sample set" : "New scan" }}</h2>
    <label class="block text-sm">
      <span class="text-gray-700">Name</span>
      <input
        v-model="name"
        type="text"
        maxlength="200"
        :disabled="props.sample"
        class="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm disabled:bg-gray-100 disabled:text-gray-500"
      />
    </label>

    <label v-if="!props.sample" class="flex items-start gap-2 text-sm">
      <input v-model="removeBackground" type="checkbox" name="remove_background" class="mt-0.5" />
      <span>
        <span class="text-gray-700">Remove background</span>
        <span class="block text-xs text-gray-400">For turntable scans. Keeps only the object in the finished model.</span>
      </span>
    </label>

    <template v-if="props.sample">
      <div class="flex items-baseline justify-between text-sm">
        <span class="text-gray-700">
          <span class="font-medium">{{ sampleCount }} photos</span>
          <span class="ml-2 text-gray-400">bundled sample — reconstructs server-side, nothing to upload</span>
        </span>
        <button type="button" class="text-xs text-indigo-600 hover:underline" @click="emit('use-own-photos')">
          Use my own photos instead
        </button>
      </div>
      <PhotoGrid :photos="samplePhotos" :loading="sampleLoading" :error="sampleError" />
    </template>
    <ImageDropzone v-else @files-changed="files = $event" />

    <div class="flex items-center gap-3">
      <button
        type="submit"
        class="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        :disabled="!canSubmit"
      >{{ submitting ? (props.sample ? "Starting…" : "Uploading…") : "Start scan" }}</button>
      <span v-if="store.uploadProgress" class="text-sm text-gray-600">
        Uploading {{ store.uploadProgress.done }}/{{ store.uploadProgress.total }}
      </span>
    </div>
  </form>
</template>
