#!/usr/bin/env python3
"""KP Kids light Short editor.

Purpose: technical montage only. It does NOT inspect story logic, run QA, repair
semantic order, add music, or rewrite the generated video. The RAW is kept as-is
apart from technical normalization, with optional brand intro and/or closure.
"""
import argparse
import base64
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_INTRO_DRIVE_ID = "1stHOtc3CGBDU0gmr5tr0Q1t4gntpVvdf"
DEFAULT_CLOSURE_DRIVE_ID = "1_T_4-TtHeXCtniOkDlxct8dG1QI_uSnP"


def run(cmd):
    print("+", " ".join(str(x) for x in cmd), flush=True)
    subprocess.run([str(x) for x in cmd], check=True)


def capture(cmd):
    return subprocess.check_output([str(x) for x in cmd], text=True).strip()


def has_audio(path):
    try:
        out = capture([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=index", "-of", "csv=p=0", path,
        ])
        return bool(out.strip())
    except Exception:
        return False


def looks_like_media(path):
    try:
        return Path(path).stat().st_size > 100_000
    except Exception:
        return False


def download(url, dest, label="video"):
    raw = str(url or "").strip()
    if not raw:
        raise RuntimeError(f"Missing URL for {label}")
    if raw.startswith("file://"):
        shutil.copyfile(raw[7:], dest)
        return
    if Path(raw).exists():
        shutil.copyfile(raw, dest)
        return
    req = urllib.request.Request(raw, headers={"User-Agent": "KP-Kids-Light-Editor/1.0"})
    with urllib.request.urlopen(req, timeout=240) as r, open(dest, "wb") as f:
        while True:
            chunk = r.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    if not looks_like_media(dest):
        raise RuntimeError(f"Downloaded {label} is too small or invalid")


def download_drive_public(file_id, dest, label="Google Drive video"):
    if not file_id:
        raise RuntimeError(f"Missing Drive file id for {label}")
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", "Mozilla/5.0 KP-Kids-Light-Editor/1.0"), ("Accept", "*/*")]
    endpoints = [
        f"https://drive.usercontent.google.com/download?id={urllib.parse.quote(file_id)}&export=download&confirm=t",
        f"https://drive.google.com/uc?export=download&id={urllib.parse.quote(file_id)}&confirm=t",
    ]
    last = None
    for url in endpoints:
        for attempt in range(3):
            try:
                req = urllib.request.Request(url)
                with opener.open(req, timeout=240) as r:
                    ctype = (r.headers.get("Content-Type") or "").lower()
                    data = r.read()
                Path(dest).write_bytes(data)
                if "text/html" not in ctype and looks_like_media(dest):
                    return
                last = RuntimeError(f"{label} returned HTML or invalid media")
            except Exception as e:
                last = e
            if attempt < 2:
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f"Could not download {label}: {last}")


def normalize_clip(src, dest):
    vf = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1,fps=30,format=yuv420p"
    common = [
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
    ]
    if has_audio(src):
        run([
            "ffmpeg", "-y", "-i", src,
            "-map", "0:v:0", "-map", "0:a:0",
            "-vf", vf,
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            *common, dest,
        ])
    else:
        run([
            "ffmpeg", "-y", "-i", src,
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-map", "0:v:0", "-map", "1:a:0", "-shortest",
            "-vf", vf,
            *common, dest,
        ])


def concat_normalized(parts, dest, work):
    if len(parts) == 1:
        shutil.copyfile(parts[0], dest)
        return
    manifest = Path(work) / "concat.txt"
    manifest.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts), encoding="utf-8")
    # All parts were normalized to the same codecs/geometry/audio format.
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", manifest, "-c", "copy", "-movflags", "+faststart", dest])


def source_download(payload, dest):
    drive_id = str(payload.get("drive_file_id") or "").strip()
    proxy_base = str(payload.get("raw_proxy_base_url") or "").strip()
    proxy_token = str(payload.get("raw_proxy_token") or "").strip()
    if drive_id and proxy_base and proxy_token:
        sep = "&" if "?" in proxy_base else "?"
        url = proxy_base + sep + urllib.parse.urlencode({"token": proxy_token, "file_id": drive_id})
        download(url, dest, "saved RAW via n8n proxy")
        return "drive_proxy"
    url = str(payload.get("video_url") or payload.get("videoUrl") or "").strip()
    if url:
        download(url, dest, "RAW Short")
        return "video_url"
    if drive_id:
        download_drive_public(drive_id, dest, "saved RAW")
        return "drive_public"
    raise RuntimeError("Payload has neither video_url nor drive_file_id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload-b64", required=True)
    ap.add_argument("--output", default="kp_kids_edited_short.mp4")
    args = ap.parse_args()

    payload = json.loads(base64.b64decode(args.payload_b64).decode("utf-8"))
    include_intro = bool(payload.get("include_intro"))
    include_closure = bool(payload.get("include_closure"))
    intro_id = str(payload.get("intro_drive_file_id") or DEFAULT_INTRO_DRIVE_ID)
    closure_id = str(payload.get("closure_drive_file_id") or DEFAULT_CLOSURE_DRIVE_ID)

    with tempfile.TemporaryDirectory(prefix="kp_kids_short_") as td:
        work = Path(td)
        raw = work / "raw_source.mp4"
        source_kind = source_download(payload, raw)
        raw_norm = work / "raw_norm.mp4"
        normalize_clip(raw, raw_norm)

        parts = []
        if include_intro:
            intro = work / "intro_source.mp4"
            intro_norm = work / "intro_norm.mp4"
            download_drive_public(intro_id, intro, "KP Kids intro")
            normalize_clip(intro, intro_norm)
            parts.append(intro_norm)

        parts.append(raw_norm)

        if include_closure:
            closure = work / "closure_source.mp4"
            closure_norm = work / "closure_norm.mp4"
            download_drive_public(closure_id, closure, "KP Kids closure")
            normalize_clip(closure, closure_norm)
            parts.append(closure_norm)

        concat_normalized(parts, args.output, work)

    result = {
        "status": "success",
        "short_id": payload.get("short_id", ""),
        "edit_variant": payload.get("edit_variant", "none"),
        "edit_variant_label": payload.get("edit_variant_label", "Montage Only"),
        "include_intro": include_intro,
        "include_closure": include_closure,
        "source_kind": source_kind,
        "editor": "light_technical_montage",
        "story_qa": False,
        "story_repair": False,
        "added_music": False,
        "output": args.output,
    }
    Path("edit_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
