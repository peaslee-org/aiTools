import { describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import PhotoGrid from "../PhotoGrid.vue"

const photos = [
  { filename: "0001.jpg", url: "https://s3/full/0001.jpg", thumb_url: "https://s3/thumbs/0001.jpg" },
  { filename: "0002.jpg", url: "https://s3/full/0002.jpg", thumb_url: "https://s3/thumbs/0002.jpg" },
]

describe("PhotoGrid", () => {
  it("renders one lazy thumbnail per photo, named by filename", () => {
    const w = mount(PhotoGrid, { props: { photos } })
    const imgs = w.findAll("img")
    expect(imgs).toHaveLength(2)
    expect(imgs[0].attributes("src")).toBe("https://s3/thumbs/0001.jpg")
    expect(imgs[0].attributes("loading")).toBe("lazy")
    expect(imgs[0].attributes("alt")).toBe("0001.jpg")
    expect(imgs[0].attributes("title")).toBe("0001.jpg")
  })

  it("clicking a thumbnail emits open and shows the full-size image in an overlay", async () => {
    const w = mount(PhotoGrid, { props: { photos }, attachTo: document.body })
    await w.findAll("img")[1].trigger("click")
    expect(w.emitted("open")?.[0]).toEqual([photos[1]])
    const overlay = w.find('[data-testid="photo-overlay"]')
    expect(overlay.exists()).toBe(true)
    expect(overlay.find("img").attributes("src")).toBe("https://s3/full/0002.jpg")
    expect(overlay.text()).toContain("0002.jpg")
    w.unmount()
  })

  it("Escape closes the overlay", async () => {
    const w = mount(PhotoGrid, { props: { photos }, attachTo: document.body })
    await w.findAll("img")[0].trigger("click")
    expect(w.find('[data-testid="photo-overlay"]').exists()).toBe(true)
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    await w.vm.$nextTick()
    expect(w.find('[data-testid="photo-overlay"]').exists()).toBe(false)
    w.unmount()
  })

  it("clicking the backdrop closes the overlay", async () => {
    const w = mount(PhotoGrid, { props: { photos }, attachTo: document.body })
    await w.findAll("img")[0].trigger("click")
    await w.find('[data-testid="photo-overlay"]').trigger("click")
    expect(w.find('[data-testid="photo-overlay"]').exists()).toBe(false)
    w.unmount()
  })

  it("shows skeleton squares while loading and an error message on failure", () => {
    const loading = mount(PhotoGrid, { props: { photos: [], loading: true } })
    expect(loading.findAll('[data-testid="skeleton"]').length).toBeGreaterThan(0)
    const failed = mount(PhotoGrid, { props: { photos: [], error: "Could not load photos" } })
    expect(failed.text()).toContain("Could not load photos")
  })

  describe("overlay navigation", () => {
    const three = [
      ...photos,
      { filename: "0003.jpg", url: "https://s3/full/0003.jpg", thumb_url: "https://s3/thumbs/0003.jpg" },
    ]
    const overlayImg = (w: ReturnType<typeof mount>) => w.find('[data-testid="photo-overlay"] img').attributes("src")
    const key = (k: string) => document.dispatchEvent(new KeyboardEvent("keydown", { key: k }))

    it("arrow keys move to the next and previous photo", async () => {
      const w = mount(PhotoGrid, { props: { photos: three }, attachTo: document.body })
      await w.findAll("img")[0].trigger("click")
      key("ArrowRight"); await w.vm.$nextTick()
      expect(overlayImg(w)).toBe("https://s3/full/0002.jpg")
      key("ArrowRight"); await w.vm.$nextTick()
      expect(overlayImg(w)).toBe("https://s3/full/0003.jpg")
      key("ArrowLeft"); await w.vm.$nextTick()
      expect(overlayImg(w)).toBe("https://s3/full/0002.jpg")
      w.unmount()
    })

    it("stops at the ends and disables the chevron there", async () => {
      const w = mount(PhotoGrid, { props: { photos: three }, attachTo: document.body })
      await w.findAll("img")[0].trigger("click")
      expect(w.find('[data-testid="photo-prev"]').attributes("disabled")).toBeDefined()
      key("ArrowLeft"); await w.vm.$nextTick()
      expect(overlayImg(w)).toBe("https://s3/full/0001.jpg")
      key("ArrowRight"); key("ArrowRight"); key("ArrowRight"); await w.vm.$nextTick()
      expect(overlayImg(w)).toBe("https://s3/full/0003.jpg")
      expect(w.find('[data-testid="photo-next"]').attributes("disabled")).toBeDefined()
      w.unmount()
    })

    it("captions the photo with its position", async () => {
      const w = mount(PhotoGrid, { props: { photos: three }, attachTo: document.body })
      await w.findAll("img")[1].trigger("click")
      expect(w.find('[data-testid="photo-overlay"]').text()).toContain("0002.jpg · 2 / 3")
      w.unmount()
    })

    it("clicking a chevron navigates without closing", async () => {
      const w = mount(PhotoGrid, { props: { photos: three }, attachTo: document.body })
      await w.findAll("img")[0].trigger("click")
      await w.find('[data-testid="photo-next"]').trigger("click")
      expect(w.find('[data-testid="photo-overlay"]').exists()).toBe(true)
      expect(overlayImg(w)).toBe("https://s3/full/0002.jpg")
      w.unmount()
    })
  })

  describe("thumbnail loading state", () => {
    it("keeps a tile pending with no img while its thumbnail is still being generated", () => {
      // thumb_url is null until the API's background generation catches up; an <img src="null">
      // would fire error and mark the tile ✕ even though the thumbnail is merely on its way.
      const w = mount(PhotoGrid, {
        props: { photos: [{ filename: "0001.jpg", url: "u", thumb_url: null, status: null }] },
      })
      expect(w.findAll('[data-testid="thumb-pending"]')).toHaveLength(1)
      expect(w.find('[data-testid="thumb-error"]').exists()).toBe(false)
      expect(w.find('[data-testid="photo-tile"] img').exists()).toBe(false)
    })

    it("counts tiles as they load, then reports the total", async () => {
      const w = mount(PhotoGrid, { props: { photos } })
      expect(w.text()).toContain("Loading photos… 0 of 2")
      expect(w.findAll('[data-testid="thumb-pending"]')).toHaveLength(2)
      await w.findAll("img")[0].trigger("load")
      expect(w.text()).toContain("Loading photos… 1 of 2")
      expect(w.findAll('[data-testid="thumb-pending"]')).toHaveLength(1)
      await w.findAll("img")[1].trigger("load")
      expect(w.text()).toContain("2 photos")
      expect(w.text()).not.toContain("Loading photos")
      expect(w.findAll('[data-testid="thumb-pending"]')).toHaveLength(0)
    })

    it("a thumbnail that fails shows a muted tile and still counts as done", async () => {
      const w = mount(PhotoGrid, { props: { photos } })
      await w.findAll("img")[0].trigger("error")
      await w.findAll("img")[1].trigger("load")
      expect(w.findAll('[data-testid="thumb-error"]')).toHaveLength(1)
      expect(w.text()).toContain("2 photos")
    })

    it("says it is preparing thumbnails while the list is still loading", () => {
      const w = mount(PhotoGrid, { props: { photos: [], loading: true } })
      expect(w.text()).toContain("Preparing thumbnails…")
    })
  })

  describe("photo status", () => {
    const statused = [
      { filename: "0001.jpg", url: "u1", thumb_url: "t1", status: "registered" },
      { filename: "0002.jpg", url: "u2", thumb_url: "t2", status: "unregistered" },
      { filename: "0003.jpg", url: "u3", thumb_url: "t3", status: "skipped:unreadable file" },
    ]

    it("marks tiles by status: matched check, not matched dimmed, skipped tagged with its reason", () => {
      const w = mount(PhotoGrid, { props: { photos: statused, matched: 1 } })
      const tiles = w.findAll('[data-testid="photo-tile"]')
      expect(tiles[0].find('[data-testid="status-registered"]').exists()).toBe(true)
      expect(tiles[1].classes()).toEqual(expect.arrayContaining(["opacity-50", "grayscale"]))
      expect(tiles[1].find('[data-testid="status-tag"]').text()).toBe("not matched")
      expect(tiles[2].find('[data-testid="status-tag"]').text()).toBe("skipped")
      expect(tiles[2].find('[data-testid="status-tag"]').attributes("title")).toBe("unreadable file")
      expect(tiles[0].classes()).not.toContain("grayscale")
    })

    it("appends the matched count to the status line once thumbnails have loaded", async () => {
      const w = mount(PhotoGrid, { props: { photos: statused, matched: 1 } })
      for (const img of w.findAll("img")) await img.trigger("load")
      expect(w.find('[data-testid="photo-status"]').text()).toBe("3 photos · 1 matched")
    })

    it("shows no matched count when the job has no photo status yet", async () => {
      const w = mount(PhotoGrid, { props: { photos, matched: null } })
      for (const img of w.findAll("img")) await img.trigger("load")
      expect(w.find('[data-testid="photo-status"]').text()).toBe("2 photos")
    })
  })

  it("the Escape that closes the overlay does not reach other document listeners", async () => {
    const w = mount(PhotoGrid, { props: { photos }, attachTo: document.body })
    await w.findAll("img")[0].trigger("click")
    const other = vi.fn()
    document.addEventListener("keydown", other)
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", cancelable: true }))
    await w.vm.$nextTick()
    expect(w.find('[data-testid="photo-overlay"]').exists()).toBe(false)
    expect(other).not.toHaveBeenCalled()
    document.removeEventListener("keydown", other)
    w.unmount()
  })
})

describe("PhotoGrid — Show masks", () => {
  const masked = [
    { filename: "0001.jpg", url: "https://s3/full/0001.jpg", thumb_url: "https://s3/thumbs/0001.jpg", mask_url: "https://s3/masks/0001.jpg" },
    { filename: "0002.jpg", url: "https://s3/full/0002.jpg", thumb_url: "https://s3/thumbs/0002.jpg", mask_url: null },
  ]

  it("offers no toggle when no photo has a mask", () => {
    const w = mount(PhotoGrid, { props: { photos } })
    expect(w.find('[data-testid="show-masks"]').exists()).toBe(false)
  })

  it("toggle swaps tiles with a mask to the overlay and leaves the rest", async () => {
    const w = mount(PhotoGrid, { props: { photos: masked } })
    const toggle = w.find('[data-testid="show-masks"]')
    expect(toggle.exists()).toBe(true)
    expect((toggle.element as HTMLInputElement).checked).toBe(false)
    await toggle.setValue(true)
    const imgs = w.findAll('[data-testid="photo-tile"] img')
    expect(imgs[0].attributes("src")).toBe("https://s3/masks/0001.jpg")
    expect(imgs[1].attributes("src")).toBe("https://s3/thumbs/0002.jpg")
    await toggle.setValue(false)
    expect(w.findAll('[data-testid="photo-tile"] img')[0].attributes("src")).toBe("https://s3/thumbs/0001.jpg")
  })
})
