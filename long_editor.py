#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KP Kids Long Video Editor V1.1

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

EDITOR_VERSION = "KP Kids Long Editor V1.1 - Same-Category Compilation + Audible Smart Music"
INTRO_DRIVE_FILE_ID = "1stHOtc3CGBDU0gmr5tr0Q1t4gntpVvdf"
CLOSURE_DRIVE_FILE_ID = "1_T_4-TtHeXCtniOkDlxct8dG1QI_uSnP"
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


def normalize_vertical_clip(src, dest, title=""):
    """Turn a vertical RAW short into an attractive 16:9 scene."""
    dur = max(0.5, ffprobe_duration(src))
    audio = has_audio(src)
    fade_out = max(0.0, dur - SEGMENT_FADE)
    # Background: enlarged + heavily blurred + slightly darkened.
    # Foreground: full vertical frame centered and nearly full height.
    fg = (
        f"[0:v]split=2[bg][fg];"
        f"[bg]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},gblur=sigma=32,eq=brightness=-0.11:saturation=0.82[bg2];"
        f"[fg]scale=-2:{FOREGROUND_H}:force_original_aspect_ratio=decrease,"
        f"setsar=1[fg2];"
        f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,"
        f"fade=t=in:st=0:d={SEGMENT_FADE:.2f},"
        f"fade=t=out:st={fade_out:.3f}:d={SEGMENT_FADE:.2f},"
        f"fps={OUTPUT_FPS},format=yuv420p[v]"
    )
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if not audio:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"anullsrc=r={AUDIO_RATE}:cl=stereo"]
    cmd += ["-filter_complex", fg, "-map", "[v]"]
    if audio:
        cmd += [
            "-map", "0:a:0", "-af",
            f"aresample=48000,afade=t=in:st=0:d={SEGMENT_FADE:.2f},"
            f"afade=t=out:st={fade_out:.3f}:d={SEGMENT_FADE:.2f},"
            "loudnorm=I=-15:LRA=11:TP=-1.5"
        ]
    else:
        cmd += ["-map", "1:a:0"]
    cmd += [
        "-r", str(OUTPUT_FPS), "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", VIDEO_BITRATE, "-maxrate", VIDEO_MAXRATE, "-bufsize", VIDEO_BUFSIZE,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2",
        "-movflags", "+faststart", "-shortest", str(dest)
    ]
    run(cmd)
    return dur


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
            dur = normalize_vertical_clip(raw, norm, clip.get("title", ""))
            normalized.append(norm)
            used.append({
                "short_id": str(clip.get("short_id") or ""),
                "title": sanitize_text(clip.get("title")),
                "category": str(clip.get("category") or ""),
                "duration": round(dur, 3),
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
    if payload.get("include_intro", True):
        intro_raw = work / "intro_raw.mp4"
        intro_norm = work / "intro_norm.mp4"
        if raw_proxy_base_url and raw_proxy_token:
            download_via_n8n_proxy(raw_proxy_base_url, raw_proxy_token, INTRO_DRIVE_FILE_ID, intro_raw, label="Landscape intro via n8n")
        else:
            download_google_drive_file(INTRO_DRIVE_FILE_ID, intro_raw, label="Landscape intro")
        normalize_brand_clip(intro_raw, intro_norm)
        final_parts.append(intro_norm)

    final_parts.append(body)

    if payload.get("include_closure", True):
        closure_raw = work / "closure_raw.mp4"
        closure_norm = work / "closure_norm.mp4"
        if raw_proxy_base_url and raw_proxy_token:
            download_via_n8n_proxy(raw_proxy_base_url, raw_proxy_token, CLOSURE_DRIVE_FILE_ID, closure_raw, label="Landscape closure via n8n")
        else:
            download_google_drive_file(CLOSURE_DRIVE_FILE_ID, closure_raw, label="Landscape closure")
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
        "intro_drive_file_id": INTRO_DRIVE_FILE_ID if payload.get("include_intro", True) else "",
        "closure_drive_file_id": CLOSURE_DRIVE_FILE_ID if payload.get("include_closure", True) else "",
    }
    Path(args.meta_out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
