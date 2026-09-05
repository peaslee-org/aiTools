<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue"
import type { PhotoItem } from "@/types"

/**
 * Dense thumbnail grid over a scan's input photos. Thumbnails are ≤256 px JPEGs served by the
 * API, so a 150-photo set is a few MB, lazily; the full-size original loads only when a
 * thumbnail is clicked (overlay with prev/next). Shared by the scan page and the New Scan
 * form's sample mode.
 */
const props = withDefaults(defineProps<{
  photos: PhotoItem[]
  loading?: boolean
  error?: string | null
  /** Photos SfM registered, once the worker has written per-photo status; null/undefined before. */
  matched?: number | null
}>(), { loading: false, error: null, matched: null })

const emit = defineEmits<{ open: [photo: PhotoItem] }>()

const SKELETON_COUNT = 24

// ── per-tile load state (keyed by filename: thumb_url is null while the API is still
// generating the thumbnail — such a tile stays pending, without an <img>) ──
type TileState = "pending" | "loaded" | "failed"
const tiles = ref<Record<string, TileState>>({})
watch(() => props.photos, (list) => {
  const next: Record<string, TileState> = {}
  for (const p of list) next[p.filename] = tiles.value[p.filename] ?? "pending"
  tiles.value = next
}, { immediate: true })
function tileState(photo: PhotoItem): TileState {
  return photo.thumb_url ? (tiles.value[photo.filename] ?? "pending") : "pending"
}
function onThumbLoad(photo: PhotoItem) { tiles.value[photo.filename] = "loaded" }
function onThumbError(photo: PhotoItem) { tiles.value[photo.filename] = "failed" }

const doneCount = computed(() => props.photos.filter(p => tileState(p) !== "pending").length)
const status = computed(() => {
  if (props.loading) return "Preparing thumbnails…"
  if (props.photos.length === 0) return ""
  if (doneCount.value < props.photos.length) return `Loading photos… ${doneCount.value} of ${props.photos.length}`
  const base = `${props.photos.length} photos`
  return props.matched == null ? base : `${base} · ${props.matched} matched`
})

// ── per-photo SfM status ("registered" | "unregistered" | "skipped:<reason>" | null) ──
type PhotoMark = { kind: "registered" } | { kind: "unregistered" } | { kind: "skipped"; reason: string } | null
function mark(photo: PhotoItem): PhotoMark {
  const st = photo.status ?? null
  if (st === "registered") return { kind: "registered" }
  if (st === "unregistered") return { kind: "unregistered" }
  if (st?.startsWith("skipped")) return { kind: "skipped", reason: st.slice("skipped:".length).trim() || "skipped" }
  return null
}
function tagText(photo: PhotoItem): string {
  return mark(photo)?.kind === "skipped" ? "skipped" : "not matched"
}
function tagTitle(photo: PhotoItem): string {
  const m = mark(photo)
  return m?.kind === "skipped" ? m.reason : "not matched to the other photos"
}
const stillLoading = computed(() => props.loading || doneCount.value < props.photos.length)

// ── mask overlays (remove_background scans): the worker's darkened-background preview per photo ──
const showMasks = ref(false)
const hasMasks = computed(() => props.photos.some(p => !!p.mask_url))
function tileSrc(photo: PhotoItem): string | null {
  return showMasks.value && photo.mask_url ? photo.mask_url : photo.thumb_url
}

// ── overlay with prev/next ──
const openIndex = ref<number | null>(null)
const open = computed(() => (openIndex.value === null ? null : props.photos[openIndex.value] ?? null))
const hasPrev = computed(() => openIndex.value !== null && openIndex.value > 0)
const hasNext = computed(() => openIndex.value !== null && openIndex.value < props.photos.length - 1)

function show(index: number) {
  openIndex.value = index
  const photo = props.photos[index]
  if (photo) emit("open", photo)
}
function close() { openIndex.value = null }
function prev() { if (hasPrev.value && openIndex.value !== null) openIndex.value -= 1 }
function next() { if (hasNext.value && openIndex.value !== null) openIndex.value += 1 }

// Registered in the capture phase so the overlay owns these keys while it is open: the Escape
// that closes it must not also close the scan (ScanDetailView listens on document too).
function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") close()
  else if (e.key === "ArrowLeft") prev()
  else if (e.key === "ArrowRight") next()
  else return
  e.preventDefault()
  e.stopImmediatePropagation()
}

watch(openIndex, (idx, prevIdx) => {
  if (idx !== null && prevIdx === null) document.addEventListener("keydown", onKeydown, true)
  else if (idx === null && prevIdx !== null) document.removeEventListener("keydown", onKeydown, true)
})

onUnmounted(() => document.removeEventListener("keydown", onKeydown, true))

const chevron = "absolute top-1/2 -translate-y-1/2 select-none px-3 text-5xl leading-none text-white/70 hover:text-white disabled:opacity-30 disabled:hover:text-white/70"
</script>

<template>
  <div>
    <p v-if="props.error" class="text-sm text-red-600">{{ props.error }}</p>

    <template v-else>
      <p v-if="status" class="mb-2 flex items-center gap-2 text-xs text-gray-500" data-testid="photo-status">
        <svg v-if="stillLoading" class="h-3.5 w-3.5 animate-spin text-gray-400" viewBox="0 0 24 24" fill="none" aria-hidden="true">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" class="opacity-25" />
          <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="4" stroke-linecap="round" />
        </svg>
        <span>{{ status }}</span>
        <label v-if="hasMasks" class="ml-auto flex items-center gap-1 text-xs text-gray-600" title="Swap thumbnails for the background-removal masks">
          <input v-model="showMasks" type="checkbox" data-testid="show-masks" />
          Show masks
        </label>
      </p>

      <div v-if="props.loading" class="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1">
        <div v-for="i in SKELETON_COUNT" :key="i" data-testid="skeleton" class="aspect-square animate-pulse rounded bg-gray-200" />
      </div>

      <div v-else class="grid grid-cols-[repeat(auto-fill,minmax(112px,1fr))] gap-1">
        <button
          v-for="(photo, i) in props.photos"
          :key="photo.filename"
          type="button"
          data-testid="photo-tile"
          class="relative aspect-square overflow-hidden rounded bg-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          :class="mark(photo)?.kind === 'unregistered' || mark(photo)?.kind === 'skipped' ? 'opacity-50 grayscale' : ''"
          @click="show(i)"
        >
          <img
            v-if="tileSrc(photo)"
            :src="tileSrc(photo)!"
            :alt="photo.filename"
            :title="photo.filename"
            loading="lazy"
            decoding="async"
            class="h-full w-full object-cover transition-opacity hover:scale-105"
            :class="tileState(photo) === 'loaded' ? 'opacity-100 transition-transform' : 'opacity-0'"
            @load="onThumbLoad(photo)"
            @error="onThumbError(photo)"
          />
          <span
            v-if="tileState(photo) === 'pending'"
            data-testid="thumb-pending"
            class="absolute inset-0 flex animate-pulse items-center justify-center bg-gray-200"
          >
            <svg class="h-4 w-4 animate-spin text-gray-400" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" class="opacity-25" />
              <path d="M4 12a8 8 0 018-8" stroke="currentColor" stroke-width="4" stroke-linecap="round" />
            </svg>
          </span>
          <span
            v-else-if="tileState(photo) === 'failed'"
            data-testid="thumb-error"
            class="absolute inset-0 flex items-center justify-center bg-gray-100 text-lg text-gray-400"
            :title="`${photo.filename} — thumbnail failed`"
          >✕</span>
          <template v-if="mark(photo)">
            <span
              v-if="mark(photo)!.kind === 'registered'"
              data-testid="status-registered"
              class="absolute right-1 top-1 rounded-full bg-green-600/90 px-1 text-[10px] leading-4 text-white"
              title="matched by SfM"
            >✓</span>
            <span
              v-else
              data-testid="status-tag"
              class="absolute inset-x-0 bottom-0 bg-black/60 px-1 py-0.5 text-[10px] leading-3 text-white"
              :title="tagTitle(photo)"
            >{{ tagText(photo) }}</span>
          </template>
        </button>
      </div>
    </template>

    <div
      v-if="open"
      data-testid="photo-overlay"
      data-photo-overlay
      class="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/80 p-4"
      @click="close"
    >
      <img
        :src="open.url"
        :alt="open.filename"
        class="max-h-[85vh] max-w-[calc(100vw-9rem)] rounded object-contain"
        @click.stop
      />
      <p class="mt-2 text-sm text-white/80">{{ open.filename }} · {{ (openIndex ?? 0) + 1 }} / {{ props.photos.length }}</p>
      <button
        type="button"
        data-testid="photo-prev"
        :class="[chevron, 'left-4']"
        :disabled="!hasPrev"
        aria-label="Previous photo"
        @click.stop="prev"
      >‹</button>
      <button
        type="button"
        data-testid="photo-next"
        :class="[chevron, 'right-4']"
        :disabled="!hasNext"
        aria-label="Next photo"
        @click.stop="next"
      >›</button>
      <button
        type="button"
        class="absolute right-4 top-4 rounded-full bg-black/60 px-3 py-1 text-sm text-white hover:bg-black/80"
        aria-label="Close"
        @click.stop="close"
      >✕</button>
    </div>
  </div>
</template>
