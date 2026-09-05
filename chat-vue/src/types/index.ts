// Mirrors ModelOut from app/schemas/models.py
export interface Model {
  model_id: string
  model_name: string
  provider_name: string
  input_price_per_1k_tokens: number | null
  output_price_per_1k_tokens: number | null
}

// Mirrors ConversationOut from app/schemas/conversation.py
export interface Conversation {
  id: string
  title: string | null
  model_id: string | null
  input_price_per_1k_tokens: number | null
  output_price_per_1k_tokens: number | null
  created_at: string
  updated_at: string
  messages: Message[]
  is_public?: boolean
}

// Mirrors ChatRequest from app/schemas/chat.py
export interface ChatRequest {
  conversation_id?: string
  message: string
  model_id?: string
  input_price_per_1k_tokens?: number | null
  output_price_per_1k_tokens?: number | null
}

// Mirrors ChatResponse from app/schemas/chat.py
export interface ChatResponse {
  conversation_id: string
  reply: string
}

// Local UI-only type for displaying a message in the chat thread
export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: Date
}

// Mirrors MessageOut from app/schemas/conversation.py
export interface ApiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

// ── Speaker profiles ──────────────────────────────────────────────────────

export interface SpeakerProfile {
  speaker_id: string
  speaker_name: string | null
  created_at: string
  samples: SpeakerSample[]
}

export interface SpeakerSample {
  sample_id: string
  status: 'processing' | 'ready' | 'failed'
  duration_seconds: number | null
  error_message: string | null
  created_at: string
}

export interface SpeakerListResponse {
  items: SpeakerProfile[]
  next_cursor: string | null
}

export interface SampleUploadInitResponse {
  sample_id: string
  upload_url: string
}

// ── Transcription jobs ────────────────────────────────────────────────────

export interface TranscriptionJob {
  job_id: string
  status: 'pending' | 'transcribing' | 'matching' | 'complete' | 'failed'
  speaker_count_hint: number
  language: string
  speaker_ids: string[]
  error_message: string | null
  partial_transcript_available: boolean
  matched_speaker_count: number | null
  total_segment_count: number | null
  created_at: string
  updated_at: string
  completed_at: string | null
  worker_state?: WorkerState | null
  estimated_wait_seconds?: number
  gpu_notice?: string | null
  is_public?: boolean
}

export type WorkerState = 'off' | 'starting' | 'running'

export type GpuFamily = "transcription" | "photogrammetry"

export type EstimateBasis = "measured" | "default"

export interface GpuState {
  worker_state: WorkerState
  /** Remaining seconds while starting (server subtracts elapsed); the full estimate while off; 0 running. */
  estimated_wait_seconds: number
  warm_until: string | null
  notice: string | null
  /** The open session's launch time while starting, else null. */
  starting_since: string | null
  /** Full expected off→ready duration: median of recent measured starts, or the config default. */
  startup_estimate_seconds: number
  estimate_basis: EstimateBasis
  estimate_samples: number
  /** Which kind of start the quoted estimate is for: cold = a new instance must boot; warm = one is still up. */
  start_kind: StartKind
}

export type StartKind = "cold" | "warm"

/** Seconds per startup stage (launch → first job claimed); null when a timestamp is unknown or the stage does not apply. */
export interface StartupStages {
  capacity: number | null   // RunTask → instance booted (— for warm starts)
  boot: number | null       // instance booted → image pull started
  pull: number | null
  container: number | null  // pull finished → task running
  init: number | null       // task running → first job claimed
}

/** The job a worker was launched for: a scan has a name, a transcript only its created_at. */
export interface GpuSessionJob {
  id: string
  name: string | null
  created_at: string
}

export interface GpuSessionSummary {
  started_at: string; ended_at: string | null; reason: string; started_by: string
  end_reason: string | null; hours: number; family: GpuFamily
  /** What the UI promised when this worker was launched. */
  estimated_startup_seconds: number | null
  /** Launch → first job claimed; null for sessions that never took a job (warm-ups). */
  actual_startup_seconds: number | null
  kind: StartKind | null
  stages: StartupStages | null
  /** null for warm-ups, or when the job has since been deleted. */
  job: GpuSessionJob | null
  /** hours × rate — what this session cost, wall clock including startup and idle tail. */
  cost_usd?: number | null
  /** Scans completed inside this session with their own compute cost (photogrammetry only). */
  billable_jobs?: GpuBillableJob[]
}

/** A scan's billable compute: worker claim → complete, startup excluded. id/name only for
 * the caller's own scans. */
export interface GpuBillableJob {
  id: string | null
  name: string | null
  image_count: number
  billable_seconds: number
  billable_usd: number
  usd_per_photo: number | null
}

export interface GpuUsage {
  today_hours: number; month_hours: number; daily_cap_hours: number; monthly_cap_hours: number
  warms_today_for_user: number; warm_cap_per_user_per_day: number
  estimated_month_cost_usd: number; hourly_rate_usd: number
  actual_month_to_date_usd: number | null; actual_fetched_at: string | null
  /** Same as the cold figures, kept for compatibility. */
  startup_median_seconds: number | null
  startup_samples: number
  cold_median_seconds: number | null
  cold_samples: number
  warm_median_seconds: number | null
  warm_samples: number
  /** Compute-$/photo over the last N completed scans — worst is the price-per-photo floor. */
  photo_cost_median_usd?: number | null
  photo_cost_worst_usd?: number | null
  photo_cost_best_usd?: number | null
  photo_cost_samples?: number
  sessions: GpuSessionSummary[]
}

export interface JobListResponse {
  items: TranscriptionJob[]
  next_cursor: string | null
}

export interface JobCreateRequest {
  speaker_count_hint?: number
  speaker_ids?: string[]
  language?: string
}

export interface JobCreateResponse {
  job_id: string
  upload_url: string
}

/** The bundled sample audio + Barry/Jane voice samples, as shown in NewJobForm's sample-review mode. */
export interface SamplePreview {
  name: string
  audio: { filename: string; url: string }
  speakers: { speaker_name: string; url: string }[]
}

/** Mirrors AudioUrlResponse from app/schemas/transcription.py — a job's raw input audio,
 *  or a speaker enrollment sample. */
export interface AudioUrlResponse {
  url: string             // plain GET — what <audio controls> plays
  download_url: string    // same object, Content-Disposition: attachment
  filename: string
  expires_at: string
}

/** Store-side cache entry for AudioUrlResponse (camelCase, epoch-ms expiry). */
export interface AudioUrls {
  url: string
  downloadUrl: string
  filename: string
  expiresAt: number
}

// ── Job activity log ──────────────────────────────────────────────────────

export interface JobLogEntry {
  ts: string
  direction: 'request' | 'response'
  label: string
  detail?: string
  error?: boolean
}

// ── Transcript ────────────────────────────────────────────────────────────

export interface TranscriptSegment {
  segment_id: string
  anonymous_label: string
  speaker_name: string | null
  start_time: number
  end_time: number
  text: string
}

export type MatchType = "high" | "medium" | "low" | "none"

export interface CompileSettings {
  cosine_dist_threshold: number
  separation_min: number
  quality_min: number
  confidence_min: number
}

export interface CompiledTurn {
  start_time: number
  end_time: number
  text: string
  label: string
  match_type: MatchType
}

export interface TranscriptResponse {
  segments: TranscriptSegment[]
  turns: CompiledTurn[] | null      // null: job has no turn-distance data
  settings: CompileSettings
  compiled_at: string | null
}

// ── Turn distances (client-side matching analysis) ────────────────────────

export interface TurnCandidate {
  candidate_id: string
  speaker_name: string | null
  cosine_dist: number
}

export interface TurnDistanceData {
  start_time: number
  end_time: number
  text: string
  candidates: TurnCandidate[]
}

export interface TurnDistancesResponse {
  turns: TurnDistanceData[]
}

// ── Photogrammetry ────────────────────────────────────────────────────────

export type PhotogrammetryJobStatus = 'pending' | 'queued' | 'processing' | 'complete' | 'failed'
export type PhotogrammetryStage = 'sfm' | 'dense' | 'mesh' | 'texture'

export interface PhotogrammetryJob {
  job_id: string
  name: string
  status: PhotogrammetryJobStatus
  stage: PhotogrammetryStage | null
  image_count: number
  preview_url: string | null
  error_message: string | null
  warnings: string[]
  mock: boolean
  created_at: string
  updated_at: string
  completed_at: string | null
  worker_state?: WorkerState | null
  estimated_wait_seconds?: number | null
  gpu_notice?: string | null
  is_public?: boolean
  /** Backdrop masked out of the dense stage (turntable scans). */
  remove_background?: boolean
}

export interface UploadTarget {
  filename: string
  key: string
  url: string
}

export interface PhotogrammetryJobCreateResponse {
  job_id: string
  uploads: UploadTarget[]
}

export interface PhotogrammetryJobListResponse {
  items: PhotogrammetryJob[]
  next_cursor: string | null
}

export interface MeshUrlResponse {
  url: string                           // plain GET, for <model-viewer>
  download_url: string                  // same GLB, served as an attachment
  preview_download_url: string | null   // preview.png as an attachment, when it exists
  expires_at: string
}

/** One input photo of a scan: presigned full-size and thumbnail (≤256 px) GET URLs. */
export interface PhotoItem {
  filename: string
  url: string
  /** Presigned thumbnail URL; null while the API is still generating it in the background. */
  thumb_url: string | null
  /** "registered" | "unregistered" | "skipped:<reason>" once SfM has run; null before (and for the sample set). */
  status?: string | null
  /** Presigned mask-overlay URL (the worker's masks/<name>.jpg) once the dense stage ran with background removal; null/absent otherwise. */
  mask_url?: string | null
}

export interface JobPhotosResponse {
  photos: PhotoItem[]
  /** Photos SfM registered; null until the worker has written per-photo status. */
  matched: number | null
  total: number
}

/** The bundled sample photo set, as shown in the New Scan form's sample mode. */
export interface SamplePhotos {
  name: string
  image_count: number
  photos: PhotoItem[]
}

/** Store-side cache entry for MeshUrlResponse (camelCase, epoch-ms expiry). */
export interface MeshUrls {
  url: string
  downloadUrl: string
  previewDownloadUrl: string | null
  expiresAt: number
}

// ── Public demo (mirrors chat-api app/schemas/public.py) ─────────────────────
export interface PublicScanSummary {
  job_id: string
  name: string
  image_count: number
  status: string
  preview_url: string | null
  created_at: string
}

export interface PublicScanDetail extends PublicScanSummary {
  warnings: string[]
  matched: number | null
  total: number | null
  mesh_url: string | null
  expires_at: string | null
  completed_at: string | null
}

export interface PublicTranscriptionSummary {
  job_id: string
  created_at: string
  duration_seconds: number | null
  segment_count: number | null
  speaker_count: number | null
}

export interface PublicTranscriptionDetail extends PublicTranscriptionSummary {
  segments: TranscriptSegment[]
  turns: CompiledTurn[] | null
  settings: CompileSettings
  compiled_at: string | null
}

export interface PublicMessage {
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface PublicConversationSummary {
  conversation_id: string
  title: string | null
  model_id: string | null
  created_at: string
}

export interface PublicConversationDetail extends PublicConversationSummary {
  messages: PublicMessage[]
}

export interface ShowcaseResponse {
  scans: PublicScanSummary[]
  transcriptions: PublicTranscriptionSummary[]
  conversations: PublicConversationSummary[]
}
