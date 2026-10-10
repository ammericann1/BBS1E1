import os
from dotenv import load_dotenv
from config import Config
from scheduler import VideoScheduler

def main():
    load_dotenv()
    config = Config()

    VIDEO_START_TIME = 0
    DROPBOX_VIDEO_URL = "https://dl.dropboxusercontent.com/scl/fi/24yybeouwv7qlxddt11nj/Young.Sheldon.S01E05.720p.BluRay.x264-GalaxyTV.mkv?rlkey=60vp4yi4wujnuv1sjuqyr61uk"

    GDRIVE_VIDEO_URL = "https://drive.google.com/uc?id=1At6VPIBB02SNAdJSOg9eDlQKPqtvhSsw&export=download"

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
            gdrive_url=DROPBOX_VIDEO_URL,  # still uses same param name
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
