from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    AUDIO_BUCKET_NAME: str
    PHOTOGRAMMETRY_SQS_QUEUE_URL: str
    AWS_REGION: str = "us-east-1"
    IDLE_EXIT_SECONDS: int = 900
    MAX_LIFETIME_SECONDS: int = 10800
    PHOTOGRAMMETRY_JOB_TIMEOUT_SECONDS: int = 3600
    SQS_VISIBILITY_TIMEOUT: int = 600
    SQS_VISIBILITY_EXTENSION_INTERVAL: int = 300
    WORK_DIR: str = "/tmp/pg"
    COLMAP_USE_GPU: int = 1   # 0 runs SIFT/matching on CPU (fitlet smoke test)
    TEXTURE_MAX_SIZE: int = 4096
    MASK_MODEL_PATH: str = "/opt/models/u2netp.onnx"   # u2netp ONNX for remove_background (CPU inference)   # each GLB atlas is cropped to its used UVs, then capped at this² pixels (JPEG)
