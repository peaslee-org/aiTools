import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

vi.mock("@/lib/prepareImage", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/prepareImage")>()),
  photosMissingFocalLength: vi.fn().mockResolvedValue([]),
}))
import ImageDropzone from "@/components/photogrammetry/ImageDropzone.vue"
import * as prepare from "@/lib/prepareImage"

const drop = (files: File[]) => ({ dataTransfer: { files } })

const jpeg = (name: string) => new File(["x"], name, { type: "image/jpeg" })
const heic = (name: string) => new File(["x"], name, { type: "image/heic" })

describe("ImageDropzone", () => {
  beforeEach(() => {
    vi.mocked(prepare.photosMissingFocalLength).mockReset().mockResolvedValue([])
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:thumb"),
      revokeObjectURL: vi.fn(),
    })
  })

  it("names the files it could not take instead of dropping them silently", async () => {
    // An iPhone hands over HEIC whenever Safari declines to transcode. Filtering those out
    // quietly left the picker looking like it had simply ignored the whole selection.
    const wrapper = mount(ImageDropzone)

    await wrapper.get(".border-dashed").trigger("drop", drop([heic("IMG_0001.HEIC")]))

    expect(wrapper.text()).toContain("IMG_0001.HEIC")
  })

  it("keeps accepting the usable photos from a mixed selection", async () => {
    const wrapper = mount(ImageDropzone)

    await wrapper.get(".border-dashed").trigger("drop", drop([jpeg("a.jpg"), heic("b.HEIC")]))

    expect(wrapper.text()).toContain("1 photos")
    expect(wrapper.text()).toContain("b.HEIC")
  })

  it("forgets earlier rejections once the selection is cleared", async () => {
    const wrapper = mount(ImageDropzone)
    await wrapper.get(".border-dashed").trigger("drop", drop([jpeg("a.jpg"), heic("b.HEIC")]))

    await wrapper.get("button").trigger("click")

    expect(wrapper.text()).not.toContain("b.HEIC")
  })
})

describe("ImageDropzone focal-length warning", () => {
  beforeEach(() => {
    vi.mocked(prepare.photosMissingFocalLength).mockReset().mockResolvedValue([])
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn(() => "blob:thumb"),
      revokeObjectURL: vi.fn(),
    })
  })

  it("warns about photos with no focal length but still accepts them", async () => {
    // A capture taken through the page carries no FocalLength, so COLMAP falls back to a
    // guessed focal prior. Worth knowing before committing to a GPU run, not worth refusing.
    vi.mocked(prepare.photosMissingFocalLength).mockResolvedValue(["a.jpg"])
    const wrapper = mount(ImageDropzone)

    await wrapper.get(".border-dashed").trigger("drop", drop([jpeg("a.jpg")]))
    await flushPromises()

    expect(wrapper.text()).toContain("focal length")
    expect(wrapper.text()).toContain("1 photos")
  })

  it("says nothing when every photo carries one", async () => {
    const wrapper = mount(ImageDropzone)

    await wrapper.get(".border-dashed").trigger("drop", drop([jpeg("a.jpg")]))
    await flushPromises()

    expect(wrapper.text()).not.toContain("focal length")
  })

  it("forgets the warning once the selection is cleared", async () => {
    vi.mocked(prepare.photosMissingFocalLength).mockResolvedValue(["a.jpg"])
    const wrapper = mount(ImageDropzone)
    await wrapper.get(".border-dashed").trigger("drop", drop([jpeg("a.jpg")]))
    await flushPromises()

    await wrapper.get("button").trigger("click")

    expect(wrapper.text()).not.toContain("focal length")
  })
})
