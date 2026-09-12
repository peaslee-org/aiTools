# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Local dev:**
```bash
cp .env.example .env.local    # fill in values
npm install
npm run dev                    # Vite dev server on http://localhost:5173
```

**Tests (vitest + @vue/test-utils, jsdom):**
```bash
npm run test                   # vitest run — specs live in src/**/__tests__/*.spec.ts
```

**Type check:**
```bash
npm run type-check             # vue-tsc --noEmit — KNOWN GAP: checks nothing (root tsconfig is
                               # solution-style); use: npx vue-tsc -p tsconfig.app.json --noEmit
                               # (four pre-existing errors in components/transcribe/*.vue surface)
```

**Lint:**
```bash
npm run lint
```

**Production build (for S3):**
```bash
npm run build                  # outputs to dist/
```

**Preview production build locally:**
```bash
npm run preview                # serves dist/ on http://localhost:4173
```

Tests cover the photogrammetry API client and store, `PhotoGrid`, `MeshViewer`, `ScanDetailView`,
`GpuStatusBar`, the `gpu` store and `lib/workerState.ts` (58 specs as of 2026-09-02). `vitest.config.ts`
mirrors the `@` alias and the `model-viewer` custom-element rule from `vite.config.ts`; the
`@google/model-viewer` module is mocked in tests (jsdom has no WebGL).

## Local dev auth bypass

`VITE_DEV_AUTH_BYPASS=true` in `.env.local` (honoured only by a Vite **dev** build — `import.meta.env.DEV`) makes `auth.isAuthenticated` true with no token, so the router guard passes, `login()` is a no-op, `logout()` just reloads `/`, and axios sends no `Authorization` header. Pair it with `DEV_AUTH_BYPASS=true` + `ENVIRONMENT=dev` in `chat-api/.env`, which accepts header-less requests as `DEV_AUTH_USER_SUB`. `isAdmin` stays false under bypass.

## Which mock to use

- **Full local stack** (default for feature work): `chat-api` running with `DEV_AUTH_BYPASS=true`,
  `USE_MOCK_TRANSCRIPTION=true`, `USE_MOCK_PHOTOGRAMMETRY=true` and a pgvector Postgres on 5433
  (see `chat-api/CLAUDE.md`), plus `VITE_DEV_AUTH_BYPASS=true` here. The API's photogrammetry mock
  walks a job through the stages, serves a placeholder GLB, seeds the bundled sample photos and
  runs the real thumbnail code. Open the app on **`http://localhost:5173`** — `127.0.0.1` is not
  in the API's `CORS_ORIGINS`, and `<model-viewer>` fetches the GLB with CORS (thumbnails still
  load because `<img>` isn't CORS-gated, which makes the mismatch confusing).
- **Traffic replay** (below): no backend at all, replays recorded responses through MSW. Good for
  layout work against production-shaped data; nothing progresses and uploads are stubbed.

## Mock API (traffic capture + replay)

**Record traffic from any deployed build:**
```js
// In browser console on production (or any build):
localStorage.setItem('__recordTraffic', 'true')
location.reload()

window.__traffic.count()   // how many entries captured
window.__traffic.export()  // downloads traffic.json
window.__traffic.clear()   // reset
localStorage.removeItem('__recordTraffic'); location.reload()  // stop recording
```

**Replay in dev:**
1. Copy downloaded `traffic.json` → `src/mocks/traffic.json` (gitignored)
2. Add to `.env.local`: `VITE_MOCK_API=true`
3. `npm run dev` — MSW intercepts matched requests; unmatched (e.g. Cognito) pass through

**Key files:**
- `src/lib/trafficRecorder.ts` — axios interceptor, localStorage store, console helpers
- `src/mocks/traffic.json` — paste export here (gitignored)
- `src/mocks/handlers.ts` — converts traffic.json → MSW handlers (deduplicates by method+path, last capture wins)
- `src/mocks/browser.ts` — MSW worker setup
- `public/mockServiceWorker.js` — MSW service worker (committed, required at runtime)
- `vite.config.ts` — `mock-traffic` plugin provides `virtual:mock-traffic`; returns `[]` if file absent (safe for CI)

## Architecture

Layered: **router (Vue Router) → views → components → stores (Pinia) → lib/axios → backend API**

```
src/
  main.ts              App entry: creates Vue app, installs Pinia + Router
  App.vue              Root component — just <RouterView>
  env.d.ts             Vite env var type augmentation
  types/index.ts       Shared TypeScript interfaces (mirrors backend Pydantic schemas);
                       includes chat types (Conversation, Message, Model) and transcribe
                       types (SpeakerProfile, SpeakerSample, TranscriptionJob, TranscriptResponse, etc.)
  config/cognito.ts    Cognito OAuth URL builders from VITE_ env vars
  config/models.ts     Static Bedrock model list (AVAILABLE_MODELS) and DEFAULT_MODEL_ID
  lib/
    pkce.ts            PKCE code_verifier + code_challenge generation (WebCrypto)
    prepareImage.ts    Phone photo → upright, long-edge-capped (2400 px) JPEG with the original's
                       EXIF spliced back on, so COLMAP keeps its focal prior. rotationFor() decides
                       rotation by comparing the JPEG frame header's stored size with what the
                       decoder returned, rather than trusting Safari to honour EXIF
    exifJpeg.ts        Pure JPEG byte surgery behind it: extractApp1 / readOrientation /
                       readStoredSize / setOrientation / insertApp1 (DOM-free, so it unit-tests)
    axios.ts           Axios instance with auth interceptor and 401 handler
    transcribeApi.ts   Transcribe feature API calls (speakers, samples, jobs, transcripts)
    photogrammetryApi.ts   Photogrammetry API calls (jobs, uploads, mesh URLs, job photos, sample photos)
    publicApi.ts       Public API calls (unauthenticated: showcase, public conversations/scans/transcripts)
    gpuApi.ts          GET /gpu/state, POST /gpu/warm, GET /gpu/usage (all take `family`)
    jobQuery.ts        useJobDeepLink(open): `?job=<id>` deep link for both views — a watch (the Startups
                       links are same-route navigations, so onMounted never sees them) plus a check()
                       for the cold load; the param is stripped once consumed
    workerState.ts     workerStateLabel() ("GPU ready" / "GPU starting · ~N min left" / "GPU off — starts
                       on your next job"), elapsedLabel() m:ss, durationLabel() "6m20s"
    trafficRecorder.ts Axios interceptor for recording/replaying API traffic (mock dev)
  stores/
    auth.ts            Authentication state: token, login(), handleCallback(), logout()
    chat.ts            Conversation list + active thread, sendMessage(), deleteConversation()
    models.ts          Fetches available models from GET /api/v1/models; falls back to
                       static AVAILABLE_MODELS list on error
    transcribe.ts      Speaker profiles + samples + transcription jobs state; polling for
                       in-flight jobs (5 s interval) and processing samples (3 s interval)
    photogrammetry.ts   Scan jobs state, concurrent uploads, 3 s polling, presigned mesh URL cache,
                       per-job photo cache (fetchJobPhotos → {photos, matched, total}; force refetch;
                       a listing smaller than the job's image_count — upload still in flight — is
                       never cached),
                       fetchSamplePhotos(), selectJob()/clearSelection(), toasts
    gpu.ts             GPU worker state per family (transcription | photogrammetry): 30 s polling,
                       warm(), usage; while `starting` a 1 s clock derives elapsedSeconds /
                       remainingSeconds from starting_since + startup_estimate_seconds
    profile.ts         Current user's profile (name, email, sub) for /profile
    admin.ts           Admin users list for /admin
  router/index.ts      Routes: / (ChatView), /demo (DemoView), /transcribe, /photogrammetry, /profile, /admin
                       (requiresAdmin; AdminLayout → DashboardView, UsersView), /callback
  views/
    ChatView.vue       Main layout: sidebar + message thread
    CallbackView.vue   OAuth callback handler — exchanges code for tokens
    DemoView.vue       Public demo page: showcases public conversations, scans, and transcripts
    TranscribeView.vue Transcribe layout: resizable sidebar (RunSidebar) + detail panel (RunDetailView)
    PhotogrammetryView.vue  Scan layout: resizable ScanSidebar + ScanDetailView, GpuStatusBar
                       (family="photogrammetry") on top; owns formMode ('closed'|'blank'|'sample');
                       toast stack is absolute top-right of the body pane (same in TranscribeView)
    profile/ProfileView.vue   Name / email / user id
    admin/*            AdminLayout, DashboardView (placeholder), UsersView (user list)
  components/
    ConversationSidebar.vue              Left panel: conversation list, new/delete, logout
    MessageList.vue                      Scrollable message thread with typing indicator
    MessageBubble.vue                    Single message (user = right/indigo, assistant = left/white)
    MessageInput.vue                     Textarea + send button; shows model selector above input
                                         when isNewConversation is true; Enter to send, Shift+Enter for newline
    PublicToggle.vue                     Shared toggle to mark work public/private (emits `toggle` with flipped boolean)
    transcribe/
      GpuStatusBar.vue         Shared GPU bar (prop `family`): state dot + label with remaining/elapsed
                               while starting (title says cold/warm start and the estimate basis),
                               idle-out countdown, Warm button (transcription family only), Usage
                               panel: hours vs caps, cost, warm-ups, and a collapsed **Startups**
                               section (the family's cold/warm medians; expand for the last 5 of
                               this family's launches: job (RouterLink to `/photogrammetry?job=` or
                               `/transcribe?job=` — a scan by name, a transcript by its time) ·
                               when · kind · capacity · boot · pull · container · init · total ·
                               promised · Δ · cost; under a photogrammetry session, indented rows
                               per completed scan with billable compute ($ and ¢/photo; another
                               user's scan is anonymous), and a "Compute: median/worst/best ¢/photo"
                               summary line — the worst case is the per-photo price floor; choice in
                               localStorage "gpuStartupsOpen")
      RunSidebar.vue           Left panel: job list + new job form toggle
      RunDetailView.vue        Right panel: job detail, transcript, speaker panel; for a
                               complete/failed job, an "Input audio" row lazily fetches
                               GET /jobs/{id}/audio and renders an <audio> player + Download link
                               (re-presigned at click time via downloadJobAudio); a 404 (object
                               expired) shows "Input audio expired" instead. Sliders are seeded from
                               the loaded transcript's `settings`; `RunDetailView` shows the stored
                               `turns` until a slider differs, then the local preview; `MatchingAnalysis`
                               offers Re-compile / Reset while they differ.
      NewJobForm.vue           Audio file dropzone + job params (language, speaker count, speaker IDs);
                               "Try the sample" flips the audio + speakers sections into a read-only
                               sample-review mode in place (players for the bundled audio +
                               Barry/Jane samples, from GET /samples) — "Start transcription"
                               submits (submitSampleJob, unchanged), "Use my own audio instead"
                               returns to the normal form
      AudioFileDropzone.vue    Drag-and-drop / click-to-upload audio file picker
      TranscribeJobCard.vue    Job summary card in the sidebar list
      JobStatusBadge.vue       Status chip (pending / transcribing / matching / complete / failed)
      JobPanel.vue             Job metadata + activity log for the selected job
      TranscriptDisplay.vue    Rendered transcript segments with speaker labels
      SpeakerPanel.vue         Speaker profile management panel
      SpeakerProfileCard.vue   Expandable card: speaker name, samples list, upload
      SpeakerSampleRow.vue     Single sample row with status and delete action; a `ready` sample
                               gets a Play toggle that lazily fetches GET .../samples/{sid}/audio
                               and renders an <audio> player + Download link (re-presigned at click
                               time via downloadSample) beneath the row
      SampleStatusBadge.vue    Status chip (processing / ready / failed) for a sample
    photogrammetry/
      ScanSidebar.vue          Left panel: job list + "New scan" (formMode blank) / "Sample" (formMode
                               sample) buttons — Sample no longer submits directly
      ScanJobCard.vue          Job summary card; ⚠ glyph with the job's warnings as its title
      ScanStatusBadge.vue      Status chip (pending / queued / processing · <stage> / complete /
                               failed); while in flight and the worker isn't running it shows the
                               GPU label with the remaining wait
      StageStrip.vue           Four-step strip (Cameras (SfM) · Dense cloud · Mesh · Texture)
      ImageDropzone.vue        Multi-file drag/drop image picker (5–150 photos) with local thumbnails;
                               names any file it could not accept instead of dropping it silently
      NewScanForm.vue          Name + dropzone + Start scan (uploadProgress); prop `sample` = read-only
                               sample mode: name locked, PhotoGrid of the bundled set from
                               GET /samples, "Use my own photos instead", Start runs POST /jobs/sample
      newScanMode.ts           NewScanMode = 'closed' | 'blank' | 'sample'
      PhotoGrid.vue            Dense lazy thumbnail grid (thumb_url; null while the API is still
                               generating a thumbnail — the tile stays a spinner with no <img>, and
                               ScanDetailView force-refetches every 5 s, capped ≈5 min, until all
                               thumbs arrive); tiles keyed by filename; per-tile skeleton+spinner
                               until load, ✕ tile on error; status line "Preparing thumbnails…" →
                               "Loading photos… n of N" → "N photos[ · M matched]"; tiles marked ✓ /
                               "not matched" / "skipped" from photo.status; click → overlay with the
                               original, ‹ › chevrons outside the image, ←/→/Esc (capture-phase
                               listener), caption "0007.jpg · 7 / 22"
      ScanDetailView.vue       Header (name, badge, photo count, gpu notice, 3D | Photos toggle,
                               Download GLB / preview, ✕ Close scan) + body: form / stage strip +
                               preview + PhotoGrid while running / MeshViewer or PhotoGrid when
                               complete / error. Pane choice remembered per job; photos refetched on
                               complete/failed; Esc clears the selection when no overlay is open
      MeshViewer.vue           <model-viewer> (custom element per vite.config.ts) with a top-left pill:
                               "Loading mesh… NN%" from progress events (also while `pending`, i.e.
                               resolving the presigned URL), hidden on load, "Couldn't load the mesh"
                               on error; state resets when `src` changes. A plain <script> block sets
                               ModelViewerElement.meshoptDecoderLocation to the bundled UMD meshopt
                               decoder (worker GLBs use EXT_meshopt_compression; the
                               `meshopt-decoder.cjs` alias in vite.config.ts bypasses meshoptimizer's
                               require-only export condition — model-viewer loads it as a classic
                               script and reads the global, so the ESM build won't work)
```

All imports use the `@` alias which maps to `src/`.

## Auth Flow

1. Router guard detects unauthenticated → redirects to `/demo`
2. `/demo` is the public demo page (no login required); signed-out visitors see a "Sign in" button that calls `auth.login()`
3. `login()` generates PKCE verifier/challenge, stores verifier in `sessionStorage`, redirects to Cognito Hosted UI
4. Cognito redirects to `/callback?code=...`
5. `CallbackView` calls `auth.handleCallback(code)` → POST to Cognito `/oauth2/token`
6. `id_token` stored in `localStorage`; user redirected to `/`
7. All API calls via `apiClient` (lib/axios.ts) include `Authorization: Bearer <id_token>`
8. On logout: `localStorage` cleared, browser redirected to Cognito `/logout`

The logout URL is derived from `VITE_COGNITO_REDIRECT_URI` by stripping `/callback` to get the app root.

All routes except `/demo` and `/callback` require auth; `/admin` additionally requires `isAdmin`. The guard redirects unauthenticated users to `/demo` instead of forcing login immediately.

## Environment Variables

All env vars use the `VITE_` prefix (required by Vite for client-side exposure).

| Variable | Example |
|---|---|
| `VITE_API_BASE_URL` | `https://api.example.com` |
| `VITE_COGNITO_DOMAIN` | `myapp.auth.us-east-1.amazoncognito.com` |
| `VITE_COGNITO_CLIENT_ID` | `abc123xyz` |
| `VITE_COGNITO_REDIRECT_URI` | `https://app.example.com/callback` |
| `VITE_COGNITO_SCOPE` | `openid email profile` |

**Local dev API connectivity:** Two options:
- Set `VITE_API_BASE_URL=http://localhost:8000` — axios calls the backend directly (default in `.env.example`)
- Set `VITE_API_BASE_URL=` (empty) — axios uses relative paths, Vite dev server proxies `/api/*` → `http://localhost:8000`

## Deployment

Push to `main` with changes under `chat-vue/` → `.github/workflows/deploy.yml` (change detection, API
first) calls the reusable `vue.yml`: `npm ci` → `npm run build` with the `VITE_*` secrets → `aws s3
sync dist/ --delete` to the frontend bucket → CloudFront invalidation. Manual redeploy without a code
change: `gh workflow run Deploy -f vue=true`. See `docs/runbooks/deploy.md`.

Manual fallback:
```bash
npm run build
aws s3 sync dist/ s3://<frontend-bucket> --delete
aws cloudfront create-invalidation --distribution-id <distribution-id> --paths "/*"
```

CloudFront must serve `index.html` for all 403/404 responses (required for SPA routing — especially for the `/callback` redirect).

## Backend

The chat-api lives in `../chat-api` (see its `CLAUDE.md` for the full list). Key endpoints:

**Chat:**

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/models` | List available Bedrock models |
| POST | `/api/v1/chat` | `{conversation_id?, message, model_id?}` → `{conversation_id, reply}`; `model_id` only used when creating a new conversation |
| GET | `/api/v1/conversations` | List conversations for current user |
| GET | `/api/v1/conversations/{id}/messages` | Fetch message history for a conversation |
| PATCH | `/api/v1/conversations/{id}` | `{is_public: bool}` — owner-only, toggle public visibility; → updated ConversationOut |
| DELETE | `/api/v1/conversations/{id}` | 204 |

**Transcribe (`/api/v1/transcribe/`):**

| Method | Path | Notes |
|---|---|---|
| POST | `/speakers` | Create speaker profile; `{speaker_name?}` → `SpeakerProfile` |
| GET | `/speakers` | Paginated list; `?cursor&limit` → `{items, next_cursor}` |
| GET | `/speakers/{id}` | Get speaker with samples |
| PATCH | `/speakers/{id}` | Rename; `{speaker_name}` |
| DELETE | `/speakers/{id}` | 204 |
| POST | `/speakers/{id}/samples` | Initiate upload → `{sample_id, upload_url}` |
| POST | `/speakers/{id}/samples/{sid}/confirm` | Confirm S3 upload → `SpeakerSample` with actual status |
| GET | `/speakers/{id}/samples/{sid}/audio` | Presigned playback/download URLs for the sample → `{url, download_url, filename, expires_at}`; any sample status may be fetched, the SPA only offers it for `ready` |
| DELETE | `/speakers/{id}/samples/{sid}` | 204 |
| GET | `/samples` | Bundled sample bundle: `{name, audio: {filename, url}, speakers: [{speaker_name, url}]}` — presigned; what NewJobForm's sample-review mode plays |
| POST | `/jobs` | Create job; `{speaker_count_hint?, speaker_ids?, language?}` → `{job_id, upload_url}` |
| POST | `/jobs/{id}/confirm` | Confirm audio uploaded; transitions job → `transcribing` |
| POST | `/jobs/{id}/rerun` | Rerun a completed/failed job — new job row reusing its audio + params; 404 if the source job or its audio is gone → job status (JobStatusResponse) |
| GET | `/jobs` | Paginated list; `?cursor&limit` → `{items, next_cursor}` |
| GET | `/jobs/{id}` | Get job status (`TranscriptionJob`) |
| PATCH | `/jobs/{id}` | `{is_public: bool}` — owner-only, toggle public visibility; → updated job status (JobStatusResponse) |
| GET | `/jobs/{id}/transcript` | Get transcript → `{segments, transcript_url}` |
| GET | `/jobs/{id}/audio` | Presigned playback/download URLs for the job's raw input audio → `{url, download_url, filename, expires_at}`; 404 if the job has no audio or the object has expired from the bucket |
| DELETE | `/jobs/{id}` | 204 |

**Transcribe upload flow (two-phase S3 presigned PUT):**
1. Call POST to create job/sample → get `upload_url`
2. PUT file directly to S3 using the presigned URL (no auth header — use plain `fetch`, not `apiClient`)
3. Call confirm endpoint → backend transitions status and begins processing

**Photogrammetry (`/api/v1/photogrammetry/`):**

| Method | Path | Notes |
|---|---|---|
| POST | `/jobs` | `{name, filenames[]}` → `{job_id, uploads[{filename,key,url}]}` (5–150 photos) |
| POST | `/jobs/{id}/confirm` | Confirm photos uploaded; transitions job → `queued` |
| GET | `/jobs` | Paginated list; `?cursor&limit` → `{items, next_cursor}` |
| GET | `/jobs/{id}` | Job status incl. `stage`, `warnings[]`, `preview_url`, `worker_state`, `estimated_wait_seconds`, `gpu_notice` |
| PATCH | `/jobs/{id}` | `{is_public: bool}` — owner-only, toggle public visibility; → updated job status (JobStatusResponse) |
| DELETE | `/jobs/{id}` | 204 |
| POST | `/jobs/sample` | Create a job over the bundled sample photo set (server-side, no upload) |
| GET | `/samples` | `{name, image_count, photos[{filename,url,thumb_url}]}` — what sample mode shows |
| GET | `/jobs/{id}/photos` | `{photos[{filename,url,thumb_url,status}], matched, total}` — thumbnails are made on first request; `status` is registered / unregistered / skipped:<reason> once SfM has run |
| GET | `/jobs/{id}/mesh` | `{url, download_url, preview_download_url, expires_at}` — presigned GLB (viewer) + attachment URLs |

**Public (`/api/v1/public/`, no auth required):**

| Method | Path | Notes |
|---|---|---|
| GET | `/showcase` | List public conversations, transcriptions, and scans |
| GET | `/conversations/{conversation_id}` | Get a public conversation with messages (read-only) |
| GET | `/transcriptions/{job_id}` | Get a public transcription job (read-only) |
| GET | `/photogrammetry/{job_id}` | Get a public photogrammetry job (read-only) |

**GPU (`/api/v1/gpu/`):** `GET /state` → `{worker_state, estimated_wait_seconds (remaining while
starting), starting_since, startup_estimate_seconds, estimate_basis, estimate_samples, start_kind,
warm_until, notice}`; `POST /warm`; `GET /usage` → hours/caps/cost, `sessions[]` with
`kind`, `stages`, `estimated_startup_seconds`, `actual_startup_seconds`, and the cold/warm medians.

## Notes

- No Dockerfile — static site deployed to S3 + CloudFront
- Tailwind CSS 3 with PostCSS + autoprefixer
- `vue-tsc` runs as part of `npm run build` — type errors block the build
- PKCE flow uses native `window.crypto.subtle` — no external auth SDK
- Switching conversations calls `GET /api/v1/conversations/{id}/messages` and merges the result into the store; previously loaded messages are preserved in-memory across switches
- Add `http://localhost:5173/callback` to Cognito App Client allowed callback URLs for local dev
- Set `CORS_ORIGINS` in chat-api to include the CloudFront domain in production
- Transcribe sidebar width is persisted to `localStorage` under `transcribeSidebarWidth`
- Transcribe job polling uses 5 s intervals; sample status polling uses 3 s intervals; both resume on page reload via `resumePollingForActiveJobs()` / `resumePollingForProcessingSamples()`; polling has a cutoff so jobs that stay in a terminal state stop being polled
- Job statuses: `pending → transcribing → matching → complete | failed`; `partial_transcript_available` may be true before `complete`
- Sample statuses: `processing → ready | failed`; `SpeakerSample.error_message` holds the worker error reason when `failed`
- When sample polling detects a `processing → failed` transition, `transcribe` store pushes a toast (auto-dismisses after 8 s); both `TranscribeView` and `PhotogrammetryView` render their store's toasts as an `absolute right-4 top-4` stack inside the body pane (not a viewport `Teleport`); stores export `toasts` and `dismissToast`
- S3 uploads must use plain `fetch` (not `apiClient`) — no Authorization header allowed on presigned PUT requests
- Scan sidebar width is persisted under `scanSidebarWidth`; job polling every 3 s (`VITE_PHOTOGRAMMETRY_POLL_INTERVAL_MS`), 60 s while the GPU worker is off; resumes on reload via `resumePollingForActiveJobs()`
- `GpuStatusBar` takes `family`: `/transcribe` shows the transcription worker (with the Warm button), `/photogrammetry` the photogrammetry worker (no Warm — a scan launches it). With the local mocks (`GPU_CONTROLLER_ENABLED=false`) it shows "off" and the estimate basis is `default`.
- Photogrammetry toasts: scan/sample failures, a job's new `warnings` as they appear, and `failed` transitions. The scan page also shows the warnings list above the body and ⚠ on the card.
