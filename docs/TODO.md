# TODO / backlog

Grouped by **what a change forces you to rebuild or redeploy**, so items can be batched. Worker-image
items cost an ECR push + task-def revision (automatic on push) and, to keep cold starts from pulling,
an AMI re-bake (`scripts/deploy/build-gpu-ami.sh`); API items an ECS deploy; Vue items a CloudFront
deploy; infra items a Terraform apply. `deploy.yml` orders the first three; see
`docs/runbooks/deploy.md`.

## Worker image (batch these — rebake the GPU AMI once)

- [ ] **Turntable sets: sequential matching.** The masked 76-frame cat scan (2026-09-06, job
  `7130f4af`) spent 8 min 7 s of its 19.5 min in `exhaustive_matcher` (2 850 pairs) while the
  dense stage took 47 s. A turntable sequence only needs neighbours + loop closure
  (`sequential_matcher --SequentialMatching.loop_detection 1` with a vocab tree, or a window of
  ~10). Gate it on the `remove_background` flag or a future rig profile.
- [ ] **Decimate before texturing, not only for the viewer.** Same scan: `TextureMesh` took
  6 min 22 s on 855 k faces for a hand-sized toy; texturing scales with faces. A per-scan face
  target (~200 k for small objects) before `TextureMesh` would take most of that off and shrink
  the GLB. Related: refine is skipped above 400 k faces, so small objects never get it.
- [ ] **RefineMesh runaway on sparse wide scenes.** Harbor-view scan (2026-09-06, job `7d121b76`,
  65 registered, unmasked): densify 48 s → only 45 782 points, ReconstructMesh 56 k faces in 3 s,
  then `RefineMesh` **28 min** to reach 117 k faces; the whole job was ~45 min. The refine gate
  (≤ 100 images, ≤ 400 k faces) lets a low-density scene through. Gate on dense-point count or
  cap refine's `--max-face-area`/iterations; also `feature_extractor` 4.5 min and
  `image_undistorter` 6 min suggest large phone frames — consider `--ImageReader` downscale.
- [ ] Background removal follow-ups (spec 2026-09-04): saved rig profile (camera settings from
  the rig's `session.json`, a static-region SfM mask for rigs where the room is in frame);
  re-running a job with the flag flipped from the same uploads; BiRefNet on the GPU host if
  u2netp proves weak on other objects (untestable on the 8 GB fitlet); geometric clean-up of the
  wire stand and its shadow after meshing (ROI / plane cut).
- [ ] GPU AMI re-bake is optional after `721860c`: the worker image gained ~100 MB (onnxruntime,
  opencv-python-headless, u2netp.onnx); cold starts pull those layers (~5 min) until a re-bake.
- [ ] **Immediate release leaves the message invisible for up to `SQS_VISIBILITY_TIMEOUT` (600 s).**
  Only `SpotWatcher` calls `change_message_visibility(0)`; the shell's `except Interrupted` should
  do the same when `ReleaseWatcher.abort` is set so the next worker gets the job at once.
- [ ] **GPU idle-release countdown that survives polling.** Today `warm_until` is only in the
  *Warm* response; the 30 s `/gpu/state` poll returns `null`, so the bar's `idle-out in mm:ss`
  vanishes, and a job-launched worker never has one. Real release time is
  `max(last_work + IDLE_EXIT_SECONDS, warm_until)`, known only to the worker. Plan: add
  `gpu_sessions.idle_release_at`, written on each heartbeat; `get_state()` returns
  `max(idle_release_at, warm_until)` as `warm_until`; bar keeps the label across polls.
- [ ] **Gravity-align the reconstruction.** After `27a23f1` orientation follows the first photo's roll
  (portrait vs landscape shots come out sideways). Options: COLMAP `model_orientation_aligner`
  (Manhattan-world; may fail on organic subjects), or a rotate control in `MeshViewer.vue`.
- [ ] Headless mesh-render `preview.png` (pyrender + EGL) — replaces the first-photo preview
  (worker spec decision 4).
- [ ] Mixed cameras: `--ImageReader.single_camera_per_folder` with one folder per pixel size
  instead of skipping.
- [ ] `photogrammetry-worker/tests/test_handler.py` takes minutes instead of seconds when the
  handler's generic-exception path is hit repeatedly (seen 2026-08-29 while a test fixture was
  broken) — probably a retry/cleanup branch sleeping; find and cap it.

## API (chat-api)

- [ ] **Per-session GPU rate stamping** (designed 2026-08-31, approved — next session's work).
  Historical costs must never change when prices do: resolve the rate at time of use and stamp it.
  `gpu_sessions` gains `instance_type`, `market` (spot|on-demand) and `hourly_rate_usd` — filled
  where the controller already touches the open session for ECS pull timings (one
  `DescribeInstances` on the known `instance_id`). Rates resolve from a `GPU_RATES` JSON env in
  terraform (versioned in git; e.g. `{"g4dn.xlarge": {"on-demand": 0.526, "spot": 0.21}}`);
  unknown type/market falls back to `GPU_HOURLY_RATE_USD`, which demotes to fallback-only. All
  usage-panel math (session `cost_usd`, per-job billable, $/photo summary) switches to the
  session's stamped rate, so mixed g4dn/g6 pools compare honestly; unstamped pre-migration rows
  fall back to the current setting. Edge, accepted: a spot-interrupted job resuming on another
  instance type bills at the completing session's rate. Not live AWS Pricing API — spot floats
  hourly and only Cost Explorer (already in the panel) knows actuals; the table + stamp is the
  estimator, CE the audit. Supersedes flatly bumping `GPU_HOURLY_RATE_USD` to 0.526.

- [ ] **Transcribe confirm: SQS publish failure after the status commit strands the job.**
  `confirm_job_upload` commits `status="transcribing"` before `publish_transcription_job`; if the
  publish raises, the row sits in `transcribing` forever with no queue entry and the SPA has no
  re-confirm affordance. Publish before commit, or add a sweep/retry. Flagged in the 2026-09-02
  rerun review (reruns create such a row the user never asked to exist).
- [ ] **`POST /gpu/release` 200 means "flag written", not "worker acknowledged"** — a worker on an image
  without the watcher never reads it. Either return the session's `release_requested_at`/`ended_at`
  on a follow-up `GET /gpu/state`, or have the controller fall back to `StopTask` when the row is
  still open after N seconds.
- [ ] `repositories/transcription.py` `list_jobs` / `list_speakers`: keyset cursor is built from
  the popped overflow row, so page 2 skips one item. Fixed in `repositories/photogrammetry.py`
  (cursor from the last *returned* row); port the fix + test.
- [ ] `pending` photogrammetry jobs never expire and count against `MAX_CONCURRENT_JOBS`
  (presigned PUTs live 15 min). Exclude `pending` older than the URL TTL from
  `count_active_jobs`, or sweep. Same precedent in transcribe.
- [ ] Malformed `cursor` → 500 (both repositories); return 422.
- [ ] Mock walk (`LocalPhotogrammetryService`) has no `failed` transition; a mid-walk exception
  leaves the job `processing` (transcribe mock does the same). The mock also never writes
  `photo_status`, so the matched/not-matched tiles can't be seen locally.
- [ ] Photogrammetry rows outlive their S3 objects (30-day lifecycle on `photogrammetry/`): mark
  rows `expired` (or split the output prefix — a contract change) so the viewer doesn't get a 404.

## Vue (chat-vue)

- [ ] **Transcribe "Try the sample" still submits blind** (silently a no-op while a sample job is
  pending). The Scan tab's Sample now opens a preloaded form; give Transcribe the same treatment.
- [ ] Sidebar placeholder shows "0 photos" for a few seconds after starting a sample scan (the
  placeholder row doesn't know the count until the first poll) — pass the sample's `image_count`.
- [ ] Photogrammetry store: no polling cutoff for a job stuck in `pending`; sibling uploads are
  not cancelled when one fails; dropzone not keyboard-focusable; dragover flicker over children.
- [ ] Delete "✕" in `RunSidebar` / `ScanJobCard` is a span inside a button (not keyboard-operable).
- [ ] `npm run type-check` and the `vue-tsc` step of `npm run build` check nothing — the root
  `tsconfig.json` is solution-style (`files: []`) and neither passes `-p tsconfig.app.json`/`-b`.
  Make the script `vue-tsc -p tsconfig.app.json --noEmit` (four pre-existing errors in
  `transcribe/*.vue` will surface). `npm run test` (vitest) exists since 2026-08-29.

## CI

- [ ] When the API job of a Deploy run fails, the surfaces behind it are skipped and a code-only fix
  redeploys only what changed — the skipped ones need `gh workflow run Deploy -f vue=true`
  (runbook). Consider a "redeploy what the last failed run skipped" dispatch input, or making the
  `changes` job compare against the last *successful* deploy's sha instead of `before`.

## Infra (terraform)

- [ ] **ECS managed scaling launches 2 instances for 1 task** from zero (observed every cold start
  2026-08-26 → 29); the spare idles ~10 min until scale-in. Check the capacity provider's
  `minimum_scaling_step_size` / target and the estimate with mixed instance types.
- [ ] **~15 min scale-in lag after the worker exits** (ECS managed scale-in = 15 low datapoints;
  exit 16:38 → ASG 0 at ~16:55 on 2026-08-29). Options: worker terminates its own instance on exit
  (`ec2:TerminateInstances` scoped by tag, `InstanceInitiatedShutdownBehavior`), or a custom
  scale-in alarm on `CapacityProviderReservation` with fewer datapoints. Note this window is also
  what makes a **warm** start possible (measured since PR #13) — shortening it trades warm starts for cost.
- [ ] `gpu_on_demand_percentage` back to 0 once spot placement scores recover.
- [ ] **`scripts/deploy/migrate.sh` is dead**: it execs `uv run alembic …`, but the runtime stage of
  `chat-api/Dockerfile` never copies `uv`. Migrations run from `scripts/entrypoint.sh` at container
  start, so the script is only for a manual re-run. Fix: `CMD="/app/.venv/bin/python -m alembic -c
  /app/app/db/alembic.ini upgrade head"`, and say in the header that the entrypoint already does this.
- [ ] Photogrammetry: `gpu_max_size` 2 — two g4dn.xlarge is exactly the account's current
  On-Demand G/VT quota; a third family needs a quota increase.

## Local dev

- [ ] `chat-api/docker-compose.yml` uses `postgres:16-alpine`, which lacks pgvector — a clean
  `docker compose up` + `alembic upgrade head` fails on the speaker-embedding migration. Switch
  to `pgvector/pgvector:pg16`. (Until then: `docker run -d --name chatapi-pg-dev -p 5433:5432 -e
  POSTGRES_PASSWORD=… -e POSTGRES_DB=chatapi pgvector/pgvector:pg16`, which is what `.env` points at.)
- [ ] Presigned PUT TTL (15 min) is tight for 150 photos on a slow link; consider 30–60 min for
  the PUT side only.
- [ ] `chat-api/.env` pins `GPU_WAIT_ESTIMATE_*` to the old 120/180 s; drop them so local dev uses
  the code defaults (420 / 90).

## Recently done (2026-08-28 → 09-06)

- 2026-09-06 **Background removal for turntable scans** (API migration `w3x4y5z6a7b8` + worker
  image `721860c` / task-def `:24` + Vue). Per-scan **Remove background** checkbox; the worker
  keeps the dotted backdrop for SfM and, inside the dense stage, runs u2netp (CPU, onnxruntime,
  4.5 MB model baked into the image) over the *undistorted* images → `<stem>.mask.png` for
  `DensifyPointCloud --mask-path … --ignore-mask-label 0`; a mask outside 1–90 % coverage runs
  unmasked with one warning; 640 px overlay previews go to `…/<job>/masks/` and the Photos pane
  gets a **Show masks** toggle. Verified in prod on the 76-frame cat set: all 76 masked, dense
  stage 47 s, no backdrop in the mesh. Spec
  `docs/superpowers/specs/2026-09-04-photogrammetry-background-removal-design.md`.

- 2026-08-31 **Cost per job in the usage panel** (API migration `t0u1v2w3x4y5` + worker + Vue).
  Two numbers with different jobs: a session's `cost_usd` (wall clock × `GPU_HOURLY_RATE_USD` —
  what a launch costs) and per completed scan the **billable compute** (worker claim → complete,
  startup excluded — what one would bill): the worker stamps
  `photogrammetry_jobs.processing_started_at` at first claim (kept across resumes), `/gpu/usage`
  matches completed scans into their session's window and adds a $/photo summary over the last 20
  scans (median/worst/best — worst is the floor for a per-photo price). Another user's scan shows
  cost but not identity (same rule as the job link). Startups table: Cost column + indented
  billable-job rows + the compute summary line. **Note:** `GPU_HOURLY_RATE_USD` is still 0.20
  (spot-era) while the pool runs on-demand g4dn.xlarge ($0.526/h) — set it honestly or every
  figure reads 2.6× low; the panel prints the rate it used.

- 2026-08-31 **Thumbnails made asynchronous** (API + Vue) after the predicted "if it bites" bit:
  the first `/photos` for a 147-photo scan generated thumbnails synchronously for 2 m 28 s —
  CloudFront's `/api/*` origin timeout is 30 s (default, unconfigured) → 504 → "Could not load
  the photos", while uvicorn logged nothing (client-disconnect cancels the handler's log line;
  the orphaned thread finished the thumbs anyway). Now: generation is kicked in the background at
  `confirm` and by any listing that finds thumbs missing (one task per prefix,
  `ensure_thumbnails` skips existing so re-kicks are cheap); `/photos` answers immediately with
  `thumb_url: null` for not-yet-generated thumbs; PhotoGrid keeps those tiles as spinners (keyed
  by filename now) and ScanDetailView force-refetches every 5 s (capped ≈5 min) until they
  arrive. Also fixed on the way: the Photos pane's fetch fired 0.5 s after job creation (before
  any presigned PUT landed) and **cached the near-empty list for the session** — the store no
  longer caches a listing smaller than the job's `image_count`.

- 2026-08-31 **GLB meshopt compression, FACE_BUDGET 500 k → 1 M** (worker image + Vue). The
  published GLB is packed with `gltfpack -cc` (pinned meshoptimizer v1.2, built in the Dockerfile's
  build stage): KHR_mesh_quantization + EXT_meshopt_compression, textures pass through. Sample
  GLB 4.70 → 1.20 MB (geometry ~7×); gltfpack failure ships the raw GLB with a warning instead of
  failing the job. Viewer: `ModelViewerElement.meshoptDecoderLocation` points at the bundled UMD
  decoder (`meshoptimizer` npm pin; `meshopt-decoder.cjs` alias in vite config — the UMD build is
  behind a require-only export condition, and it must be the UMD build because model-viewer loads
  it as a classic script and reads the global). Verified in Chrome against model-viewer 4.3.1:
  packed sample GLB decodes and renders textured. With the budget at 1 M a refined mesh
  (≤ 2 × 400 k) never decimates; the 51-photo set (~680 k) now keeps all its faces. Draco was
  rejected: needs Node/glTF-Transform in the GPU image and decodes slower.

- 2026-08-31 **OpenMVS seam leveling root-caused and fixed** (worker image, needs the next AMI
  re-bake batch). Not our build environment: stock v2.4.0's sampler refactor passes the float
  interpolation type as `cv::Mat::at`'s pixel type in `TImage::sample` (`libs/Common/Types.inl`), so
  both leveling passes read 8-bit source images as raw floats — denormals ≈ 0 with stray
  huge-exponent texels, i.e. the black faces; the raw patch copy never samples. Upstream fixed it in
  `eeedab7` (2026-02-27, after v2.4.0; no release has it), now cherry-picked as
  `photogrammetry-worker/openmvs-v2.4.0-seam-leveling.patch` and leveling re-enabled (`1 1`).
  Verified on the fitlet, sample scan, atlas sampled at all 173 k face centroids: leveling-on median
  luminance 0.7 / 97 % near-black unpatched (prod binary and a CPU-only control build agree) →
  152.3 / 0.1 % patched, matching the leveling-off baseline. The same upstream commit also fixes
  `MAXF`→`MINF` on the atlas-size growth cap and the seam-vertex row mapping.

- 2026-08-30 worker batch (one AMI re-bake): GLB atlases cropped to their used UV box, capped at
  `TEXTURE_MAX_SIZE`² pixels and embedded as JPEG q85; sample 3.28 → 2.17 MB. Inspection showed
  OpenMVS atlases were already JPEG and the bulk was unwelded geometry (3 verts/face) — vertices
  sharing position + UV are now welded (sample GLB 2.17 → 1.22 MB, 86 232 → 17 384 vertices;
  51-photo set: 45 → 40.8 MB with the crop alone → **15.9 MB** measured with the weld
  on 2026-08-30: 11.8 MB geometry of which 6.0 MB is uint32 indices, 4.1 MB atlases). Draco / quantised attributes still open if it's not enough. Photogrammetry fails
  the row on permanent S3 errors (`TRANSIENT_S3_CODES` allowlist) and declares `botocore`.
  Transcription honours `ReleaseWatcher.abort` (Transcribe wait + per-turn) → row `transcribing`,
  message redelivered.
- 2026-08-30 Usage panel Startups: 5 rows, this family only (usage now takes `family`), Job column
  linking to the scan/transcript that launched the worker (`gpu_sessions.job_id`, `?job=` deep link).

- Photogrammetry robustness batch (resumable stages, no-cycling backstop at receive 5, mesh budget,
  photo orientation) — deployed and smoked on the 51-photo set: one attempt, 14 min, no refine above
  400 k faces, decimate to 500 k, 0 mixed-dimension errors.
- `deploy.yml`: one Deploy workflow, change detection, API → Vue/workers ordering, manual dispatch.
- Scan page: sample preload, Photos view with API-made thumbnails, mesh loading pill, toasts
  top-right, overlay ‹ › + arrow keys, per-thumbnail loading state. Pillow moved to runtime deps
  after `:83` crash-looped on it. Sample-job thumbnails were briefly written inside `images/` (44
  "photos") — fixed, S3 cleaned.
- GPU startup estimate measured from the ledger (median of RunTask → first claim), promised value
  recorded per launch, Usage panel Startups section; PR #13 (merged, deployed 23:23 Z): stage
  timings from ECS, cold/warm, per-photo match status, close scan.
- Infra: scratch volume + `maxReceiveCount` 5 applied; CORS GET; admin release endpoint; mesh
  download URLs; warnings column; both worker task-defs pinned to the deployed sha in tfvars.
- Docs: deploy runbook rewritten; private docs track (`docs/private/`, gitudl + Henry) with the
  VPC map and an ops log; gitleaks denylist for real account/resource ids in the git hooks.
