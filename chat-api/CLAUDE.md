# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Local dev (without Docker):**
```bash
uv sync --extra dev
uv run scripts/run_local.sh      # uvicorn --reload on port 8000
```

**Local database.** `docker-compose.yml` uses `postgres:16-alpine`, which lacks pgvector, so a clean
`docker compose up` fails on the speaker-embedding migration (open item in `docs/TODO.md`). The
working setup is a pgvector container on **5433** (the machine's native PostgreSQL owns 5432), with
`.env`'s `DATABASE_URL` pointing at it:

```bash
docker run -d --name chatapi-pg-dev -p 5433:5432 -e POSTGRES_PASSWORD=<pw> -e POSTGRES_DB=chatapi pgvector/pgvector:pg16
uv run alembic -c app/db/alembic.ini upgrade head
```

With `DEV_AUTH_BYPASS=true`, `USE_MOCK_TRANSCRIPTION=true`, `USE_MOCK_PHOTOGRAMMETRY=true` and
`USE_MOCK_BEDROCK=true` in `.env` the API runs with no AWS credentials at all (see *Mock / local dev*
below and `docs/mock-api.md`). Open the SPA on `http://localhost:5173` — not `127.0.0.1` — or the
`<model-viewer>` fetch fails CORS against `CORS_ORIGINS`.

**Tests:**
```bash
uv run pytest -q                                                          # whole suite (259 on 2026-08-29); no external deps
uv run pytest tests/unit/services/test_transcription_service.py -q       # transcription service + regression tests
uv run pytest tests/unit/services/test_photogrammetry_service.py -q      # photogrammetry service + mock walk + photo listing
uv run pytest tests/unit/services/test_gpu_controller.py -q              # GPU controller: launch, caps, startup estimates, stages
uv run pytest tests/unit/services/test_thumbnails.py -q                  # thumbnail generation (real Pillow images)
uv run pytest tests/unit/api/ -q                                          # HTTP layer for every router
```

`tests/unit/test_runtime_dependencies.py` guards that Pillow stays in `[project].dependencies`:
the image installs with `uv sync --frozen --no-dev` (no extras), and a package that only lives in
the `dev` extra is missing in production (`chat-api-prod:83` crash-looped on `No module named
'PIL'`, 2026-08-29). Put runtime imports in the base dependency list.

Note: `tests/integration/` exists but contains only stubs — no integration tests are written yet.

**Lint / type-check:**
```bash
uv run ruff check .
uv run ruff format .
uv run mypy app
```

**Migrations:**
```bash
uv run alembic -c app/db/alembic.ini revision --autogenerate -m "your message"
uv run alembic -c app/db/alembic.ini upgrade head
```

Migrations run automatically at container start (`scripts/entrypoint.sh` runs `alembic upgrade
head` before gunicorn binds), so an API deploy *is* the schema deploy. Chain ids in
`app/db/migrations/versions/` (latest: `w3x4y5z6a7b8`). Deploy the API before any worker whose ORM
model reads a new column.

## Architecture

The app follows a layered pattern: **router → endpoint → service → repository/external service**.

```
app/
  main.py          FastAPI app factory; registers CORS, exception handlers, v1 router
  config.py        Pydantic Settings loaded from .env; accessed via get_settings() (lru_cache)
  dependencies.py  FastAPI DI: get_db (async session) and get_current_user (Cognito JWT / dev bypass)
  api/v1/          Versioned HTTP layer — thin, delegates to services
    endpoints/     chat.py, conversations.py, health.py, models.py
    transcribe/    jobs.py, speakers.py, dev.py (dev-upload sink), deps.py
    photogrammetry/ jobs.py (jobs, samples, photos, mesh), deps.py
    gpu/           router.py (state, warm, release, usage), deps.py
    admin/         users.py (Cognito admin group only)
    profile/       profile.py (the caller's own profile / groups)
  services/        Business logic
    chat.py        ChatService: orchestrates conversation repo + BedrockService
    conversation.py ConversationService: list/delete/get_messages
    bedrock.py     BedrockService: Bedrock invocation + model listing
    transcription_service.py  TranscriptionService (real) + LocalTranscriptionService (mock)
    photogrammetry_service.py PhotogrammetryService (real) + LocalPhotogrammetryService (mock)
    thumbnails.py  ensure_thumbnails(): 256 px JPEG thumbnails made on demand, cached in S3
    gpu_controller.py  GpuController: worker launch/caps/release, startup estimates, usage
    ecs_launcher.py    EcsLauncher (RunTask on the GPU capacity provider, ListTasks/DescribeTasks) + mock
    cost_explorer.py   Month-to-date GPU cost by cost-allocation tag (usage panel)
    audio_storage.py   S3 presigned URLs, object read/write/list, Transcribe job start (+ local/dev variants)
    sqs_publisher.py   SQS publish for transcription, embedding and photogrammetry jobs
  repositories/    Async SQLAlchemy queries only; no business logic
    conversation.py, transcription.py, photogrammetry.py, gpu.py
  models/          SQLAlchemy ORM models (inherit from app/models/base.py)
    conversation.py
    transcription.py  SpeakerProfile, SpeakerSample (pgvector embedding), TranscriptionJob, TranscriptSegment
    photogrammetry.py PhotogrammetryJob (status/stage, input_prefix, mesh/preview keys, warnings, photo_status, remove_background)
    gpu.py            GpuSession (the worker-launch ledger), GpuCostSnapshot
  schemas/         Pydantic request/response schemas (chat, conversation, models, transcription, photogrammetry, gpu)
  core/            security.py (Cognito JWKS verification), exceptions.py, logging.py
  db/              session.py (engine + sessionmaker init'd at startup), Alembic migrations
  assets/photogrammetry/  22-photo sample set + placeholder mesh for the mock
```

## Endpoints

All under `/api/v1`; every route except `health` and `public` requires a Cognito bearer token.

| Router | Routes |
|---|---|
| health | `GET /health`, `GET /health/ready` (`SELECT 1`) |
| chat | `POST /chat` |
| conversations | `GET /conversations`, `GET /conversations/{id}/messages`, `PATCH /conversations/{id}`, `DELETE /conversations/{id}` |
| models | `GET /models` (Bedrock model list) |
| profile | `GET /profile` (sub, email, groups) |
| admin | `GET /admin/users` (Cognito `admin` group) |
| public | `GET /public/showcase`, `GET /public/photogrammetry/{job_id}`, `GET /public/transcriptions/{job_id}`, `GET /public/conversations/{conversation_id}` (read-only; `is_public` rows only) |
| transcribe | `GET /transcribe/samples` (bundled sample bundle: audio + Barry/Jane voice samples, presigned), `POST /transcribe/jobs`, `POST /transcribe/jobs/sample`, `GET /transcribe/jobs`, `GET /transcribe/jobs/{id}`, `PATCH /transcribe/jobs/{id}`, `POST /transcribe/jobs/{id}/confirm`, `POST /transcribe/jobs/{id}/rerun`, `GET /transcribe/jobs/{id}/transcript`, `GET /transcribe/jobs/{id}/audio` (presigned playback/download URLs for the job's raw input audio), `GET /transcribe/jobs/{id}/turn-distances`, `POST /transcribe/jobs/{id}/compile`, `GET /transcribe/jobs/{id}/events`, `GET /transcribe/jobs/{id}/events/stream`, `DELETE /transcribe/jobs/{id}`; speakers: `POST/GET /transcribe/speakers`, `GET/PATCH/DELETE /transcribe/speakers/{id}`, `POST /transcribe/speakers/{id}/samples`, `POST /transcribe/speakers/{id}/samples/{sid}/confirm`, `GET /transcribe/speakers/{id}/samples/{sid}/audio` (presigned playback/download URLs for the sample), `DELETE …/samples/{sid}`; dev sink `PUT/GET /transcribe/dev-upload/{path}` (mock mode only) |
| photogrammetry | `GET /photogrammetry/samples` (bundled set with thumbnails), `POST /photogrammetry/jobs/sample`, `POST /photogrammetry/jobs`, `GET /photogrammetry/jobs`, `GET /photogrammetry/jobs/{id}`, `PATCH /photogrammetry/jobs/{id}`, `POST /photogrammetry/jobs/{id}/confirm`, `GET /photogrammetry/jobs/{id}/photos` (originals + thumbnails + per-photo match status), `GET /photogrammetry/jobs/{id}/mesh` (viewer URL + attachment download URLs), `DELETE /photogrammetry/jobs/{id}` |
| gpu | `GET /gpu/state`, `POST /gpu/warm`, `POST /gpu/release` (admin), `GET /gpu/usage` — all take `?family=transcription|photogrammetry` |

`POST …/compile` takes `CompileSettings` and re-compiles the stored transcript (see `services/transcript_compiler.py`); `GET …/transcript` compiles lazily with the `compile_*` defaults on first read.

**Request flow for chat:**
1. `POST /api/v1/chat` → `dependencies.get_current_user` verifies Cognito JWT (JWKS cached in-process)
2. `ChatService.handle()` — loads or creates a conversation, fetches message history, appends the user message; when creating, stores the `model_id` from the request (falls back to `BEDROCK_MODEL_ID`)
3. `BedrockService.invoke()` — calls `bedrock-runtime` synchronously via boto3 using `conversation.model_id` (falls back to `BEDROCK_MODEL_ID` for legacy rows)
4. Both user and assistant messages are persisted via `ConversationRepository`

**Request flow for transcription:**
1. `POST /api/v1/transcribe/jobs` — creates a `TranscriptionJob` (status: `pending`) and returns a presigned S3 upload URL
2. Client uploads audio directly to S3, then calls `POST /api/v1/transcribe/jobs/{id}/confirm`
3. `TranscriptionService.confirm_job_upload()` — verifies the S3 object exists, starts an AWS Transcribe job (word timestamps only — `ShowSpeakerLabels` is **not** set), publishes an SQS message with the job ID + AWS job name + optional speaker IDs, transitions status to `transcribing`, and asks the GPU controller for a worker
4. The `transcription-worker` consumes SQS, runs pyannote-audio diarization + ECAPA-TDNN speaker matching, writes segments, and updates job status to `complete` or `failed`
5. Client polls `GET /api/v1/transcribe/jobs/{id}` for status, then fetches `GET /api/v1/transcribe/jobs/{id}/transcript` when ready
6. `POST /api/v1/transcribe/jobs/{id}/rerun` — for a completed/failed job, creates a new job row copying `audio_s3_key` / `language` / `speaker_count_hint` / `speaker_ids` from the source and immediately runs it through the same confirm path (SQS publish + GPU ensure); the audio object's bucket-lifecycle expiry is checked before the new row is created, so a stale rerun 404s instead of creating an orphaned job

**Speaker profile flow:**
- `POST /api/v1/transcribe/speakers` — creates a named speaker profile
- `POST /api/v1/transcribe/speakers/{id}/samples` — returns a presigned S3 upload URL for a voice sample
- `POST /api/v1/transcribe/speakers/{id}/samples/{sid}/confirm` — validates audio (10–60 s, decodeable), transitions to `processing`, publishes SQS message to enqueue embedding generation; a worker updates status to `ready`
- Speaker IDs can be passed at job-confirm time so the worker knows which profiles to match against

**Request flow for photogrammetry (scan):**
1. `POST /api/v1/photogrammetry/jobs` with the filenames (5–150, jpg/png) — creates a `PhotogrammetryJob` (`pending`) under `photogrammetry/<user>/<job>/input/` and returns one presigned PUT per photo (`PHOTOGRAMMETRY_UPLOAD_TTL_SECONDS`, 60 min — a phone-sized
   set outlives the 15 min the transcribe default allows)
2. The browser PUTs the photos, then `POST /jobs/{id}/confirm` — verifies the objects, publishes the SQS message, asks the GPU controller for a worker; status → `queued`
3. `POST /jobs/sample` skips the upload: the job's `input_prefix` is the shared `samples/photogrammetry/images/` set (`PHOTOGRAMMETRY_SAMPLE_PREFIX`), uploaded once by hand
4. The photogrammetry worker walks `processing/sfm → dense → mesh → texture → complete` (with `remove_background`, the dense stage masks the backdrop out with u2netp and uploads mask overlays under `masks/`), writing `warnings` (photo problems, mesh simplification, unmasked photos) and `photo_status` (which photos SfM registered) on the row, `output/mesh.glb` + `output/preview.png` to S3
5. `GET /jobs/{id}` is polled every 3 s; `GET /jobs/{id}/mesh` returns a plain presigned GET for `<model-viewer>` plus attachment-disposition download URLs; `GET /jobs/{id}/photos` lists the inputs with thumbnails, per-photo status and — for a scan created with `remove_background` — `mask_url`, the worker's overlay preview presigned from `…/<job>/masks/<name>.jpg`
6. Objects under `photogrammetry/` expire after 30 days (bucket lifecycle) — rows outlive them today (`docs/TODO.md`)

**Photos and thumbnails.** `GET /jobs/{id}/photos` and `GET /samples` list the inputs
(`_input_keys`: only direct children of the prefix — nothing nested counts as a photo) and return
presigned GETs. Thumbnails are generated **in the background** (`_kick_thumbnails`: one
fire-and-forget task per thumbs prefix, kicked at `confirm` and by any listing that finds thumbs
missing) via `thumbnails.ensure_thumbnails()`, which lists the thumbs prefix once, generates only
the missing 256 px JPEGs (Pillow `draft` decode at reduced size, EXIF-upright, q80) in a bounded
thread pool, and writes them to S3. A listing never blocks on generation — a 147-photo set takes
~2.5 min, and CloudFront's `/api/*` origin timeout is 30 s (504 on 2026-08-31) — so a photo whose
thumbnail isn't stored yet has `thumb_url: null` and clients refetch (ScanDetailView polls every
5 s while nulls remain). The thumbs prefix is the **sibling** of the inputs' own directory
(`…/<job>/input/` → `…/<job>/thumbs/`, `samples/photogrammetry/images/` →
`samples/photogrammetry/thumbs/`) — never inside the inputs, where the worker and the next listing
would take them for photos (that happened once, 2026-08-29). A photo that can't be decoded keeps
`thumb_url: null` (re-attempted on later listings; the client's poll is capped). The task role's
S3 policy covers the writes.

**GPU controller.** Both GPU workers are run-to-completion ECS tasks (EC2 launch type, shared
`gpu-<env>` capacity provider) launched on demand by `GpuController` via `RunTask` — one
controller per family, bound to `GPU_WORKER_TASK_FAMILY` (transcription) or
`GPU_PHOTOGRAMMETRY_TASK_FAMILY` (photogrammetry), sharing the cluster, capacity provider and the
`gpu_sessions` ledger. A launch happens when a job is confirmed, when `POST /gpu/warm` is called,
or when a status poll finds an active job with no worker. `ensure_worker()` takes a DB advisory
lock, reconciles stale open sessions, enforces the daily/monthly GPU-hour caps and the per-user
warm cap, and records the launch as a `gpu_sessions` row. `GET /gpu/state` caches `ListTasks` for a
few seconds and, while a session is open, copies the task's `pullStartedAt / pullStoppedAt /
startedAt` onto the row once (`DescribeTasks`).

*Startup estimates are measured, not configured.* The estimate is the median of
`started_processing_at − started_at` (RunTask → the worker's first claim: capacity-provider
reaction, instance boot, image pull, container up) over the family's last 20 job-triggered
launches, split by kind: **cold** (the instance booted for this launch — the worker stamps
`instance_booted_at` from the host's uptime) vs **warm** (the instance was still up, inside the
ASG's scale-in lag after an idle exit, so only a container start is needed). `/gpu/state` quotes
the warm median when the last session ended within `GPU_SCALE_IN_SECONDS`, else cold; below 3
samples of a kind it falls back to `GPU_WAIT_ESTIMATE_OFF_SECONDS` / `_WARM_SECONDS`. While
`starting` the response carries `starting_since` and `estimated_wait_seconds` is the *remaining*
time. Each launch records the estimate it promised (`estimated_startup_seconds`); `GET /gpu/usage`
returns per session the kind, the promised and actual startup, the stage breakdown
(capacity, boot, pull, container, init) so the panel can hold the estimate to account, and the
`job` it was launched for (`gpu_sessions.job_id`, stamped by `ensure_worker("job", …, job_id=)`;
`{id, name, created_at}` looked up in the family's jobs table, `null` for warm-ups, a deleted job, or
another user's launch — the list is everyone's, but a scan's name is its owner's content and the link
only works for them). Each session also carries `cost_usd` (hours × `GPU_HOURLY_RATE_USD`) and, for
photogrammetry, `billable_jobs`: scans completed inside the session window with their own compute
cost (`processing_started_at` — stamped by the worker at first claim — → `completed_at`; startup
excluded), plus a response-level $/photo summary over the last 20 completed scans
(`photo_cost_median/worst/best_usd`, the worst being the floor for a per-photo price). Another
user's scan shows cost but not id/name (the job-link privacy rule).

*Admin release.* `POST /api/v1/gpu/release?family=…&mode=graceful|immediate` (Cognito `admin`
group; 403 otherwise, 409 with no live worker) writes `release_mode` / `release_requested_at` /
`release_requested_by` on the family's open row and clears `warm_until`; the worker's
`ReleaseWatcher` (shared `gpu-worker` package) polls the row every 10 s and exits after its
current job (`graceful`) or aborts it so the message is redelivered (`immediate` — the
photogrammetry runner kills the tool process; the transcription handler checks the flag in the
Transcribe wait loop and between turns in the embedding loop, then puts the row back to
`transcribing`; the in-process pyannote call itself is not interruptible; either worker's message
reappears only after the SQS visibility timeout). Ledger
`end_reason = released`. `/api/v1/gpu/*` returns 503 unless `GPU_CONTROLLER_ENABLED=true` or a mock
flag is set.

**Database:** Async SQLAlchemy with asyncpg driver. `init_db()` is called at lifespan startup; `get_db()` yields an `AsyncSession` that commits on success and rolls back on exception. `SpeakerSample.embedding` uses `pgvector` (`Vector(192)`); `SpeakerSample.error_message` stores embedding failure reason set by the transcription worker. Production PostgreSQL is EC2-hosted (RDS was decommissioned 2026-03-15); the DSN is a Secrets Manager secret injected by ECS.

**Auth:** All endpoints (except `/api/v1/health` and `/api/v1/public/*`) use `get_current_user`, which validates RS256 JWTs against Cognito's JWKS URL. The JWKS response is cached as a module-level global. Cognito groups drive `admin`-only routes.

## CI/CD

`.github/workflows/deploy.yml` runs on every push to `main`, detects which directories changed and
calls the reusable per-surface workflows in dependency order — `api.yml` first (this service),
then `vue.yml` and the workers. `api.yml`: `uv run pytest tests/unit` → Docker build → ECR push
tagged with the commit SHA and `latest` → register a new `chat-api-prod` task-definition revision
→ `update-service` → `aws ecs wait services-stable`. ECS keeps the previous task set serving while
a new revision crash-loops, so a failed rollout is "stuck", not "down". Manual redeploy:
`gh workflow run Deploy -f api=true`. Details and recovery in `docs/runbooks/deploy.md`.
`buildspec.yml`/CodePipeline are gone.

Infrastructure is Terraform under `infra/` (per-environment in `infra/environments/`, shared modules in `infra/modules/`).

## Environment variables

All settings are in `app/config.py` (`Settings` class, `pydantic-settings`, read from `.env`). Copy `.env.example` to `.env` for local dev.

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `dev` | `prod` disables `/docs` and the dev-only overrides |
| `LOG_LEVEL` | `INFO` | |
| `CORS_ORIGINS` | `["*"]` | JSON list; must include `http://localhost:5173` for local dev and the CloudFront domain in prod |
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/chatapi` | asyncpg DSN (prod: Secrets Manager) |
| `AWS_REGION` / `AWS_ACCOUNT_ID` | `us-east-1` / `""` | |
| `COGNITO_USER_POOL_ID`, `COGNITO_CLIENT_ID`, `COGNITO_REGION` | `""`, `""`, `us-east-1` | JWT validation |
| `BEDROCK_MODEL_ID` | `anthropic.claude-3-sonnet-20240229-v1:0` | Default model for new conversations |
| `USE_MOCK_BEDROCK`, `MOCK_BEDROCK_DELAY_SECONDS` | `false`, `0` | Canned response instead of Bedrock |
| `AUDIO_BUCKET_NAME` | `""` | S3 bucket for audio, photos, meshes, thumbnails, samples |
| `TRANSCRIBE_SQS_QUEUE_URL` | `""` | Transcription worker queue |
| `MAX_CONCURRENT_JOBS` | `3` | Per-user active-job limit (transcription and photogrammetry each) |
| `USE_MOCK_TRANSCRIPTION` | `false` | `LocalTranscriptionService`; also enables the mock GPU launcher |
| `MOCK_UPLOAD_BASE_URL` | `http://localhost:8000` | Base of dev-upload sink URLs as the browser sees them |
| `MOCK_SAMPLE_PROCESSING_DELAY_SECONDS`, `MOCK_JOB_TRANSCRIBING_DELAY_SECONDS`, `MOCK_JOB_MATCHING_DELAY_SECONDS` | `3`, `5`, `3` | Mock transcription timings |
| `MOCK_WORKER_EXTERNAL` | `false` | Leave confirmed jobs in `transcribing` for an external dev worker |
| `DEV_AUTH_BYPASS`, `DEV_AUTH_USER_SUB` | `false`, `dev-user-001` | Skip Cognito entirely (never active when `ENVIRONMENT=prod`) |
| `LOCAL_STORAGE_PATH` | `/tmp/mock-audio` | Where the dev-upload sink stores files |
| `SAMPLE_AUDIO_S3_KEY`, `SAMPLE_BARRY_S3_KEY`, `SAMPLE_JANE_S3_KEY` | `samples/…` | Transcribe "Try the sample" objects |
| `USE_MOCK_PHOTOGRAMMETRY` | `false` | `LocalPhotogrammetryService` (see below) |
| `MOCK_PHOTOGRAMMETRY_STAGE_DELAY_SECONDS` | `2.0` | Seconds per mock stage |
| `PHOTOGRAMMETRY_MAX_IMAGES` | `150` | Per-scan cap (min is 5) |
| `PHOTOGRAMMETRY_SAMPLE_PREFIX` | `samples/photogrammetry/` | Bundled set: `images/` + sibling `thumbs/` |
| `PHOTOGRAMMETRY_UPLOAD_TTL_SECONDS` | `3600` | Presigned PUT window for a whole photo set |
| `GPU_PHOTOGRAMMETRY_TASK_FAMILY`, `PHOTOGRAMMETRY_SQS_QUEUE_URL` | `""` | Empty = worker not deployed → confirm returns 503 |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_PROJECT`, `LANGCHAIN_API_KEY` | `false`, `chat-api`, `""` | LangSmith tracing of `BedrockService.invoke()` |
| `GPU_CONTROLLER_ENABLED` | `false` | Real ECS launches; otherwise `/gpu/*` is 503 unless a mock flag is set |
| `GPU_CLUSTER`, `GPU_WORKER_TASK_FAMILY`, `GPU_CAPACITY_PROVIDER` | `""` | ECS cluster, transcription task family, capacity provider (e.g. `gpu-prod`) |
| `GPU_IDLE_EXIT_SECONDS`, `GPU_MAX_LIFETIME_SECONDS` | `900`, `10800` | Must match the workers' values |
| `GPU_DAILY_CAP_HOURS`, `GPU_MONTHLY_CAP_HOURS`, `GPU_WARM_PER_USER_PER_DAY` | `3`, `30`, `3` | Caps enforced at launch |
| `GPU_HOURLY_RATE_USD` | `0.20` | Estimate shown in the usage panel |
| `GPU_COST_TAG_KEY`, `GPU_COST_TAG_VALUE` | `CostCenter`, `gpu` | Cost Explorer tag for actual month-to-date |
| `GPU_WAIT_ESTIMATE_STARTING_SECONDS`, `GPU_WAIT_ESTIMATE_OFF_SECONDS` | `420`, `420` | Cold-start fallback until ≥3 measured launches |
| `GPU_WAIT_ESTIMATE_WARM_SECONDS` | `90` | Warm-start fallback until ≥3 measured |
| `GPU_SCALE_IN_SECONDS` | `900` | ASG scale-in lag; a launch inside it is quoted as warm |

In production (`ENVIRONMENT=prod`), `/docs` (Swagger UI) is disabled.

## Mock / local dev notes

- **`USE_MOCK_BEDROCK=true`** — skips AWS Bedrock entirely, returns a canned response.
- **`USE_MOCK_TRANSCRIPTION=true`** — uses `LocalTranscriptionService` instead of the real one. Jobs are completed in-process via `asyncio.create_task`: `transcribing` → `matching` → `complete` with configurable delays. Seeded with 4 mock transcript segments. Samples transition to `ready` after `MOCK_SAMPLE_PROCESSING_DELAY_SECONDS`. Also enables the mock GPU launcher for the `/api/v1/gpu/*` endpoints, so the Warm button and GPU state queries work locally without AWS resources. `GET /samples` seeds the committed `app/assets/transcribe/{conversation,barry,jane}.wav` into the dev-upload sink on first call (once per key), then presigns like prod — what NewJobForm's sample-review mode plays.
- **`/api/v1/transcribe/dev-upload/{path}`** — PUT/GET sink registered at app startup when either mock flag is set; acts as an S3 replacement so browser presigned-URL uploads succeed and stored files (mesh, preview, thumbnails) are served back.
- **`USE_MOCK_PHOTOGRAMMETRY=true`** — uses `LocalPhotogrammetryService`. Confirmed jobs walk `queued → processing (sfm → dense → mesh → texture) → complete` with `MOCK_PHOTOGRAMMETRY_STAGE_DELAY_SECONDS` per step, then the placeholder `app/assets/photogrammetry/{mesh.glb,preview.png}` is copied into the dev-upload sink under the job's `output/` keys; every status response carries `mock: true`. `POST /jobs/sample` copies the committed sample photos into the sink and confirms. `GET /samples` seeds the same photos under `samples/photogrammetry/images/` on first call; it and `GET /jobs/{id}/photos` then run the real listing + thumbnail path. The mock walk does **not** write `warnings` or `photo_status`, so photo tiles show no match marks locally. Details: `docs/mock-api.md`.
