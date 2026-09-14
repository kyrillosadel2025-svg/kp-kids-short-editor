#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# KP Kids Short Editor V6.0: Professional Human-Like Edit + Intro + Closure
# V6.0 professional edit pass:
# - keeps the generated video as the visual hero and removes template-like overload.
# - uses deterministic metadata-aware edit plans and editorial styles per episode.
# - keeps 0.95x body pacing with synced audio/video; intro/closure remain normal speed.
# - uses direct 9:16 scaling when possible; blurred framing only as a fallback.
# - ties reveal emphasis / optional SFX to lesson timing instead of fixed seconds.
# - uses restrained overlays, safe zones, mild motion, and professional loudness control.
# - preserves robust Drive retry, intro/closure crossfades, payload and result metadata.

import argparse
import base64
import hashlib
import http.cookiejar
import json
import re
import html
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
INTRO_DRIVE_FILE_ID = "1stHOtc3CGBDU0gmr5tr0Q1t4gntpVvdf"
CLOSURE_DRIVE_FILE_ID = "1_T_4-TtHeXCtniOkDlxct8dG1QI_uSnP"
SHORT_PLAYBACK_SPEED = 0.95

EDITOR_VERSION = "V6.0 Professional Human-Like Edit"
TARGET_LUFS = -15.0
TARGET_TRUE_PEAK_DB = -1.5
MAX_ZOOM = 1.03
ASPECT_TOLERANCE = 0.025
SAFE_TOP = 145
SAFE_BOTTOM = 260
SAFE_LEFT = 70
SAFE_RIGHT = 170
OUTPUT_W = 1080
OUTPUT_H = 1920
OUTPUT_FPS = 24


def run(cmd):
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run(cmd, check=True)

def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "KP-Kids-Short-Editor/2.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)


def looks_like_mp4(path):
    """Quick sanity check: MP4 files should contain an ftyp box near the beginning."""
    try:
        head = Path(path).read_bytes()[:64]
        return b"ftyp" in head
    except Exception:
        return False

def download_google_drive_file(file_id, dest, label="Google Drive video"):
    """
    Robust public Google Drive downloader using only the Python standard library.

    Improvements in V5.5:
    - retries transient Google 429/5xx errors
    - tries several Drive download endpoints
    - handles Drive HTML confirmation/interstitial forms
    - validates that the downloaded file is really an MP4 before FFmpeg sees it
    """
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 KP-Kids-Editor/5.5"),
        ("Accept", "*/*"),
    ]

    endpoints = [
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download",
        f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={file_id}",
        f"https://drive.google.com/uc?export=download&id={file_id}&confirm=t",
        f"https://drive.google.com/file/d/{file_id}/view?usp=sharing",
    ]

    retryable = {429, 500, 502, 503, 504}
    last_error = None

    def fetch(url, attempts=3):
        nonlocal last_error
        for attempt in range(1, attempts + 1):
            try:
                req = urllib.request.Request(url)
                with opener.open(req, timeout=180) as r:
                    return r.read(), (r.headers.get("Content-Type") or "").lower(), r.geturl()
            except urllib.error.HTTPError as e:
                last_error = e
                if e.code in retryable and attempt < attempts:
                    wait = 2 if attempt == 1 else 6
                    print(
                        f"{label}: Google Drive HTTP {e.code}; retrying in {wait}s "
                        f"({attempt}/{attempts})...",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue
                raise
            except Exception as e:
                last_error = e
                if attempt < attempts:
                    wait = 2 if attempt == 1 else 6
                    print(
                        f"{label}: download attempt failed; retrying in {wait}s "
                        f"({attempt}/{attempts})...",
                        flush=True,
                    )
                    time.sleep(wait)
                    continue
                raise
        raise RuntimeError("unreachable")

    def save_if_mp4(data, ctype):
        Path(dest).write_bytes(data)
        return "text/html" not in ctype and looks_like_mp4(dest)

    def candidate_urls_from_html(html_text, base_url):
        candidates = []

        # Direct links / JSON download URL exposed by Drive pages.
        patterns = [
            r'href="([^"]*?/uc\?export=download[^"]+)"',
            r'action="([^"]*?/download[^"]+)"',
            r'"downloadUrl":"([^"]+)"',
        ]
        for pat in patterns:
            for m in re.finditer(pat, html_text):
                u = html.unescape(m.group(1))
                u = (
                    u.replace("\u003d", "=")
                     .replace("\u0026", "&")
                     .replace("\\/", "/")
                )
                if u.startswith("/"):
                    u = urllib.parse.urljoin(base_url, u)
                if u.startswith("http"):
                    candidates.append(u)

        # Drive confirmation forms commonly contain hidden confirm / uuid values.
        form = re.search(
            r'<form[^>]+(?:id="download-form"[^>]*|action="([^"]*/download[^"]*)")[^>]*>(.*?)</form>',
            html_text,
            re.I | re.S,
        )
        if form:
            whole = form.group(0)
            action_m = re.search(r'action="([^"]+)"', whole, re.I)
            action = html.unescape(action_m.group(1)) if action_m else (
                "https://drive.usercontent.google.com/download"
            )
            action = urllib.parse.urljoin(base_url, action)

            params = {}
            for m in re.finditer(
                r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
                whole,
                re.I,
            ):
                params[html.unescape(m.group(1))] = html.unescape(m.group(2))

            # Some markup uses value before name.
            for m in re.finditer(
                r'<input[^>]+value="([^"]*)"[^>]+name="([^"]+)"[^>]+type="hidden"',
                whole,
                re.I,
            ):
                params[html.unescape(m.group(2))] = html.unescape(m.group(1))

            params.setdefault("id", file_id)
            params.setdefault("export", "download")
            if params:
                candidates.append(action + "?" + urllib.parse.urlencode(params))

        # Fallback token scrape.
        m = re.search(r'confirm=([0-9A-Za-z_-]+)', html_text)
        if m:
            candidates.append(
                "https://drive.usercontent.google.com/download?"
                + urllib.parse.urlencode(
                    {"id": file_id, "export": "download", "confirm": m.group(1)}
                )
            )

        # Preserve order while removing duplicates.
        seen = set()
        uniq = []
        for u in candidates:
            if u not in seen:
                seen.add(u)
                uniq.append(u)
        return uniq

    for first_url in endpoints:
        try:
            data, ctype, final_url = fetch(first_url)

            if save_if_mp4(data, ctype):
                print(f"{label}: downloaded successfully.", flush=True)
                return

            # Google returned HTML: try confirmation/form/download links from the page.
            html_text = data.decode("utf-8", errors="ignore")
            candidates = candidate_urls_from_html(html_text, final_url)

            for u in candidates:
                try:
                    data2, ctype2, _ = fetch(u)
                    if save_if_mp4(data2, ctype2):
                        print(f"{label}: downloaded successfully after Drive confirmation.", flush=True)
                        return
                except Exception as e:
                    last_error = e

            if "text/html" in ctype:
                last_error = RuntimeError(
                    f"{label}: Google Drive returned an HTML page instead of the MP4. "
                    "Make sure sharing is 'Anyone with the link -> Viewer'."
                )
            else:
                last_error = RuntimeError(
                    f"{label}: downloaded data is not a valid MP4 file."
                )

        except Exception as e:
            last_error = e
            continue

    raise RuntimeError(
        f"Could not download {label} from Google Drive after retries. "
        "Check that the file ID is correct and sharing is 'Anyone with the link -> Viewer'."
    ) from last_error


def ffprobe_video_info(path):
    """Return robust stream/format information using one ffprobe JSON call."""
    p = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", "-show_format", str(path)
        ],
        capture_output=True, text=True, check=True
    )
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = 0.0
    for candidate in (
        (data.get("format") or {}).get("duration"),
        video.get("duration"),
        (audio or {}).get("duration") if audio else None,
    ):
        try:
            if candidate is not None:
                duration = max(duration, float(candidate))
        except (TypeError, ValueError):
            pass
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "duration": duration,
        "has_audio": audio is not None,
        "video_codec": video.get("codec_name") or "",
        "audio_codec": (audio or {}).get("codec_name") or "",
    }


def ffprobe_duration(path):
    return ffprobe_video_info(path)["duration"]


def esc(s):
    return (
        str(s or "")
        .replace("'", "’")
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("%", r"\%")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def fade_alpha(start, end, fade=0.18):
    span = max(end - start, 0.02)
    fade = min(fade, span / 2)
    return (
        f"if(lt(t,{start:.3f}),0,"
        f"if(lt(t,{start+fade:.3f}),(t-{start:.3f})/{fade:.3f},"
        f"if(lt(t,{end-fade:.3f}),1,"
        f"if(lt(t,{end:.3f}),({end:.3f}-t)/{fade:.3f},0))))"
    )


def soft_rise_y(base_y, start, pixels=8.0, settle=0.28):
    """Small, premium rise rather than an exaggerated pop animation."""
    return (
        f"{base_y}+if(lt(t,{start:.3f}),{pixels:.1f},"
        f"if(lt(t,{start+settle:.3f}),"
        f"{pixels:.1f}*(1-(t-{start:.3f})/{settle:.3f}),0))"
    )


def stable_hash_int(*parts):
    raw = "|".join(str(p or "") for p in parts)
    return int.from_bytes(hashlib.sha256(raw.encode("utf-8")).digest()[:8], "big")


def category_label(category):
    mapping = {
        "alphabet": "ALPHABET", "letters": "LETTER MATCH", "phonics": "PHONICS",
        "numbers": "NUMBERS", "counting": "COUNTING", "shapes": "SHAPES",
        "colors": "COLORS", "science": "SCIENCE", "space": "SPACE",
        "nature": "NATURE", "body": "HEALTHY BODY", "safety": "SAFE & SMART",
        "emotions": "FEELINGS", "manners": "KINDNESS", "community": "HELPERS",
        "transport": "LET'S GO", "weather": "WEATHER", "animals": "ANIMALS",
        "food": "FOOD", "math": "MATH", "opposites": "OPPOSITES",
        "world": "WORLD", "positions": "POSITION WORDS", "sorting": "SORTING",
        "patterns": "PATTERNS", "sizes": "SIZES", "directions": "DIRECTIONS",
        "calendar": "CALENDAR", "seasons": "SEASONS", "time": "ROUTINES",
    }
    return mapping.get(str(category or "").lower(), "KP KIDS")


def category_theme(category):
    c = str(category or "").lower()
    themes = {
        "alphabet":  {"box":"0x2E86FF", "accent":"0xFFD54A"},
        "letters":   {"box":"0x3F7DFF", "accent":"0xFFD54A"},
        "phonics":   {"box":"0x6A5AE0", "accent":"0xFFB347"},
        "numbers":   {"box":"0x28B463", "accent":"0xFFA630"},
        "counting":  {"box":"0x16A085", "accent":"0xF5B041"},
        "shapes":    {"box":"0x8E44AD", "accent":"0x5DADE2"},
        "colors":    {"box":"0xFF5E7E", "accent":"0x7DFF7A"},
        "science":   {"box":"0x1F618D", "accent":"0x76D7C4"},
        "space":     {"box":"0x34495E", "accent":"0xF4D03F"},
        "nature":    {"box":"0x239B56", "accent":"0x7DCEA0"},
        "body":      {"box":"0xE67E22", "accent":"0xF8C471"},
        "safety":    {"box":"0xC0392B", "accent":"0xF7DC6F"},
        "emotions":  {"box":"0xEC7063", "accent":"0xF8C471"},
        "manners":   {"box":"0xAF7AC5", "accent":"0xF9E79F"},
        "community": {"box":"0x2980B9", "accent":"0xAED6F1"},
        "transport": {"box":"0x2874A6", "accent":"0xF5B041"},
        "weather":   {"box":"0x5DADE2", "accent":"0xF7DC6F"},
        "animals":   {"box":"0x27AE60", "accent":"0xF4D03F"},
        "food":      {"box":"0xE74C3C", "accent":"0x82E0AA"},
        "math":      {"box":"0x16A085", "accent":"0xF8C471"},
        "opposites": {"box":"0x6C5CE7", "accent":"0xFFEAA7"},
        "world":     {"box":"0x2874A6", "accent":"0x58D68D"},
        "positions": {"box":"0x7D3C98", "accent":"0xF7DC6F"},
        "sorting":   {"box":"0x17A589", "accent":"0xF8C471"},
        "patterns":  {"box":"0xAF601A", "accent":"0xAED6F1"},
        "sizes":     {"box":"0x884EA0", "accent":"0xF9E79F"},
        "directions":{"box":"0x2E86C1", "accent":"0xF8C471"},
        "calendar":  {"box":"0xCB4335", "accent":"0xF9E79F"},
        "seasons":   {"box":"0x239B56", "accent":"0x5DADE2"},
        "time":      {"box":"0x566573", "accent":"0xF8C471"},
    }
    return themes.get(c, {"box":"0x3A7BFF", "accent":"0xFFD54A"})


def clean_topic(topic):
    t = re.sub(r"#Shorts\b", "", str(topic or ""), flags=re.I).strip()
    t = re.sub(r"\s+", " ", t)
    # Titles may contain channel boilerplate. Keep the educational phrase only.
    t = re.sub(r"\s+with\s+Kevin.*$", "", t, flags=re.I).strip()
    t = re.sub(r"^(Quick Quiz|Choose One|Guess and Reveal|Kids Discovery)\s*:\s*", "", t, flags=re.I)
    return t[:42]


def keyword_from_lesson(payload):
    category = str(payload.get("category") or "").lower()
    lesson_key = str(payload.get("lesson_key") or "").lower()
    topic = clean_topic(payload.get("topic") or payload.get("title") or "")

    # Strong structured cases first.
    m = re.search(r"(?:number|count)-(\d+)$", lesson_key)
    if m:
        return m.group(1)
    m = re.search(r"(?:letter-|pair-)([a-z])$", lesson_key)
    if m:
        return m.group(1).upper()

    parts = [p for p in lesson_key.split("-") if p]
    stop = {
        category, "number", "count", "letter", "pair", "sound", "color", "shape",
        "lesson", "job", "time", "feeling", "weather", "habitat", "routine",
    }
    meaningful = [p for p in parts if p not in stop and len(p) > 1]
    if meaningful:
        # Prefer the final concept (science-sink-float -> FLOAT; space-astronaut -> ASTRONAUT).
        candidate = meaningful[-1]
        aliases = {
            "float": "FLOATS", "happy": "HAPPY", "sad": "SAD", "angry": "ANGRY",
            "calm": "CALM", "earth": "EARTH", "moon": "MOON", "sun": "SUN",
            "astronaut": "ASTRONAUT", "triangle": "TRIANGLE", "rectangle": "RECTANGLE",
            "circle": "CIRCLE", "square": "SQUARE", "rainbow": "RAINBOW",
        }
        return aliases.get(candidate, candidate.replace("_", " ").upper())[:18]

    bad = {
        "and", "the", "with", "for", "from", "into", "about", "this", "that",
        "time", "kids", "learn", "learning", "number", "color", "job", "fun",
    }
    words = [w.strip(".,!?;:-_()[]{}'\"") for w in topic.split()]
    candidates = [w for w in words if len(w) >= 2 and w.lower() not in bad]
    if not candidates:
        return ""
    return candidates[-1].upper()[:18]


def parse_timeline_event(timeline, keywords):
    """Find the first timeline segment whose text contains any keyword."""
    text = str(timeline or "")
    for line in text.splitlines():
        low = line.lower()
        if not any(k in low for k in keywords):
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*(?:sec|s)\b", low)
        if m:
            return (float(m.group(1)) + float(m.group(2))) / 2.0
        m = re.search(r"(?:at\s*)?(\d+(?:\.\d+)?)\s*(?:sec|s)\b", low)
        if m:
            return float(m.group(1))
    return None


def source_time_to_output_time(seconds):
    try:
        return max(0.0, float(seconds)) / SHORT_PLAYBACK_SPEED
    except (TypeError, ValueError):
        return None


def build_timing_plan(payload, duration):
    timeline = payload.get("dialogue_timeline") or ""
    reveal = payload.get("reveal_time")
    interaction = payload.get("interaction_time")
    if reveal is None:
        reveal = parse_timeline_event(timeline, ["reveal", "answer", "correct", "result"])
    if interaction is None:
        interaction = parse_timeline_event(timeline, ["challenge", "viewer", "particip", "your turn", "response"])
    reveal = source_time_to_output_time(reveal)
    interaction = source_time_to_output_time(interaction)

    if reveal is None:
        reveal = duration * 0.36
    if interaction is None:
        interaction = duration * 0.68

    # Clamp safely for both normal 15s Shorts and unexpectedly short inputs.
    if duration < 6.0:
        reveal = min(max(reveal, duration * 0.28), max(duration * 0.62, 0.8))
        interaction = min(max(interaction, reveal + 0.45), max(reveal + 0.45, duration - 0.35))
    else:
        reveal = min(max(reveal, 2.6), duration - 2.4)
        interaction = min(max(interaction, reveal + 1.4), duration - 0.9)
    return {
        "opening_start": 0.18,
        "opening_end": min(1.65, max(0.9, reveal - 1.2)),
        "reveal": reveal,
        "keyword_start": max(0.0, reveal - 0.08),
        "keyword_end": min(duration - 0.8, reveal + 1.15),
        "interaction": interaction,
        "fade_out_start": max(0.0, duration - 0.20),
    }


def choose_editorial_style(payload):
    category = str(payload.get("category") or "").lower()
    fmt = str(payload.get("episode_format") or "").lower()
    h = stable_hash_int(payload.get("short_id"), payload.get("lesson_key"), category, fmt)

    if category in {"counting", "numbers", "patterns", "sorting"}:
        return "COUNT_AND_PLAY"
    if category in {"emotions", "body", "time", "manners"} and h % 3 != 0:
        return "CALM_LEARNING"
    if any(k in fmt for k in ("quiz", "choose", "find", "pattern", "guess", "scan", "mistake")):
        return "PLAYFUL_QUIZ"
    if any(k in fmt for k in ("story", "mystery", "before", "experiment")):
        return "STORY_MODE"
    return ["CLEAN_DISCOVERY", "PLAYFUL_QUIZ", "STORY_MODE"][h % 3]


def build_edit_plan(payload, duration):
    category = str(payload.get("category") or "").lower()
    topic = clean_topic(payload.get("topic") or payload.get("title") or "")
    keyword = keyword_from_lesson(payload)
    style = choose_editorial_style(payload)
    h = stable_hash_int(payload.get("short_id"), payload.get("lesson_key"), style)

    # Branding is intentionally quiet and not treated as a primary overlay.
    show_brand = True

    # One opening idea only: either topic OR category, never both.
    if style == "STORY_MODE":
        opening = "none" if h % 2 == 0 else ("topic" if topic else "none")
    elif style == "PLAYFUL_QUIZ":
        opening = "topic" if topic and len(topic) <= 30 else "category"
    elif style == "COUNT_AND_PLAY":
        opening = "category"
    elif style == "CALM_LEARNING":
        opening = "topic" if topic and h % 3 != 0 else "none"
    else:
        opening = "topic" if topic and h % 2 == 0 else "category"

    show_keyword = bool(keyword) and style in {"CLEAN_DISCOVERY", "PLAYFUL_QUIZ", "COUNT_AND_PLAY"}
    if style == "CLEAN_DISCOVERY" and h % 4 == 0:
        show_keyword = False

    use_progress = style == "COUNT_AND_PLAY" and category in {"counting", "numbers", "patterns"}
    camera_mode = {
        "CLEAN_DISCOVERY": "REVEAL_PUSH",
        "PLAYFUL_QUIZ": "REVEAL_PUSH",
        "CALM_LEARNING": "STATIC",
        "COUNT_AND_PLAY": "GENTLE_PUSH",
        "STORY_MODE": "STATIC" if h % 2 == 0 else "GENTLE_PUSH",
    }[style]

    # Closure already performs the real ending, so the body avoids a second end card.
    return {
        "style": style,
        "show_brand": show_brand,
        "opening": opening,
        "show_keyword": show_keyword,
        "show_caption": False,
        "use_progress": use_progress,
        "camera_mode": camera_mode,
        "end_card_style": "none",
        "use_reveal_sfx": show_keyword and style in {"PLAYFUL_QUIZ", "COUNT_AND_PLAY"},
    }


def build_text_plan(payload, edit_plan):
    topic = clean_topic(payload.get("topic") or payload.get("title") or "")
    category = str(payload.get("category") or "").lower()
    return {
        "category": category_label(category)[:24],
        "topic": topic[:34],
        "keyword": keyword_from_lesson(payload)[:18],
    }


def is_near_vertical_9_16(width, height):
    if width <= 0 or height <= 0:
        return True
    return abs((width / height) - (9 / 16)) <= ASPECT_TOLERANCE


def build_camera_filter(chain_in, chain_out, mode, timing):
    if mode == "STATIC":
        return f"[{chain_in}]null[{chain_out}];"

    if mode == "GENTLE_PUSH":
        # Slow 1.8% push across the body: subtle enough not to feel like floating UI.
        scale = "1+0.018*min(max(t/12,0),1)"
    else:
        # A short emphasis centered on the actual reveal; no continuous motion outside it.
        r = timing["reveal"]
        start = max(0.0, r - 0.30)
        end = r + 0.70
        span = max(end - start, 0.2)
        scale = (
            f"if(between(t,{start:.3f},{end:.3f}),"
            f"1+0.024*sin(PI*(t-{start:.3f})/{span:.3f}),1)"
        )
    return (
        f"[{chain_in}]scale=w='{OUTPUT_W}*({scale})':h='{OUTPUT_H}*({scale})':eval=frame[camz];"
        f"[camz]crop={OUTPUT_W}:{OUTPUT_H}:(iw-{OUTPUT_W})/2:(ih-{OUTPUT_H})/2[{chain_out}];"
    )


def build_visual_filter(info, payload, duration, edit_plan, timing, texts, theme):
    parts = []
    # Pace adjustment first, preserving A/V sync with the audio chain.
    parts.append(f"[0:v]setpts=PTS/{SHORT_PLAYBACK_SPEED:.5f}[pacedv];")

    if is_near_vertical_9_16(info["width"], info["height"]):
        # Native vertical source: keep it full-frame and clean. No blurred duplicate.
        parts.append(
            f"[pacedv]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{OUTPUT_H},setsar=1,"
            "eq=contrast=1.018:saturation=1.025:gamma=1.005,"
            "unsharp=5:5:0.22:5:5:0.0[base];"
        )
    else:
        # Fallback only for mismatched aspect ratios.
        parts.append("[pacedv]split=2[bg][fg];")
        parts.append(
            f"[bg]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{OUTPUT_H},gblur=sigma=28,eq=brightness=-0.04:saturation=0.92[bg2];"
        )
        parts.append(
            f"[fg]scale={OUTPUT_W-80}:{OUTPUT_H-142}:force_original_aspect_ratio=decrease,"
            "setsar=1,eq=contrast=1.018:saturation=1.025[fg2];"
        )
        parts.append("[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];")

    parts.append(
        f"[base]fade=t=in:st=0:d=0.10,"
        f"fade=t=out:st={timing['fade_out_start']:.3f}:d=0.20,fps={OUTPUT_FPS}[clean];"
    )
    parts.append(build_camera_filter("clean", "cam", edit_plan["camera_mode"], timing))

    chain = "cam"
    idx = 0
    def step(filter_text):
        nonlocal chain, idx
        nxt = f"v{idx}"
        parts.append(f"[{chain}]{filter_text}[{nxt}];")
        chain = nxt
        idx += 1

    # Tiny quiet brand mark. It never occupies the main teaching area.
    if edit_plan["show_brand"]:
        step(
            f"drawbox=x={SAFE_LEFT}:y={SAFE_TOP}:w=176:h=48:color=black@0.22:t=fill"
        )
        step(
            f"drawtext=fontfile={FONT}:text='KP KIDS':fontcolor=white@0.88:fontsize=25:"
            f"shadowx=1:shadowy=1:shadowcolor=black@0.35:x={SAFE_LEFT+20}:y={SAFE_TOP+10}"
        )

    # One opening overlay only.
    opening_text = ""
    if edit_plan["opening"] == "topic":
        opening_text = texts["topic"]
    elif edit_plan["opening"] == "category":
        opening_text = texts["category"]
    if opening_text:
        st, en = timing["opening_start"], timing["opening_end"]
        # Top-center chip within safe UI area, without a large black banner.
        box_w = min(730, max(330, 28 * len(opening_text) + 90))
        box_x = int((OUTPUT_W - box_w) / 2)
        step(
            f"drawbox=x={box_x}:y={SAFE_TOP+72}:w={box_w}:h=76:"
            f"color={theme['box']}@0.54:t=fill:enable='between(t,{st:.3f},{en:.3f})'"
        )
        step(
            f"drawtext=fontfile={FONT}:text='{esc(opening_text)}':fontcolor=white:fontsize=38:"
            f"shadowx=1:shadowy=1:shadowcolor=black@0.38:x=(w-text_w)/2:"
            f"y='{soft_rise_y(SAFE_TOP+91, st, pixels=7)}':"
            f"alpha='{fade_alpha(st,en)}'"
        )

    # Educational target keyword appears only when it genuinely clarifies the reveal.
    if edit_plan["show_keyword"] and texts["keyword"]:
        st, en = timing["keyword_start"], timing["keyword_end"]
        kw = texts["keyword"]
        box_w = min(610, max(250, 34 * len(kw) + 90))
        box_x = int((OUTPUT_W - box_w) / 2)
        y = SAFE_TOP + 190
        step(
            f"drawbox=x={box_x}:y={y}:w={box_w}:h=88:"
            f"color={theme['accent']}@0.82:t=fill:enable='between(t,{st:.3f},{en:.3f})'"
        )
        step(
            f"drawtext=fontfile={FONT}:text='{esc(kw)}':fontcolor=black:fontsize=46:"
            f"shadowx=1:shadowy=1:shadowcolor=white@0.20:x=(w-text_w)/2:"
            f"y='{soft_rise_y(y+20, st, pixels=6)}':alpha='{fade_alpha(st,en)}'"
        )

    # Progress is meaningful only for count/pattern episodes.
    if edit_plan["use_progress"]:
        py = OUTPUT_H - SAFE_BOTTOM
        step(f"drawbox=x={SAFE_LEFT}:y={py}:w=780:h=8:color=black@0.18:t=fill")
        progress = f"(780*min(t/{max(duration,0.1):.3f},1))"
        step(f"drawbox=x={SAFE_LEFT}:y={py}:w='{progress}':h=8:color={theme['accent']}@0.72:t=fill")

    parts.append(f"[{chain}]format=yuv420p[vout]")
    return "".join(parts)


def build_audio_filter(info, duration, edit_plan, timing):
    parts = []
    if info["has_audio"]:
        parts.append(
            f"[0:a]atempo={SHORT_PLAYBACK_SPEED:.5f},"
            "aformat=sample_rates=48000:channel_layouts=stereo,"
            "highpass=f=70,lowpass=f=15500,"
            "acompressor=threshold=-20dB:ratio=1.8:attack=15:release=180:makeup=1.0,"
            f"loudnorm=I={TARGET_LUFS:.1f}:LRA=7:TP={TARGET_TRUE_PEAK_DB:.1f},"
            "alimiter=limit=0.94,"
            "afade=t=in:st=0:d=0.08,"
            f"afade=t=out:st={max(0.0,duration-0.18):.3f}:d=0.18[amain];"
        )
    else:
        parts.append(
            f"anullsrc=r=48000:cl=stereo:d={duration:.3f},asetpts=PTS-STARTPTS[amain];"
        )

    if edit_plan["use_reveal_sfx"]:
        # One quiet, event-tied two-note cue. No fixed mobile-game chimes.
        delay = int(max(0.0, timing["reveal"] - 0.03) * 1000)
        delay2 = delay + 85
        parts.append(
            "sine=frequency=660:sample_rate=48000:duration=0.09,"
            "afade=t=in:st=0:d=0.012,afade=t=out:st=0.045:d=0.04,"
            f"volume=0.0065,adelay={delay}|{delay}[sfx1];"
        )
        parts.append(
            "sine=frequency=880:sample_rate=48000:duration=0.10,"
            "afade=t=in:st=0:d=0.012,afade=t=out:st=0.05:d=0.04,"
            f"volume=0.0055,adelay={delay2}|{delay2}[sfx2];"
        )
        parts.append("[amain][sfx1][sfx2]amix=inputs=3:normalize=0:duration=first[aout]")
    else:
        parts.append("[amain]anull[aout]")
    return "".join(parts)


def normalize_intro(src, dest):
    """Normalize intro/closure to exact output format; speed is NOT changed."""
    info = ffprobe_video_info(src)
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    cmd += [
        "-vf",
        f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},fps={OUTPUT_FPS},setsar=1,format=yuv420p",
    ]
    if info["has_audio"]:
        cmd += [
            "-af", "aformat=sample_rates=48000:channel_layouts=stereo,aresample=async=1:first_pts=0"
        ]
    else:
        # Add silence so crossfades remain robust even if a branding clip has no audio stream.
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={max(info['duration'],0.1):.3f}", "-shortest"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(OUTPUT_FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(dest),
    ]
    run(cmd)


def prepend_intro(intro, body, output, xfade_dur=0.28):
    """Subtle intro-to-story dissolve; kept short so the generated hook stays energetic."""
    intro_dur = ffprobe_duration(intro)
    xfade_dur = min(xfade_dur, max(intro_dur - 0.05, 0.05))
    offset = max(0.0, intro_dur - xfade_dur)
    cmd = [
        "ffmpeg", "-y", "-i", str(intro), "-i", str(body),
        "-filter_complex",
        f"[0:v]setpts=PTS-STARTPTS,fps={OUTPUT_FPS},settb=expr=1/{OUTPUT_FPS}[v0];"
        f"[1:v]setpts=PTS-STARTPTS,fps={OUTPUT_FPS},settb=expr=1/{OUTPUT_FPS}[v1];"
        f"[v0][v1]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}[vout];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]acrossfade=d={xfade_dur:.3f}[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(OUTPUT_FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(output),
    ]
    run(cmd)


def append_closure(body_with_intro, closure, output, xfade_dur=0.30):
    """Append the fixed closure with one clean transition, no redundant body end-card."""
    main_dur = ffprobe_duration(body_with_intro)
    closure_dur = ffprobe_duration(closure)
    xfade_dur = min(
        xfade_dur,
        max(main_dur - 0.05, 0.05),
        max(closure_dur - 0.05, 0.05),
    )
    offset = max(0.0, main_dur - xfade_dur)
    cmd = [
        "ffmpeg", "-y", "-i", str(body_with_intro), "-i", str(closure),
        "-filter_complex",
        f"[0:v]setpts=PTS-STARTPTS,fps={OUTPUT_FPS},settb=expr=1/{OUTPUT_FPS}[v0];"
        f"[1:v]setpts=PTS-STARTPTS,fps={OUTPUT_FPS},settb=expr=1/{OUTPUT_FPS}[v1];"
        f"[v0][v1]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}[vout];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]acrossfade=d={xfade_dur:.3f}[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(OUTPUT_FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(output),
    ]
    run(cmd)


def edit_video(src, out, payload):
    info = ffprobe_video_info(src)
    source_duration = min(15.0, info["duration"] or 15.0)
    duration = source_duration / SHORT_PLAYBACK_SPEED
    theme = category_theme(payload.get("category"))
    edit_plan = build_edit_plan(payload, duration)
    timing = build_timing_plan(payload, duration)
    texts = build_text_plan(payload, edit_plan)

    vf = build_visual_filter(info, payload, duration, edit_plan, timing, texts, theme)
    af = build_audio_filter(info, duration, edit_plan, timing)

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", vf + ";" + af,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(OUTPUT_FPS),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", "-t", f"{duration:.3f}", str(out),
    ]
    run(cmd)
    return {
        "editorial_style": edit_plan["style"],
        "edit_plan": edit_plan,
        "timing_plan": timing,
        "theme": theme,
        "body_duration": duration,
        "source_info": info,
        "texts": texts,
    }


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
        intro_raw = td / "kp_kids_intro_raw.mp4"
        intro_norm = td / "kp_kids_intro_normalized.mp4"
        closure_raw = td / "kp_kids_closure_raw.mp4"
        closure_norm = td / "kp_kids_closure_normalized.mp4"
        edited_body = td / "kp_kids_edited_body.mp4"
        body_with_intro = td / "kp_kids_with_intro.mp4"

        download(source_url, src)
        result = edit_video(src, edited_body, payload)

        download_google_drive_file(INTRO_DRIVE_FILE_ID, intro_raw, label="KP Kids intro")
        normalize_intro(intro_raw, intro_norm)
        prepend_intro(intro_norm, edited_body, body_with_intro)

        download_google_drive_file(CLOSURE_DRIVE_FILE_ID, closure_raw, label="KP Kids closure")
        normalize_intro(closure_raw, closure_norm)
        append_closure(body_with_intro, closure_norm, Path(args.output))

    meta = dict(payload)
    meta["editor_version"] = EDITOR_VERSION
    meta["short_playback_speed"] = SHORT_PLAYBACK_SPEED
    meta["editorial_style"] = result["editorial_style"]
    meta["edit_plan"] = result["edit_plan"]
    meta["timing_plan"] = result["timing_plan"]
    meta["edit_theme"] = result["theme"]
    meta["source_video_info"] = result["source_info"]
    meta["body_duration_after_speed"] = result["body_duration"]
    meta["intro_drive_file_id"] = INTRO_DRIVE_FILE_ID
    meta["intro_prepend_enabled"] = True
    meta["closure_drive_file_id"] = CLOSURE_DRIVE_FILE_ID
    meta["closure_append_enabled"] = True
    Path("edit_result.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
