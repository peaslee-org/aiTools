<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue"
import { photosMissingFocalLength } from "@/lib/prepareImage"

const props = withDefaults(defineProps<{ min?: number; max?: number }>(), { min: 5, max: 150 })
const emit = defineEmits<{ "files-changed": [files: File[]] }>()

const ACCEPT = "image/jpeg,image/png"
const fileInput = ref<HTMLInputElement | null>(null)
const files = ref<File[]>([])
const isDragOver = ref(false)
const thumbs = ref<string[]>([])
const rejected = ref<string[]>([])
const noFocal = ref<string[]>([])

const error = computed(() => {
  if (files.value.length === 0) return null
  if (files.value.length < props.min) return `Add at least ${props.min} photos (${files.value.length} so far).`
  if (files.value.length > props.max) return `At most ${props.max} photos (${files.value.length} selected).`
  return null
})
const totalMb = computed(() => (files.value.reduce((s, f) => s + f.size, 0) / 1024 / 1024).toFixed(1))

function isImage(f: File): boolean {
  return f.type === "image/jpeg" || f.type === "image/png" || /\.(jpe?g|png)$/i.test(f.name)
}

function addFiles(list: FileList | File[]) {
  const picked = Array.from(list)
  const incoming = picked.filter(isImage)
  // Named, not silently discarded: an iPhone hands over HEIC whenever Safari declines to
  // transcode, and an empty-looking picker gave no clue why.
  rejected.value = picked.filter(f => !isImage(f)).map(f => f.name)
  const seen = new Set(files.value.map(f => `${f.name}:${f.size}`))
  const next: File[] = []
  for (const f of incoming) {
    const key = `${f.name}:${f.size}`
    if (seen.has(key)) continue
    seen.add(key)
    next.push(f)
  }
  files.value = [...files.value, ...next]
  void flagMissingFocalLength(next)
}

/** Advisory: names the photos COLMAP will have to guess a focal length for. */
async function flagMissingFocalLength(added: File[]): Promise<void> {
  if (added.length === 0) return
  try {
    const missing = await photosMissingFocalLength(added)
    if (missing.length) noFocal.value = [...noFocal.value, ...missing]
  } catch {
    // Reading metadata is a nicety; never fail a selection over it.
  }
}

function clear() {
  files.value = []
  rejected.value = []
  noFocal.value = []
}

function onDrop(e: DragEvent) {
  isDragOver.value = false
  if (e.dataTransfer?.files) addFiles(e.dataTransfer.files)
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) addFiles(input.files)
  input.value = ""
}

function revokeThumbs() {
  thumbs.value.forEach(u => URL.revokeObjectURL(u))
  thumbs.value = []
}

watch(files, (list) => {
  revokeThumbs()
  thumbs.value = list.slice(0, 12).map(f => URL.createObjectURL(f))
  emit("files-changed", error.value ? [] : list)
})

onUnmounted(revokeThumbs)
</script>

<template>
  <div>
    <div
      class="border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors"
      :class="isDragOver ? 'border-indigo-400 bg-indigo-50' : files.length ? 'border-green-300 bg-green-50 hover:border-green-400' : 'border-gray-300 hover:border-gray-400'"
      @dragover.prevent="isDragOver = true"
      @dragleave="isDragOver = false"
      @drop.prevent="onDrop"
      @click="fileInput?.click()"
    >
      <input ref="fileInput" type="file" :accept="ACCEPT" multiple class="hidden" @change="onFileChange" />
      <div v-if="files.length" class="text-sm text-gray-700">
        <span class="font-medium">{{ files.length }} photos</span>
        <span class="text-gray-400 ml-2">({{ totalMb }} MB)</span>
        <div class="mt-3 grid grid-cols-3 gap-1 sm:grid-cols-6" @click.stop>
          <img v-for="(src, i) in thumbs" :key="i" :src="src" class="aspect-square w-full rounded object-cover" alt="" />
          <div v-if="files.length > thumbs.length" class="flex aspect-square items-center justify-center rounded bg-gray-200 text-xs text-gray-600">
            +{{ files.length - thumbs.length }}
          </div>
        </div>
        <button type="button" class="mt-2 text-xs text-gray-500 hover:text-red-600" @click.stop="clear">Clear</button>
      </div>
      <div v-else class="text-sm text-gray-500">
        <p>Drop photos here or click to browse</p>
        <p class="text-xs text-gray-400 mt-1">JPG or PNG · {{ min }}–{{ max }} photos orbiting one object</p>
      </div>
    </div>
    <p v-if="error" class="mt-1 text-xs text-red-600">{{ error }}</p>
    <p v-if="noFocal.length" class="mt-1 text-xs text-amber-600">
      {{ noFocal.length }} photo{{ noFocal.length === 1 ? " has" : "s have" }} no focal length —
      taken with the camera here rather than picked from your library. They still reconstruct, but
      less accurately; shooting in the Camera app and picking from the library keeps it.
    </p>
    <p v-if="rejected.length" class="mt-1 text-xs text-amber-600">
      {{ rejected.length }} file{{ rejected.length === 1 ? "" : "s" }} skipped, not a JPG or PNG:
      {{ rejected.slice(0, 5).join(", ") }}{{ rejected.length > 5 ? ", …" : "" }}
    </p>
  </div>
</template>
