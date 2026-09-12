from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # App
    environment: str = "dev"
    log_level: str = "INFO"
    cors_origins: List[str] = ["*"]

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/chatapi"

    # AWS
    aws_region: str = "us-east-1"
    aws_account_id: str = ""

    # Cognito
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    cognito_region: str = "us-east-1"

    # Bedrock
    bedrock_model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0"
    use_mock_bedrock: bool = False
    mock_bedrock_delay_seconds: float = 0.0

    # Audio Transcription
    audio_bucket_name: str = ""
    transcribe_sqs_queue_url: str = ""
    max_concurrent_jobs: int = 3
    use_mock_transcription: bool = False
    # Base URL of the API as seen by the browser; used to generate mock upload/download URLs
    mock_upload_base_url: str = "http://localhost:8000"
    # Seconds to wait before transitioning mock samples from processing → ready
    mock_sample_processing_delay_seconds: float = 3.0
    # Seconds to wait in each stage of a mock transcription job: transcribing → matching → complete
    mock_job_transcribing_delay_seconds: float = 5.0
    mock_job_matching_delay_seconds: float = 3.0

    # Local dev overrides — never active when environment="prod"
    dev_auth_bypass: bool = False
    dev_auth_user_sub: str = "dev-user-001"
    local_storage_path: str = "/tmp/mock-audio"
    # When true, confirm_job_upload leaves job in 'transcribing' for dev_worker.py to pick up
    # instead of spawning an in-process simulation task
    mock_worker_external: bool = False

    # Sample audio — shared S3 objects uploaded once; referenced directly (no user upload)
    sample_audio_s3_key: str = "samples/conversation.wav"
    sample_barry_s3_key: str = "samples/speakers/barry.wav"
    sample_jane_s3_key: str = "samples/speakers/jane.wav"

    # Photogrammetry (spec: docs/design/photogrammetry-ui-spec.md)
    use_mock_photogrammetry: bool = False
    # Seconds spent in each mock stage: queued → sfm → dense → mesh → texture → complete
    mock_photogrammetry_stage_delay_seconds: float = 2.0
    photogrammetry_max_images: int = 150
    # Upload window for a whole photo set. The 15-minute default fits one audio file, not 150
    # phone photos over cellular — an expired signature fails the PUT with nothing to resume.
    photogrammetry_upload_ttl_seconds: int = 3600
    # Shared sample photo set in the audio bucket, uploaded once by hand (images/0001.jpg …)
    photogrammetry_sample_prefix: str = "samples/photogrammetry/"
    # ECS task family of the photogrammetry worker; empty = not deployed (confirm returns 503)
    gpu_photogrammetry_task_family: str = ""
    photogrammetry_sqs_queue_url: str = ""   # worker queue; empty = not deployed (confirm returns 503)

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_project: str = "chat-api"
    langchain_api_key: str = ""

    # GPU controller (transcription worker on the shared GPU capacity provider)
    gpu_controller_enabled: bool = False
    gpu_cluster: str = ""                       # ECS cluster name
    gpu_worker_task_family: str = ""            # task-definition family; RunTask uses family:latest
    gpu_capacity_provider: str = ""             # capacity provider name (e.g. gpu-prod)
    gpu_idle_exit_seconds: int = 900            # must match the worker's IDLE_EXIT_SECONDS
    gpu_max_lifetime_seconds: int = 10800       # must match the worker's MAX_LIFETIME_SECONDS
    gpu_daily_cap_hours: float = 3.0
    gpu_monthly_cap_hours: float = 30.0
    gpu_warm_per_user_per_day: int = 3
    gpu_hourly_rate_usd: float = 0.20           # estimate only; the usage panel labels it so
    gpu_cost_tag_key: str = "CostCenter"
    gpu_cost_tag_value: str = "gpu"
    # Fallbacks only: GpuController measures the off→ready time from the ledger (median of the
    # last 10 job starts) once it has 3 samples. 420 s is what the ledger showed on 2026-08-29
    # (capacity-provider reaction + instance boot + image pull + first claim).
    gpu_wait_estimate_starting_seconds: int = 420
    gpu_wait_estimate_off_seconds: int = 420    # cold start (new instance) until ≥3 measured
    gpu_wait_estimate_warm_seconds: int = 90    # warm start (instance still up) until ≥3 measured
    gpu_scale_in_seconds: int = 900             # ASG scale-in lag: a job inside it lands on the idle instance

    # Defaults for compiling a transcript from turn distances (see transcript_compiler.py).
    # Must stay equal to the CompileSettings field defaults and the chat-vue slider defaults.
    compile_cosine_dist_threshold: float = 0.25
    compile_separation_min: float = 0.0
    compile_quality_min: float = 0.0
    compile_confidence_min: float = 0.0

    @property
    def cognito_jwks_url(self) -> str:
        return (
            f"https://cognito-idp.{self.cognito_region}.amazonaws.com"
            f"/{self.cognito_user_pool_id}/.well-known/jwks.json"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
