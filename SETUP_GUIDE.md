# 🚀 Quick Setup Guide — Getting Your Credentials

This guide shows you **exactly** what to fill in and where to get each credential.

---

## Step 1: Create Your `.env` File

Copy the example file:

```bash
cp .env.example .env
```

Now open `.env` in your text editor. You'll fill in these fields:

---

## 📋 Fields to Fill In

### 🔷 Section 1: Google Drive (Source Video)

```bash
GOOGLE_DRIVE_FILE_ID=___________________  # ← Fill this
GOOGLE_APPLICATION_CREDENTIALS=credentials.json
```

**How to get these:**

1. **GOOGLE_DRIVE_FILE_ID:**
   - Upload your video to Google Drive
   - Right-click the file → "Get link"
   - URL looks like: `https://drive.google.com/file/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ/view`
   - Copy the part between `/d/` and `/view` → `1aBcDeFgHiJkLmNoPqRsTuVwXyZ`
   - **Paste that into GOOGLE_DRIVE_FILE_ID**

2. **Service Account JSON Key:**
   - Go to https://console.cloud.google.com/
   - Create a new project (or select existing)
   - Enable "Google Drive API":
     - Go to "APIs & Services" → "Library"
     - Search "Google Drive API" → Enable
   - Create service account:
     - Go to "IAM & Admin" → "Service Accounts"
     - Click "Create Service Account"
     - Name it "instagram-reel-bot" → Create
     - No need to grant roles → Continue → Done
   - Create key:
     - Click on the service account email
     - Go to "Keys" tab → "Add Key" → "Create new key"
     - Choose JSON → Download
     - **Save the file as `credentials.json` in your project folder** (same folder as .env)
   - **Share your Drive video with the service account:**
     - Open your video in Google Drive
     - Click "Share"
     - Paste the service account email (looks like `instagram-reel-bot@your-project.iam.gserviceaccount.com`)
     - Give "Viewer" access → Share

---

### 🔷 Section 2: Processing Settings (No credentials needed)

```bash
DATA_DIR=/data
CLIP_DURATION_SECONDS=60        # ← Change if you want different clip length
AUDIO_LANGUAGE=hin              # ← Change to 'eng' for English, or stream number
UPLOAD_INTERVAL_SECONDS=3600    # ← 1 hour between posts (change if needed)
VIDEO_CRF=23
VIDEO_PRESET=veryfast
MAX_RETRIES=3
DRY_RUN=true                    # ← KEEP true for testing!
AUTO_PUBLISH=false              # ← KEEP false until you're ready!
```

**Leave these as-is for now.** Only change:
- `AUDIO_LANGUAGE` if your video has different audio tracks
- `CLIP_DURATION_SECONDS` if you want longer/shorter clips

---

### 🔷 Section 3: Cloudinary (Video Hosting)

```bash
CLOUDINARY_CLOUD_NAME=___________________  # ← Fill this
CLOUDINARY_API_KEY=___________________     # ← Fill this
CLOUDINARY_API_SECRET=___________________  # ← Fill this
```

**How to get these:**

1. Go to https://cloudinary.com/users/register/free
2. Sign up for free account
3. After login, you'll see your dashboard
4. **Copy these 3 values from the dashboard:**
   - **Cloud name** → Paste into `CLOUDINARY_CLOUD_NAME`
   - **API Key** → Paste into `CLOUDINARY_API_KEY`
   - **API Secret** → Paste into `CLOUDINARY_API_SECRET`

**Screenshot location:** They're right at the top of your Cloudinary dashboard under "Product Environment Credentials"

---

### 🔷 Section 4: Instagram (Publishing)

```bash
INSTAGRAM_ACCESS_TOKEN=___________________  # ← Fill this (LONG token)
INSTAGRAM_USER_ID=___________________      # ← Fill this
INSTAGRAM_API_VERSION=v21.0                # ← Leave as-is
```

**This is the most complex part. Follow carefully:**

#### 4.1: Convert to Professional Account

1. Open Instagram app on your phone
2. Go to your profile
3. Settings → Account → Switch to Professional Account
4. Choose "Business" or "Creator" (either works)
5. Complete the setup

#### 4.2: Link to Facebook Page

1. Go to https://www.facebook.com/pages/create
2. Create a Facebook Page (any name, any category)
3. In Instagram app:
   - Settings → Account → Linked Accounts
   - Link your Facebook Page

#### 4.3: Create Meta Developer App

1. Go to https://developers.facebook.com/
2. Click "My Apps" → "Create App"
3. Choose "Business" app type
4. Fill in:
   - **App name:** "Instagram Reel Automation"
   - **Contact email:** Your email
   - Create app
5. In left sidebar, click "Add Product"
6. Find "Instagram" → Click "Set Up"
7. You'll see Instagram Basic Display and Instagram Graph API — **use Graph API**

#### 4.4: Get Your Instagram User ID

1. In your Meta app dashboard, go to Tools → Graph API Explorer
2. In top-right, select your app name
3. Click "Generate Access Token"
4. Select these permissions:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_read_engagement`
   - `pages_show_list`
5. Click "Generate Token" → Copy it (save temporarily)
6. In the Graph API Explorer:
   - Change GET request to: `me/accounts`
   - Click "Submit"
   - You'll see your Facebook pages
   - Find your page, copy its `id`
7. Now change GET request to: `YOUR_PAGE_ID?fields=instagram_business_account`
8. Click "Submit"
9. **Copy the `instagram_business_account` id** → **This is your INSTAGRAM_USER_ID**
10. **Paste it into your .env file**

#### 4.5: Get Long-Lived Access Token

The token you generated expires in 1 hour. You need a long-lived one (60 days):

1. Go to https://developers.facebook.com/tools/accesstoken/
2. Find your app
3. Click "Extend Access Token"
4. **Copy the long-lived token**
5. **Paste it into INSTAGRAM_ACCESS_TOKEN in your .env**

**Important:** This token expires after 60 days. You'll need to refresh it.

---

## ✅ Final `.env` Example (Filled)

```bash
# Google Drive
GOOGLE_DRIVE_FILE_ID=1aBcDeFgHiJkLmNoPqRsTuVwXyZ
GOOGLE_APPLICATION_CREDENTIALS=credentials.json

# Processing
DATA_DIR=/data
CLIP_DURATION_SECONDS=60
AUDIO_LANGUAGE=hin
UPLOAD_INTERVAL_SECONDS=3600
VIDEO_CRF=23
VIDEO_PRESET=veryfast
MAX_RETRIES=3
DRY_RUN=true
AUTO_PUBLISH=false

# Cloudinary
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=123456789012345
CLOUDINARY_API_SECRET=abcdefghijklmnopqrstuvwxyz1234

# Instagram
INSTAGRAM_ACCESS_TOKEN=EAABwzLixnjYBO1234...very_long_token...xyz789
INSTAGRAM_USER_ID=17841401234567890
INSTAGRAM_API_VERSION=v21.0
```

---

## 🧪 Step 2: Test Your Setup

After filling in all fields:

```bash
# 1. Validate credentials
python -m app.main validate-config

# 2. Test download (downloads to /data/sources/)
python -m app.main download

# 3. Inspect the video
python -m app.main inspect-source

# 4. Generate clips (saved to /data/clips/)
python -m app.main process

# 5. Check status
python -m app.main status
```

**Expected output:**
- `validate-config` → "[OK] All configuration variables are valid."
- `download` → Downloads your video (may take 10-20 min for 4GB)
- `inspect-source` → Shows video info and audio tracks
- `process` → Creates all clips (may take 1-2 hours)
- `status` → Shows how many clips are ready

---

## 🚨 Important: Keep DRY_RUN=true

While `DRY_RUN=true`:
- ✅ Downloads source
- ✅ Generates clips
- ❌ Does NOT upload to Cloudinary
- ❌ Does NOT publish to Instagram

This lets you test everything safely!

---

## 🔐 Security Checklist

Before committing to Git:

```bash
# Make sure these files are ignored:
cat .gitignore | grep -E "\.env|credentials\.json"
```

Should show:
```
.env
credentials.json
```

**Never commit these files!** They contain your secrets.

---

## ❓ Troubleshooting

### "GOOGLE_DRIVE_FILE_ID: Not set"
→ You didn't save the `.env` file, or it's in the wrong location. It should be in the same folder as `app/` and `requirements.txt`.

### "Failed to get file metadata: 404"
→ Your service account doesn't have access to the Drive file. Go to Drive → Share the file with the service account email.

### "Invalid token" from Instagram
→ Your access token expired or is wrong. Generate a new long-lived token.

### "Audio language 'hin' not found"
→ Your video doesn't have a Hindi track. Run `inspect-source` to see available tracks, then change `AUDIO_LANGUAGE` in `.env`.

---

## 📝 Quick Reference

| What | Where to Get It | Where to Put It |
|------|----------------|-----------------|
| Drive File ID | Google Drive file URL | `GOOGLE_DRIVE_FILE_ID` in .env |
| Service Account JSON | Google Cloud Console | Save as `credentials.json` |
| Cloudinary credentials | cloudinary.com dashboard | 3 fields in .env |
| Instagram User ID | Meta Graph API Explorer | `INSTAGRAM_USER_ID` in .env |
| Access Token | Meta Access Token Tool | `INSTAGRAM_ACCESS_TOKEN` in .env |

---

## 🎯 Next Steps

Once all tests pass:

1. **Try a dry run of the full worker:**
   ```bash
   python -m app.main run-worker
   ```
   (Press Ctrl+C to stop)

2. **When ready to actually publish:**
   ```bash
   # In .env, change:
   DRY_RUN=false
   AUTO_PUBLISH=true
   ```

3. **Run the worker for real:**
   ```bash
   python -m app.main run-worker
   ```

---

Need help? Check the full README.md for detailed troubleshooting!
