#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import hashlib
import json
import os
import random
import subprocess
import tempfile
import urllib.request
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

def run(cmd):
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, check=True)

def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "KP-Kids-Short-Editor/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

def ffprobe_duration(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True
    )
    return float(p.stdout.strip())

def esc(s):
    # Escape for ffmpeg drawtext
    return (
        str(s or "")
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )

def category_label(category):
    mapping = {
        "alphabet": "ABC TIME",
        "letters": "LETTER MATCH",
        "phonics": "PHONICS",
        "numbers": "NUMBERS",
        "counting": "COUNT WITH US",
        "shapes": "SHAPES",
        "colors": "COLORS",
        "science": "SCIENCE TIME",
        "space": "SPACE TIME",
        "nature": "NATURE TIME",
        "body": "HEALTHY BODY",
        "safety": "SAFE & SMART",
        "emotions": "FEELINGS",
        "manners": "KINDNESS TIME",
        "community": "COMMUNITY HELPERS",
        "transport": "LET'S GO",
        "weather": "WEATHER TIME",
        "animals": "ANIMAL TIME",
        "food": "HEALTHY FOOD",
        "math": "MATH TIME",
        "opposites": "OPPOSITES",
        "world": "EXPLORE THE WORLD",
        "positions": "POSITION WORDS",
        "sorting": "SORT & LEARN",
        "patterns": "PATTERN TIME",
        "sizes": "SIZE TIME",
        "directions": "DIRECTIONS",
        "calendar": "DAYS & CALENDAR",
        "seasons": "SEASONS",
        "time": "TIME & ROUTINES",
    }
    return mapping.get(str(category or "").lower(), "KP KIDS")

def style_from_id(short_id):
    digest = hashlib.sha256(str(short_id).encode("utf-8")).digest()
    return digest[0] % 5

def edit_video(src, out, payload):
    short_id = payload.get("short_id", "")
    category = str(payload.get("category", "")).lower()
    topic = str(payload.get("topic", "") or payload.get("title", "KP Kids"))
    lead = str(payload.get("lead_character", ""))
    style = style_from_id(short_id)
    duration = min(15.0, ffprobe_duration(src))

    # Keep overlays concise so educational visual stays dominant.
    top_text = category_label(category)[:28]
    topic_text = topic.replace("#Shorts", "").strip()[:34]
    end_texts = ["GREAT JOB!", "YOU DID IT!", "KEEP LEARNING!", "AWESOME WORK!", "SEE YOU NEXT TIME!"]
    end_text = end_texts[style]

    # Five real editing variants: composition, framing, timing, overlay placement.
    if style == 0:
        zoom = "1+0.018*sin(2*PI*t/6)"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"
        top_y, topic_y = 80, 1510
    elif style == 1:
        zoom = "1.015+0.012*sin(2*PI*t/5)"
        x = "(iw-iw/zoom)/2+8*sin(2*PI*t/7)"
        y = "(ih-ih/zoom)/2"
        top_y, topic_y = 1515, 95
    elif style == 2:
        zoom = "1.008+0.016*sin(2*PI*t/7)"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2+10*sin(2*PI*t/8)"
        top_y, topic_y = 85, 1460
    elif style == 3:
        zoom = "1.012+0.010*sin(2*PI*t/4.5)"
        x = "(iw-iw/zoom)/2+6*sin(2*PI*t/5)"
        y = "(ih-ih/zoom)/2+6*cos(2*PI*t/6)"
        top_y, topic_y = 1480, 90
    else:
        zoom = "1.01+0.014*sin(2*PI*t/6.5)"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"
        top_y, topic_y = 92, 1490

    # Build a blurred 9:16 background + contained main video.
    # Then add subtle dynamic reframing and text layers.
    vf = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=28[bg2];"
        "[fg]scale=1000:1778:force_original_aspect_ratio=decrease[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];"
        f"[base]zoompan=z='{zoom}':x='{x}':y='{y}':"
        "d=1:s=1080x1920:fps=24[z];"
        f"[z]drawbox=x=55:y={top_y-30}:w=970:h=125:color=black@0.28:t=fill:"
        f"enable='between(t,0,2.2)'[b1];"
        f"[b1]drawtext=fontfile={FONT}:text='{esc(top_text)}':"
        f"fontcolor=white:fontsize=54:x=(w-text_w)/2:y={top_y}:"
        "enable='between(t,0,2.2)'[t1];"
        f"[t1]drawbox=x=55:y={topic_y-25}:w=970:h=120:color=black@0.24:t=fill:"
        "enable='between(t,2.4,5.2)'[b2];"
        f"[b2]drawtext=fontfile={FONT}:text='{esc(topic_text)}':"
        f"fontcolor=white:fontsize=48:x=(w-text_w)/2:y={topic_y}:"
        "enable='between(t,2.4,5.2)'[t2];"
        f"[t2]drawtext=fontfile={FONT}:text='{esc(end_text)}':"
        "fontcolor=white:fontsize=60:"
        "box=1:boxcolor=black@0.30:boxborderw=24:"
        "x=(w-text_w)/2:y=145:"
        f"enable='between(t,{max(0,duration-1.8):.2f},{duration:.2f})'[vout]"
    )

    # Add a very low-volume synthesized sparkle/chime at answer and ending moments.
    # Original dialogue stays dominant.
    af = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0[a0];"
        "sine=frequency=880:sample_rate=48000:duration=0.10,volume=0.025,"
        "adelay=3700|3700[ch1];"
        f"sine=frequency=1046:sample_rate=48000:duration=0.12,volume=0.020,"
        f"adelay={int(max(0,duration-1.5)*1000)}|{int(max(0,duration-1.5)*1000)}[ch2];"
        "[a0][ch1][ch2]amix=inputs=3:normalize=0:duration=first[aout]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", vf + ";" + af,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(out)
    ]
    run(cmd)
    return style

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload-b64", required=True)
    ap.add_argument("--output", default="kp_kids_edited_short.mp4")
    args = ap.parse_args()

    payload = json.loads(base64.b64decode(args.payload_b64).decode("utf-8"))
    source_url = payload["video_url"]

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "source.mp4"
        download(source_url, src)
        style = edit_video(src, Path(args.output), payload)

    meta = dict(payload)
    meta["edit_style"] = style
    Path("edit_result.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
