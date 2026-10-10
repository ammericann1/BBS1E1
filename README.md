# Instagram Reel Automation Agent

Automated pipeline for downloading source episodes from Google Drive, splitting them into clips, uploading to Cloudinary, and publishing as Instagram Reels on a schedule.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ Google Drive  │────▶│  Downloader  │────▶│ Source Cache  │
│  (Episode)   │     │              │     │ /data/sources │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
                                          ┌──────────────┐
                                          │  Processor   │
                                          │  (FFmpeg)    │
                                          └──────┬───────┘
                                                  │
                                                  ▼
                                          ┌──────────────┐
                                          │  Clip Cache  │
                                          │ /data/clips  │
                                          └──────┬───────┘
                                                  │
                                                  ▼
                                          ┌──────────────┐     ┌──────────────┐
                                          │  Cloudinary  │────▶│   Instagram   │
                                          │   Uploader   │     │  Graph API   │
                                          └──────────────┘     └──────────────┘

State: SQLite @ /data/state/reel_automation.db
Worker: Single scheduled process on Railway
```

## Clip Lifecycle

```
PENDING → PROCESSING → PROCESSED → CLOUDINARY_UPLOADED
    → INSTAGRAM_PROCESSING → READY_TO_PUBLISH → PUBLISHED
                                                 ↘ FAILED → RETRY_PENDING ↗
```

## Requirements

- **Python 3.12+**
- **FFmpeg & FFprobe** (must be on PATH)
- Google Cloud service account with Drive API access
- Cloudinary account
- Meta developer app with Instagram Graph API access
- Instagram Professional (Business or Creator) account

## Local Installation

```bash
# Clone
git clone <repo-url>
cd BBS1E1

# Create virtual environment
python -m venv venv
source venv/bin/activate   # Linux/Mac
venv\Scripts\activate      # Windows

# Install dependencies
pip install -r requirements.txt

# Copy and fill environment config
cp .env.example .env
# Edit .env with your credentials
```

### FFmpeg Installation

**Windows:**
```bash
# Using winget
winget install ffmpeg

# Or download from https://ffmpeg.org/download.html
# Add ffmpeg/bin to your PATH
```

**macOS:**
```bash
brew install ffmpeg
```

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install -y ffmpeg
```

**Verify:**
```bash
ffmpeg -version
ffprobe -version
```

## Google Drive Authentication

### Option 1: Service Account (Recommended for Production)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or select existing)
3. Enable the **Google Drive API**
4. Go to **IAM & Admin → Service Accounts**
5. Create a service account
6. Create a JSON key → download as `credentials.json`
7. Share the target Drive file/folder with the service account email
8. Set `GOOGLE_APPLICATION_CREDENTIALS=credentials.json`

### Option 2: Raw JSON (Railway Deployment)

For Railway, set the `GOOGLE_CREDENTIALS_JSON` env var with the full JSON content of your service account key.

### Finding the Drive File ID

Open your Google Drive file → the URL looks like:
```
https://drive.google.com/file/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ/view
                                    ^^^^^^^^^^^^^^^^^^^^^^^^
                                    This is your FILE_ID
```

Set `GOOGLE_DRIVE_FILE_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ`

## Cloudinary Account Setup

1. Sign up at [cloudinary.com](https://cloudinary.com/)
2. Go to **Dashboard** → find your Cloud Name, API Key, API Secret
3. Set the environment variables:
   ```
   CLOUDINARY_CLOUD_NAME=your_cloud_name
   CLOUDINARY_API_KEY=your_api_key
   CLOUDINARY_API_SECRET=your_api_secret
   ```
4. Ensure your plan supports video uploads (free tier includes 25 GB)

## Meta / Instagram Setup

### 1. Create a Meta Developer App

1. Go to [developers.facebook.com](https://developers.facebook.com/)
2. Create a new app → select **Business** type
3. Add the **Instagram Graph API** product

### 2. Instagram Professional Account

Your Instagram account must be a **Business** or **Creator** account:
- Instagram → Settings → Account → Switch to Professional Account

### 3. Connect to Facebook Page

Link your Instagram Professional account to a Facebook Page.

### 4. Required Permissions

Your app needs these permissions:
- `instagram_basic`
- `instagram_content_publish`
- `pages_read_engagement`

### 5. Access Token

Generate a long-lived User Access Token:
1. Use the [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
2. Select your app
3. Generate a User Token with the required permissions
4. Exchange for a long-lived token (60-day expiry)
5. Set `INSTAGRAM_ACCESS_TOKEN=your_token`

### 6. Instagram User ID

Find your Instagram Professional account ID:
```bash
curl "https://graph.facebook.com/v21.0/me/accounts?access_token=YOUR_TOKEN"
# Get the Page ID, then:
curl "https://graph.facebook.com/v21.0/PAGE_ID?fields=instagram_business_account&access_token=YOUR_TOKEN"
```

Set `INSTAGRAM_USER_ID=your_instagram_business_account_id`

## Environment Variable Reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_DRIVE_FILE_ID` | ✓ | — | Google Drive file ID of the source episode |
| `GOOGLE_APPLICATION_CREDENTIALS` | ✓* | — | Path to service account JSON key |
| `GOOGLE_CREDENTIALS_JSON` | ✓* | — | Raw JSON of service account key (alternative) |
| `DATA_DIR` | — | `/data` | Persistent data directory |
| `CLIP_DURATION_SECONDS` | — | `60` | Duration of each clip in seconds |
| `AUDIO_LANGUAGE` | — | `hin` | Audio language code or stream index |
| `UPLOAD_INTERVAL_SECONDS` | — | `3600` | Minimum seconds between publications |
| `VIDEO_CRF` | — | `23` | H.264 quality (0=lossless, 51=worst) |
| `VIDEO_PRESET` | — | `veryfast` | FFmpeg encoding speed preset |
| `MAX_RETRIES` | — | `3` | Maximum retry attempts per clip |
| `DRY_RUN` | — | `true` | Skip actual uploads/publishes when true |
| `AUTO_PUBLISH` | — | `false` | Auto-publish after container is ready |
| `CLOUDINARY_CLOUD_NAME` | ✓ | — | Cloudinary cloud name |
| `CLOUDINARY_API_KEY` | ✓ | — | Cloudinary API key |
| `CLOUDINARY_API_SECRET` | ✓ | — | Cloudinary API secret |
| `INSTAGRAM_ACCESS_TOKEN` | ✓ | — | Instagram Graph API access token |
| `INSTAGRAM_USER_ID` | ✓ | — | Instagram Professional account ID |
| `INSTAGRAM_API_VERSION` | — | `v21.0` | Instagram Graph API version |

*One of `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_CREDENTIALS_JSON` is required.

## Local Commands

```bash
# Validate environment configuration
python -m app.main validate-config

# Download source episode
python -m app.main download

# Inspect source video and audio tracks
python -m app.main inspect-source
python -m app.main inspect-source --file path/to/video.mp4

# Generate clips (no upload/publish)
python -m app.main process

# Upload clips to Cloudinary
python -m app.main upload-cloudinary

# Publish one clip to Instagram
python -m app.main publish-one

# Show full pipeline status
python -m app.main status

# Retry failed jobs
python -m app.main retry-failed

# Clean up published clip files
python -m app.main cleanup
python -m app.main cleanup --published-only

# Run the scheduled worker
python -m app.main run-worker
```

## Audio Selection

The `AUDIO_LANGUAGE` variable controls which audio track is used:

```bash
# Select Hindi audio
AUDIO_LANGUAGE=hin

# Select English audio
AUDIO_LANGUAGE=eng

# Select by stream index (e.g., stream #2)
AUDIO_LANGUAGE=2
```

Use `inspect-source` to see all available audio tracks before processing.

## Clip Duration Configuration

```bash
# 60-second clips (default)
CLIP_DURATION_SECONDS=60

# 90-second clips
CLIP_DURATION_SECONDS=90

# 30-second clips
CLIP_DURATION_SECONDS=30
```

The final clip may be shorter if the source doesn't divide evenly.

## Railway Deployment

### 1. Create Railway Project

1. Go to [railway.app](https://railway.app/)
2. Create a new project
3. Connect your Git repository

### 2. Add Persistent Volume

1. In your Railway service, go to **Settings → Volumes**
2. Add a volume:
   - **Mount Path:** `/data`
   - **Size:** 10 GB minimum (recommended: 15-20 GB for a 4 GB source + clips)

### 3. Set Environment Variables

Add all required env vars in Railway's **Variables** tab. Use `GOOGLE_CREDENTIALS_JSON` with the raw JSON content instead of a file path.

### 4. Deploy

Railway auto-deploys from Git. The start command is configured in the Dockerfile:

```
python -m app.main run-worker
```

Or set the **Start Command** in Railway settings:
```
python -m app.main run-worker --log-level INFO
```

### 5. Storage Capacity Planning

| Item | Size |
|---|---|
| Source episode | ~4 GB |
| All clips (60s each, ~237 clips) | ~4 GB |
| SQLite database | < 10 MB |
| Working space | ~2 GB |
| **Total recommended** | **15-20 GB** |

## Scheduler Configuration

The scheduler runs continuously and:
1. Downloads the source (if not cached)
2. Generates all clips
3. Uploads clips to Cloudinary one at a time
4. Publishes to Instagram respecting the interval
5. Retries failed clips with exponential backoff
6. Exits when all clips are published or permanently failed

Key settings:
- `UPLOAD_INTERVAL_SECONDS=3600` — wait 1 hour between publications
- `MAX_RETRIES=3` — retry up to 3 times
- `DRY_RUN=true` — test without publishing (start with this!)
- `AUTO_PUBLISH=false` — require manual publish (safety net)

**Recommended first deployment:**
```
DRY_RUN=true
AUTO_PUBLISH=false
```

Then verify the status, and when ready:
```
DRY_RUN=false
AUTO_PUBLISH=true
```

## Troubleshooting

### "FFmpeg/FFprobe not found"
Install FFmpeg and ensure it's on your PATH.

### "Audio language 'hin' not found"
Run `inspect-source` to see available tracks. Set `AUDIO_LANGUAGE` accordingly.

### "Insufficient disk space"
Ensure at least 6 GB free. On Railway, increase the persistent volume size.

### "Invalid token" from Instagram
Tokens expire after 60 days. Generate a new long-lived token.

### "Rate limited"
The scheduler automatically backs off. Reduce `UPLOAD_INTERVAL_SECONDS` if needed.

### Clips re-processed after restart
This is expected for clips in PROCESSING state at crash time. Completed clips are not re-processed.

## Recovery & Duplicate Prevention

- **Source caching**: Downloaded episodes are reused across runs and restarts
- **Clip identity**: Each clip has a deterministic ID based on source + index
- **State persistence**: SQLite database on persistent volume survives restarts
- **Duplicate prevention**: Published clips cannot transition back to publishable states
- **Idempotent clip creation**: Creating a clip with the same source+index returns the existing record
- **Cloudinary dedup**: Checks for existing assets before uploading

## Security

- All credentials in environment variables (never in code or Git)
- `.gitignore` excludes `.env`, credentials, keys, and media
- FFmpeg commands use argument arrays (no shell injection)
- File operations validate paths against the data directory
- API tokens are redacted in logs
- Graceful SIGTERM handling for clean Railway shutdowns
- Single-writer lock prevents concurrent scheduler instances

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_state.py

# Run only fast tests (skip FFmpeg tests)
pytest -m "not slow"

# Verbose output
pytest -v
```

Tests use mocked external services — no credentials required.

FFmpeg-dependent tests require FFmpeg installed and are auto-skipped if absent.

## Cost Considerations

| Service | Free Tier | Notes |
|---|---|---|
| **Railway** | $5/mo credit | Persistent volumes: $0.25/GB/mo |
| **Cloudinary** | 25 GB storage, 25 GB bandwidth | Video processing credits apply |
| **Instagram API** | Free | Rate limits: 25 posts/24h (Business) |
| **Google Drive** | 15 GB free | Service account has no storage quota |

For a typical 4 GB episode split into ~237 clips published hourly:
- Railway: ~$5-10/mo (compute + 15 GB volume)
- Cloudinary: Free tier likely sufficient for one episode
- Total runtime: ~237 hours (~10 days) to publish all clips

## License

Private — internal use only.
