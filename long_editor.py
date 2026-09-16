#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KP Kids Long Video Editor V1.4

Builds a native 16:9 YouTube long-form compilation from existing KP Kids RAW shorts.
- Prefers archived Google Drive RAWs, falls back to original video URLs.
- Converts vertical shorts to 1920x1080 with a blurred side/background fill.
- Preserves original speech/audio.
- Adds one continuous real-music bed from the existing music/ library.
- Uses gentle sidechain ducking so speech remains dominant.
- Prepends/append the dedicated landscape intro/closure from Google Drive.
- Writes metadata for the GitHub Action callback.
"""

import argparse
import base64
import hashlib
import html
import http.cookiejar
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

EDITOR_VERSION = "KP Kids Long Editor V1.4 - Smart Pacing + Question Answer Side Sync"
INTRO_DRIVE_FILE_ID = "1K0krAhogRv5ybCw7oXdf_ZwpZ1B2_Ywv"
CLOSURE_DRIVE_FILE_ID = "1K0krAhogRv5ybCw7oXdf_ZwpZ1B2_Ywv"
OUTPUT_W = 1920
OUTPUT_H = 1080
OUTPUT_FPS = 30
VIDEO_BITRATE = "1800k"
VIDEO_MAXRATE = "2200k"
VIDEO_BUFSIZE = "4400k"
AUDIO_RATE = 48000
AUDIO_BITRATE = "128k"
FOREGROUND_H = 1010
SEGMENT_FADE = 0.18
MUSIC_GAIN = 0.72

# Same pacing philosophy as the Shorts editor.
LONG_BODY_TARGET_SPEED = 0.95
LONG_SPEECH_SPEED = 0.99
LONG_SHORT_PAUSE_SPEED = 0.96
LONG_MEDIUM_PAUSE_SPEED = 0.93
LONG_LONG_PAUSE_SPEED = 0.90
LONG_MIN_PACING_SEGMENT = 0.08
LONG_SILENCE_DB = -33
LONG_SILENCE_MIN_DURATION = 0.22

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_ITALIC = "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"

LEFT_X = 105
RIGHT_X = 1325
SIDE_W = 500
SIDE_TITLE_Y = 260
SIDE_KEYWORD_Y = 700
SIDE_CATEGORY_Y = 170

MUSIC_PROFILES = {
    "playful_dance": [
        "Bunny Hop - Quincas Moreira.mp3",
        "Little Samba - Quincas Moreira.mp3",
        "Shake It - Aakash Gandhi.mp3",
        "Hot Drop Bus - Rod Kim.mp3",
        "Kazoom - Quincas Moreira.mp3",
        "The Emperor's New Nikes - DJ Williams.mp3",
    ],
    "calm_warm": [
        "Bedtime - Reed Mathis.mp3",
        "Take it Slow - SefChol.mp3",
        "A Truly Dazzling Dream - National Sweetheart.mp3",
        "Little Fish - Quincas Moreira.mp3",
    ],
    "curious_space": [
        "Event Horizon - The Grey Room _ Density & Time.mp3",
        "Frame-Dragging - The Grey Room _ Density & Time.mp3",
        "Rapid Unscheduled Disassembly - The Grey Room _ Density & Time.mp3",
    ],
    "sunny_plucks": [
        "Sunny Day - Reed Mathis.mp3",
        "My Dog Is Happy - Reed Mathis.mp3",
        "Good Days - Yung Logos.mp3",
        "Friday Fugue - Trevor Garrod.mp3",
        "Little Fish - Quincas Moreira.mp3",
    ],
    "learning_plucks": [
        "Ballerina - Quincas Moreira.mp3",
        "Good Days - Yung Logos.mp3",
        "Friday Fugue - Trevor Garrod.mp3",
        "Sunny Day - Reed Mathis.mp3",
        "Snake on the Beach - Nico Staf.mp3",
        "The Emperor's New Nikes - DJ Williams.mp3",
    ],
}


def run(cmd):
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, check=True)


def capture(cmd):
    return subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip()


def ffprobe_duration(path):
    try:
        return float(capture([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path)
        ]))
    except Exception:
        return 0.0



def ffprobe_dimensions(path):
    out = capture([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", str(path)
    ])
    try:
        w, h = out.split("x", 1)
        return int(w), int(h)
    except Exception:
        raise RuntimeError(f"Could not read video dimensions for {path}")


def assert_landscape_brand_clip(path, label):
    w, h = ffprobe_dimensions(path)
    ratio = (w / h) if h else 0.0
    print(f"{label}: source dimensions {w}x{h} ratio={ratio:.3f}", flush=True)
    if w <= h or ratio < 1.30:
        raise RuntimeError(
            f"{label} is NOT landscape ({w}x{h}). "
            "Refusing to use a Shorts/vertical branding clip in the Long video."
        )
    return w, h


def payload_bool(payload, key, default=False):
    v = payload.get(key, default)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def has_audio(path):
    try:
        out = capture([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", str(path)
        ])
        return bool(out.strip())
    except Exception:
        return False


def looks_like_media(path):
    try:
        return Path(path).stat().st_size > 40_000
    except Exception:
        return False


def download(url, dest, label="media"):
    req = urllib.request.Request(url, headers={"User-Agent": "KP-Kids-Long-Editor/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    if not looks_like_media(dest):
        raise RuntimeError(f"{label}: downloaded file is too small/invalid")


def download_via_n8n_proxy(base_url, token, file_id, dest, label="Drive proxy media"):
    base_url = str(base_url or "").strip()
    token = str(token or "").strip()
    file_id = str(file_id or "").strip()
    if not base_url or not token or not file_id:
        raise RuntimeError(f"{label}: missing proxy configuration")
    sep = "&" if "?" in base_url else "?"
    url = base_url + sep + urllib.parse.urlencode({"file_id": file_id, "token": token})
    download(url, dest, label=label)


def download_google_drive_file(file_id, dest, label="Google Drive media"):
    """Public Google Drive downloader with confirmation-page handling."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", "Mozilla/5.0 KP-Kids-Long-Editor/1.0"), ("Accept", "*/*")]
    endpoints = [
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download",
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}",
    ]

    def fetch(url):
        req = urllib.request.Request(url)
        with opener.open(req, timeout=180) as r:
            return r.read(), (r.headers.get("Content-Type") or "").lower(), r.geturl()

    last = None
    for url in endpoints:
        for attempt in range(3):
            try:
                data, ctype, final_url = fetch(url)
                if "text/html" not in ctype and len(data) > 40_000:
                    Path(dest).write_bytes(data)
                    print(f"{label}: downloaded from Google Drive", flush=True)
                    return
                text = data.decode("utf-8", "ignore")
                candidates = []
                for m in re.finditer(r'href="([^"]*?/uc\?export=download[^"]+)"', text):
                    u = html.unescape(m.group(1)).replace("\\/", "/")
                    if u.startswith("/"):
                        u = urllib.parse.urljoin(final_url, u)
                    candidates.append(u)
                token = re.search(r'confirm=([0-9A-Za-z_-]+)', text)
                if token:
                    candidates.append(
                        "https://drive.usercontent.google.com/download?" +
                        urllib.parse.urlencode({"id": file_id, "export": "download", "confirm": token.group(1)})
                    )
                for c in candidates:
                    d2, ct2, _ = fetch(c)
                    if "text/html" not in ct2 and len(d2) > 40_000:
                        Path(dest).write_bytes(d2)
                        print(f"{label}: downloaded after confirmation", flush=True)
                        return
                last = RuntimeError(f"{label}: Google Drive returned HTML instead of media")
            except Exception as e:
                last = e
                if attempt < 2:
                    time.sleep(2 + attempt * 3)
        # next endpoint
    raise RuntimeError(f"{label}: failed to download Google Drive file {file_id}: {last}")


def sanitize_text(s):
    return str(s or "").replace("\n", " ").strip()




def detect_silence_intervals(path, duration):
    if not has_audio(path) or duration <= 0:
        return []
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-t", f"{duration:.3f}", "-i", str(path),
        "-af", f"silencedetect=noise={LONG_SILENCE_DB}dB:d={LONG_SILENCE_MIN_DURATION}",
        "-f", "null", "-"
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    intervals = []
    for i, st in enumerate(starts):
        en = ends[i] if i < len(ends) else duration
        st = max(0.0, min(st, duration))
        en = max(st, min(en, duration))
        if en - st >= LONG_SILENCE_MIN_DURATION - 0.01:
            intervals.append((st, en))
    merged = []
    for st, en in sorted(intervals):
        if merged and st <= merged[-1][1] + 0.03:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((st, en))
    return merged


def pause_speed(length):
    if length >= 0.85:
        return LONG_LONG_PAUSE_SPEED
    if length >= 0.45:
        return LONG_MEDIUM_PAUSE_SPEED
    return LONG_SHORT_PAUSE_SPEED


def build_pacing_plan(duration, silence_intervals):
    if duration <= 0:
        return {"segments": [], "output_duration": 0.0, "target_duration": 0.0, "silence_count": 0}

    marks = {0.0, duration}
    for st, en in silence_intervals:
        marks.add(max(0.0, min(st, duration)))
        marks.add(max(0.0, min(en, duration)))
    marks = sorted(marks)

    raw = []
    for a, b in zip(marks, marks[1:]):
        if b - a < LONG_MIN_PACING_SEGMENT:
            continue
        mid = (a + b) / 2.0
        is_silence = any(st <= mid <= en for st, en in silence_intervals)
        raw.append({
            "source_start": a,
            "source_end": b,
            "kind": "silence" if is_silence else "speech",
            "speed": pause_speed(b-a) if is_silence else LONG_SPEECH_SPEED,
        })

    if not raw:
        raw = [{
            "source_start": 0.0,
            "source_end": duration,
            "kind": "speech",
            "speed": LONG_BODY_TARGET_SPEED,
        }]

    target_duration = duration / LONG_BODY_TARGET_SPEED
    current = sum((x["source_end"] - x["source_start"]) / x["speed"] for x in raw)
    factor = current / target_duration if target_duration > 0 else 1.0

    for x in raw:
        x["speed"] = min(1.0, max(0.88, x["speed"] * factor))

    out_t = 0.0
    segments = []
    for x in raw:
        seg = dict(x)
        seg["output_start"] = out_t
        seg_dur = (seg["source_end"] - seg["source_start"]) / seg["speed"]
        out_t += seg_dur
        seg["output_end"] = out_t
        segments.append(seg)

    return {
        "segments": segments,
        "output_duration": out_t,
        "target_duration": target_duration,
        "silence_count": len(silence_intervals),
    }


def map_source_time_to_output(seconds, pacing_plan):
    try:
        t = max(0.0, float(seconds))
    except Exception:
        return None
    segs = pacing_plan.get("segments") or []
    if not segs:
        return t / LONG_BODY_TARGET_SPEED
    for seg in segs:
        if t <= seg["source_end"] + 1e-6:
            local = max(0.0, t - seg["source_start"])
            return seg["output_start"] + local / seg["speed"]
    return pacing_plan.get("output_duration", t / LONG_BODY_TARGET_SPEED)


def build_paced_video_prefix(pacing_plan):
    segs = pacing_plan.get("segments") or []
    if not segs:
        return f"[0:v]setpts=PTS/{LONG_BODY_TARGET_SPEED:.5f}[pacedv];"
    parts, labels = [], []
    for i, seg in enumerate(segs):
        label = f"pv{i}"
        labels.append(f"[{label}]")
        parts.append(
            f"[0:v]trim=start={seg['source_start']:.6f}:end={seg['source_end']:.6f},"
            f"setpts=(PTS-STARTPTS)/{seg['speed']:.6f},settb=AVTB[{label}];"
        )
    parts.append("".join(labels) + f"concat=n={len(segs)}:v=1:a=0[pacedv];")
    return "".join(parts)


def build_paced_audio_prefix(pacing_plan):
    segs = pacing_plan.get("segments") or []
    if not segs:
        return f"[0:a]atempo={LONG_BODY_TARGET_SPEED:.5f}[paceda];"
    parts, labels = [], []
    for i, seg in enumerate(segs):
        label = f"pa{i}"
        labels.append(f"[{label}]")
        parts.append(
            f"[0:a]atrim=start={seg['source_start']:.6f}:end={seg['source_end']:.6f},"
            f"asetpts=PTS-STARTPTS,atempo={seg['speed']:.6f}[{label}];"
        )
    parts.append("".join(labels) + f"concat=n={len(segs)}:v=0:a=1[paceda];")
    return "".join(parts)


def create_paced_clip(src, dest, pacing_plan):
    """Apply the variable pacing to video AND original audio before landscape composition."""
    audio = has_audio(src)
    fc = build_paced_video_prefix(pacing_plan)

    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if audio:
        fc += build_paced_audio_prefix(pacing_plan)
        fc += (
            f"[pacedv]fps={OUTPUT_FPS},format=yuv420p[v];"
            f"[paceda]aresample={AUDIO_RATE},aformat=channel_layouts=stereo[a]"
        )
        cmd += [
            "-filter_complex", fc,
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        dur = pacing_plan.get("output_duration") or max(0.5, ffprobe_duration(src) / LONG_BODY_TARGET_SPEED)
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
        fc += f"[pacedv]fps={OUTPUT_FPS},format=yuv420p[v]"
        cmd += [
            "-filter_complex", fc,
            "-map", "[v]", "-map", "1:a:0",
        ]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", VIDEO_BITRATE, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(dest)
    ]
    run(cmd)


def parse_timeline_segment(timeline, keywords):
    text = str(timeline or "")
    for line in text.splitlines():
        low = line.lower()
        if not any(k in low for k in keywords):
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:sec|s)\b", low)
        if m:
            return float(m.group(1)), float(m.group(2))
        m = re.search(r"(?:at\s*)?(\d+(?:\.\d+)?)\s*(?:sec|s)\b", low)
        if m:
            t = float(m.group(1))
            return t, t + 0.8
    return None


def choose_answer_time_from_silence(silence_intervals, duration):
    """Answer often begins right after the main thinking pause."""
    lo, hi = duration * 0.18, duration * 0.62
    candidates = []
    target = duration * 0.36
    for st, en in silence_intervals:
        mid = (st + en) / 2.0
        if lo <= mid <= hi and en - st >= 0.26:
            score = abs(mid - target) - min(en-st, 1.4) * 0.30
            candidates.append((score, en + 0.04))
    return min(candidates)[1] if candidates else None


def derive_question_from_title(title):
    first = clean_display_title(title).split("|")[0].strip()
    return first if "?" in first else ""


def build_qa_timing(clip, source_duration, pacing_plan, silence_intervals):
    timeline = clip.get("dialogue_timeline") or ""
    q_seg = parse_timeline_segment(timeline, [" asks ", "asks only", "question"])
    a_seg = parse_timeline_segment(timeline, ["reveal/answer", "answer.", "answer ", "correct", "result"])

    answer_src = a_seg[0] if a_seg else choose_answer_time_from_silence(silence_intervals, source_duration)
    if answer_src is None:
        answer_src = source_duration * 0.35

    if q_seg:
        q_start_src, q_end_src = q_seg
    else:
        q_start_src = 0.55 if source_duration >= 6 else source_duration * 0.08
        q_end_src = min(answer_src - 0.25, q_start_src + 2.4)

    if a_seg:
        a_start_src, a_end_src = a_seg
    else:
        a_start_src = answer_src
        a_end_src = min(source_duration - 0.15, a_start_src + 1.5)

    q_start = map_source_time_to_output(q_start_src, pacing_plan)
    q_end = map_source_time_to_output(max(q_start_src + 0.7, q_end_src + 0.25), pacing_plan)
    a_start = map_source_time_to_output(a_start_src, pacing_plan)
    a_end = map_source_time_to_output(min(source_duration, max(a_start_src + 1.25, a_end_src + 1.0)), pacing_plan)

    out_dur = pacing_plan.get("output_duration") or source_duration / LONG_BODY_TARGET_SPEED
    q_start = max(0.18, min(q_start or 0.5, out_dur - 1.5))
    a_start = max(q_start + 0.8, min(a_start or out_dur * 0.36, out_dur - 1.0))
    q_end = min(max(q_start + 1.0, q_end or a_start - 0.2), a_start - 0.12)
    a_end = min(out_dur - 0.18, max(a_start + 1.25, a_end or a_start + 2.1))

    return {
        "question_start": q_start,
        "question_end": q_end,
        "answer_start": a_start,
        "answer_end": a_end,
        "question_source_start": q_start_src,
        "answer_source_start": a_start_src,
    }


CATEGORY_LABELS = {
    "alphabet":"ALPHABET", "letters":"LETTER MATCH", "phonics":"PHONICS",
    "numbers":"NUMBERS", "counting":"COUNTING", "shapes":"SHAPES",
    "colors":"COLORS", "science":"SCIENCE", "space":"SPACE",
    "nature":"NATURE", "body":"HEALTHY BODY", "safety":"SAFE & SMART",
    "emotions":"FEELINGS", "manners":"KINDNESS", "community":"HELPERS",
    "transport":"LET'S GO", "weather":"WEATHER", "animals":"ANIMALS",
    "food":"FOOD", "math":"MATH", "opposites":"OPPOSITES",
    "world":"WORLD", "positions":"POSITION WORDS", "sorting":"SORTING",
    "patterns":"PATTERNS", "sizes":"SIZES", "directions":"DIRECTIONS",
    "calendar":"CALENDAR", "seasons":"SEASONS", "time":"ROUTINES",
    "entertainment":"PLAY & MOVE",
}

CATEGORY_ACCENTS = {
    "alphabet":"0xFFD54A", "letters":"0xFFD54A", "phonics":"0xFFB347",
    "numbers":"0xFFA630", "counting":"0xF5B041", "shapes":"0x5DADE2",
    "colors":"0x7DFF7A", "science":"0x76D7C4", "space":"0xF4D03F",
    "nature":"0x7DCEA0", "body":"0xF8C471", "safety":"0xF7DC6F",
    "emotions":"0xF8C471", "manners":"0xF9E79F", "community":"0xAED6F1",
    "transport":"0xF5B041", "weather":"0xF7DC6F", "animals":"0xF4D03F",
    "food":"0x82E0AA", "math":"0xF8C471", "opposites":"0xFFEAA7",
    "world":"0x58D68D", "positions":"0xF7DC6F", "sorting":"0xF8C471",
    "patterns":"0xAED6F1", "sizes":"0xF9E79F", "directions":"0xF8C471",
    "calendar":"0xF9E79F", "seasons":"0x5DADE2", "time":"0xF8C471",
    "entertainment":"0xFFD54A",
}

def category_label(category):
    return CATEGORY_LABELS.get(str(category or "").strip().lower(), "KP KIDS")


def category_accent(category):
    return CATEGORY_ACCENTS.get(str(category or "").strip().lower(), "0xFFD54A")


def clean_display_title(value):
    t = sanitize_text(value)
    t = re.sub(r"#Shorts\b", "", t, flags=re.I)
    t = re.sub(r"\|\s*KP\s*Kids.*$", "", t, flags=re.I)
    t = re.sub(r"\s+with\s+(Kevin|Patrick|Lumi|Bibo).*$", "", t, flags=re.I)
    t = re.sub(r"^(Learn Something New|Kids Discovery|Quick Quiz|Can You Solve It)\s*:\s*", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()[:90]


def derive_side_keyword(clip):
    explicit = sanitize_text(
        clip.get("keyword") or clip.get("topic") or clip.get("subject") or ""
    )
    if explicit:
        return clean_display_title(explicit)[:34]

    title = clean_display_title(clip.get("title") or "")
    # Prefer the first useful phrase before a separator/question suffix.
    title = re.split(r"\s*[|—–]\s*", title)[0].strip()
    title = re.sub(r"^(What|Why|How|Which|Can)\s+", "", title, flags=re.I)
    title = re.sub(r"\?$", "", title).strip()
    return title[:34] or "KP KIDS"


def wrap_side_text(text, max_chars=18, max_lines=3):
    words = str(text or "").split()
    if not words:
        return ""
    lines, cur = [], ""
    for word in words:
        test = word if not cur else cur + " " + word
        if len(test) <= max_chars or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = word
            if len(lines) >= max_lines - 1:
                break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    consumed = " ".join(lines)
    original = " ".join(words)
    if len(consumed) < len(original) and lines:
        lines[-1] = lines[-1].rstrip(" .") + "…"
    return "\n".join(lines[:max_lines])


def write_text_file(path, text):
    Path(path).write_text(str(text or ""), encoding="utf-8")
    return str(Path(path).resolve())


def drawtext_file_filter(textfile, fontfile, color, fontsize, x, y, alpha, borderw=0, bordercolor=None, line_spacing=8):
    parts = [
        f"drawtext=fontfile={fontfile}",
        f"textfile={textfile}",
        f"fontcolor={color}",
        f"fontsize={fontsize}",
        f"x='{x}'",
        f"y='{y}'",
        f"alpha='{alpha}'",
        f"line_spacing={line_spacing}",
    ]
    if borderw:
        parts.append(f"borderw={borderw}")
        parts.append(f"bordercolor={bordercolor or color}")
    return ":".join(parts)


def fade_alpha_expr(start, end, fade=0.28):
    start = float(start)
    end = float(end)
    fade = max(0.08, min(float(fade), max(0.08, (end - start) / 3)))
    return (
        f"if(lt(t,{start:.3f}),0,"
        f"if(lt(t,{start+fade:.3f}),(t-{start:.3f})/{fade:.3f},"
        f"if(lt(t,{end-fade:.3f}),1,"
        f"if(lt(t,{end:.3f}),({end:.3f}-t)/{fade:.3f},0))))"
    )


def build_side_graphics_filters(
    clip, duration, clip_index, clip_total, work_dir,
    qa_timing=None, qa_sync_enabled=True
):
    """Category on the left; spoken question then spoken answer on the right."""
    category = str(clip.get("category") or "").strip().lower()
    accent = category_accent(category)
    label = category_label(category)
    series = sanitize_text(clip.get("series_name") or "")

    question = sanitize_text(clip.get("question_line") or "") or derive_question_from_title(clip.get("title") or "")
    answer = sanitize_text(clip.get("answer_line") or "")

    if series:
        left_label = f"{label}\n{series[:34]}"
    else:
        left_label = label

    left_text = write_text_file(Path(work_dir) / f"side_left_{clip_index:02d}.txt", left_label)

    # User requested NO "1 / 4" or clip counter.
    filters = []
    cat_alpha = fade_alpha_expr(0.15, max(0.7, duration - 0.2), 0.30)

    # LEFT — persistent identity only.
    filters.append(drawtext_file_filter(
        left_text, FONT, f"{accent}@0.16", 34, LEFT_X, SIDE_CATEGORY_Y,
        cat_alpha, borderw=10, bordercolor=f"{accent}@0.13", line_spacing=10
    ))
    filters.append(drawtext_file_filter(
        left_text, FONT, "white@0.92", 34, LEFT_X, SIDE_CATEGORY_Y,
        cat_alpha, borderw=2, bordercolor=f"{accent}@0.92", line_spacing=10
    ))

    meta = {
        "category_label": label,
        "accent": accent,
        "question": question,
        "answer": answer,
        "clip_index": clip_index,
        "clip_total": clip_total,
        "counter_drawn": False,
    }

    if not qa_sync_enabled:
        return filters, meta

    timing = qa_timing or {
        "question_start": 0.45,
        "question_end": min(3.0, duration * 0.28),
        "answer_start": min(4.2, duration * 0.38),
        "answer_end": min(duration - 0.2, 6.8),
    }

    # Question appears while the lead asks it.
    if question:
        q_text = write_text_file(
            Path(work_dir) / f"side_question_{clip_index:02d}.txt",
            wrap_side_text(question, max_chars=19, max_lines=3)
        )
        qs, qe = timing["question_start"], timing["question_end"]
        q_alpha = fade_alpha_expr(qs, qe, 0.25)
        q_x = f"if(lt(t,{qs+0.42:.3f}),{RIGHT_X}+55*({qs+0.42:.3f}-t)/0.42,{RIGHT_X})"

        filters.append(drawtext_file_filter(
            q_text, FONT_ITALIC, f"{accent}@0.17", 43, q_x, SIDE_TITLE_Y,
            q_alpha, borderw=11, bordercolor=f"{accent}@0.14", line_spacing=10
        ))
        filters.append(drawtext_file_filter(
            q_text, FONT_ITALIC, "white@0.98", 43, q_x, SIDE_TITLE_Y,
            q_alpha, borderw=2, bordercolor=f"{accent}@0.96", line_spacing=10
        ))

    # Answer replaces the question exactly around the spoken reveal.
    if answer:
        a_text = write_text_file(
            Path(work_dir) / f"side_answer_{clip_index:02d}.txt",
            wrap_side_text(answer.upper(), max_chars=16, max_lines=2)
        )
        ast, aen = timing["answer_start"], timing["answer_end"]
        a_alpha = fade_alpha_expr(ast, aen, 0.24)
        a_x = f"if(lt(t,{ast+0.48:.3f}),{RIGHT_X}+72*({ast+0.48:.3f}-t)/0.48,{RIGHT_X})"

        # Wide soft halo + brighter entrance pulse + crisp core.
        filters.append(drawtext_file_filter(
            a_text, FONT_ITALIC, f"{accent}@0.20", 56, a_x, SIDE_KEYWORD_Y,
            a_alpha, borderw=15, bordercolor=f"{accent}@0.15", line_spacing=9
        ))

        pulse_end = min(aen, ast + 0.58)
        pulse_alpha = fade_alpha_expr(ast, pulse_end, 0.16)
        filters.append(drawtext_file_filter(
            a_text, FONT_ITALIC, f"{accent}@0.17", 58, a_x, SIDE_KEYWORD_Y,
            pulse_alpha, borderw=20, bordercolor=f"{accent}@0.12", line_spacing=9
        ))

        filters.append(drawtext_file_filter(
            a_text, FONT_ITALIC, "white@0.99", 56, a_x, SIDE_KEYWORD_Y,
            a_alpha, borderw=2, bordercolor=f"{accent}@0.99", line_spacing=9
        ))

    meta["qa_timing"] = timing
    return filters, meta


def normalize_brand_clip(src, dest):
    """Normalize landscape intro/closure to the same delivery format."""
    dur = max(0.5, ffprobe_duration(src))
    audio = has_audio(src)
    vf = (
        f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease,"
        f"pad={OUTPUT_W}:{OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,"
        f"fps={OUTPUT_FPS},format=yuv420p"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not audio:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
    cmd += ["-vf", vf]
    if audio:
        cmd += ["-map", "0:v:0", "-map", "0:a:0", "-af", "aresample=48000,volume=1.0"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += [
        "-r", str(OUTPUT_FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", VIDEO_BITRATE, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(dest)
    ]
    run(cmd)


def normalize_vertical_clip(
    src, dest, clip=None, clip_index=1, clip_total=1,
    side_graphics_enabled=True, smart_pacing_enabled=True, qa_sync_enabled=True
):
    """Smart-pace a Short, then place it untouched in the center of a 16:9 canvas."""
    clip = clip or {}
    source_dur = max(0.5, ffprobe_duration(src))
    silence_intervals = detect_silence_intervals(src, source_dur) if smart_pacing_enabled else []
    pacing_plan = (
        build_pacing_plan(source_dur, silence_intervals)
        if smart_pacing_enabled
        else {
            "segments": [{"source_start":0.0,"source_end":source_dur,"kind":"speech","speed":1.0,
                          "output_start":0.0,"output_end":source_dur}],
            "output_duration": source_dur,
            "target_duration": source_dur,
            "silence_count": 0,
        }
    )

    paced_src = Path(dest).with_name(Path(dest).stem + "_paced.mp4")
    if smart_pacing_enabled:
        create_paced_clip(src, paced_src, pacing_plan)
    else:
        # Still normalize container/audio layout once for predictable composition.
        run([
            "ffmpeg","-y","-i",str(src),
            "-c:v","libx264","-preset","veryfast",
            "-c:a","aac","-b:a",AUDIO_BITRATE,"-ar",str(AUDIO_RATE),"-ac","2",
            "-movflags","+faststart",str(paced_src)
        ])

    dur = max(0.5, ffprobe_duration(paced_src))
    fade_out = max(0.0, dur - SEGMENT_FADE)
    qa_timing = build_qa_timing(clip, source_dur, pacing_plan, silence_intervals)

    base_graph = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},gblur=sigma=32,eq=brightness=-0.11:saturation=0.82[bg2];"
        f"[fg]scale=-2:{FOREGROUND_H}:force_original_aspect_ratio=decrease,"
        f"setsar=1[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base]"
    )

    graphic_meta = {}
    if side_graphics_enabled:
        filters, graphic_meta = build_side_graphics_filters(
            clip, dur, clip_index, clip_total, Path(dest).parent,
            qa_timing=qa_timing, qa_sync_enabled=qa_sync_enabled
        )
        chain = "[base]"
        for i, flt in enumerate(filters):
            out_label = f"g{i}"
            base_graph += f";{chain}{flt}[{out_label}]"
            chain = f"[{out_label}]"
        base_graph += (
            f";{chain}fade=t=in:st=0:d={SEGMENT_FADE:.2f},"
            f"fade=t=out:st={fade_out:.3f}:d={SEGMENT_FADE:.2f},"
            f"fps={OUTPUT_FPS},format=yuv420p[v]"
        )
    else:
        base_graph += (
            f";[base]fade=t=in:st=0:d={SEGMENT_FADE:.2f},"
            f"fade=t=out:st={fade_out:.3f}:d={SEGMENT_FADE:.2f},"
            f"fps={OUTPUT_FPS},format=yuv420p[v]"
        )

    cmd = ["ffmpeg", "-y", "-i", str(paced_src), "-filter_complex", base_graph, "-map", "[v]"]
    if has_audio(paced_src):
        cmd += [
            "-map", "0:a:0",
            "-af",
            f"aresample=48000,afade=t=in:st=0:d={SEGMENT_FADE:.2f},"
            f"afade=t=out:st={fade_out:.3f}:d={SEGMENT_FADE:.2f},"
            "loudnorm=I=-15:LRA=11:TP=-1.5"
        ]
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo", "-map", "1:a:0"]

    cmd += [
        "-r", str(OUTPUT_FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", VIDEO_BITRATE, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(dest)
    ]
    run(cmd)

    pacing_meta = {
        "source_duration": round(source_dur, 3),
        "output_duration": round(dur, 3),
        "silence_count": len(silence_intervals),
        "segments": [
            {
                "kind": x["kind"],
                "source_start": round(x["source_start"], 3),
                "source_end": round(x["source_end"], 3),
                "speed": round(x["speed"], 4),
            }
            for x in pacing_plan.get("segments", [])
        ],
    }
    return dur, graphic_meta, pacing_meta


def concat_files(files, dest):
    list_path = Path(dest).with_suffix(".concat.txt")
    with open(list_path, "w", encoding="utf-8") as f:
        for p in files:
            escaped = str(Path(p).resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy", str(dest)
    ])


def choose_music_profile(payload, clips):
    explicit = str(payload.get("music_profile") or "").strip()
    if explicit in MUSIC_PROFILES:
        return explicit
    text = " ".join(
        [str(payload.get("title") or ""), str(payload.get("content_mode") or "")] +
        [f"{c.get('category','')} {c.get('title','')} {c.get('lesson_key','')}" for c in clips]
    ).lower()
    if re.search(r"entertainment|dance|freeze|wiggle|party|clap|stomp|rhythm", text):
        return "playful_dance"
    if re.search(r"night|sleep|bedtime|calm|emotion|mindful", text):
        return "calm_warm"
    if re.search(r"space|rocket|moon|mars|planet|astronaut|mystery", text):
        return "curious_space"
    if re.search(r"nature|animal|weather|forest|garden|plant|butterfly|rain|snow", text):
        return "sunny_plucks"
    return "learning_plucks"


def choose_music_file(profile, seed_text=""):
    root = Path(__file__).resolve().parent / "music"
    candidates = [root / n for n in MUSIC_PROFILES.get(profile, []) if (root / n).exists()]
    if not candidates:
        # Any MP3 fallback.
        candidates = list(root.glob("*.mp3"))
    if not candidates:
        return None
    seed = int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:12], 16)
    return candidates[seed % len(candidates)]


def mix_music(body_video, dest, profile, seed_text=""):
    music = choose_music_file(profile, seed_text)
    if not music:
        print("No music library found; keeping body audio only.", flush=True)
        run(["ffmpeg", "-y", "-i", str(body_video), "-c", "copy", str(dest)])
        return {"profile": profile, "track": "", "segment_start": 0.0, "source": "none"}

    body_dur = ffprobe_duration(body_video)
    music_dur = ffprobe_duration(music)
    seed = int(hashlib.sha256((seed_text + str(music)).encode("utf-8")).hexdigest()[:12], 16)
    rng = random.Random(seed)
    max_start = max(0.0, music_dur - min(body_dur, 45.0) - 2.0)
    start = round(rng.uniform(0.0, max_start), 2) if max_start > 1 else 0.0
    fade_out = max(0.0, body_dur - 1.2)

    # Loop music if the compilation is longer than the track. Sidechain compressor
    # reduces the bed while source speech is present, but lets it breathe between lines.
    fc = (
        f"[0:a]aresample=48000,volume=1.0[speech];"
        f"[1:a]aresample=48000,highpass=f=70,lowpass=f=12500,"
        f"loudnorm=I=-20:TP=-2.5:LRA=9,volume={MUSIC_GAIN:.3f},"
        f"afade=t=in:st=0:d=0.8,afade=t=out:st={fade_out:.3f}:d=1.2[music];"
        f"[music][speech]sidechaincompress=threshold=0.080:ratio=1.8:attack=22:release=360:makeup=1:mix=0.30[ducked];"
        f"[speech][ducked]amix=inputs=2:duration=first:dropout_transition=2:normalize=0,"
        f"alimiter=limit=0.95[aout]"
    )
    run([
        "ffmpeg", "-y", "-i", str(body_video),
        "-ss", f"{start:.2f}", "-stream_loop", "-1", "-i", str(music),
        "-filter_complex", fc,
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", AUDIO_BITRATE,
        "-ar", str(AUDIO_RATE), "-ac", "2", "-t", f"{body_dur:.3f}",
        "-movflags", "+faststart", str(dest)
    ])
    return {"profile": profile, "track": music.name, "segment_start": start, "source": "real_music_library"}


def decode_payload(payload_b64):
    try:
        return json.loads(base64.b64decode(payload_b64).decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Invalid payload_b64: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload-b64", required=True)
    ap.add_argument("--output", default="kp_kids_long_video.mp4")
    ap.add_argument("--meta-out", default="long_result_meta.json")
    args = ap.parse_args()

    payload = decode_payload(args.payload_b64)
    clips = payload.get("clips") or payload.get("selected_clips") or []
    raw_proxy_base_url = str(payload.get("raw_proxy_base_url") or "").strip()
    raw_proxy_token = str(payload.get("raw_proxy_token") or "").strip()

    include_intro = payload_bool(payload, "include_intro", False)
    include_closure = payload_bool(payload, "include_closure", False)
    intro_drive_file_id = str(payload.get("intro_drive_file_id") or "1stHOtc3CGBDU0gmr5tr0Q1t4gntpVvdf").strip()
    closure_drive_file_id = str(payload.get("closure_drive_file_id") or "1_T_4-TtHeXCtniOkDlxct8dG1QI_uSnP").strip()

    print(
        f"Branding choice: intro={include_intro} closure={include_closure} "
        f"intro_id={intro_drive_file_id} closure_id={closure_drive_file_id}",
        flush=True,
    )
    if not isinstance(clips, list) or not clips:
        raise RuntimeError("Long-video payload contains no clips")

    work = Path(tempfile.mkdtemp(prefix="kp_kids_long_"))
    print("Workdir:", work, flush=True)
    normalized = []
    used = []
    failed = []

    for idx, clip in enumerate(clips, start=1):
        raw = work / f"clip_{idx:02d}_raw.mp4"
        ok = False
        drive_id = str(clip.get("drive_file_id") or "").strip()
        url = str(clip.get("video_url") or "").strip().replace("^=", "")
        if drive_id and raw_proxy_base_url and raw_proxy_token:
            try:
                download_via_n8n_proxy(raw_proxy_base_url, raw_proxy_token, drive_id, raw, label=f"RAW clip {idx} via n8n")
                ok = True
            except Exception as e:
                print(f"Clip {idx}: n8n Drive proxy failed: {e}", flush=True)
        if drive_id and not ok:
            try:
                download_google_drive_file(drive_id, raw, label=f"RAW clip {idx}")
                ok = True
            except Exception as e:
                print(f"Clip {idx}: public Drive failed: {e}", flush=True)
        if not ok and url:
            try:
                download(url, raw, label=f"RAW clip {idx} URL")
                ok = True
            except Exception as e:
                print(f"Clip {idx}: URL failed: {e}", flush=True)
        if not ok:
            failed.append({"short_id": clip.get("short_id", ""), "title": clip.get("title", ""), "reason": "download_failed"})
            continue

        norm = work / f"clip_{idx:02d}_landscape.mp4"
        try:
            side_graphics_enabled = payload_bool(payload, "side_graphics_enabled", True)
            smart_pacing_enabled = payload_bool(payload, "smart_pacing_enabled", True)
            qa_sync_enabled = payload_bool(payload, "qa_sync_enabled", True)
            dur, graphic_meta, pacing_meta = normalize_vertical_clip(
                raw, norm, clip=clip, clip_index=idx, clip_total=len(clips),
                side_graphics_enabled=side_graphics_enabled,
                smart_pacing_enabled=smart_pacing_enabled,
                qa_sync_enabled=qa_sync_enabled
            )
            normalized.append(norm)
            used.append({
                "short_id": str(clip.get("short_id") or ""),
                "title": sanitize_text(clip.get("title")),
                "category": str(clip.get("category") or ""),
                "duration": round(dur, 3),
                "side_graphics": graphic_meta,
                "pacing": pacing_meta,
            })
        except Exception as e:
            print(f"Clip {idx}: normalize failed: {e}", flush=True)
            failed.append({"short_id": clip.get("short_id", ""), "title": clip.get("title", ""), "reason": "normalize_failed"})

    if len(normalized) < 3:
        raise RuntimeError(f"Need at least 3 usable clips for a long video; got {len(normalized)}")

    body_no_music = work / "body_no_music.mp4"
    concat_files(normalized, body_no_music)

    profile = choose_music_profile(payload, clips)
    body = work / "body_with_music.mp4"
    music_result = mix_music(body_no_music, body, profile, str(payload.get("long_id") or payload.get("title") or "kp-kids-long"))

    final_parts = []
    brand_meta = {
        "include_intro": include_intro,
        "include_closure": include_closure,
        "intro_drive_file_id": intro_drive_file_id if include_intro else "",
        "closure_drive_file_id": closure_drive_file_id if include_closure else "",
        "intro_source_dimensions": "",
        "closure_source_dimensions": "",
    }

    if include_intro:
        intro_raw = work / "intro_raw.mp4"
        intro_norm = work / "intro_norm.mp4"
        if raw_proxy_base_url and raw_proxy_token:
            download_via_n8n_proxy(
                raw_proxy_base_url, raw_proxy_token, intro_drive_file_id,
                intro_raw, label="Dedicated LANDSCAPE intro via n8n"
            )
        else:
            download_google_drive_file(
                intro_drive_file_id, intro_raw, label="Dedicated LANDSCAPE intro"
            )
        iw, ih = assert_landscape_brand_clip(intro_raw, "Long intro")
        brand_meta["intro_source_dimensions"] = f"{iw}x{ih}"
        normalize_brand_clip(intro_raw, intro_norm)
        final_parts.append(intro_norm)

    final_parts.append(body)

    if include_closure:
        closure_raw = work / "closure_raw.mp4"
        closure_norm = work / "closure_norm.mp4"
        if raw_proxy_base_url and raw_proxy_token:
            download_via_n8n_proxy(
                raw_proxy_base_url, raw_proxy_token, closure_drive_file_id,
                closure_raw, label="Dedicated LANDSCAPE closure via n8n"
            )
        else:
            download_google_drive_file(
                closure_drive_file_id, closure_raw, label="Dedicated LANDSCAPE closure"
            )
        cw, ch = assert_landscape_brand_clip(closure_raw, "Long closure")
        brand_meta["closure_source_dimensions"] = f"{cw}x{ch}"
        normalize_brand_clip(closure_raw, closure_norm)
        final_parts.append(closure_norm)

    concat_tmp = work / "final_concat.mp4"
    concat_files(final_parts, concat_tmp)

    # Final delivery pass keeps size reasonable for Telegram preview while remaining 1080p.
    run([
        "ffmpeg", "-y", "-i", str(concat_tmp),
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", VIDEO_BITRATE, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
        "-pix_fmt", "yuv420p", "-r", str(OUTPUT_FPS),
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart", args.output
    ])

    result = {
        "editor_version": EDITOR_VERSION,
        "long_id": str(payload.get("long_id") or ""),
        "title": str(payload.get("title") or "KP Kids Long Video"),
        "output_file": Path(args.output).name,
        "duration_seconds": round(ffprobe_duration(args.output), 3),
        "resolution": f"{OUTPUT_W}x{OUTPUT_H}",
        "fps": OUTPUT_FPS,
        "clips_requested": len(clips),
        "clips_used": used,
        "clip_short_ids": [u["short_id"] for u in used if u["short_id"]],
        "clips_failed": failed,
        "music_result": music_result,
        "side_graphics_enabled": payload_bool(payload, "side_graphics_enabled", True),
        "side_graphics_style": "category left + spoken question/answer right",
        "smart_pacing_enabled": payload_bool(payload, "smart_pacing_enabled", True),
        "qa_sync_enabled": payload_bool(payload, "qa_sync_enabled", True),
        "pacing_mode": "silence-aware variable speed, Shorts-inspired",
        "branding": brand_meta,
        "intro_drive_file_id": intro_drive_file_id if include_intro else "",
        "closure_drive_file_id": closure_drive_file_id if include_closure else "",
    }
    Path(args.meta_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
