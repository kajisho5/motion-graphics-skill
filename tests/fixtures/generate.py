"""Synthetic media fixtures generated with ffmpeg at test time (nothing binary is committed). Only the tests call
ffmpeg directly; the skill under test never does.

  video.mp4       6 s 320x180 H.264 + mono AAC tone
  video_short.mp4 2 s 320x180 H.264, no audio (used for time-range negative tests)
  logo.png        64x64 PNG with alpha (a translucent red square) for image_overlay tests
  font.ttf        a copy of a real font already on this machine (DejaVu preferred; any .ttf/.ttc otherwise),
                  for the font_file path -- the test only needs valid font bytes, not a specific family
  text.txt        not media"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Dict

FF = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-nostdin"]
# DejaVu first (Linux CI installs fonts-dejavu-core); otherwise any font ffmpeg-skill/overlay --font-file already
# knows how to load, so this fixture (and the tests using it) works on every CI platform without extra installs.
SYSTEM_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",     # macOS
    "/System/Library/Fonts/Helvetica.ttc",              # macOS
    "/Library/Fonts/Arial.ttf",                         # macOS (older layout)
    "C:\\Windows\\Fonts\\arial.ttf",                    # Windows
    "C:\\Windows\\Fonts\\calibri.ttf",                  # Windows
)


def available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args):
    subprocess.run(FF + args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def build_all(directory: Path) -> Dict[str, Path]:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    f = {k: d / v for k, v in {"video": "video.mp4", "video_short": "video_short.mp4", "logo": "logo.png", "font": "font.ttf", "text": "text.txt"}.items()}
    _run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-f", "lavfi", "-i", "aevalsrc='0.1*sin(2*PI*440*t)':s=48000",
          "-t", "6", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(f["video"])])
    _run(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25", "-t", "2", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(f["video_short"])])
    _run(["-f", "lavfi", "-i", "color=c=red@0.6:s=64x64", "-frames:v", "1", str(f["logo"])])
    for cand in SYSTEM_FONT_CANDIDATES:
        if Path(cand).is_file():
            shutil.copy(cand, f["font"])
            break
    f["text"].write_text("not media\n", encoding="utf-8")
    return f
