import os
import json
import requests

class DropboxDownloader:
    def __init__(self, config):
        self.config = config

    def download_video(self, video_url: str, output_path: str):
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Handle different URL types
            if "dropbox" in video_url.lower():
                # Ensure it's a direct download link
                if "?dl=1" not in video_url:
                    video_url = video_url.split("?")[0] + "?dl=1"
                print(f"\n📥 Downloading video from Dropbox...")
            elif "drive.google.com" in video_url.lower():
                print(f"\n📥 Downloading video from Google Drive...")
                # Ensure proper Google Drive download format
                if "/file/d/" in video_url:
                    file_id = video_url.split("/file/d/")[1].split("/")[0]
                    video_url = f"https://drive.google.com/uc?id={file_id}&export=download"
            else:
                print(f"\n📥 Downloading video...")

            print(f"→ {video_url}")

            # Stream download for large files
            response = requests.get(video_url, stream=True, allow_redirects=True)

            if response.status_code != 200:
                raise Exception(f"Failed to download video. Status code: {response.status_code}. The link may be expired or restricted.")

            # Detect if the response is HTML (instead of video)
            head = response.content[:200].lower() if not response.headers.get('content-type', '').startswith('video') else b''
            if b"<html" in head or b"<!doctype html" in head:
                raise Exception("Downloaded content looks like a web page (HTML), not a video. The link may be invalid or expired.")

            # Write file in chunks
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            print(f"✅ Video downloaded successfully: {output_path}")

        except Exception as e:
            print(f"❌ Error downloading video: {e}")
            print("💡 Tip: Make sure your video link is publicly accessible and not expired.")
            raise




class StateManager:
    def __init__(self, state_file: str):
        self.state_file = state_file

    def load_state(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {'current_position': 0, 'clip_count': 0, 'last_processed': None}

    def save_state(self, state: dict):
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)
