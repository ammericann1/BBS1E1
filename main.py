import os
from dotenv import load_dotenv
from config import Config
from scheduler import VideoScheduler

def main():
    load_dotenv()
    config = Config()

    VIDEO_START_TIME = 0

    # Use Google Drive URL - more reliable than Dropbox
    VIDEO_URL = "https://drive.google.com/uc?id=1At6VPIBB02SNAdJSOg9eDlQKPqtvhSsw&export=download"

    # Or use a fresh Dropbox link (this one seems expired)
    # VIDEO_URL = "your_updated_dropbox_link_here"

    VIDEO_END_TIME = None

    required_vars = [
        'INSTAGRAM_ACCESS_TOKEN',
        'INSTAGRAM_USER_ID',
        'CLOUDINARY_CLOUD_NAME',
        'CLOUDINARY_API_KEY',
        'CLOUDINARY_API_SECRET'
    ]
    missing_vars = [var for var in required_vars if not getattr(config, var)]
    if missing_vars:
        print("Missing required environment variables:")
        for var in missing_vars:
            print(f"  - {var}")
        return

    os.makedirs(config.DOWNLOAD_PATH, exist_ok=True)
    os.makedirs(config.CLIPS_PATH, exist_ok=True)

    try:
        scheduler = VideoScheduler(
            config=config,
            video_url=VIDEO_URL,
            video_start_time=VIDEO_START_TIME,
            video_end_time=VIDEO_END_TIME
        )
        scheduler.start_scheduling()

    except KeyboardInterrupt:
        print("\nScheduler stopped by user")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
