#!/usr/bin/env python3
import argparse, base64, json, math, re, shutil, subprocess, tempfile, urllib.request
from pathlib import Path
import cv2
import numpy as np

def run(cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)

def decode_payload(payload_b64):
    return json.loads(base64.b64decode(payload_b64).decode("utf-8"))

def download(url, dest):
    url = str(url or "").strip().lstrip("=")
    if not url:
        raise RuntimeError("Missing video_url")
    if url.startswith("file://"):
        shutil.copyfile(url[7:], dest)
        return
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 KP-Kids-QA/1.0"})
    with urllib.request.urlopen(req, timeout=90) as r, open(dest, "wb") as f:
        while True:
            block = r.read(1024 * 1024)
            if not block:
                break
            f.write(block)
    if Path(dest).stat().st_size < 100_000:
        raise RuntimeError("Downloaded RAW video is unexpectedly small")

def ffprobe(path):
    p = run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=index,codec_type,width,height,sample_rate,channels",
        "-of", "json", str(path)
    ])
    return json.loads(p.stdout)

def silence_intervals(path, duration):
    p = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "silencedetect=noise=-35dB:d=0.15", "-f", "null", "-"
    ], check=False)
    text = (p.stderr or "") + "\n" + (p.stdout or "")
    starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", text)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", text)]
    out = []
    for i, st in enumerate(starts):
        en = ends[i] if i < len(ends) else duration
        out.append((max(0.0, min(st, duration)), max(st, min(en, duration))))
    return out

def nonsilent_coverage(window, silences):
    a, b = window
    total = max(0.0, b - a)
    silent = 0.0
    for st, en in silences:
        silent += max(0.0, min(b, en) - max(a, st))
    return max(0.0, total - silent)

def volume_metrics(path):
    p = run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "volumedetect", "-f", "null", "-"
    ], check=False)
    text = p.stderr or ""
    def get(name):
        m = re.search(rf"{name}:\s*(-?[0-9.]+) dB", text)
        return float(m.group(1)) if m else None
    return get("mean_volume"), get("max_volume")

def global_zoom_metrics(path, sample_dt=0.25):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / fps if fps else 0.0
    frames, times = [], []
    t = 0.0
    while t < duration:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
        times.append(t)
        t += sample_dt
    cap.release()

    cumulative = [1.0]
    valid_pairs = 0
    for a, b in zip(frames, frames[1:]):
        h, w = a.shape
        mask = np.zeros_like(a)
        mask[: int(h * 0.38), :] = 255
        mask[:, : int(w * 0.15)] = 255
        mask[:, int(w * 0.85):] = 255

        pts = cv2.goodFeaturesToTrack(a, maxCorners=350, qualityLevel=0.01, minDistance=7, mask=mask)
        scale = None
        if pts is not None and len(pts) >= 12:
            p2, st, _ = cv2.calcOpticalFlowPyrLK(
                a, b, pts, None, winSize=(21, 21), maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01)
            )
            p1 = pts[st == 1].reshape(-1, 2)
            p2v = p2[st == 1].reshape(-1, 2)
            if len(p1) >= 10:
                M, _ = cv2.estimateAffinePartial2D(
                    p1, p2v, method=cv2.RANSAC,
                    ransacReprojThreshold=2.0, maxIters=2000, confidence=0.99
                )
                if M is not None:
                    candidate = math.sqrt(M[0, 0] ** 2 + M[0, 1] ** 2)
                    if 0.88 <= candidate <= 1.15:
                        scale = candidate
                        valid_pairs += 1

        cumulative.append(cumulative[-1] if scale is None else cumulative[-1] * scale)

    if not cumulative:
        return {"zoom_ratio":1.0,"max_zoom":1.0,"min_zoom":1.0,"peak_time":0.0,"valid_pairs":0,"samples":0}

    mn, mx = min(cumulative), max(cumulative)
    peak_index = int(np.argmax(cumulative))
    peak_time = times[peak_index] if times and peak_index < len(times) else 0.0
    return {
        "zoom_ratio": mx / max(mn, 1e-6),
        "max_zoom": mx,
        "min_zoom": mn,
        "peak_time": peak_time,
        "valid_pairs": valid_pairs,
        "samples": len(frames),
    }

def analyze(path, payload):
    info = ffprobe(path)
    duration = float(info.get("format", {}).get("duration") or 0.0)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    content_mode = str(payload.get("content_mode") or "education").lower()

    issues, warnings = [], []
    if not (14.0 <= duration <= 16.3):
        issues.append({"code":"BAD_DURATION","detail":f"{duration:.2f}s"})
    if not (width > 0 and height > width and 0.50 <= width / height <= 0.66):
        issues.append({"code":"BAD_ASPECT","detail":f"{width}x{height}"})
    if audio is None:
        issues.append({"code":"NO_AUDIO","detail":"No audio stream"})

    zoom = global_zoom_metrics(path)
    if zoom["zoom_ratio"] > 1.45:
        issues.append({
            "code":"EXCESSIVE_CAMERA_ZOOM",
            "detail":f"Global camera scale changed {zoom['zoom_ratio']:.2f}x; peak around {zoom['peak_time']:.2f}s"
        })
    elif zoom["zoom_ratio"] > 1.25:
        warnings.append({"code":"CAMERA_ZOOM_WARNING","detail":f"Global camera scale changed {zoom['zoom_ratio']:.2f}x"})

    silences = []
    mean_db = max_db = None
    if audio is not None:
        silences = silence_intervals(path, duration)
        mean_db, max_db = volume_metrics(path)
        windows = [
            ("QUESTION",0.35,3.20,0.70),
            ("ANSWER",3.90,6.30,0.45),
            ("INVITE",8.80,12.30,0.25),
        ]
        if content_mode == "entertainment":
            windows = [
                ("OPENING",0.20,2.20,0.45),
                ("CUE",4.30,6.30,0.25),
                ("INVITE",8.70,11.30,0.20),
            ]
        for name, a, b, need in windows:
            coverage = nonsilent_coverage((a, min(b, duration)), silences)
            if coverage < need:
                issues.append({
                    "code":f"MISSING_{name}_AUDIO",
                    "detail":f"Only {coverage:.2f}s non-silent audio in {a:.1f}-{b:.1f}s"
                })
        if max_db is not None and max_db > -0.2:
            warnings.append({"code":"AUDIO_NEAR_CLIPPING","detail":f"Max {max_db:.1f} dB"})
        if mean_db is not None and mean_db < -30.0:
            issues.append({"code":"AUDIO_TOO_QUIET","detail":f"Mean {mean_db:.1f} dB"})

    return {
        "qa_pass": len(issues) == 0,
        "qa_status": "pass" if not issues else "fail",
        "issues": issues,
        "warnings": warnings,
        "metrics": {
            "duration": duration,
            "resolution": f"{width}x{height}",
            "zoom": zoom,
            "audio_mean_db": mean_db,
            "audio_max_db": max_db,
            "silence_intervals": silences,
        }
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload-b64", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payload = decode_payload(args.payload_b64)
    with tempfile.TemporaryDirectory(prefix="kp_raw_qa_") as td:
        video = Path(td) / "raw.mp4"
        download(payload.get("video_url"), video)
        result = analyze(video, payload)
    result.update({
        "short_id": payload.get("short_id",""),
        "video_url": payload.get("video_url",""),
        "category": payload.get("category",""),
        "lesson_key": payload.get("lesson_key",""),
        "title": payload.get("title",""),
        "content_mode": payload.get("content_mode","education"),
    })
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
