# Instagram Reel Automation Agent

Simple automated Instagram Reel posting from long-form videos.

## Features

- Downloads videos from Dropbox/Google Drive
- Splits videos into 60-second clips
- Enhances video quality (brightness, contrast, saturation, sharpness)
- Uploads to Cloudinary
- Auto-publishes to Instagram on schedule
- Maintains processing state (resume capability)

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Create `.env` file:
```env
INSTAGRAM_ACCESS_TOKEN=your_token
INSTAGRAM_USER_ID=your_user_id
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret
```

3. Edit `main.py` to set your video URL and settings:
```python
DROPBOX_VIDEO_URL = "your_video_url"
VIDEO_START_TIME = 0  # Start position in seconds
VIDEO_END_TIME = None  # None = entire video
```

## Usage

```bash
python main.py
```

## Railway Deployment

This project is configured for Railway deployment:

1. Push to GitHub
2. Connect Railway to your repo
3. Add environment variables in Railway dashboard
4. Deploy automatically with `nixpacks.toml`

## Configuration

Edit `config.py` for:
- `CLIP_DURATION`: Clip length (default: 60 seconds)
- `SLEEP_INTERVAL`: Time between posts (default: 3600 seconds = 1 hour)

## Project Structure

```
.
├── main.py           # Entry point
├── config.py         # Configuration
├── scheduler.py      # Scheduling logic
├── downloader.py     # Video download + state management
├── processor.py      # Video processing + enhancement
├── uploader.py       # Cloudinary + Instagram upload
├── requirements.txt  # Dependencies
├── nixpacks.toml     # Railway config
└── .env             # Environment variables (not in git)
```

## How It Works

1. Downloads source video once
2. Loads processing state (resumes from last position)
3. Creates next 60-second clip with enhancements
4. Uploads clip to Cloudinary
5. Posts to Instagram
6. Saves state and sleeps for configured interval
7. Repeats until video end

## Notes

- State is saved in `processing_state.json`
- Videos are saved in `downloads/`
- Clips are saved in `clips/`
- Requires ffmpeg (included in Railway via nixpacks.toml)
