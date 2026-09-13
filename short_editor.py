#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import hashlib
import json
import subprocess
import tempfile
import urllib.request
from pathlib import Path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

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

def edit_video(src, out, payload):
    short_id = payload.get("short_id", "")
    category = str(payload.get("category", "")).lower()
    topic = str(payload.get("topic", "") or payload.get("title", "KP Kids"))
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

    # Main effect timings
    intro_start, intro_end = 0.0, 2.0
    topic_start, topic_end = 2.2, 5.0
    accent_start, accent_end = 5.0, 6.0
    end_start = max(0.0, duration - 1.9)

    # Progress bar width expression based on time
    progress_expr = f"(970*min(t/{max(duration,0.1):.3f},1))"

    vf = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=30,eq=brightness=-0.03:saturation=1.05[bg2];"
        "[fg]scale=1000:1778:force_original_aspect_ratio=decrease[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2[base];"
        f"[base]zoompan=z='{zoom}':x='{x}':y='{y}':d=1:s=1080x1920:fps=24[z0];"
        # soft frame / clean border
        "[z0]drawbox=x=40:y=90:w=1000:h=1780:color=white@0.16:t=4[z1];"
        # progress rail
        f"[z1]drawbox=x=55:y={progress_y}:w=970:h=18:color=black@0.25:t=fill[p0];"
        f"[p0]drawbox=x=55:y={progress_y}:w='{progress_expr}':h=18:color={theme['accent']}@0.95:t=fill[p1];"
        # intro label box + accent
        f"[p1]drawbox=x=70:y={hook_y-26}:w=880:h=118:color={theme['box']}@0.82:t=fill[i0];"
        f"[i0]drawbox=x=70:y={hook_y-26}:w=18:h=118:color={theme['accent']}@0.98:t=fill[i1];"
        f"[i1]drawtext=fontfile={FONT}:text='{esc(hook)}':fontcolor=white:fontsize=54:"
        f"borderw=2:bordercolor=black@0.20:shadowx=2:shadowy=2:shadowcolor=black@0.45:"
        f"x=(w-text_w)/2:y={hook_y}:enable='between(t,{intro_start},{intro_end})'[i2];"
        # topic card
        f"[i2]drawbox=x=70:y={topic_y-22}:w=940:h=112:color=black@0.30:t=fill[t0];"
        f"[t0]drawbox=x=70:y={topic_y-22}:w=12:h=112:color={theme['accent']}@0.98:t=fill[t1];"
        f"[t1]drawtext=fontfile={FONT}:text='{esc(topic_text)}':fontcolor=white:fontsize=50:"
        f"borderw=2:bordercolor=black@0.18:shadowx=2:shadowy=2:shadowcolor=black@0.50:"
        f"x=(w-text_w)/2:y={topic_y}:enable='between(t,{topic_start},{topic_end})'[t2];"
        # simple decorative accent burst (box pair)
        f"[t2]drawbox=x=120:y=240:w=48:h=48:color={theme['accent']}@0.92:t=fill:enable='between(t,{accent_start},{accent_end})'[a0];"
        f"[a0]drawbox=x=920:y=280:w=28:h=28:color=white@0.85:t=fill:enable='between(t,{accent_start},{accent_end})'[a1];"
        # end card
        f"[a1]drawbox=x=160:y=120:w=760:h=128:color={theme['end']}@0.84:t=fill:enable='between(t,{end_start:.2f},{duration:.2f})'[e0];"
        f"[e0]drawbox=x=160:y=120:w=20:h=128:color={theme['accent']}@0.98:t=fill:enable='between(t,{end_start:.2f},{duration:.2f})'[e1];"
        f"[e1]drawtext=fontfile={FONT}:text='{esc(end_text)}':fontcolor=white:fontsize=60:"
        "borderw=2:bordercolor=black@0.20:shadowx=3:shadowy=3:shadowcolor=black@0.50:"
        f"x=(w-text_w)/2:y=152:enable='between(t,{end_start:.2f},{duration:.2f})'[vout]"
    )

    # Keep original dialogue dominant. Add tiny low-volume sparkle cues.
    ch1_delay = 3400
    ch2_delay = int(max(0, duration - 1.55) * 1000)
    af = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=1.0[a0];"
        "sine=frequency=880:sample_rate=48000:duration=0.08,volume=0.020,adelay="
        f"{ch1_delay}|{ch1_delay}[c1];"
        "sine=frequency=1175:sample_rate=48000:duration=0.12,volume=0.016,adelay="
        f"{ch2_delay}|{ch2_delay}[c2];"
        "[a0][c1][c2]amix=inputs=3:normalize=0:duration=first[aout]"
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
        download(source_url, src)
        result = edit_video(src, Path(args.output), payload)

    meta = dict(payload)
    meta["edit_style"] = result["style"]
    meta["edit_theme"] = result["theme"]
    Path("edit_result.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
