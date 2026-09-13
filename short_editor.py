#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# KP Kids Short Editor V4 Smart Kids Edit: smart captions, keyword highlight, one smooth punch-in

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

    # Main effect timings
    intro_start, intro_end = 0.0, 2.0
    topic_start, topic_end = 2.2, 5.0
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
        f"1+0.018*sin(PI*(t-{punch_start:.2f})/"
        f"{max(punch_end-punch_start,0.1):.3f}),1)"
    )

    caption_start = 2.15
    caption_end = min(4.7, max(3.2, duration - 4.5))
    keyword_start = 5.15
    keyword_end = min(6.35, max(5.75, duration - 3.5))

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
        f"x=(w-text_w)/2:y={hook_y+4}:enable='between(t,{intro_start},{intro_end})'[i2];"

        # smart short caption
        f"[i2]drawbox=x=95:y=1500:w=890:h=104:color=black@0.34:t=fill:"
        f"enable='between(t,{caption_start:.2f},{caption_end:.2f})'[c0];"
        f"[c0]drawtext=fontfile={FONT}:text='{esc(caption_text)}':fontcolor=white:fontsize=46:"
        "borderw=1:bordercolor=black@0.20:shadowx=2:shadowy=2:shadowcolor=black@0.45:"
        f"x=(w-text_w)/2:y=1528:enable='between(t,{caption_start:.2f},{caption_end:.2f})'[c1];"

        # highlighted keyword
        f"[c1]drawbox=x=270:y=250:w=540:h=104:color={theme['accent']}@0.86:t=fill:"
        f"enable='between(t,{keyword_start:.2f},{keyword_end:.2f})'[k0];"
        f"[k0]drawtext=fontfile={FONT}:text='{esc(keyword_text)}':fontcolor=black:fontsize=52:"
        "borderw=0:shadowx=1:shadowy=1:shadowcolor=white@0.25:"
        f"x=(w-text_w)/2:y=278:enable='between(t,{keyword_start:.2f},{keyword_end:.2f})'[k1];"

        # light decorative accents
        f"[k1]drawbox=x=118:y=240:w=34:h=34:color={theme['accent']}@0.72:t=fill:"
        f"enable='between(t,{accent_start},{accent_end})'[a0];"
        f"[a0]drawbox=x=930:y=286:w=20:h=20:color=white@0.65:t=fill:"
        f"enable='between(t,{accent_start},{accent_end})'[a1];"

        # progress bar
        f"[a1]drawbox=x=55:y={progress_y}:w=970:h=12:color=black@0.24:t=fill[p0];"
        f"[p0]drawbox=x=55:y={progress_y}:w='{progress_expr}':h=12:color={theme['accent']}@0.92:t=fill[p1];"

        # end card
        f"[p1]drawbox=x=145:y=116:w=790:h=138:color=black@0.34:t=fill:"
        f"enable='between(t,{end_start:.2f},{duration:.2f})'[e0];"
        f"[e0]drawbox=x=145:y=116:w=18:h=138:color={theme['accent']}@0.98:t=fill:"
        f"enable='between(t,{end_start:.2f},{duration:.2f})'[e1];"
        f"[e1]drawtext=fontfile={FONT}:text='{esc(end_text)}':fontcolor=white:fontsize=58:"
        "borderw=1:bordercolor=black@0.18:shadowx=3:shadowy=3:shadowcolor=black@0.50:"
        f"x=(w-text_w)/2:y=153:enable='between(t,{end_start:.2f},{duration:.2f})'[vout]"
    )

    # Audio polish: dialogue stays dominant, gentle leveling + limiter.
    ch1_delay = 3400
    ch2_delay = int(max(0, duration - 1.55) * 1000)
    audio_fade_out = max(0.0, duration - 0.22)

    af = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        "highpass=f=70,lowpass=f=15000,"
        "acompressor=threshold=-18dB:ratio=2.2:attack=12:release=180:makeup=1.4,"
        "alimiter=limit=0.96,"
        "afade=t=in:st=0:d=0.12,"
        f"afade=t=out:st={audio_fade_out:.3f}:d=0.22[a0];"
        "sine=frequency=880:sample_rate=48000:duration=0.07,volume=0.014,adelay="
        f"{ch1_delay}|{ch1_delay}[c1];"
        "sine=frequency=1175:sample_rate=48000:duration=0.10,volume=0.012,adelay="
        f"{ch2_delay}|{ch2_delay}[c2];"
        "[a0][c1][c2]amix=inputs=3:normalize=0:duration=first[aout]"
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
        download(source_url, src)
        result = edit_video(src, Path(args.output), payload)

    meta = dict(payload)
    meta["edit_style"] = result["style"]
    meta["edit_theme"] = result["theme"]
    meta["editor_version"] = "V4 Smart Kids Edit"
    Path("edit_result.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
