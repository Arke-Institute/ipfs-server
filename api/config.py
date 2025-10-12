from pydantic_settings import BaseSettings
from pydantic import ConfigDict

class Settings(BaseSettings):
    # Configuration values used by the API
    IPFS_API_URL: str
    INDEX_POINTER_PATH: str
    SNAPSHOT_INTERVAL_MINUTES: int = 60  # Default: 1 hour
    AUTO_SNAPSHOT: bool = True

    model_config = ConfigDict(
        env_file=".env",
        extra="ignore"  # Ignore extra fields in .env (used by scripts)
    )

settings = Settings()
