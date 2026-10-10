import os
from dataclasses import dataclass

@dataclass
class Config:
    # Instagram credentials
    INSTAGRAM_ACCESS_TOKEN: str = os.getenv('INSTAGRAM_ACCESS_TOKEN', '')
    INSTAGRAM_USER_ID: str = os.getenv('INSTAGRAM_USER_ID', '')

    # Cloudinary credentials
    CLOUDINARY_CLOUD_NAME: str = os.getenv('CLOUDINARY_CLOUD_NAME', '')
    CLOUDINARY_API_KEY: str = os.getenv('CLOUDINARY_API_KEY', '')
    CLOUDINARY_API_SECRET: str = os.getenv('CLOUDINARY_API_SECRET', '')

    # Video settings
    CLIP_DURATION: int = 60  # seconds
    SLEEP_INTERVAL: int = 3600  # 1 hour

    # File paths
    DOWNLOAD_PATH: str = 'downloads/'
    CLIPS_PATH: str = 'clips/'
    STATE_FILE: str = 'processing_state.json'
