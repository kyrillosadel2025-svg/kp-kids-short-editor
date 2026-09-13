#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# KP Kids Short Editor V5.5: Smart Kids Edit + Intro + Closure + Robust Drive Retry
# V5.2 polish pass: smooth alpha fades on all on-screen text (no more hard pop
# in/out), a real crossfade between the intro and the Short instead of a hard
# cut, the topic line now actually renders (was computed but never drawn),
# and the two UI chime tones are lightly enveloped so they don't click.
# V5.3 kid-fun pass: bouncy "boing" pop-ins on every text element instead of
# a flat fade, a playful wiggle on the keyword highlight, a scattered field
# of twinkling confetti squares throughout the clip, a celebratory sparkle
# burst around the end card, and the chimes are now a 2-note "sparkle" cue
# plus a 3-note rising "ta-da!" instead of two flat beeps. Also fixed a
# latent filtergraph bug where the audio chain reused the video chain's
# [a0]/[c1] labels (ffmpeg requires every pad label to be globally unique).

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
                     .replace("\/", "/")
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

def ffprobe_duration(path):
    p = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path)
        ],
        capture_output=True, text=True, check=True
    )
    return float(p.stdout.strip())

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

def fade_alpha(start, end, fade=0.15):
    """
    Alpha expression for drawtext: smooth ease-in/ease-out instead of an
    instant pop, using ffmpeg's 'alpha' option (per-frame expression).
    Fully transparent outside [start, end], fully opaque in the middle,
    with a short linear ramp of `fade` seconds on each edge.
    """
    span = max(end - start, 0.02)
    fade = min(fade, span / 2)
    return (
        f"if(lt(t,{start:.3f}),0,"
        f"if(lt(t,{start+fade:.3f}),(t-{start:.3f})/{fade:.3f},"
        f"if(lt(t,{end-fade:.3f}),1,"
        f"if(lt(t,{end:.3f}),({end:.3f}-t)/{fade:.3f},0))))"
    )

def bounce_offset(start, amp=16.0, dur=0.32, freq=15.0):
    """
    A kid-friendly "boing" used for text pop-ins: at `start` the element is
    offset by `amp` pixels, then springs toward its resting position with a
    couple of quick decaying oscillations before settling at 0. Add this to
    a resting y (or x) coordinate instead of using a flat fade-in, so text
    feels like it bounces onto screen rather than just appearing.
    """
    return (
        f"if(lt(t,{start:.3f}),{amp:.1f},"
        f"{amp:.1f}*exp(-9*(t-{start:.3f}))*cos({freq:.1f}*(t-{start:.3f})))"
    )

def build_confetti(chain_in, specs, prefix):
    """
    Chain a handful of tiny squares onto `chain_in` that twinkle on and off
    forever on their own repeating cycle, like a scattered field of
    confetti/sparkles. Each spec is (x, y, size, color, phase, period,
    on_frac). Keep specs near the frame edges so they never compete with the
    hook/caption/keyword text for attention.
    """
    parts = []
    label = chain_in
    for idx, (x, y, size, color, phase, period, on_frac) in enumerate(specs):
        nxt = f"{prefix}{idx}"
        on_dur = period * on_frac
        parts.append(
            f"[{label}]drawbox=x={x}:y={y}:w={size}:h={size}:color={color}@0.85:t=fill:"
            f"enable='lt(mod(t+{phase:.2f},{period:.2f}),{on_dur:.2f})'[{nxt}];"
        )
        label = nxt
    return "".join(parts), label

def build_sparkle_burst(chain_in, specs, prefix, final_label=None):
    """
    Chain a handful of tiny squares onto `chain_in` that each flash once
    inside their own (t0, t1) window, used for a one-off celebratory burst
    around the end card rather than a continuous twinkle.
    """
    parts = []
    label = chain_in
    n = len(specs)
    for idx, (x, y, size, color, t0, t1) in enumerate(specs):
        nxt = final_label if (final_label and idx == n - 1) else f"{prefix}{idx}"
        parts.append(
            f"[{label}]drawbox=x={x}:y={y}:w={size}:h={size}:color={color}@0.92:t=fill:"
            f"enable='between(t,{t0:.2f},{t1:.2f})'[{nxt}];"
        )
        label = nxt
    return "".join(parts), label

def style_from_id(short_id):
    digest = hashlib.sha256(str(short_id).encode("utf-8")).digest()
    return digest[0] % 6

def category_label(category):
    mapping = {
        "alphabet": "ALPHABET TIME",
        "letters": "LETTER MATCH",
        "phonics": "PHONICS FUN",
        "numbers": "NUMBER FUN",
        "counting": "COUNT WITH US",
        "shapes": "SHAPE TIME",
        "colors": "COLOR FUN",
        "science": "SCIENCE TIME",
        "space": "SPACE TIME",
        "nature": "NATURE TIME",
        "body": "HEALTHY BODY",
        "safety": "SAFE & SMART",
        "emotions": "FEELINGS TIME",
        "manners": "KINDNESS TIME",
        "community": "HELPERS TIME",
        "transport": "LET'S GO",
        "weather": "WEATHER TIME",
        "animals": "ANIMAL TIME",
        "food": "HEALTHY FOOD",
        "math": "MATH TIME",
        "opposites": "OPPOSITES",
        "world": "WORLD EXPLORERS",
        "positions": "POSITION WORDS",
        "sorting": "SORT & LEARN",
        "patterns": "PATTERN TIME",
        "sizes": "SIZE TIME",
        "directions": "DIRECTIONS",
        "calendar": "DAYS & CALENDAR",
        "seasons": "SEASONS",
        "time": "ROUTINE TIME",
    }
    return mapping.get(str(category or "").lower(), "KP KIDS")

def category_theme(category):
    c = str(category or "").lower()
    themes = {
        "alphabet":  {"box":"0x2E86FF", "accent":"0xFFD54A", "end":"0x1C6DD0"},
        "letters":   {"box":"0x3F7DFF", "accent":"0xFFD54A", "end":"0x2F5FD0"},
        "phonics":   {"box":"0x6A5AE0", "accent":"0xFFB347", "end":"0x4D3FCF"},
        "numbers":   {"box":"0x28B463", "accent":"0xFFA630", "end":"0x239B56"},
        "counting":  {"box":"0x16A085", "accent":"0xF5B041", "end":"0x138D75"},
        "shapes":    {"box":"0x8E44AD", "accent":"0x5DADE2", "end":"0x6C3483"},
        "colors":    {"box":"0xFF5E7E", "accent":"0x7DFF7A", "end":"0xF39C12"},
        "science":   {"box":"0x1F618D", "accent":"0x76D7C4", "end":"0x154360"},
        "space":     {"box":"0x34495E", "accent":"0xF4D03F", "end":"0x1B2631"},
        "nature":    {"box":"0x239B56", "accent":"0x7DCEA0", "end":"0x196F3D"},
        "body":      {"box":"0xE67E22", "accent":"0xF8C471", "end":"0xCA6F1E"},
        "safety":    {"box":"0xC0392B", "accent":"0xF7DC6F", "end":"0x922B21"},
        "emotions":  {"box":"0xEC7063", "accent":"0xF8C471", "end":"0xCD6155"},
        "manners":   {"box":"0xAF7AC5", "accent":"0xF9E79F", "end":"0x8E44AD"},
        "community": {"box":"0x2980B9", "accent":"0xAED6F1", "end":"0x1F618D"},
        "transport": {"box":"0x2874A6", "accent":"0xF5B041", "end":"0x1B4F72"},
        "weather":   {"box":"0x5DADE2", "accent":"0xF7DC6F", "end":"0x3498DB"},
        "animals":   {"box":"0x27AE60", "accent":"0xF4D03F", "end":"0x1E8449"},
        "food":      {"box":"0xE74C3C", "accent":"0x82E0AA", "end":"0xCB4335"},
        "math":      {"box":"0x16A085", "accent":"0xF8C471", "end":"0x117A65"},
        "opposites": {"box":"0x6C5CE7", "accent":"0xFFEAA7", "end":"0x5B4DD1"},
        "world":     {"box":"0x2874A6", "accent":"0x58D68D", "end":"0x1B4F72"},
        "positions": {"box":"0x7D3C98", "accent":"0xF7DC6F", "end":"0x5B2C6F"},
        "sorting":   {"box":"0x17A589", "accent":"0xF8C471", "end":"0x117864"},
        "patterns":  {"box":"0xAF601A", "accent":"0xAED6F1", "end":"0x935116"},
        "sizes":     {"box":"0x884EA0", "accent":"0xF9E79F", "end":"0x6C3483"},
        "directions":{"box":"0x2E86C1", "accent":"0xF8C471", "end":"0x21618C"},
        "calendar":  {"box":"0xCB4335", "accent":"0xF9E79F", "end":"0xA93226"},
        "seasons":   {"box":"0x239B56", "accent":"0x5DADE2", "end":"0x196F3D"},
        "time":      {"box":"0x566573", "accent":"0xF8C471", "end":"0x34495E"},
    }
    return themes.get(c, {"box":"0x3A7BFF", "accent":"0xFFD54A", "end":"0x2C5FCC"})


def smart_caption(category, topic):
    c = str(category or "").lower()
    t = str(topic or "").strip()
    # Keep captions intentionally short and generic-safe.
    mapping = {
        "alphabet": "Let’s learn a letter!",
        "letters": "Match the letters!",
        "phonics": "Listen to the sound!",
        "numbers": "Let’s count together!",
        "counting": "Count with us!",
        "shapes": "Can you spot the shape?",
        "colors": "Look at the color!",
        "science": "Let’s discover something!",
        "space": "Explore space with us!",
        "nature": "Look closely at nature!",
        "body": "Learn about your body!",
        "safety": "Stay safe and smart!",
        "emotions": "Let’s talk about feelings!",
        "manners": "Kind words matter!",
        "community": "Meet our helpers!",
        "transport": "Let’s learn how we move!",
        "weather": "What’s the weather like?",
        "animals": "Meet an amazing animal!",
        "food": "Let’s learn about food!",
        "math": "Let’s solve it together!",
        "opposites": "Find the opposite!",
        "world": "Explore our world!",
        "positions": "Where is it?",
        "sorting": "Let’s sort them!",
        "patterns": "Find the pattern!",
        "sizes": "Which one is bigger?",
        "directions": "Which way should we go?",
        "calendar": "Let’s learn the days!",
        "seasons": "What season is it?",
        "time": "Let’s learn our routine!",
    }
    return mapping.get(c, "Let’s learn together!")

def keyword_from_topic(topic, category):
    t = str(topic or "").strip()
    if not t:
        return category_label(category).replace(" TIME", "").replace(" FUN", "")[:16]
    # Choose a useful compact token from the provided topic, avoiding filler words.
    bad = {"and","the","with","for","from","into","about","this","that","time","kids","learn","learning"}
    words = [w.strip(".,!?;:-_()[]{}'\"") for w in t.split()]
    candidates = [w for w in words if len(w) >= 3 and w.lower() not in bad]
    if not candidates:
        return words[0][:16] if words else "LEARN"
    candidates.sort(key=lambda w: (-len(w), words.index(w)))
    return candidates[0][:16].upper()


def normalize_intro(src, dest):
    """Normalize an intro/closure clip to the exact same technical format as edited Shorts."""
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=24,setsar=1,format=yuv420p",
        "-af",
        "aformat=sample_rates=48000:channel_layouts=stereo,"
        "aresample=async=1:first_pts=0",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dest),
    ]
    run(cmd)

def prepend_intro(intro, body, output, xfade_dur=0.35):
    """
    Place the KP Kids intro before the edited Short and export one final MP4.
    Uses a short professional crossfade (video xfade + audio acrossfade)
    instead of a hard cut, so the intro dissolves into the Short.
    """
    intro_dur = ffprobe_duration(intro)
    xfade_dur = min(xfade_dur, max(intro_dur - 0.05, 0.05))
    offset = max(0.0, intro_dur - xfade_dur)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(intro),
        "-i", str(body),
        "-filter_complex",
        "[0:v]setpts=PTS-STARTPTS[v0];"
        "[1:v]setpts=PTS-STARTPTS[v1];"
        f"[v0][v1]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}[vout];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]acrossfade=d={xfade_dur:.3f}[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]
    run(cmd)


def append_closure(body_with_intro, closure, output, xfade_dur=0.35):
    """
    Append the fixed KP Kids closure AFTER the edited Short.

    A short video/audio crossfade is used so the Short flows naturally
    into the closure instead of ending with a hard cut.
    """
    main_dur = ffprobe_duration(body_with_intro)
    closure_dur = ffprobe_duration(closure)

    # Keep the dissolve safely shorter than either clip.
    xfade_dur = min(
        xfade_dur,
        max(main_dur - 0.05, 0.05),
        max(closure_dur - 0.05, 0.05),
    )
    offset = max(0.0, main_dur - xfade_dur)

    cmd = [
        "ffmpeg", "-y",
        "-i", str(body_with_intro),
        "-i", str(closure),
        "-filter_complex",
        "[0:v]setpts=PTS-STARTPTS[v0];"
        "[1:v]setpts=PTS-STARTPTS[v1];"
        f"[v0][v1]xfade=transition=fade:duration={xfade_dur:.3f}:offset={offset:.3f}[vout];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a0];"
        "[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[a1];"
        f"[a0][a1]acrossfade=d={xfade_dur:.3f}[aout]",
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(output),
    ]
    run(cmd)


def edit_video(src, out, payload):
    short_id = payload.get("short_id", "")
    category = str(payload.get("category", "")).lower()
    topic = str(payload.get("topic", "") or payload.get("title", "KP Kids"))
    caption_text = smart_caption(category, topic)
    keyword_text = keyword_from_topic(topic, category)
    style = style_from_id(short_id)
    theme = category_theme(category)
    duration = min(15.0, ffprobe_duration(src))

    hook = category_label(category)[:28]
    topic_text = topic.replace("#Shorts", "").strip()[:34]
    end_texts = [
        "GREAT JOB!",
        "YOU DID IT!",
        "KEEP LEARNING!",
        "AWESOME WORK!",
        "SEE YOU NEXT TIME!",
        "LET’S LEARN MORE!"
    ]
    end_text = end_texts[style]

    # 6 deterministic reframing variants
    if style == 0:
        zoom = "1.010+0.012*sin(2*PI*on/(24*6.0))"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"
        hook_y, topic_y, progress_y = 100, 1500, 1872
    elif style == 1:
        zoom = "1.012+0.010*sin(2*PI*on/(24*5.5))"
        x = "(iw-iw/zoom)/2+8*sin(2*PI*on/(24*7.0))"
        y = "(ih-ih/zoom)/2"
        hook_y, topic_y, progress_y = 1520, 95, 1850
    elif style == 2:
        zoom = "1.008+0.015*sin(2*PI*on/(24*7.0))"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2+10*sin(2*PI*on/(24*8.0))"
        hook_y, topic_y, progress_y = 100, 1460, 1844
    elif style == 3:
        zoom = "1.012+0.010*sin(2*PI*on/(24*4.5))"
        x = "(iw-iw/zoom)/2+6*sin(2*PI*on/(24*5.0))"
        y = "(ih-ih/zoom)/2+6*cos(2*PI*on/(24*6.0))"
        hook_y, topic_y, progress_y = 1490, 90, 1860
    elif style == 4:
        zoom = "1.010+0.014*sin(2*PI*on/(24*6.5))"
        x = "(iw-iw/zoom)/2"
        y = "(ih-ih/zoom)/2"
        hook_y, topic_y, progress_y = 95, 1490, 1838
    else:
        zoom = "1.011+0.011*sin(2*PI*on/(24*5.8))"
        x = "(iw-iw/zoom)/2+4*cos(2*PI*on/(24*6.7))"
        y = "(ih-ih/zoom)/2"
        hook_y, topic_y, progress_y = 1505, 110, 1848

    # Topic line shares the hook's intro window (0-2s). When that slot sits
    # near the top of the frame, right-align it so it clears the always-on
    # "KP KIDS" brand pill in the top-left instead of sweeping under it.
    topic_x_expr = "(w-text_w-40)" if topic_y < 300 else "(w-text_w)/2"

    # Main effect timings
    intro_start, intro_end = 0.0, 2.0
    accent_start, accent_end = 5.0, 6.0
    end_start = max(0.0, duration - 1.9)

    # Progress bar width expression based on time
    progress_expr = f"(970*min(t/{max(duration,0.1):.3f},1))"

    # V4 Smart Kids Edit:
    # - stable base image
    # - one smooth punch-in only (not continuous motion)
    # - short safe caption
    # - one highlighted keyword
    # - branded UI without covering too much of the scene
    fade_out_start = max(0.0, duration - 0.28)
    punch_start = 6.0
    punch_end = min(duration - 2.2, 7.2)
    if punch_end <= punch_start:
        punch_start, punch_end = 5.2, min(duration - 1.8, 6.2)

    # Smooth one-time scale bump. Outside the punch window scale stays at 1.0.
    punch_scale = (
        f"if(between(t,{punch_start:.2f},{punch_end:.2f}),"
        f"1+0.032*sin(PI*(t-{punch_start:.2f})/"
        f"{max(punch_end-punch_start,0.1):.3f}),1)"
    )

    caption_start = 2.15
    caption_end = min(4.7, max(3.2, duration - 4.5))
    keyword_start = 5.15
    keyword_end = min(6.35, max(5.75, duration - 3.5))

    # Scattered confetti/sparkle field: small squares near the frame edges
    # that twinkle on a repeating cycle for the whole clip.
    twinkle_specs = [
        (46, 260, 20, theme["accent"], 0.05, 1.6, 0.35),
        (1006, 320, 16, "white", 0.40, 1.9, 0.30),
        (50, 700, 18, theme["box"], 0.90, 1.7, 0.32),
        (996, 760, 22, theme["accent"], 1.30, 2.1, 0.28),
        (48, 1150, 16, "white", 0.60, 1.5, 0.35),
        (998, 1620, 20, theme["box"], 1.10, 1.8, 0.30),
    ]
    twinkle_vf, twinkle_out = build_confetti("a1", twinkle_specs, "tw")

    # One-off celebratory sparkle burst timed to pop around the end card.
    burst_specs = [
        (100, 96, 16, "white", end_start + 0.05, min(duration - 0.02, end_start + 0.40)),
        (60, 210, 14, theme["accent"], end_start + 0.25, min(duration - 0.02, end_start + 0.60)),
        (990, 100, 16, theme["accent"], end_start + 0.15, min(duration - 0.02, end_start + 0.55)),
        (950, 220, 14, "white", end_start + 0.40, min(duration - 0.02, end_start + 0.80)),
        (520, 60, 12, theme["box"], end_start + 0.55, min(duration - 0.02, end_start + 0.95)),
    ]
    burst_vf, _ = build_sparkle_burst("etxt", burst_specs, "sp", final_label="vout")

    vf = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=32,eq=brightness=-0.045:contrast=1.04:saturation=1.08[bg2];"
        "[fg]scale=1000:1778:force_original_aspect_ratio=decrease,"
        "eq=contrast=1.035:saturation=1.055:gamma=1.01,"
        "unsharp=5:5:0.35:5:5:0.0[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];"
        "[base]vignette=PI/5.5:eval=frame,"
        "fade=t=in:st=0:d=0.18,"
        f"fade=t=out:st={fade_out_start:.3f}:d=0.28,"
        "fps=24[stable];"

        # one smooth micro punch-in
        f"[stable]scale=w='1080*{punch_scale}':h='1920*{punch_scale}':eval=frame[pz];"
        "[pz]crop=1080:1920:(iw-1080)/2:(ih-1920)/2[z0];"

        # ultra-subtle frame
        "[z0]drawbox=x=38:y=88:w=1004:h=1784:color=white@0.08:t=3[z1];"

        # small brand pill
        f"[z1]drawbox=x=54:y=54:w=235:h=64:color={theme['box']}@0.78:t=fill[b0];"
        f"[b0]drawbox=x=54:y=54:w=10:h=64:color={theme['accent']}@0.98:t=fill[b1];"
        f"[b1]drawtext=fontfile={FONT}:text='KP KIDS':fontcolor=white:fontsize=31:"
        "shadowx=1:shadowy=1:shadowcolor=black@0.45:x=82:y=69[b2];"

        # category badge
        f"[b2]drawbox=x=80:y={hook_y-18}:w=920:h=102:color=black@0.28:t=fill:"
        f"enable='between(t,{intro_start},{intro_end})'[i0];"
        f"[i0]drawbox=x=80:y={hook_y-18}:w=14:h=102:color={theme['accent']}@0.98:t=fill:"
        f"enable='between(t,{intro_start},{intro_end})'[i1];"
        f"[i1]drawtext=fontfile={FONT}:text='{esc(hook)}':fontcolor=white:fontsize=50:"
        "borderw=1:bordercolor=black@0.20:shadowx=2:shadowy=2:shadowcolor=black@0.45:"
        f"x=(w-text_w)/2:y='{hook_y+4}+{bounce_offset(intro_start)}':"
        f"alpha='{fade_alpha(intro_start, intro_end)}'[i2];"

        # specific topic line (previously computed but never rendered)
        f"[i2]drawtext=fontfile={FONT}:text='{esc(topic_text)}':fontcolor=white@0.92:fontsize=34:"
        "shadowx=1:shadowy=1:shadowcolor=black@0.55:"
        f"x={topic_x_expr}:y='{topic_y}+{bounce_offset(intro_start, amp=10.0)}':"
        f"alpha='{fade_alpha(intro_start, intro_end)}'[i3];"

        # smart short caption
        f"[i3]drawbox=x=95:y=1500:w=890:h=104:color=black@0.34:t=fill:"
        f"enable='between(t,{caption_start:.2f},{caption_end:.2f})'[c0];"
        f"[c0]drawtext=fontfile={FONT}:text='{esc(caption_text)}':fontcolor=white:fontsize=46:"
        "borderw=1:bordercolor=black@0.20:shadowx=2:shadowy=2:shadowcolor=black@0.45:"
        f"x=(w-text_w)/2:y='1528+{bounce_offset(caption_start, amp=14.0)}':"
        f"alpha='{fade_alpha(caption_start, caption_end)}'[c1];"

        # highlighted keyword: pops in with a bounce, then wiggles playfully
        # side-to-side for the rest of its on-screen time.
        f"[c1]drawbox=x=270:y=250:w=540:h=104:color={theme['accent']}@0.86:t=fill:"
        f"enable='between(t,{keyword_start:.2f},{keyword_end:.2f})'[k0];"
        f"[k0]drawtext=fontfile={FONT}:text='{esc(keyword_text)}':fontcolor=black:fontsize=52:"
        "borderw=0:shadowx=1:shadowy=1:shadowcolor=white@0.25:"
        f"x=(w-text_w)/2+7*sin(24*(t-{keyword_start:.3f})):"
        f"y='278+{bounce_offset(keyword_start, amp=14.0)}':"
        f"alpha='{fade_alpha(keyword_start, keyword_end)}'[k1];"

        # light decorative accents
        f"[k1]drawbox=x=118:y=240:w=34:h=34:color={theme['accent']}@0.72:t=fill:"
        f"enable='between(t,{accent_start},{accent_end})'[a0];"
        f"[a0]drawbox=x=930:y=286:w=20:h=20:color=white@0.65:t=fill:"
        f"enable='between(t,{accent_start},{accent_end})'[a1];"

        # scattered confetti/sparkle field along the edges, twinkling on and
        # off for the whole clip so the frame always feels a little alive
        f"{twinkle_vf}"

        # progress bar
        f"[{twinkle_out}]drawbox=x=55:y={progress_y}:w=970:h=12:color=black@0.24:t=fill[p0];"
        f"[p0]drawbox=x=55:y={progress_y}:w='{progress_expr}':h=12:color={theme['accent']}@0.92:t=fill[p1];"

        # end card
        f"[p1]drawbox=x=145:y=116:w=790:h=138:color=black@0.34:t=fill:"
        f"enable='between(t,{end_start:.2f},{duration:.2f})'[e0];"
        f"[e0]drawbox=x=145:y=116:w=18:h=138:color={theme['accent']}@0.98:t=fill:"
        f"enable='between(t,{end_start:.2f},{duration:.2f})'[e1];"
        f"[e1]drawtext=fontfile={FONT}:text='{esc(end_text)}':fontcolor=white:fontsize=58:"
        "borderw=1:bordercolor=black@0.18:shadowx=3:shadowy=3:shadowcolor=black@0.50:"
        f"x=(w-text_w)/2:y='153+{bounce_offset(end_start, amp=18.0)}':"
        f"alpha='{fade_alpha(end_start, duration)}'[etxt];"

        # celebratory sparkle burst around the end card
        f"{burst_vf}"
    )
    vf = vf.rstrip(";")

    # Audio polish: dialogue stays dominant, gentle leveling + limiter, plus
    # two playful UI cues: a quick 2-note "sparkle" as the hook pops in, and
    # a happy 3-note rising "ta-da!" arpeggio for the end card. Note: the
    # labels here (aa0, ac1...) are deliberately distinct from the video
    # chain's a0/c0/c1 labels above — vf and af get concatenated into one
    # filter_complex string, and ffmpeg requires every pad label in that
    # string to be globally unique, not just unique within its own "half".
    ch1_delay = 3400
    ch1b_delay = ch1_delay + 90
    ch2_delay = int(max(0, duration - 1.65) * 1000)
    ch2b_delay = ch2_delay + 90
    ch2c_delay = ch2_delay + 180
    audio_fade_out = max(0.0, duration - 0.22)

    af = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        "highpass=f=70,lowpass=f=15000,"
        "acompressor=threshold=-18dB:ratio=2.2:attack=12:release=180:makeup=1.4,"
        "alimiter=limit=0.96,"
        "afade=t=in:st=0:d=0.12,"
        f"afade=t=out:st={audio_fade_out:.3f}:d=0.22[aa0];"
        # sparkle cue: two quick ascending notes when the hook badge pops in
        "sine=frequency=880:sample_rate=48000:duration=0.07,"
        "afade=t=in:st=0:d=0.015,afade=t=out:st=0.04:d=0.03,"
        "volume=0.014,adelay="
        f"{ch1_delay}|{ch1_delay}[ac1];"
        "sine=frequency=1175:sample_rate=48000:duration=0.08,"
        "afade=t=in:st=0:d=0.015,afade=t=out:st=0.045:d=0.03,"
        "volume=0.013,adelay="
        f"{ch1b_delay}|{ch1b_delay}[ac1b];"
        # "ta-da!" cue: three-note rising arpeggio for the end card
        "sine=frequency=784:sample_rate=48000:duration=0.10,"
        "afade=t=in:st=0:d=0.015,afade=t=out:st=0.06:d=0.04,"
        "volume=0.013,adelay="
        f"{ch2_delay}|{ch2_delay}[ac2];"
        "sine=frequency=988:sample_rate=48000:duration=0.10,"
        "afade=t=in:st=0:d=0.015,afade=t=out:st=0.06:d=0.04,"
        "volume=0.013,adelay="
        f"{ch2b_delay}|{ch2b_delay}[ac2b];"
        "sine=frequency=1245:sample_rate=48000:duration=0.15,"
        "afade=t=in:st=0:d=0.02,afade=t=out:st=0.08:d=0.06,"
        "volume=0.015,adelay="
        f"{ch2c_delay}|{ch2c_delay}[ac2c];"
        "[aa0][ac1][ac1b][ac2][ac2b][ac2c]amix=inputs=6:normalize=0:duration=first[aout]"
    )

    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-filter_complex", vf + ";" + af,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", "24",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        "-t", f"{duration:.3f}",
        str(out)
    ]
    run(cmd)
    return {"style": style, "theme": theme}

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

        # 1) Download the generated Short
        download(source_url, src)

        # 2) Apply KP Kids Smart Kids Edit to the Short itself
        result = edit_video(src, edited_body, payload)

        # 3) Download + normalize the fixed KP Kids INTRO from Google Drive
        download_google_drive_file(INTRO_DRIVE_FILE_ID, intro_raw, label="KP Kids intro")
        normalize_intro(intro_raw, intro_norm)

        # 4) Put the intro before the edited Short with a short crossfade
        prepend_intro(intro_norm, edited_body, body_with_intro)

        # 5) Download + normalize the fixed KP Kids CLOSURE from Google Drive
        download_google_drive_file(CLOSURE_DRIVE_FILE_ID, closure_raw, label="KP Kids closure")
        normalize_intro(closure_raw, closure_norm)

        # 6) Put the closure after the Short with another short crossfade
        append_closure(body_with_intro, closure_norm, Path(args.output))

    meta = dict(payload)
    meta["edit_style"] = result["style"]
    meta["edit_theme"] = result["theme"]
    meta["editor_version"] = "V5.5 Smart Kids Edit + KP Kids Intro + Closure + Drive Retry"
    meta["intro_drive_file_id"] = INTRO_DRIVE_FILE_ID
    meta["intro_prepend_enabled"] = True
    meta["closure_drive_file_id"] = CLOSURE_DRIVE_FILE_ID
    meta["closure_append_enabled"] = True
    Path("edit_result.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
