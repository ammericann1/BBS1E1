import cloudinary
import cloudinary.uploader
import requests
import time
from typing import Optional, Dict, Any
from config import Config

class CloudinaryUploader:
    def __init__(self, config: Config):
        self.config = config
        cloudinary.config(
            cloud_name=config.CLOUDINARY_CLOUD_NAME,
            api_key=config.CLOUDINARY_API_KEY,
            api_secret=config.CLOUDINARY_API_SECRET
        )

    def upload_video(self, video_path: str, public_id: Optional[str] = None) -> Dict[str, Any]:
        try:
            result = cloudinary.uploader.upload(
                video_path,
                resource_type="video",
                public_id=public_id,
                chunk_size=6000000,
                eager=[{"quality": "auto", "fetch_format": "mp4"}]
            )
            print(f"Uploaded to Cloudinary: {result['secure_url']}")
            return result
        except Exception as e:
            print(f"Cloudinary upload failed: {e}")
            raise

class InstagramUploader:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = "https://graph.facebook.com/v18.0"

    def upload_video(self, video_url: str, caption: str = "") -> bool:
        try:
            # Step 1: Create media container
            container_url = f"{self.base_url}/{self.config.INSTAGRAM_USER_ID}/media"
            container_params = {
                'media_type': 'VIDEO',
                'video_url': video_url,
                'caption': caption,
                'access_token': self.config.INSTAGRAM_ACCESS_TOKEN
            }
            response = requests.post(container_url, data=container_params)
            data = response.json()
            if 'id' not in data:
                print("Container creation failed:", data)
                return False
            creation_id = data['id']
            print("Container created:", creation_id)

            # Step 2: Wait for processing
            for _ in range(30):
                time.sleep(10)
                status = requests.get(f"{self.base_url}/{creation_id}", params={
                    'fields': 'status_code',
                    'access_token': self.config.INSTAGRAM_ACCESS_TOKEN
                }).json()
                if status.get("status_code") == "FINISHED":
                    print("Video processed.")
                    break
                elif status.get("status_code") == "ERROR":
                    print("Video processing failed:", status)
                    return False
                else:
                    print("Processing status:", status.get("status_code"))

            # Step 3: Publish
            publish_response = requests.post(f"{self.base_url}/{self.config.INSTAGRAM_USER_ID}/media_publish", data={
                'creation_id': creation_id,
                'access_token': self.config.INSTAGRAM_ACCESS_TOKEN
            })
            publish_data = publish_response.json()
            if 'id' in publish_data:
                print("Published successfully:", publish_data['id'])
                return True
            else:
                print("Publishing failed:", publish_data)
                return False

        except Exception as e:
            print(f"Instagram upload error: {e}")
            return False
