# User guide

What you can do at **aitools.peaslee.org** (also reachable at chat.peaslee.org), tab by tab. Nothing here is about how it's built — for
that see the `CLAUDE.md` files.

## Signing in

If you're not signed in, you land on the **Demo** page (`/demo`) where you can browse public work. Click the **Sign in** button to open the hosted sign-in page; after signing in, you land back on the app. **Sign out** is at the bottom of the left panel on every page. **Profile** (`/profile`) shows the name, email and user id on your account.

Three tabs run along the top of the left panel: **Chat**, **Transcribe**, **Scan**.

## Demo

Visit `/demo` to see a public showcase without signing in. It displays conversations, transcriptions, and scans that owners have marked public — `/demo` itself is the shareable page; there are no separate per-item links to hand out. To mark your own work public, use the **Public** toggle on the active conversation's sidebar row, or on a transcript/scan's detail header. Marking a transcript public also publishes the enrolled speaker names shown in it, not just the transcript text. Files are kept for 30 days, then removed; download before then if you want to keep them.

## Chat

- The left panel lists your conversations; the button at the top starts a new one, ✕ deletes one.
- Pick a **Model** above the message box before the first message of a new conversation. The
  model is fixed for that conversation from then on; start a new conversation to use another.
- Type in the box and press **Enter** to send (**Shift+Enter** for a new line). The conversation
  title is taken from your first message.
- Your history is kept per conversation and reloads when you switch back.

## Transcribe

Upload a recording and get a transcript with the speakers told apart.

**Speaker profiles (optional, left side).** Add a speaker and upload a short reference sample of
their voice — **10 to 60 seconds**, MP3 / WAV / M4A. Once a sample shows **ready**, transcripts
can label that person by name instead of "Speaker 1 / Speaker 2". Click a name to rename it.

**New transcription job.**
1. **New job** → drop an audio file (MP3 / WAV / M4A).
2. Optionally set the **language** and a **speaker count hint**, and tick the speaker profiles you
   expect to hear. You can also add a speaker (with a sample) right in the form.
3. **Submit**. The file uploads, then the job runs. States: **pending → transcribing → matching →
   complete** (or **failed**). The job card and the detail panel update on their own.
4. When it's complete, the **Transcript** shows the segments with speaker labels; the audio player
   lets you listen along. **Matching analysis** shows how confidently each voice was matched to a
   profile (distances and thresholds) — useful when a label looks wrong.

The form also offers a bundled **sample** recording, so you can see a finished result without
uploading anything.

You can have **3 jobs in flight** at a time; the form tells you when to wait for one to finish.

## Scan

Turn a set of photos of one object into a textured 3D model you can spin around in the browser.

### Photos that work

- **5 to 150 photos** of a single object, JPG or PNG.
- Walk around the object taking a photo every 10–20°, keeping the whole object in frame, at a
  steady distance. **Overlap matters most**: each photo should share plenty of the scene with the
  ones before and after it. Big jumps in angle, or photos of different subjects, won't connect.
- Matte, textured, well-lit objects reconstruct best. Shiny, transparent or featureless surfaces
  (glass, chrome, plain walls) are hard.
- Use one camera and one resolution. Photos with a **different resolution** from the rest are
  skipped (you'll see a warning). Phone photos taken in portrait are fine — rotation is handled.
- Blurry or unreadable files are skipped with a warning rather than failing the scan.

The bundled **Sample** (22 photos of a crocheted cat) shows what a good set looks like — open it
with the **Sample** button and look at the thumbnails before shooting your own.

### Starting a scan

- **New scan** → give it a name (or keep the date), drop your photos, **Start scan**. The photos
  upload one by one ("Uploading 12/40"); if the page loses the connection mid-upload, start again.
  Upload links are valid for about 15 minutes.
- **Remove background** — tick it for turntable scans on a patterned backdrop. The backdrop stays
  in the photos for camera matching and is masked out before the dense reconstruction, so the
  model is the object alone and the scan runs faster. Leave it off for scenes, rooms and anything
  where the surroundings *are* the subject.
- **Sample** → the form opens preloaded with the sample photos (you can't edit that set — use
  **Use my own photos instead** to switch). **Start scan** runs it with nothing to upload.
- **3 scans in flight** at a time; more than that is refused with a message.

### While it runs

The card in the left panel and the header show the state: **pending** (uploads finishing) →
**queued** (waiting for the GPU) → **processing · Cameras (SfM) / Dense cloud / Mesh / Texture** →
**complete** or **failed**. The strip across the top of the scan page lights up each stage.

**Photos** are viewable the whole time: the scan page opens on the Photos view while the scan runs,
so you can check the set while you wait. Thumbnails load in as they're prepared ("Loading photos… 7
of 40").

**The GPU bar** (top of the page) tells you what the wait is about. The GPU that does the work is
switched off when nobody is using it, so the first scan after a quiet spell has to wait for it:

- **GPU off — starts on your next job**: nothing is running. Your scan will start it.
- **GPU starting · ~6 min left · 2:10 elapsed**: it's coming up. The estimate is measured from
  recent starts, not a guess; hover the label to see whether this is a **cold start** (a new
  machine has to boot, typically 6–7 minutes) or a **warm start** (the machine is still up from a
  recent job and only the software has to start, about a minute).
- **GPU ready · idle-out in 12:30**: it's running and will switch itself off after 15 idle minutes.
  Scans started before then skip the wait.

**Usage** (right end of the bar) opens a panel with today's and this month's GPU hours against the
caps (3 h/day, 30 h/month), the estimated cost, and **Startups** — how long recent starts actually
took compared with what was promised. It's collapsed to one line; click it to see the last five
starts broken down by stage, each linked to the scan (or transcript) it was started for. If a cap is reached, new scans wait until it resets; the header on the scan shows
the notice.

The **Transcribe** page has the same bar for its own GPU, plus a **Warm it up** button that starts
the GPU before you upload (3 warm-ups per person per day).

### The result

When a scan completes the page switches to the **3D** view:

- **Drag** to orbit, scroll to zoom. The model spins slowly on its own until you touch it.
- A small **Loading mesh… 45%** pill in the corner shows the model downloading — large scans are
  tens of MB, so give it a moment. If it reads *Couldn't load the mesh*, reload the page; if that
  persists, use the download instead.
- **Download GLB** saves the model (opens in any glTF viewer, Blender, etc.); **Download preview**
  saves a still image.
- **Photos** switches to the input photos. Click one to see it full size; **‹ ›** or the **← →**
  keys step through the set; **Esc** closes. After the scan, each thumbnail is marked: **✓** for
  photos that were matched into the model, **not matched** for those that weren't, **skipped** for
  ones that couldn't be used (hover for the reason). The line above the grid sums it up:
  *40 photos · 34 matched*. For a scan with **Remove background**, a **Show masks** toggle above
  the grid swaps each thumbnail for the mask preview (background dimmed, object outlined). If a
  mask clips part of the object, start a new scan with the box unticked.

**Warnings** appear above the result (and as ⚠ on the card, and briefly as a notice top-right).
Common ones:

- *Mesh simplified from 681,578 to about 500,000 faces to fit the viewer* — a very detailed scan
  was reduced so it loads in the browser; the shape is intact.
- *3 photos with a different resolution were skipped: 0049.jpg, …* or *… unreadable …* — see
  "Photos that work". The scan still runs on the rest.

### When a scan fails

- ***Only 6 of 28 photos could be matched — add overlap and try again*** — the software could
  only connect 6 photos into one consistent set of camera positions. Open **Photos**: the ✓ tiles
  are the ones that connected. Usually it's a run of neighbouring shots; the rest jumped too far or
  don't overlap. Reshoot with smaller steps around the object and more photos where the ✓ run ends.
- Other failures show the message from the reconstruction step. Try again — if the GPU was
  interrupted mid-scan, a retry resumes from the last completed stage rather than starting over.

### Housekeeping

- **✕** in the scan header (or **Esc**) closes the scan and returns to the empty page; the scan
  itself is kept.
- **✕** on a card in the left panel **deletes** the scan and its files (it asks first).
- Scan results and uploaded photos are kept for **30 days**, then removed automatically. Download
  the GLB if you want to keep it.
