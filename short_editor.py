#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# KP Kids Short Editor V7.3: Intelligent Edit + Motion Graphics Text + Contextual Graphics + Intro + Closure
# V7.3 motion-graphics typography pass:
# - keeps the generated video as the visual hero and removes template-like overload.
# - uses deterministic metadata-aware edit plans and editorial styles per episode.
# - uses silence-aware smart pacing: speech stays natural while real pauses breathe longer.
# - keeps overall pacing near the old 0.95x target while intro/closure remain normal speed.
# - uses direct 9:16 scaling when possible; blurred framing only as a fallback.
# - ties reveal camera/color emphasis and optional SFX to real/metadata-derived edit points.
# - adds conservative dialogue focus, SFX ducking, category color polish, adaptive transitions.
# - logs detailed pacing/audio/color/timing telemetry for future retention analysis.
# - preserves robust Drive retry, intro/closure crossfades, payload and result metadata.
# - upgrades text to true motion graphics: animated plates, accent wipes, impact flashes, settle motion, and synchronized SFX.
# - synchronizes tiny generated SFX with text entrances and ducks them under dialogue.
# - keeps motion deterministic per episode and never uses continuous wiggle/jitter.
# - adds one contextual motion-graphic cue at most, selected from lesson/category metadata.
# - motion graphics explain/direct/reward only: focus brackets, direction arrow, shape badge, color sweep, or scan line.
# - avoids decorative particle fields, confetti, random squares, and persistent HUD clutter.

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
SHORT_PLAYBACK_SPEED = 0.95  # target overall body pace; individual sections vary intelligently
SPEECH_BASE_SPEED = 0.99
SHORT_PAUSE_SPEED = 0.96
MEDIUM_PAUSE_SPEED = 0.93
LONG_PAUSE_SPEED = 0.90
MIN_PACING_SEGMENT = 0.08
SILENCE_DB = -33
SILENCE_MIN_DURATION = 0.22

EDITOR_VERSION = "V7.3 Intelligent Edit + Motion Graphics Text + Contextual Motion Graphics"
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

TEXT_MOTION_DURATION = 0.32
TEXT_BOUNCE_DURATION = 0.40
TEXT_PANEL_DURATION = 0.30
TEXT_ACCENT_DURATION = 0.38
TEXT_SFX_MAX_GAIN = 0.0062
MOTION_GRAPHICS_DURATION = 0.90
MOTION_GRAPHICS_ENTRANCE = 0.22
MOTION_GRAPHICS_MAX_ALPHA = 0.55



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


def motion_slide_x(base_expr, start, offset=80.0, dur=TEXT_MOTION_DURATION):
    """Short decelerating horizontal entrance, then perfectly still for readability."""
    return (
        f"({base_expr})+if(lt(t,{start:.3f}),{offset:.1f},"
        f"if(lt(t,{start+dur:.3f}),{offset:.1f}*"
        f"(1-(t-{start:.3f})/{dur:.3f})*(1-(t-{start:.3f})/{dur:.3f}),0))"
    )


def motion_rise_y(base_y, start, pixels=14.0, dur=TEXT_MOTION_DURATION):
    """Premium short upward settle; motion stops after ~250 ms."""
    return (
        f"{base_y}+if(lt(t,{start:.3f}),{pixels:.1f},"
        f"if(lt(t,{start+dur:.3f}),{pixels:.1f}*"
        f"(1-(t-{start:.3f})/{dur:.3f})*(1-(t-{start:.3f})/{dur:.3f}),0))"
    )


def motion_pop_y(base_y, start, amp=15.0, dur=TEXT_BOUNCE_DURATION):
    """One restrained spring-in used only for playful reveal words."""
    # Decaying oscillation is limited to the entrance window; after that text is static.
    return (
        f"{base_y}+if(lt(t,{start:.3f}),{amp:.1f},"
        f"if(lt(t,{start+dur:.3f}),"
        f"{amp:.1f}*exp(-10*(t-{start:.3f}))*cos(19*(t-{start:.3f})),0))"
    )


def motion_unit(start, dur):
    """0..1 linear progress expression clipped to the entrance window."""
    return f"min(max((t-{start:.3f})/{max(dur,0.05):.3f},0),1)"


def motion_ease_out(start, dur):
    """Quadratic ease-out used by card/underline motion."""
    u = motion_unit(start, dur)
    return f"(1-(1-{u})*(1-{u}))"


def motion_panel_width(width, start, dur=TEXT_PANEL_DURATION, start_scale=0.68):
    p = motion_ease_out(start, dur)
    return f"({width}*({start_scale:.3f}+(1-{start_scale:.3f})*{p}))"


def motion_panel_x(center_x, width, start, dur=TEXT_PANEL_DURATION, start_scale=0.68):
    w = motion_panel_width(width, start, dur, start_scale)
    return f"({center_x}-({w})/2)"


def entrance_only_alpha(start, dur=0.30, peak=0.65):
    """Quick glow/outline flash that disappears once the title settles."""
    return (
        f"if(lt(t,{start:.3f}),0,"
        f"if(lt(t,{start+dur/2:.3f}),{peak:.3f}*(t-{start:.3f})/{dur/2:.3f},"
        f"if(lt(t,{start+dur:.3f}),{peak:.3f}*({start+dur:.3f}-t)/{dur/2:.3f},0)))"
    )


def lesson_accent_color(payload, fallback="0xFFD54A"):
    """Return a lesson-specific accent when the lesson itself names a color."""
    key = str(payload.get("lesson_key") or "").lower()
    topic = str(payload.get("topic") or "").lower()
    hay = f"{key} {topic}"
    colors = {
        "red": "0xFF4D5A",
        "blue": "0x4DA3FF",
        "yellow": "0xFFD84D",
        "green": "0x55C878",
        "orange": "0xFF9B42",
        "purple": "0x9B6BFF",
        "pink": "0xFF78B7",
        "black": "0x222222",
        "white": "0xF5F5F5",
    }
    for name, value in colors.items():
        if re.search(rf"\b{name}\b", hay):
            return value
    return fallback


def shape_symbol_from_payload(payload):
    """Simple glyphs that DejaVu Sans renders reliably; empty means no safe glyph."""
    hay = f"{payload.get('lesson_key','')} {payload.get('topic','')}".lower()
    mapping = [
        ("triangle", "△"),
        ("circle", "○"),
        ("square", "□"),
        ("rectangle", "▭"),
        ("star", "★"),
        ("heart", "♥"),
        ("diamond", "◇"),
        ("oval", "○"),
    ]
    for needle, glyph in mapping:
        if needle in hay:
            return glyph
    return ""


def build_motion_graphics_plan(payload, edit_plan, theme):
    """
    Choose at most one contextual motion graphic.  The cue must teach, direct
    attention, or reinforce a reveal; it must never exist merely to decorate.
    """
    category = str(payload.get("category") or "").lower()
    lesson_key = str(payload.get("lesson_key") or "").lower()
    fmt = str(payload.get("episode_format") or "").lower()
    lead = str(payload.get("lead_character") or "").lower()
    style = edit_plan.get("style") or "CLEAN_DISCOVERY"
    h = stable_hash_int(payload.get("short_id"), lesson_key, category, fmt, "motion-graphics")

    # Calm/social lessons are intentionally visually quiet most of the time.
    if style == "CALM_LEARNING" and h % 4 != 0:
        return {"type":"none", "sfx":"none", "reason":"calm_learning_breathing_room"}

    mg_type = "none"
    reason = "no_contextual_need"

    if category == "shapes" and shape_symbol_from_payload(payload):
        mg_type, reason = "shape_badge", "reinforce_shape_identity"
    elif category == "colors":
        mg_type, reason = "color_sweep", "reinforce_color_reveal"
    elif category == "directions":
        mg_type, reason = "direction_arrow", "direct_spatial_attention"
    elif category in {"sorting", "patterns"}:
        mg_type, reason = "scan_line", "support_search_or_pattern_scan"
    elif "scan" in fmt or lead == "bibo":
        mg_type, reason = "scan_line", "match_bibo_scan_behavior"
    elif category in {"science", "space", "positions", "numbers", "counting", "math"}:
        mg_type, reason = "focus_brackets", "focus_teaching_target_at_reveal"
    elif category in {"animals", "nature", "weather", "world", "transport", "community", "food", "safety", "seasons"}:
        # Only some episodes need a graphical cue; preserve natural footage in the rest.
        if h % 3 != 0:
            mg_type, reason = "focus_brackets", "brief_reveal_focus"

    # No third sound if typography already has a reveal cue; build_audio_filter enforces this too.
    sfx = "tiny_whoosh" if mg_type in {"direction_arrow", "scan_line", "color_sweep"} else "none"
    return {
        "type": mg_type,
        "reason": reason,
        "accent": lesson_accent_color(payload, theme.get("accent", "0xFFD54A")),
        "shape_symbol": shape_symbol_from_payload(payload),
        "sfx": sfx,
        "duration": MOTION_GRAPHICS_DURATION,
        "entrance": MOTION_GRAPHICS_ENTRANCE,
    }

def build_motion_typography_plan(payload, edit_plan):
    """Build true motion-graphics text behavior: animated card + accent + title + synced cue."""
    style = edit_plan.get("style") or "CLEAN_DISCOVERY"
    h = stable_hash_int(payload.get("short_id"), payload.get("lesson_key"), style, "motion-text-v73")

    if style == "CALM_LEARNING":
        opening_motion = "cinematic_rise"
        keyword_motion = "soft_reveal"
        opening_sfx = "none"
        keyword_sfx = "none"
    elif style == "COUNT_AND_PLAY":
        opening_motion = "title_wipe"
        keyword_motion = "impact_pop"
        opening_sfx = "motion_whoosh" if edit_plan.get("opening") != "none" else "none"
        keyword_sfx = "motion_hit" if edit_plan.get("show_keyword") else "none"
    elif style == "PLAYFUL_QUIZ":
        opening_motion = "title_slide_left" if h % 2 == 0 else "title_slide_right"
        keyword_motion = "impact_pop"
        opening_sfx = "motion_whoosh" if edit_plan.get("opening") != "none" else "none"
        keyword_sfx = "motion_hit" if edit_plan.get("show_keyword") else "none"
    elif style == "STORY_MODE":
        opening_motion = "cinematic_rise"
        keyword_motion = "soft_reveal"
        opening_sfx = "none"
        keyword_sfx = "none"
    else:
        opening_motion = "title_wipe" if h % 2 else "title_slide_right"
        keyword_motion = "impact_pop" if h % 3 else "soft_reveal"
        opening_sfx = "motion_whoosh" if edit_plan.get("opening") != "none" else "none"
        keyword_sfx = "motion_hit" if edit_plan.get("show_keyword") and keyword_motion == "impact_pop" else (
            "soft_ding" if edit_plan.get("show_keyword") else "none"
        )

    if edit_plan.get("opening") == "none":
        opening_sfx = "none"
    if not edit_plan.get("show_keyword"):
        keyword_sfx = "none"

    return {
        "opening_motion": opening_motion,
        "keyword_motion": keyword_motion,
        "opening_sfx": opening_sfx,
        "keyword_sfx": keyword_sfx,
        "motion_duration": TEXT_MOTION_DURATION,
        "bounce_duration": TEXT_BOUNCE_DURATION,
        "panel_duration": TEXT_PANEL_DURATION,
        "accent_duration": TEXT_ACCENT_DURATION,
    }


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


def detect_silence_intervals(path, duration):
    """Detect real quiet windows from the source audio using FFmpeg silencedetect."""
    info = ffprobe_video_info(path)
    if not info["has_audio"] or duration <= 0:
        return []
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-t", f"{duration:.3f}", "-i", str(path),
        "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_DURATION}",
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
        if en - st >= SILENCE_MIN_DURATION - 0.01:
            intervals.append((st, en))
    # Merge overlapping/adjacent detections defensively.
    merged = []
    for st, en in sorted(intervals):
        if merged and st <= merged[-1][1] + 0.03:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((st, en))
    return merged


def complement_intervals(intervals, duration):
    """Return non-silent intervals over [0,duration]."""
    out = []
    cur = 0.0
    for st, en in intervals:
        if st > cur + MIN_PACING_SEGMENT:
            out.append((cur, st))
        cur = max(cur, en)
    if duration > cur + MIN_PACING_SEGMENT:
        out.append((cur, duration))
    return out


def _pause_speed(length):
    if length >= 0.85:
        return LONG_PAUSE_SPEED
    if length >= 0.45:
        return MEDIUM_PAUSE_SPEED
    return SHORT_PAUSE_SPEED


def build_pacing_plan(duration, silence_intervals):
    """
    Build deterministic A/V pacing segments.

    Speech stays very close to natural speed, while real pauses breathe a little more.
    Speeds are globally normalized so total duration stays close to the previous 0.95x
    body target instead of growing unpredictably.
    """
    if duration <= 0:
        return {"segments": [], "output_duration": 0.0, "target_duration": 0.0}

    # Convert silence regions + complement into one ordered segmentation.
    marks = {0.0, duration}
    for st, en in silence_intervals:
        marks.add(max(0.0, min(st, duration)))
        marks.add(max(0.0, min(en, duration)))
    marks = sorted(marks)
    raw = []
    for a, b in zip(marks, marks[1:]):
        if b - a < MIN_PACING_SEGMENT:
            continue
        mid = (a + b) / 2.0
        is_silence = any(st <= mid <= en for st, en in silence_intervals)
        speed = _pause_speed(b-a) if is_silence else SPEECH_BASE_SPEED
        raw.append({"source_start": a, "source_end": b, "kind": "silence" if is_silence else "speech", "speed": speed})

    if not raw:
        raw = [{"source_start": 0.0, "source_end": duration, "kind": "speech", "speed": SHORT_PLAYBACK_SPEED}]

    target_duration = duration / SHORT_PLAYBACK_SPEED
    current = sum((x["source_end"]-x["source_start"]) / x["speed"] for x in raw)
    factor = current / target_duration if target_duration > 0 else 1.0
    for x in raw:
        # Preserve the relationship (speech faster, pauses slower) while targeting the same overall duration.
        x["speed"] = min(1.0, max(0.88, x["speed"] * factor))

    # Recompute output timeline after clamping.
    out_t = 0.0
    segments = []
    for x in raw:
        seg = dict(x)
        seg["output_start"] = out_t
        seg_dur = (x["source_end"]-x["source_start"]) / x["speed"]
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
    except (TypeError, ValueError):
        return None
    segs = pacing_plan.get("segments") or []
    if not segs:
        return t / SHORT_PLAYBACK_SPEED
    for seg in segs:
        if t <= seg["source_end"] + 1e-6:
            local = max(0.0, t - seg["source_start"])
            return seg["output_start"] + local / seg["speed"]
    return pacing_plan.get("output_duration", t / SHORT_PLAYBACK_SPEED)


def map_intervals_to_output(intervals, pacing_plan):
    out = []
    for st, en in intervals:
        ost = map_source_time_to_output(st, pacing_plan)
        oen = map_source_time_to_output(en, pacing_plan)
        if ost is not None and oen is not None and oen > ost:
            out.append((ost, oen))
    return out


def choose_event_from_silence(silence_intervals, duration, window_start, window_end):
    """Use the end of a real pause as a natural edit point when metadata is missing."""
    candidates = []
    lo, hi = duration * window_start, duration * window_end
    target = duration * ((window_start + window_end) / 2.0)
    for st, en in silence_intervals:
        mid = (st + en) / 2.0
        if lo <= mid <= hi and en - st >= 0.28:
            score = abs(mid-target) - min(en-st, 1.2) * 0.35
            candidates.append((score, en + 0.05))
    return min(candidates)[1] if candidates else None


def build_timing_plan(payload, source_duration, pacing_plan, silence_intervals):
    timeline = payload.get("dialogue_timeline") or ""
    reveal_src = payload.get("reveal_time")
    interaction_src = payload.get("interaction_time")
    if reveal_src is None:
        reveal_src = parse_timeline_event(timeline, ["reveal", "answer", "correct", "result"])
    if interaction_src is None:
        interaction_src = parse_timeline_event(timeline, ["challenge", "viewer", "particip", "your turn", "response"])

    # If metadata is unavailable, use genuine quiet windows as natural edit points.
    if reveal_src is None:
        reveal_src = choose_event_from_silence(silence_intervals, source_duration, 0.24, 0.58)
    if interaction_src is None:
        interaction_src = choose_event_from_silence(silence_intervals, source_duration, 0.58, 0.88)

    duration = pacing_plan.get("output_duration") or source_duration / SHORT_PLAYBACK_SPEED
    reveal = map_source_time_to_output(reveal_src, pacing_plan) if reveal_src is not None else None
    interaction = map_source_time_to_output(interaction_src, pacing_plan) if interaction_src is not None else None

    if reveal is None:
        reveal = duration * 0.36
    if interaction is None:
        interaction = duration * 0.68

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
        "reveal_source_time": reveal_src,
        "interaction_source_time": interaction_src,
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


def merge_intervals(intervals, gap=0.12):
    merged = []
    for st, en in sorted(intervals):
        if merged and st <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], en))
        else:
            merged.append((st, en))
    return merged


def speech_enable_expr(speech_windows):
    windows = merge_intervals(speech_windows, gap=0.10)
    if not windows:
        return None
    terms = [f"between(t,{st:.3f},{en:.3f})" for st, en in windows[:18]]
    return "+".join(terms)


def category_color_plan(category):
    """Subtle editorial color polish; never a heavy look/LUT."""
    c = str(category or "").lower()
    # Values intentionally conservative to preserve generated character identity/colors.
    plans = {
        "space":      {"contrast":1.028, "saturation":1.040, "gamma":0.995, "brightness":0.002},
        "nature":     {"contrast":1.018, "saturation":1.045, "gamma":1.008, "brightness":0.004},
        "weather":    {"contrast":1.018, "saturation":1.035, "gamma":1.006, "brightness":0.004},
        "colors":     {"contrast":1.012, "saturation":1.050, "gamma":1.000, "brightness":0.002},
        "emotions":   {"contrast":1.010, "saturation":1.025, "gamma":1.012, "brightness":0.004},
        "body":       {"contrast":1.015, "saturation":1.020, "gamma":1.008, "brightness":0.003},
        "safety":     {"contrast":1.025, "saturation":1.025, "gamma":1.000, "brightness":0.001},
        "science":    {"contrast":1.024, "saturation":1.032, "gamma":1.000, "brightness":0.002},
    }
    return plans.get(c, {"contrast":1.018, "saturation":1.028, "gamma":1.004, "brightness":0.003})


def build_transition_plan(edit_plan):
    style = edit_plan.get("style") or "CLEAN_DISCOVERY"
    if style == "PLAYFUL_QUIZ":
        return {"intro_xfade":0.16, "closure_xfade":0.22}
    if style == "CALM_LEARNING":
        return {"intro_xfade":0.24, "closure_xfade":0.32}
    if style == "COUNT_AND_PLAY":
        return {"intro_xfade":0.18, "closure_xfade":0.24}
    if style == "STORY_MODE":
        return {"intro_xfade":0.18, "closure_xfade":0.28}
    return {"intro_xfade":0.20, "closure_xfade":0.26}


def build_paced_video_prefix(pacing_plan):
    segs = pacing_plan.get("segments") or []
    if not segs:
        return f"[0:v]setpts=PTS/{SHORT_PLAYBACK_SPEED:.5f}[pacedv];"
    parts = []
    labels = []
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
        return f"[0:a]atempo={SHORT_PLAYBACK_SPEED:.5f}[paceda];"
    parts = []
    labels = []
    for i, seg in enumerate(segs):
        label = f"pa{i}"
        labels.append(f"[{label}]")
        parts.append(
            f"[0:a]atrim=start={seg['source_start']:.6f}:end={seg['source_end']:.6f},"
            f"asetpts=PTS-STARTPTS,atempo={seg['speed']:.6f}[{label}];"
        )
    parts.append("".join(labels) + f"concat=n={len(segs)}:v=0:a=1[paceda];")
    return "".join(parts)


def build_camera_filter(chain_in, chain_out, mode, timing):
    if mode == "STATIC":
        return f"[{chain_in}]null[{chain_out}];"

    if mode == "GENTLE_PUSH":
        scale = "1+0.016*min(max(t/13,0),1)"
    else:
        r = timing["reveal"]
        start = max(0.0, r - 0.34)
        end = r + 0.78
        span = max(end - start, 0.2)
        scale = (
            f"if(between(t,{start:.3f},{end:.3f}),"
            f"1+0.026*sin(PI*(t-{start:.3f})/{span:.3f}),1)"
        )
    return (
        f"[{chain_in}]scale=w='{OUTPUT_W}*({scale})':h='{OUTPUT_H}*({scale})':eval=frame[camz];"
        f"[camz]crop={OUTPUT_W}:{OUTPUT_H}:(iw-{OUTPUT_W})/2:(ih-{OUTPUT_H})/2[{chain_out}];"
    )


def build_visual_filter(info, payload, duration, edit_plan, timing, texts, theme, pacing_plan, color_plan, motion_plan, motion_graphics_plan):
    parts = [build_paced_video_prefix(pacing_plan)]

    eq_base = (
        f"eq=contrast={color_plan['contrast']:.3f}:saturation={color_plan['saturation']:.3f}:"
        f"gamma={color_plan['gamma']:.3f}:brightness={color_plan['brightness']:.3f}"
    )

    if is_near_vertical_9_16(info["width"], info["height"]):
        parts.append(
            f"[pacedv]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{OUTPUT_H},setsar=1,{eq_base},"
            "unsharp=5:5:0.20:5:5:0.0[base];"
        )
    else:
        parts.append("[pacedv]split=2[bg][fg];")
        parts.append(
            f"[bg]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
            f"crop={OUTPUT_W}:{OUTPUT_H},gblur=sigma=28,eq=brightness=-0.04:saturation=0.92[bg2];"
        )
        parts.append(
            f"[fg]scale={OUTPUT_W-80}:{OUTPUT_H-142}:force_original_aspect_ratio=decrease,"
            f"setsar=1,{eq_base}[fg2];"
        )
        parts.append("[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];")

    # Gentle reveal color lift: a real editorial emphasis, not a glow/sticker effect.
    reveal_st = max(0.0, timing["reveal"] - 0.12)
    reveal_en = min(duration, timing["reveal"] + 0.70)
    if edit_plan["style"] != "CALM_LEARNING":
        parts.append(
            f"[base]eq=contrast=1.010:saturation=1.040:brightness=0.006:"
            f"enable='between(t,{reveal_st:.3f},{reveal_en:.3f})'[emph];"
        )
        base_label = "emph"
    else:
        base_label = "base"

    parts.append(
        f"[{base_label}]fade=t=in:st=0:d=0.10,"
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

    # Contextual motion graphics: one short cue at most, centered on the reveal.
    mg = motion_graphics_plan or {"type":"none"}
    mg_type = mg.get("type", "none")
    mg_st = max(0.0, timing["reveal"] - 0.05)
    mg_en = min(duration, mg_st + float(mg.get("duration", MOTION_GRAPHICS_DURATION)))
    grow = f"min(max((t-{mg_st:.3f})/{max(float(mg.get('entrance', MOTION_GRAPHICS_ENTRANCE)),0.05):.3f},0),1)"
    accent = mg.get("accent") or theme["accent"]

    if mg_type == "focus_brackets":
        # Four restrained corner brackets frame the central teaching zone without covering it.
        x0, y0, bw, bh, arm, thick = 236, 430, 608, 760, 78, 5
        alpha = 0.46
        # top-left
        step(f"drawbox=x={x0}:y={y0}:w='{arm}*{grow}':h={thick}:color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        step(f"drawbox=x={x0}:y={y0}:w={thick}:h='{arm}*{grow}':color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        # top-right
        step(f"drawbox=x={x0+bw}:y={y0}:w='-{arm}*{grow}':h={thick}:color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        step(f"drawbox=x={x0+bw-thick}:y={y0}:w={thick}:h='{arm}*{grow}':color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        # bottom-left / bottom-right
        step(f"drawbox=x={x0}:y={y0+bh}:w='{arm}*{grow}':h={thick}:color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        step(f"drawbox=x={x0}:y='{y0+bh}-{arm}*{grow}':w={thick}:h='{arm}*{grow}':color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        step(f"drawbox=x={x0+bw}:y={y0+bh}:w='-{arm}*{grow}':h={thick}:color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")
        step(f"drawbox=x={x0+bw-thick}:y='{y0+bh}-{arm}*{grow}':w={thick}:h='{arm}*{grow}':color={accent}@{alpha}:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'")

    elif mg_type == "direction_arrow":
        arrow = "↔" if "left-right" in str(payload.get("lesson_key") or "") else "→"
        ax = motion_slide_x(str(SAFE_LEFT + 8), mg_st, offset=-65, dur=0.24)
        ay = OUTPUT_H - SAFE_BOTTOM - 210
        step(
            f"drawtext=fontfile={FONT}:text='{esc(arrow)}':fontcolor={accent}@0.78:fontsize=88:"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.22:x='{ax}':y={ay}:"
            f"alpha='{fade_alpha(mg_st,mg_en,0.12)}'"
        )

    elif mg_type == "shape_badge" and mg.get("shape_symbol"):
        symbol = esc(mg["shape_symbol"])
        sy = motion_pop_y(OUTPUT_H - SAFE_BOTTOM - 220, mg_st, amp=16, dur=0.34)
        step(
            f"drawtext=fontfile={FONT}:text='{symbol}':fontcolor={accent}@0.82:fontsize=104:"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.20:x={SAFE_LEFT+16}:y='{sy}':"
            f"alpha='{fade_alpha(mg_st,mg_en,0.14)}'"
        )

    elif mg_type == "color_sweep":
        full_w = OUTPUT_W - SAFE_LEFT - SAFE_RIGHT
        yy = OUTPUT_H - SAFE_BOTTOM - 54
        step(
            f"drawbox=x={SAFE_LEFT}:y={yy}:w='{full_w}*{grow}':h=12:"
            f"color={accent}@0.62:t=fill:enable='between(t,{mg_st:.3f},{mg_en:.3f})'"
        )

    elif mg_type == "scan_line":
        scan_span = max(mg_en - mg_st, 0.25)
        yy = f"520+650*min(max((t-{mg_st:.3f})/{scan_span:.3f},0),1)"
        step(
            f"drawbox=x=175:y='{yy}':w=730:h=4:color={accent}@0.36:t=fill:"
            f"enable='between(t,{mg_st:.3f},{mg_en:.3f})'"
        )

    if edit_plan["show_brand"]:
        step(f"drawbox=x={SAFE_LEFT}:y={SAFE_TOP}:w=176:h=48:color=black@0.20:t=fill")
        step(
            f"drawtext=fontfile={FONT}:text='KP KIDS':fontcolor=white@0.86:fontsize=25:"
            f"shadowx=1:shadowy=1:shadowcolor=black@0.32:x={SAFE_LEFT+20}:y={SAFE_TOP+10}"
        )

    opening_text = ""
    if edit_plan["opening"] == "topic":
        opening_text = texts["topic"]
    elif edit_plan["opening"] == "category":
        opening_text = texts["category"]

    # MOTION GRAPHICS TITLE CARD: the plate, accent and text animate as one designed unit.
    if opening_text:
        st, en = timing["opening_start"], timing["opening_end"]
        box_w = min(760, max(340, 28 * len(opening_text) + 104))
        box_h = 82
        center_x = OUTPUT_W / 2
        base_y = SAFE_TOP + 72
        motion = motion_plan.get("opening_motion", "title_wipe")
        panel_w = motion_panel_width(box_w, st, TEXT_PANEL_DURATION, 0.66)
        panel_x = motion_panel_x(center_x, box_w, st, TEXT_PANEL_DURATION, 0.66)
        accent_w = f"({box_w}*{motion_ease_out(st+0.03, TEXT_ACCENT_DURATION)})"
        accent_x = f"({center_x}-({accent_w})/2)"

        text_x = "(w-text_w)/2"
        text_y = str(base_y + 21)
        if motion == "title_slide_left":
            text_x = motion_slide_x("(w-text_w)/2", st + 0.04, offset=-96, dur=0.30)
        elif motion == "title_slide_right":
            text_x = motion_slide_x("(w-text_w)/2", st + 0.04, offset=96, dur=0.30)
        else:
            text_y = motion_rise_y(base_y + 21, st + 0.04, pixels=18, dur=0.30)

        # soft shadow plate grows from the center
        step(
            f"drawbox=x='{panel_x}':y={base_y}:w='{panel_w}':h={box_h}:"
            f"color=black@0.34:t=fill:enable='between(t,{st:.3f},{en:.3f})'"
        )
        # colored accent wipe gives the title a real motion-graphics entrance
        step(
            f"drawbox=x='{accent_x}':y={base_y+box_h-6}:w='{accent_w}':h=6:"
            f"color={theme['accent']}@0.94:t=fill:enable='between(t,{st:.3f},{en:.3f})'"
        )
        # one short glow flash behind the title, then completely static
        step(
            f"drawtext=fontfile={FONT}:text='{esc(opening_text)}':fontcolor=white@0.10:fontsize=40:"
            f"borderw=7:bordercolor={theme['accent']}@0.55:x='{text_x}':y='{text_y}':"
            f"alpha='{entrance_only_alpha(st+0.03,0.30,0.60)}'"
        )
        step(
            f"drawtext=fontfile={FONT}:text='{esc(opening_text)}':fontcolor=white:fontsize=40:"
            f"shadowx=2:shadowy=2:shadowcolor=black@0.38:x='{text_x}':y='{text_y}':"
            f"alpha='{fade_alpha(st,en,0.14)}'"
        )

    # IMPACT KEYWORD: center-expanding plate + flash outline + underline sweep + one overshoot.
    if edit_plan["show_keyword"] and texts["keyword"]:
        st, en = timing["keyword_start"], timing["keyword_end"]
        kw = texts["keyword"]
        box_w = min(650, max(270, 34 * len(kw) + 108))
        box_h = 96
        center_x = OUTPUT_W / 2
        base_y = SAFE_TOP + 190
        motion = motion_plan.get("keyword_motion", "impact_pop")
        panel_w = motion_panel_width(box_w, st, TEXT_PANEL_DURATION, 0.58 if motion == "impact_pop" else 0.76)
        panel_x = motion_panel_x(center_x, box_w, st, TEXT_PANEL_DURATION, 0.58 if motion == "impact_pop" else 0.76)
        underline_w = f"({box_w-34}*{motion_ease_out(st+0.05, TEXT_ACCENT_DURATION)})"
        underline_x = f"({center_x}-({underline_w})/2)"

        if motion == "impact_pop":
            text_y = motion_pop_y(base_y + 23, st + 0.02, amp=18, dur=TEXT_BOUNCE_DURATION)
        else:
            text_y = motion_rise_y(base_y + 23, st + 0.02, pixels=14, dur=0.30)

        step(
            f"drawbox=x='{panel_x}':y={base_y}:w='{panel_w}':h={box_h}:"
            f"color={theme['accent']}@0.86:t=fill:enable='between(t,{st:.3f},{en:.3f})'"
        )
        # outline flash makes the reveal feel like a designed impact, not a plain subtitle
        step(
            f"drawbox=x='{panel_x}':y={base_y}:w='{panel_w}':h={box_h}:"
            f"color=white@0.72:t=3:enable='between(t,{st:.3f},{min(en,st+0.30):.3f})'"
        )
        step(
            f"drawbox=x='{underline_x}':y={base_y+box_h+7}:w='{underline_w}':h=5:"
            f"color=white@0.72:t=fill:enable='between(t,{st:.3f},{en:.3f})'"
        )
        # brief halo behind the word during impact only
        step(
            f"drawtext=fontfile={FONT}:text='{esc(kw)}':fontcolor=white@0.08:fontsize=49:"
            f"borderw=8:bordercolor=white@0.48:x=(w-text_w)/2:y='{text_y}':"
            f"alpha='{entrance_only_alpha(st,0.32,0.58)}'"
        )
        step(
            f"drawtext=fontfile={FONT}:text='{esc(kw)}':fontcolor=black:fontsize=49:"
            f"shadowx=1:shadowy=2:shadowcolor=white@0.20:x=(w-text_w)/2:y='{text_y}':"
            f"alpha='{fade_alpha(st,en,0.12)}'"
        )

    if edit_plan["use_progress"]:
        py = OUTPUT_H - SAFE_BOTTOM
        step(f"drawbox=x={SAFE_LEFT}:y={py}:w=780:h=8:color=black@0.16:t=fill")
        progress = f"(780*min(t/{max(duration,0.1):.3f},1))"
        step(f"drawbox=x={SAFE_LEFT}:y={py}:w='{progress}':h=8:color={theme['accent']}@0.68:t=fill")

    parts.append(f"[{chain}]format=yuv420p[vout]")
    return "".join(parts)


def build_audio_filter(info, duration, edit_plan, timing, pacing_plan, speech_windows, motion_plan, motion_graphics_plan):
    parts = []
    if info["has_audio"]:
        parts.append(build_paced_audio_prefix(pacing_plan))
        speech_expr = speech_enable_expr(speech_windows)
        dialogue_focus = ""
        if speech_expr:
            # Conservative mid/side focus during speech. In a mixed track this cannot isolate music,
            # but it gently favors centered dialogue and reduces wide background energy.
            dialogue_focus = f",stereotools=slev=0.88:mlev=1.045:enable='{speech_expr}'"
        parts.append(
            "[paceda]aformat=sample_rates=48000:channel_layouts=stereo,"
            "highpass=f=70,lowpass=f=15500,"
            "acompressor=threshold=-20dB:ratio=1.75:attack=15:release=180:makeup=1.0"
            f"{dialogue_focus},"
            f"loudnorm=I={TARGET_LUFS:.1f}:LRA=7:TP={TARGET_TRUE_PEAK_DB:.1f},"
            "alimiter=limit=0.94,"
            "afade=t=in:st=0:d=0.08,"
            f"afade=t=out:st={max(0.0,duration-0.18):.3f}:d=0.18[amain];"
        )
    else:
        parts.append(f"anullsrc=r=48000:cl=stereo:d={duration:.3f},asetpts=PTS-STARTPTS[amain];")

    cue_labels = []

    def add_text_cue(kind, when, prefix):
        if not kind or kind == "none":
            return
        delay = int(max(0.0, when) * 1000)
        label = f"{prefix}sfx"

        if kind in {"soft_whoosh", "motion_whoosh"}:
            # Purposeful title entrance: filtered air sweep with a soft tonal tail.
            nlab = f"{prefix}noise"
            tlab = f"{prefix}tone"
            parts.append(
                "anoisesrc=color=white:sample_rate=48000:duration=0.19,"
                "highpass=f=700,lowpass=f=3900,"
                "afade=t=in:st=0:d=0.015,afade=t=out:st=0.085:d=0.095,"
                f"volume={min(TEXT_SFX_MAX_GAIN,0.0046):.4f},adelay={delay}|{delay}[{nlab}];"
            )
            parts.append(
                "sine=frequency=720:sample_rate=48000:duration=0.11,"
                "afade=t=in:st=0:d=0.010,afade=t=out:st=0.045:d=0.060,"
                f"volume={min(TEXT_SFX_MAX_GAIN,0.0024):.4f},adelay={delay+55}|{delay+55}[{tlab}];"
            )
            parts.append(f"[{nlab}][{tlab}]amix=inputs=2:normalize=0:duration=longest[{label}];")

        elif kind == "motion_hit":
            # Reveal impact: rounded low pop + tiny high reward tone, not an arcade jingle.
            plab = f"{prefix}pop"
            dlab = f"{prefix}ding"
            parts.append(
                "sine=frequency=430:sample_rate=48000:duration=0.10,"
                "afade=t=in:st=0:d=0.006,afade=t=out:st=0.035:d=0.060,"
                f"volume={min(TEXT_SFX_MAX_GAIN,0.0052):.4f},adelay={delay}|{delay}[{plab}];"
            )
            parts.append(
                "sine=frequency=1180:sample_rate=48000:duration=0.13,"
                "afade=t=in:st=0:d=0.010,afade=t=out:st=0.050:d=0.075,"
                f"volume={min(TEXT_SFX_MAX_GAIN,0.0036):.4f},adelay={delay+42}|{delay+42}[{dlab}];"
            )
            parts.append(f"[{plab}][{dlab}]amix=inputs=2:normalize=0:duration=longest[{label}];")

        elif kind == "soft_pop":
            parts.append(
                "sine=frequency=540:sample_rate=48000:duration=0.085,"
                "afade=t=in:st=0:d=0.008,afade=t=out:st=0.035:d=0.045,"
                f"volume={min(TEXT_SFX_MAX_GAIN,0.0046):.4f},adelay={delay}|{delay}[{label}];"
            )
        else:  # soft_ding
            parts.append(
                "sine=frequency=1046:sample_rate=48000:duration=0.12,"
                "afade=t=in:st=0:d=0.010,afade=t=out:st=0.050:d=0.065,"
                f"volume={min(TEXT_SFX_MAX_GAIN,0.0042):.4f},adelay={delay}|{delay}[{label}];"
            )
        cue_labels.append(f"[{label}]")

    # The cue is synchronized to the first visible frame of the text motion.
    if edit_plan.get("opening") != "none":
        add_text_cue(motion_plan.get("opening_sfx"), timing["opening_start"], "open")
    if edit_plan.get("show_keyword"):
        add_text_cue(motion_plan.get("keyword_sfx"), timing["keyword_start"], "kw")

    # Motion graphics share the reveal sound whenever typography already owns it.
    # A separate tiny whoosh is allowed only when no keyword SFX is present.
    mg_sfx = (motion_graphics_plan or {}).get("sfx", "none")
    if mg_sfx != "none" and motion_plan.get("keyword_sfx", "none") == "none":
        add_text_cue("soft_whoosh", timing["reveal"], "mg")

    if cue_labels:
        if len(cue_labels) == 1:
            parts.append(f"{cue_labels[0]}anull[textsfx];")
        else:
            parts.append("".join(cue_labels) + f"amix=inputs={len(cue_labels)}:normalize=0:duration=longest[textsfx];")
        # Duck typography cues under dialogue/main mix. This keeps words readable and speech dominant.
        parts.append("[amain]asplit=2[amainmix][side];")
        parts.append(
            "[textsfx][side]sidechaincompress=threshold=0.040:ratio=10:attack=3:release=90:mix=1[textsfxduck];"
        )
        parts.append("[amainmix][textsfxduck]amix=inputs=2:normalize=0:duration=first[aout]")
    else:
        parts.append("[amain]anull[aout]")
    return "".join(parts)

def normalize_intro(src, dest):
    """Normalize intro/closure to exact output format; speed is NOT changed."""
    info = ffprobe_video_info(src)
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not info["has_audio"]:
        cmd += [
            "-f", "lavfi", "-i",
            f"anullsrc=r=48000:cl=stereo:d={max(info['duration'],0.1):.3f}"
        ]

    cmd += [
        "-map", "0:v:0",
        "-map", "0:a:0" if info["has_audio"] else "1:a:0",
        "-vf",
        f"scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},fps={OUTPUT_FPS},setsar=1,format=yuv420p",
        "-af",
        f"aformat=sample_rates=48000:channel_layouts=stereo,aresample=async=1:first_pts=0,"
        f"loudnorm=I={TARGET_LUFS:.1f}:LRA=7:TP={TARGET_TRUE_PEAK_DB:.1f},alimiter=limit=0.94",
        "-shortest",
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

    silence_intervals = detect_silence_intervals(src, source_duration) if info["has_audio"] else []
    pacing_plan = build_pacing_plan(source_duration, silence_intervals)
    duration = pacing_plan["output_duration"] or source_duration / SHORT_PLAYBACK_SPEED

    theme = category_theme(payload.get("category"))
    color_plan = category_color_plan(payload.get("category"))
    edit_plan = build_edit_plan(payload, duration)
    timing = build_timing_plan(payload, source_duration, pacing_plan, silence_intervals)
    texts = build_text_plan(payload, edit_plan)
    motion_plan = build_motion_typography_plan(payload, edit_plan)
    motion_graphics_plan = build_motion_graphics_plan(payload, edit_plan, theme)
    transition_plan = build_transition_plan(edit_plan)

    source_speech = complement_intervals(silence_intervals, source_duration)
    speech_windows = map_intervals_to_output(source_speech, pacing_plan)
    output_silences = map_intervals_to_output(silence_intervals, pacing_plan)

    vf = build_visual_filter(
        info, payload, duration, edit_plan, timing, texts, theme, pacing_plan, color_plan, motion_plan, motion_graphics_plan
    )
    af = build_audio_filter(
        info, duration, edit_plan, timing, pacing_plan, speech_windows, motion_plan, motion_graphics_plan
    )

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
        "transition_plan": transition_plan,
        "pacing_plan": pacing_plan,
        "source_silence_intervals": silence_intervals,
        "output_silence_intervals": output_silences,
        "output_speech_intervals": speech_windows,
        "color_plan": color_plan,
        "theme": theme,
        "body_duration": duration,
        "source_info": info,
        "texts": texts,
        "motion_typography_plan": motion_plan,
        "motion_graphics_plan": motion_graphics_plan,
        "audio_plan": {
            "target_lufs": TARGET_LUFS,
            "target_true_peak_db": TARGET_TRUE_PEAK_DB,
            "silence_threshold_db": SILENCE_DB,
            "silence_min_duration": SILENCE_MIN_DURATION,
            "dialogue_focus": bool(speech_windows and info["has_audio"]),
            "sfx_ducking": bool(motion_plan.get("opening_sfx") != "none" or motion_plan.get("keyword_sfx") != "none"),
            "text_sfx": {"opening": motion_plan.get("opening_sfx"), "keyword": motion_plan.get("keyword_sfx")},
            "motion_graphics_sfx": motion_graphics_plan.get("sfx", "none"),
        },
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
        prepend_intro(intro_norm, edited_body, body_with_intro, xfade_dur=result["transition_plan"]["intro_xfade"])

        download_google_drive_file(CLOSURE_DRIVE_FILE_ID, closure_raw, label="KP Kids closure")
        normalize_intro(closure_raw, closure_norm)
        append_closure(body_with_intro, closure_norm, Path(args.output), xfade_dur=result["transition_plan"]["closure_xfade"])

    meta = dict(payload)
    meta["editor_version"] = EDITOR_VERSION
    meta["short_playback_speed"] = SHORT_PLAYBACK_SPEED
    meta["pacing_mode"] = "silence_aware_variable_speed"
    meta["editorial_style"] = result["editorial_style"]
    meta["edit_plan"] = result["edit_plan"]
    meta["timing_plan"] = result["timing_plan"]
    meta["transition_plan"] = result["transition_plan"]
    meta["pacing_plan"] = result["pacing_plan"]
    meta["source_silence_intervals"] = result["source_silence_intervals"]
    meta["output_silence_intervals"] = result["output_silence_intervals"]
    meta["output_speech_intervals"] = result["output_speech_intervals"]
    meta["color_plan"] = result["color_plan"]
    meta["audio_plan"] = result["audio_plan"]
    meta["motion_typography_plan"] = result["motion_typography_plan"]
    meta["motion_graphics_plan"] = result["motion_graphics_plan"]
    meta["edit_theme"] = result["theme"]
    meta["source_video_info"] = result["source_info"]
    meta["body_duration_after_speed"] = result["body_duration"]
    meta["intro_drive_file_id"] = INTRO_DRIVE_FILE_ID
    meta["intro_prepend_enabled"] = True
    meta["closure_drive_file_id"] = CLOSURE_DRIVE_FILE_ID
    meta["closure_append_enabled"] = True
    try:
        meta["final_output_info"] = ffprobe_video_info(Path(args.output))
    except Exception as e:
        meta["final_output_info"] = {"probe_error": str(e)}
    Path("edit_result.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
