import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"

vi.mock("@/lib/photogrammetryApi", () => ({
  fetchJobPhotos: vi.fn(),
  fetchSamplePhotos: vi.fn(),
  deleteJob: vi.fn(),
  listJobs: vi.fn(),
  getJob: vi.fn(),
  createJob: vi.fn(),
  confirmJob: vi.fn(),
  createSampleJob: vi.fn(),
  getMeshUrl: vi.fn(),
  uploadToS3: vi.fn(),
  setJobVisibility: vi.fn(),
}))

import * as api from "@/lib/photogrammetryApi"
import { usePhotogrammetryStore } from "@/stores/photogrammetry"
import ImageDropzone from "../ImageDropzone.vue"
import NewScanForm from "../NewScanForm.vue"

const BOX = 'input[type="checkbox"][name="remove_background"]'

function file(name: string): File {
  return new File(["x"], name, { type: "image/jpeg" })
}

/** Mount with own photos, spy on the store's submitScan, drop one file. */
async function mountWithFile() {
  const w = mount(NewScanForm, { props: { sample: false } })
  const store = usePhotogrammetryStore()
  const submit = vi.spyOn(store, "submitScan").mockResolvedValue("j1")
  w.findComponent(ImageDropzone).vm.$emit("files-changed", [file("a.jpg")])
  await w.vm.$nextTick()
  return { w, submit }
}

describe("NewScanForm — Remove background", () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(api.fetchSamplePhotos).mockReset().mockResolvedValue({ name: "Sample scan", image_count: 1, photos: [] })
  })

  it("is unchecked by default and submits false", async () => {
    const { w, submit } = await mountWithFile()
    const box = w.find(BOX)
    expect(box.exists()).toBe(true)
    expect((box.element as HTMLInputElement).checked).toBe(false)
    await w.find("form").trigger("submit")
    await flushPromises()
    expect(submit).toHaveBeenCalledWith(expect.any(String), [expect.any(File)], false)
    expect(w.emitted("submitted")?.[0]).toEqual(["j1"])
  })

  it("ticked, submits true", async () => {
    const { w, submit } = await mountWithFile()
    await w.find(BOX).setValue(true)
    await w.find("form").trigger("submit")
    await flushPromises()
    expect(submit).toHaveBeenCalledWith(expect.any(String), [expect.any(File)], true)
  })

  it("is hidden in sample mode", async () => {
    const w = mount(NewScanForm, { props: { sample: true } })
    await flushPromises()
    expect(w.find(BOX).exists()).toBe(false)
  })
})
