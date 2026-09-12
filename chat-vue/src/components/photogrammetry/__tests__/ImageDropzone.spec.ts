import { beforeEach, describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import ImageDropzone from "@/components/photogrammetry/ImageDropzone.vue"

const drop = (files: File[]) => ({ dataTransfer: { files } })

const jpeg = (name: string) => new File(["x"], name, { type: "image/jpeg" })
const heic = (name: string) => new File(["x"], name, { type: "image/heic" })

describe("ImageDropzone", () => {
  beforeEach(() => {
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
