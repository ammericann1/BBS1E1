from moviepy.editor import VideoFileClip
from PIL import Image, ImageEnhance
import numpy as np
import os
from datetime import datetime
from typing import Tuple
from config import Config

def enhance_frame(frame):
    """Apply brightness, contrast, saturation, sharpness, and clarity."""
    img = Image.fromarray(frame)

    # Apply enhancements
    img = ImageEnhance.Brightness(img).enhance(1.1)    # +10% Brightness
    img = ImageEnhance.Contrast(img).enhance(1.2)      # +20% Contrast
    img = ImageEnhance.Color(img).enhance(1.15)        # +15% Saturation
    img = ImageEnhance.Sharpness(img).enhance(2.0)     # Sharpen edges
    img = ImageEnhance.Contrast(img).enhance(1.1)      # Clarity (extra contrast)

    return np.array(img)

class VideoProcessor:
    def __init__(self, config: Config):
        self.config = config

    def get_video_duration(self, video_path: str) -> float:
        with VideoFileClip(video_path) as video:
            return float(video.duration)

    def trim_video_clip(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        output_path: str
    ) -> str:
        try:
            out_dir = os.path.dirname(output_path) or "."
            os.makedirs(out_dir, exist_ok=True)

            with VideoFileClip(video_path) as video:
                actual_end_time = min(end_time, video.duration)

                if start_time >= actual_end_time:
                    raise ValueError(
                        f"Start time {start_time} is >= end ({actual_end_time}). "
                        f"Video duration: {video.duration}"
                    )

                clip = video.subclip(start_time, actual_end_time)

                # ✅ Apply enhancements
                clip = clip.fl_image(enhance_frame)

                clip.write_videofile(
                    output_path,
                    codec="libx264",
                    audio_codec="aac",
                    temp_audiofile="temp-audio.m4a",
                    remove_temp=True,
                    verbose=False,
                    logger=None
                )

            print(f"✅ Clip created: {output_path} ({start_time}s - {actual_end_time}s)")
            return output_path

        except Exception as e:
            print(f"❌ Error creating clip: {e}")
            raise

    def calculate_next_clip_times(
        self,
        current_position: float,
        clip_duration: int,
        video_duration: float
    ) -> Tuple[float, float, bool]:
        start_time = current_position
        end_time = min(current_position + clip_duration, video_duration)
        has_more = end_time < video_duration
        return start_time, end_time, has_more

    def generate_clip_filename(
        self,
        clip_count: int,
        start_time: float,
        end_time: float
    ) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"clip_{clip_count:03d}_{timestamp}_{int(start_time)}s-{int(end_time)}s.mp4"
