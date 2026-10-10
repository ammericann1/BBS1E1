import time
import os
from config import Config
from downloader import DropboxDownloader, StateManager
from processor import VideoProcessor
from uploader import CloudinaryUploader, InstagramUploader

class VideoScheduler:
    def __init__(self, config: Config, gdrive_url: str, video_start_time: float = 0, video_end_time: float = None):
        self.config = config
        self.video_url = gdrive_url
        self.video_start_time = video_start_time
        self.video_end_time = video_end_time

        self.downloader = DropboxDownloader(config)
        self.state_manager = StateManager(config.STATE_FILE)
        self.processor = VideoProcessor(config)
        self.cloud_uploader = CloudinaryUploader(config)
        self.instagram_uploader = InstagramUploader(config)

        self.video_file_path = os.path.join(config.DOWNLOAD_PATH, "source.mkv")
        self._download_video_once()

    def _download_video_once(self):
        self.downloader.download_video(self.video_url, self.video_file_path)

    def start_scheduling(self):
        while True:
            state = self.state_manager.load_state()
            current_position = max(state['current_position'], self.video_start_time)
            clip_count = state['clip_count']

            video_duration = self.processor.get_video_duration(self.video_file_path)
            end_limit = self.video_end_time if self.video_end_time else video_duration

            if current_position >= end_limit:
                print("✅ Reached end of video.")
                break

            start, end, has_more = self.processor.calculate_next_clip_times(
                current_position,
                self.config.CLIP_DURATION,
                end_limit
            )

            clip_name = self.processor.generate_clip_filename(clip_count, start, end)
            clip_path = os.path.join(self.config.CLIPS_PATH, clip_name)
            self.processor.trim_video_clip(self.video_file_path, start, end, clip_path)

            cloud_result = self.cloud_uploader.upload_video(clip_path)
            success = self.instagram_uploader.upload_video(cloud_result['secure_url'], caption="Auto Clip Upload")

            if success:
                self.state_manager.save_state({
                    'current_position': end,
                    'clip_count': clip_count + 1,
                    'last_processed': time.time()
                })
            else:
                print("⛔ Upload failed. Retrying in next cycle...")

            print(f"⏳ Sleeping for {self.config.SLEEP_INTERVAL} seconds...")
            time.sleep(self.config.SLEEP_INTERVAL)
