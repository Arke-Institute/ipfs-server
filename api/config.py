from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    IPFS_API_URL: str = "http://localhost:5001/api/v0"
    INDEX_POINTER_PATH: str = "/arke/index-pointer"
    CHUNK_SIZE: int = 10000
    REBUILD_THRESHOLD: int = 10000  # Rebuild snapshot every N new items

    class Config:
        env_file = ".env"

settings = Settings()
