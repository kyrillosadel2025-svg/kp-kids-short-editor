#!/usr/bin/env python3
import argparse, base64, hashlib, json, math, os, re, shutil, subprocess, tempfile, time, urllib.request, urllib.error
from pathlib import Path
import cv2
import numpy as np

# Shared with the editor so QA can never approve an order the editor rejects.
from story_contract import (
    STORY_LABEL_ALIASES, STORY_CONTRACTS, DEFAULT_CONTRACT,
    REPAIR_MIN_CONFIDENCE, SEGMENT_MIN_SECONDS, SEGMENT_MAX_COUNT,
    SEGMENT_OVERLAP_EPSILON,
    normalize_story_label, resolve_story_contract, resolve_reveal_mode,
    contract_order_map, contract_rule_text, audit_story_order,
)

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


def _json_from_model_text(text):
    """Parse the model's JSON answer.

    V8 fix: the V7 patterns were raw strings containing a doubled backslash
    (r"^```(?:json)?\\s*"), so they searched for a LITERAL backslash instead of
    whitespace. Fenced or prose-wrapped replies therefore never parsed, and a
    perfectly good CORRECT_REVEAL verdict was downgraded to a hard
    SEMANTIC_VISION_ERROR -- which in turn invited an unnecessary regeneration.
    """
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    # Balanced-brace scan: tolerant of leading prose and trailing commentary.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
        start = text.find("{", start + 1)
    raise RuntimeError("Vision QA returned no JSON object")


def _kie_chat_output_text(obj):
    """Extract assistant text from KIE chat responses.

    KIE documents the OpenAI-compatible success shape with choices at the root,
    but some gateway/provider responses can arrive wrapped under data.  Accept
    both, and surface KIE's own code/msg/error instead of hiding it behind a
    KeyError so Telegram shows the real cause.
    """
    if not isinstance(obj, dict):
        raise RuntimeError(f"KIE returned non-object JSON: {type(obj).__name__}")

    candidates = [obj]
    data = obj.get("data")
    if isinstance(data, dict):
        candidates.append(data)

    # KIE Logs may expose the model's JSON result directly rather than an
    # OpenAI-style choices envelope. Preserve it as JSON text so the same
    # downstream parser can handle it.
    for root in candidates:
        if isinstance(root, dict) and (
            "reveal_assessment" in root
            or "status" in root
            or "early_reveal_classification" in root
        ):
            return json.dumps(root, ensure_ascii=False)

    for root in candidates:
        choices = root.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0] if isinstance(choices[0], dict) else {}
            message = first.get("message") if isinstance(first, dict) else None
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("text") is not None:
                                parts.append(str(item.get("text")))
                            elif item.get("content") is not None:
                                parts.append(str(item.get("content")))
                    text = "\n".join(x for x in parts if x).strip()
                    if text:
                        return text

            # Some compatible gateways expose text directly on the choice.
            if isinstance(first, dict) and first.get("text") is not None:
                return str(first.get("text")).strip()

    # Accept a few wrapper/result shapes if KIE changes its gateway envelope.
    for root in candidates:
        for key in ("content", "response", "result", "output", "text"):
            value = root.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                for subkey in ("content", "text", "response"):
                    sv = value.get(subkey)
                    if isinstance(sv, str) and sv.strip():
                        return sv.strip()

    # If this is an API/provider error wrapped in HTTP 200, expose it verbatim.
    code = obj.get("code")
    msg = obj.get("msg") or obj.get("message")
    err = obj.get("error")
    if isinstance(err, dict):
        err = err.get("message") or err.get("msg") or json.dumps(err, ensure_ascii=False)
    detail = {
        "code": code,
        "msg": msg,
        "error": err,
        "keys": sorted(obj.keys()),
    }
    if isinstance(data, dict):
        detail["data_keys"] = sorted(data.keys())
        if not msg:
            detail["data_msg"] = data.get("msg") or data.get("message") or data.get("error")
    raise RuntimeError("KIE non-chat response: " + json.dumps(detail, ensure_ascii=False)[:900])



def _seg_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def summarize_story_order(segments):
    """Chronological source order of the story beats, for telemetry."""
    rows = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        a = _seg_float(seg.get("start_sec", seg.get("start", seg.get("source_start"))))
        b = _seg_float(seg.get("end_sec", seg.get("end", seg.get("source_end"))))
        if a is None:
            continue
        rows.append({
            "label": normalize_story_label(seg.get("label") or seg.get("phase") or seg.get("type")),
            "source_start": round(a, 4),
            "source_end": round(b, 4) if b is not None else None,
        })
    rows.sort(key=lambda x: x["source_start"])
    return rows


def validate_repair_plan(plan, source_duration, fallback_confidence=None, contract=DEFAULT_CONTRACT):
    """Strictly validate a reorder plan before the editor is ever allowed to cut.

    Returns (ok, reason, segments, confidence). Every rejection is explicit so
    the block reason that reaches Telegram names the real defect.
    """
    if not isinstance(plan, dict) or not plan:
        return False, "no_repair_plan", [], 0.0

    action = str(plan.get("action") or "").strip().lower()
    if action not in {"reorder_segments", "reorder", "move_segments"}:
        return False, "plan_action_is_not_reorder_segments", [], 0.0

    conf = _seg_float(plan.get("confidence"))
    if conf is None:
        conf = _seg_float(fallback_confidence)
    if conf is None:
        return False, "repair_plan_has_no_confidence", [], 0.0
    conf = max(0.0, min(1.0, conf))
    if conf < REPAIR_MIN_CONFIDENCE:
        return False, f"repair_confidence_{conf:.2f}_below_{REPAIR_MIN_CONFIDENCE}", [], conf

    raw = plan.get("output_segments")
    if not isinstance(raw, list) or not (2 <= len(raw) <= SEGMENT_MAX_COUNT):
        return False, "invalid_segment_count", [], conf

    cleaned = []
    for i, seg in enumerate(raw):
        if not isinstance(seg, dict):
            return False, f"segment_{i}_not_object", [], conf
        label = normalize_story_label(seg.get("label") or seg.get("phase") or seg.get("type"))
        a = _seg_float(seg.get("start_sec", seg.get("start", seg.get("source_start"))))
        b = _seg_float(seg.get("end_sec", seg.get("end", seg.get("source_end"))))
        if a is None or b is None:
            return False, f"segment_{i}_missing_time", [], conf
        if a < -0.01 or b > source_duration + 0.05:
            return False, f"segment_{i}_out_of_source_range", [], conf
        a = max(0.0, min(a, source_duration))
        b = max(0.0, min(b, source_duration))
        if b - a < SEGMENT_MIN_SECONDS:
            return False, f"segment_{i}_shorter_than_{SEGMENT_MIN_SECONDS}s", [], conf
        order = _seg_float(seg.get("order", seg.get("output_order", i)))
        cleaned.append({
            "label": label,
            "source_start": round(a, 4),
            "source_end": round(b, 4),
            "order": i if order is None else order,
            "source_index": i,
        })

    cleaned.sort(key=lambda x: (x["order"], x["source_index"]))
    for position, seg in enumerate(cleaned):
        seg["output_order"] = position

    labels = [x["label"] for x in cleaned]
    if "QUESTION" not in labels:
        return False, "plan_has_no_question_segment", [], conf
    if "REVEAL" not in labels:
        return False, "plan_has_no_reveal_segment", [], conf
    if labels.index("QUESTION") >= labels.index("REVEAL"):
        return False, "plan_still_places_reveal_before_question", [], conf

    # Beat order must follow THIS episode's contract. A find-it episode puts the
    # child's turn BEFORE the reveal; a teaching episode puts it after. Enforcing
    # one hardcoded order is what let "point at it" land after the answer.
    order_map = contract_order_map(contract)
    known = [order_map[l] for l in labels if l in order_map]
    if any(b < a for a, b in zip(known, known[1:])):
        return False, f"plan_output_order_violates_{contract}_contract", [], conf

    # No overlapping source ranges: overlap means duplicated dialogue.
    by_source = sorted(cleaned, key=lambda x: x["source_start"])
    for prev, cur in zip(by_source, by_source[1:]):
        if cur["source_start"] < prev["source_end"] - SEGMENT_OVERLAP_EPSILON:
            return False, "plan_segments_overlap_in_source", [], conf

    kept = sum(x["source_end"] - x["source_start"] for x in cleaned)
    if kept > source_duration + 0.15:
        return False, "plan_duration_exceeds_source", [], conf
    if source_duration > 0 and kept < min(5.0, source_duration * 0.45):
        return False, "plan_discards_too_much_of_story", [], conf

    # Project output timestamps so downstream telemetry is exact, not guessed.
    t = 0.0
    for seg in cleaned:
        span = seg["source_end"] - seg["source_start"]
        seg["output_start"] = round(t, 4)
        seg["output_end"] = round(t + span, 4)
        t += span

    return True, "repair_plan_valid", cleaned, conf


def projected_event_times(segments):
    """Where question/reveal/child-turn land in the repaired timeline."""
    first = {}
    for seg in segments or []:
        first.setdefault(seg.get("label"), seg)

    def cue(label):
        seg = first.get(label)
        if not seg:
            return None
        span = seg["output_end"] - seg["output_start"]
        return round(seg["output_start"] + min(0.25, max(0.05, span * 0.15)), 4)

    return {
        "question_time": cue("QUESTION"),
        "reveal_time": cue("REVEAL"),
        "interaction_time": cue("CHILD_TURN"),
    }


def _normalize_kie_verdict(verdict):
    """Normalize KIE semantic story responses into one stable structure."""
    if not isinstance(verdict, dict):
        raise RuntimeError("KIE verdict is not a JSON object")

    for key in ("data", "result", "output"):
        value = verdict.get(key)
        if isinstance(value, dict) and (
            "reveal_assessment" in value or "status" in value
            or "early_reveal_classification" in value or "story_segments" in value
        ):
            verdict = value
            break

    assessment = verdict.get("reveal_assessment")
    if not isinstance(assessment, dict):
        assessment = verdict

    raw_class = str(
        assessment.get("status")
        or assessment.get("early_reveal_classification")
        or assessment.get("classification")
        or "UNCERTAIN"
    ).strip().upper().replace(" ", "_").replace("-", "_")

    if raw_class.startswith("PERSISTENT_EARLY_REVE") or ("PERSISTENT" in raw_class and "EARLY" in raw_class):
        status = "PERSISTENT_EARLY_REVEAL"
    elif raw_class.startswith("EARLY_REVEAL_MOVABLE") or raw_class.startswith("MOVABLE_EARLY_REVEAL"):
        status = "EARLY_REVEAL_MOVABLE"
    elif raw_class.startswith("CORRECT_REVEAL") or raw_class in {"PASS", "OK", "SAFE"}:
        status = "CORRECT_REVEAL"
    elif "EARLY" in raw_class and "REVEAL" in raw_class:
        status = "EARLY_REVEAL_MOVABLE"
    else:
        status = "UNCERTAIN"

    def fnum(*values):
        for value in values:
            if value in (None, ""):
                continue
            try:
                return float(value)
            except Exception:
                pass
        return None

    conf = fnum(assessment.get("confidence"), verdict.get("confidence"))
    if conf is not None:
        conf = max(0.0, min(1.0, conf))

    reveal_t = fnum(
        assessment.get("intended_reveal_timing_in_seconds"),
        assessment.get("estimated_reveal_time_sec"),
        assessment.get("reveal_time_sec"),
        verdict.get("estimated_reveal_time_sec"),
        verdict.get("reveal_time_sec"),
    )
    question_t = fnum(
        assessment.get("question_time_sec"),
        assessment.get("estimated_question_time_sec"),
        verdict.get("question_time_sec"),
    )
    interaction_t = fnum(
        assessment.get("interaction_time_sec"),
        assessment.get("child_turn_time_sec"),
        verdict.get("interaction_time_sec"),
    )
    first_t = fnum(
        assessment.get("first_answer_visible_time_sec"),
        assessment.get("first_visible_time_sec"),
        verdict.get("first_answer_visible_time_sec"),
    )

    story_segments = verdict.get("story_segments")
    if not isinstance(story_segments, list):
        story_segments = assessment.get("story_segments")
    if not isinstance(story_segments, list):
        story_segments = []

    repair_plan = verdict.get("repair_plan")
    if not isinstance(repair_plan, dict):
        repair_plan = assessment.get("repair_plan")
    if not isinstance(repair_plan, dict):
        repair_plan = {}

    details = (
        assessment.get("reason") or assessment.get("details")
        or assessment.get("explanation") or verdict.get("reason") or ""
    )

    return {
        "status": status,
        "confidence": conf,
        "answer_visible_before_reveal": bool(
            assessment.get("answer_visible_before_reveal",
                           status in {"EARLY_REVEAL_MOVABLE", "PERSISTENT_EARLY_REVEAL"})
        ),
        "first_answer_visible_time_sec": first_t if first_t is not None else -1,
        "estimated_reveal_time_sec": reveal_t if reveal_t is not None else -1,
        "question_time_sec": question_t,
        "interaction_time_sec": interaction_t,
        "persistent": bool(assessment.get("persistent", status == "PERSISTENT_EARLY_REVEAL")),
        "reason": str(details),
        "story_segments": story_segments,
        "repair_plan": repair_plan,
        "provider_shape": "reveal_assessment" if "reveal_assessment" in verdict else "direct",
    }

def semantic_story_qa(path, payload, duration, silences=None):
    """Semantic story QA using KIE Gemini multimodal chat.

    KIE documents Gemini 2.5 Flash's OpenAI-compatible chat endpoint as accepting
    media URLs through the image_url content shape, including video URLs.
    The same KIE API key used by the project is supplied through KIE_API_KEY.
    """
    answer = str(
        payload.get("expected_answer_visual")
        or payload.get("answer_line")
        or payload.get("answer")
        or ""
    ).strip()
    question = str(payload.get("question_line") or payload.get("question") or "").strip()
    reveal_line = str(payload.get("reveal_line") or payload.get("reveal") or payload.get("answer_line") or "").strip()
    reveal_time = payload.get("reveal_time")
    if reveal_time is None:
        reveal_time = payload.get("answer_time")
    if reveal_time is None:
        reveal_time = payload.get("reveal_at")

    if payload.get("semantic_story_qa", True) is False:
        return {
            "status": "SKIPPED",
            "confidence": 0.0,
            "reason": "semantic_story_qa_disabled",
            "blocking": False,
            "provider": "kie.ai",
        }
    if not answer:
        return {
            "status": "SKIPPED",
            "confidence": 0.0,
            "reason": "missing_expected_answer_visual",
            "blocking": False,
            "provider": "kie.ai",
        }

    api_key = os.environ.get("KIE_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "ERROR",
            "confidence": 0.0,
            "reason": "KIE_API_KEY_missing",
            "blocking": True,
            "provider": "kie.ai",
        }

    video_url = str(payload.get("video_url") or "").strip().lstrip("=")
    if not video_url.startswith(("http://", "https://")):
        return {
            "status": "ERROR",
            "confidence": 0.0,
            "reason": "semantic_video_url_not_public_http",
            "blocking": True,
            "provider": "kie.ai",
        }

    endpoint = os.environ.get(
        "KIE_VISION_ENDPOINT",
        "https://api.kie.ai/gemini-2.5-flash/v1/chat/completions",
    ).strip()
    model_name = os.environ.get("KIE_VISION_MODEL", "gemini-2.5-flash").strip()

    timing_hint = "not supplied; infer the intended answer/reveal moment from the video's spoken/visual story"
    if reveal_time not in (None, ""):
        timing_hint = f"approximately {reveal_time} seconds"

    contract, contract_reason = resolve_story_contract(payload)
    reveal_mode, reveal_mode_reason = resolve_reveal_mode(payload)
    if reveal_mode == "gesture":
        reveal_rule = (
            "This episode's answer object is SUPPOSED to be on screen the whole time "
            "(the child has to find it among other things). Its mere presence is NOT a spoiler.\n"
            "The reveal is the ACT of identifying it: pointing at it, touching or picking it up, "
            "leaning toward it, circling or highlighting it, looking straight at it and holding that look, "
            "or naming it out loud. Judge ONLY that act."
        )
    else:
        reveal_rule = (
            "The correct answer must NOT be visible, identifiable, readable, named on screen, reflected, "
            "silhouetted, held by a character, highlighted, or otherwise disclosed BEFORE the intended "
            "ANSWER/REVEAL moment."
        )

    prompt = f"""You are the semantic story editor/QA for a 15-second KP Kids preschool video.
Watch the FULL VIDEO chronologically and use BOTH the visible video and spoken dialogue when available.

Question: {question or '[not supplied]'}
Correct answer / expected visual answer: {answer}
Reveal line: {reveal_line or '[not supplied]'}
Intended reveal timing: {timing_hint}
Episode format: {payload.get('episode_format','')}
Start policy: {payload.get('start_policy','withhold-result')}
Dialogue timeline: {payload.get('dialogue_timeline','')}
Video duration: {duration:.2f}s

Reveal mode: {reveal_mode} ({reveal_mode_reason})
Story contract: {contract} ({contract_reason})
Required beat order for this episode: {contract_rule_text(contract)}

NON-NEGOTIABLE STORY RULE:
{reveal_rule}
The cutoff is the ANSWER/REVEAL moment, NOT the question moment.

SECOND, EQUALLY IMPORTANT RULE - THE CHILD'S TURN:
If this episode invites the child to answer ("your turn", "point at it", "say it with me"),
that invitation is only worth anything if it comes BEFORE the character answers.
An invitation placed after the answer is a real defect even when the reveal itself was on time.
Label that invitation CHILD_TURN and timestamp it accurately.

First classify the reveal:
- CORRECT_REVEAL: answer is withheld until the intended reveal.
- EARLY_REVEAL_MOVABLE: the answer appears early only in one or more isolated clean segments and the story can be repaired by cutting/reordering without duplicating dialogue.
- PERSISTENT_EARLY_REVEAL: the answer is visible from the opening, repeated across much of the pre-reveal story, or baked into the scene so editing cannot hide it.
- UNCERTAIN: not enough evidence.

Then identify story beats with timestamps whenever they are clear:
HOOK, QUESTION, THINK, REVEAL, REINFORCE, CHILD_TURN, PAYOFF.

Timestamp EVERY beat you can identify, even when the reveal itself is correct. The
editor rebuilds story order from these timestamps, so their accuracy matters more
than the repair_plan below.

If and ONLY IF status is EARLY_REVEAL_MOVABLE, create a conservative repair_plan:
- action must be "reorder_segments".
- confidence 0..1.
- output_segments must be the exact SOURCE ranges to keep, in the desired OUTPUT order.
- Every output segment needs: label, start_sec, end_sec, order.
- The output order must follow this episode's contract exactly: {contract_rule_text(contract)}.
- Never overlap source ranges.
- Preserve dialogue/audio with each segment.
- Do not invent missing footage.
- Do not repair a persistent spoiler.

Return ONLY a JSON object with this shape:
{{
  "status":"CORRECT_REVEAL|EARLY_REVEAL_MOVABLE|PERSISTENT_EARLY_REVEAL|UNCERTAIN",
  "confidence":0.0,
  "answer_visible_before_reveal":false,
  "first_answer_visible_time_sec":-1,
  "question_time_sec":-1,
  "estimated_reveal_time_sec":-1,
  "interaction_time_sec":-1,
  "persistent":false,
  "reason":"short factual explanation",
  "story_segments":[
    {{"label":"QUESTION","start_sec":1.2,"end_sec":2.8}}
  ],
  "repair_plan":{{
    "action":"keep|reorder_segments|block",
    "confidence":0.0,
    "reason":"...",
    "output_segments":[]
  }}
}}

Be conservative. If clean source boundaries are not trustworthy, do NOT create a reorder plan."""
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "kp_kids_visual_story_qa",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "CORRECT_REVEAL",
                            "EARLY_REVEAL_MOVABLE",
                            "PERSISTENT_EARLY_REVEAL",
                            "UNCERTAIN",
                        ],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "answer_visible_before_reveal": {"type": "boolean"},
                    "first_answer_visible_time_sec": {"type": "number"},
                    "estimated_reveal_time_sec": {"type": "number"},
                    "persistent": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": [
                    "status",
                    "confidence",
                    "answer_visible_before_reveal",
                    "first_answer_visible_time_sec",
                    "estimated_reveal_time_sec",
                    "persistent",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    }

    # Deliberately do not send response_format here. KIE documents it, but the
    # video+structured-output combination has returned non-standard gateway
    # envelopes in production. The prompt already requires JSON-only output,
    # and _json_from_model_text validates it locally.
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": video_url}},
                ],
            }
        ],
        "stream": False,
        "include_thoughts": False,
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "KP-Kids-RAW-QA/7.0",
        },
    )

    # Retry only transient KIE/provider/network failures.
    retry_delays = (0, 12, 30, 60)
    last_error = None
    raw_text = ""
    verdict = None
    for attempt, delay in enumerate(retry_delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw_text = resp.read().decode("utf-8", errors="replace")
                obj = json.loads(raw_text)

            if isinstance(obj, dict):
                code = obj.get("code")
                msg = str(obj.get("msg") or obj.get("message") or "")
                transient_200 = (
                    code in {429, 500, 502, 503, 504}
                    or any(x in msg.lower() for x in (
                        "network error", "try again later", "temporarily",
                        "timeout", "timed out", "busy", "rate limit"
                    ))
                )
                if transient_200 and attempt < len(retry_delays):
                    print(f"KIE Vision transient response on attempt {attempt}: code={code} msg={msg[:180]}")
                    last_error = f"KIE transient response code={code}: {msg}"
                    continue

            verdict = _normalize_kie_verdict(_json_from_model_text(_kie_chat_output_text(obj)))
            last_error = None
            break

        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            detail = re.sub(r"\s+", " ", detail).strip()[:600]
            last_error = f"KIE_vision_http_{e.code}: {detail or e.reason}"
            if e.code in {429, 500, 502, 503, 504} and attempt < len(retry_delays):
                print(f"KIE Vision HTTP transient error on attempt {attempt}: {last_error}")
                continue
            return {
                "status": "ERROR", "confidence": 0.0, "reason": last_error,
                "blocking": True, "retryable": e.code in {429, 500, 502, 503, 504},
                "provider": "kie.ai", "model": model_name, "attempts": attempt,
            }

        except urllib.error.URLError as e:
            last_error = f"KIE_vision_network_error: {e.reason}"
            if attempt < len(retry_delays):
                print(f"KIE Vision network transient error on attempt {attempt}: {last_error}")
                continue

        except Exception as e:
            raw_snippet = re.sub(r"\s+", " ", str(raw_text)).strip()[:900] if raw_text else ""
            reason = f"KIE_vision_api_or_parse_error: {type(e).__name__}: {e}"
            if raw_snippet:
                reason += f" | raw={raw_snippet}"
            low = reason.lower()
            last_error = reason[:1600]
            if any(x in low for x in ("network error", "try again later", "temporarily", "timeout", "timed out", "busy", "rate limit")) and attempt < len(retry_delays):
                print(f"KIE Vision transient provider/parse error on attempt {attempt}: {last_error[:500]}")
                continue
            return {
                "status": "ERROR", "confidence": 0.0, "reason": last_error,
                "blocking": True,
                "retryable": any(x in low for x in ("network error", "try again later", "temporar", "timeout", "busy", "rate limit")),
                "provider": "kie.ai", "model": model_name, "attempts": attempt,
            }

    if verdict is None:
        return {
            "status": "ERROR", "confidence": 0.0,
            "reason": (last_error or "KIE Vision failed after transient retries")[:1600],
            "blocking": True, "retryable": True, "provider": "kie.ai", "model": model_name,
            "attempts": len(retry_delays),
        }

    status = str(verdict.get("status") or "UNCERTAIN").upper()
    allowed = {
        "CORRECT_REVEAL",
        "EARLY_REVEAL_MOVABLE",
        "PERSISTENT_EARLY_REVEAL",
        "UNCERTAIN",
    }
    if status not in allowed:
        status = "UNCERTAIN"
    raw_conf = verdict.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(raw_conf))) if raw_conf is not None else None
    except Exception:
        conf = None

    repair_plan = verdict.get("repair_plan") if isinstance(verdict.get("repair_plan"), dict) else {}
    story_segments = verdict.get("story_segments") if isinstance(verdict.get("story_segments"), list) else []

    # A reorder is considered ONLY for an explicitly movable early reveal.
    # A plan attached to any other classification is ignored, never executed.
    repair_valid = False
    repair_reason = "repair_not_applicable_for_status"
    repair_segments = []
    repair_conf = 0.0
    if status == "EARLY_REVEAL_MOVABLE":
        repair_valid, repair_reason, repair_segments, repair_conf = validate_repair_plan(
            repair_plan, duration, fallback_confidence=conf, contract=contract
        )

    # ---- Beat-order audit ----------------------------------------------------
    # Reveal safety and beat order are INDEPENDENT failures. A video can hide its
    # answer perfectly (CORRECT_REVEAL) and still invite the child to answer after
    # the character already answered. That is what this audit catches, and it
    # builds its own plan from the model's beat timestamps rather than trusting
    # the model to produce an edit decision list.
    order_report = audit_story_order(story_segments, payload, duration, silences or [])
    if not repair_valid and order_report.get("status") == "ORDER_REPAIRABLE":
        ok, why, segs, oconf = validate_repair_plan(
            order_report["repair_plan"], duration,
            fallback_confidence=order_report.get("confidence"), contract=contract,
        )
        if ok:
            repair_valid, repair_reason, repair_segments, repair_conf = True, "beat_order_repair_planned_locally", segs, oconf
            repair_plan = order_report["repair_plan"]
        else:
            order_report["status"] = "ORDER_UNREPAIRABLE"
            order_report["reason"] = "locally_planned_reorder_failed_validation:" + why

    # Persistent reveal always blocks. UNCERTAIN always holds for review.
    # A movable reveal passes only with a machine-verified high-confidence plan.
    if status == "PERSISTENT_EARLY_REVEAL":
        blocking, block_reason = True, "persistent_spoiler_cannot_be_edited_away"
    elif status == "UNCERTAIN":
        blocking, block_reason = True, "story_status_uncertain_hold_for_human_review"
    elif status == "EARLY_REVEAL_MOVABLE" and not repair_valid:
        blocking, block_reason = True, f"early_reveal_without_safe_plan:{repair_reason}"
    elif order_report.get("status") == "ORDER_UNREPAIRABLE":
        # The child is invited to answer at the wrong point and the beats cannot be
        # cleanly separated. Shipping it wastes the interaction, so hold it.
        blocking, block_reason = True, "story_beat_order_violates_contract:" + str(order_report.get("reason"))
    elif (not repair_valid) and contract == "interaction_first" and order_report.get("status") not in {"ORDER_OK", "ORDER_REPAIRABLE"}:
        # This format's whole value depends on CHILD_TURN landing before REVEAL.
        # audit_story_order() needs >=2 labeled, timestamped beats to check that -
        # if the model's story_segments came back empty/too-sparse, the order was
        # NEVER actually verified. Silently shipping here is exactly the bug this
        # audit exists to catch (a QA pass that says nothing wrong, and nothing
        # right either), so hold for a QA retry instead of assuming it's fine.
        blocking, block_reason = True, "beat_order_not_verifiable:" + str(order_report.get("reason"))
    else:
        blocking, block_reason = False, ""

    original_order = summarize_story_order(story_segments)
    final_order = [
        {
            "label": s["label"],
            "source_start": s["source_start"],
            "source_end": s["source_end"],
            "output_start": s["output_start"],
            "output_end": s["output_end"],
            "output_order": s["output_order"],
        }
        for s in repair_segments
    ] if repair_valid else []
    projected = projected_event_times(repair_segments) if repair_valid else {
        "question_time": None, "reveal_time": None, "interaction_time": None
    }

    return {
        "status": status,
        "confidence": round(conf, 3) if conf is not None else None,
        "blocking": blocking,
        "block_reason": block_reason,
        "repair_required": bool(repair_valid),
        "repair_applied_by": "editor" if repair_valid else None,
        "repair_confidence": round(repair_conf, 3) if repair_valid else None,
        "repair_validation_reason": repair_reason,
        "repair_plan": {
            "action": "reorder_segments",
            # reveal_order: the answer was disclosed too early.
            # beat_order:  the reveal was fine but the beats are out of contract
            #              order (typically the child's turn placed after the answer).
            "repair_kind": repair_plan.get("repair_kind", "reveal_order"),
            "contract": contract,
            "confidence": round(repair_conf, 3),
            "output_segments": repair_segments,
        } if repair_valid else {},
        "story_contract": contract,
        "story_contract_reason": contract_reason,
        "story_contract_order": STORY_CONTRACTS[contract],
        "reveal_mode": reveal_mode,
        "reveal_mode_reason": reveal_mode_reason,
        "beat_order_audit": order_report,
        "story_segments": story_segments,
        "original_story_order": [x["label"] for x in original_order],
        "original_story_segments": original_order,
        "final_story_order": [x["label"] for x in final_order],
        "final_story_segments": final_order,
        "projected_times_after_repair": projected,
        "answer_visible_before_reveal": bool(verdict.get("answer_visible_before_reveal", False)),
        "first_answer_visible_time": float(verdict.get("first_answer_visible_time_sec", -1) if verdict.get("first_answer_visible_time_sec") not in (None, "") else -1),
        "question_time_sec": verdict.get("question_time_sec"),
        "estimated_reveal_time": float(verdict.get("estimated_reveal_time_sec", -1) if verdict.get("estimated_reveal_time_sec") not in (None, "") else -1),
        "interaction_time_sec": verdict.get("interaction_time_sec"),
        "persistent": bool(verdict.get("persistent", status == "PERSISTENT_EARLY_REVEAL")),
        "reason": str(verdict.get("reason") or "")[:900],
        "provider": "kie.ai",
        "model": model_name,
        "endpoint": endpoint,
        "attempts": attempt,
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

    semantic = semantic_story_qa(path, payload, duration, silences)
    sem_status = str(semantic.get("status") or "UNCERTAIN").upper()
    technical_hold = False
    if sem_status == "ERROR":
        technical_hold = bool(semantic.get("retryable", True))
        code = "KIE_VISION_TEMPORARY_FAILURE" if technical_hold else "SEMANTIC_VISION_ERROR"
        issues.append({"code":code,"detail":semantic.get("reason","Semantic Vision error")})
    elif sem_status == "PERSISTENT_EARLY_REVEAL":
        sem_conf = semantic.get("confidence")
        conf_suffix = f" (confidence={sem_conf:.2f})" if isinstance(sem_conf, (int, float)) else ""
        issues.append({"code":"PERSISTENT_EARLY_REVEAL","detail":f"{semantic.get('reason','')}{conf_suffix}"})
    elif sem_status == "EARLY_REVEAL_MOVABLE":
        if semantic.get("repair_required") and semantic.get("repair_plan"):
            warnings.append({
                "code":"EARLY_REVEAL_REPAIRABLE",
                "detail":(
                    f"Reorder plan verified at confidence={semantic.get('repair_confidence')}; "
                    f"order {semantic.get('original_story_order')} -> {semantic.get('final_story_order')}. "
                    "Editor must repair before publish."
                )
            })
        else:
            issues.append({
                "code":"EARLY_VISUAL_REVEAL",
                "detail":f"Early reveal with no safe plan: {semantic.get('repair_validation_reason','unknown')}"
            })
    elif sem_status == "UNCERTAIN":
        # Spec: UNCERTAIN is a hold for human review, never a speculative cut.
        issues.append({
            "code":"STORY_STATUS_UNCERTAIN",
            "detail":semantic.get("reason","Story order could not be established with confidence")
        })
    elif sem_status == "SKIPPED":
        issues.append({
            "code":"SEMANTIC_VISION_NOT_RUN",
            "detail":semantic.get("reason","Semantic story QA did not run")
        })

    # Beat order is a separate axis from reveal safety: CORRECT_REVEAL can still
    # invite the child to answer after the answer has already been given.
    order_audit = semantic.get("beat_order_audit") or {}
    order_status = str(order_audit.get("status") or "").upper()
    if order_status == "ORDER_REPAIRABLE" and semantic.get("repair_required"):
        warnings.append({
            "code":"STORY_ORDER_REPAIRABLE",
            "detail":(
                f"Beat order breaks the {order_audit.get('contract')} contract "
                f"({order_audit.get('source_order')} -> {order_audit.get('target_order')}); "
                f"editor will reorder at confidence={order_audit.get('confidence')}."
            )
        })
    elif order_status == "ORDER_UNREPAIRABLE":
        issues.append({
            "code":"STORY_ORDER_UNREPAIRABLE",
            "detail":(
                f"Beat order breaks the {order_audit.get('contract')} contract and cannot be "
                f"cleanly reordered: {order_audit.get('reason')}. "
                f"Violations: {order_audit.get('violations')}"
            )
        })
    elif str(order_audit.get("reason") or "").startswith("required_beat_not_identified"):
        # Vision did not label a beat the contract depends on (CHILD_TURN, most
        # often). Two beats it DID label can still look correctly ordered
        # relative to each other, so this is NOT the same thing as
        # ORDER_UNREPAIRABLE above - it must be caught separately or it ships
        # silently as a false ORDER_OK.
        missing = str(order_audit.get("reason") or "").split(":", 1)[-1]
        issues.append({
            "code":"STORY_REQUIRED_BEAT_NOT_IDENTIFIED",
            "detail":(
                f"Vision did not identify required beat(s) [{missing}] for the "
                f"{order_audit.get('contract')} contract, so beat order cannot be verified. "
                "Holding for review rather than shipping an unverified story order."
            )
        })

    qa_pass = len(issues) == 0
    if technical_hold:
        qa_status = "technical_hold"
    elif not qa_pass:
        qa_status = "hold_for_review" if sem_status == "UNCERTAIN" else "fail"
    elif semantic.get("repair_required"):
        qa_status = "pass_repairable"
    else:
        qa_status = "pass"
    return {
        "qa_pass": qa_pass,
        "qa_status": qa_status,
        "repair_required": bool(semantic.get("repair_required")),
        "story_repair_plan": semantic.get("repair_plan") if isinstance(semantic.get("repair_plan"), dict) else {},
        "story_segments": semantic.get("story_segments") if isinstance(semantic.get("story_segments"), list) else [],
        "semantic_story": semantic,
        "story_telemetry": {
            "status": sem_status,
            "confidence": semantic.get("confidence"),
            "repair_required": bool(semantic.get("repair_required")),
            "repair_confidence": semantic.get("repair_confidence"),
            "story_contract": semantic.get("story_contract"),
            "reveal_mode": semantic.get("reveal_mode"),
            "beat_order_status": order_audit.get("status"),
            "beat_order_violations": order_audit.get("violations", []),
            "original_story_order": semantic.get("original_story_order", []),
            "final_story_order": semantic.get("final_story_order", []),
            "original_story_segments": semantic.get("original_story_segments", []),
            "final_story_segments": semantic.get("final_story_segments", []),
            "projected_times_after_repair": semantic.get("projected_times_after_repair", {}),
            "block_reason": semantic.get("block_reason", ""),
            "validation_reason": semantic.get("repair_validation_reason", ""),
        },
        "issues": issues,
        "warnings": warnings,
        "metrics": {
            "duration": duration,
            "resolution": f"{width}x{height}",
            "zoom": zoom,
            "audio_mean_db": mean_db,
            "audio_max_db": max_db,
            "silence_intervals": silences,
            "semantic_story": semantic,
        }
    }

def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload-b64", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    payload = decode_payload(args.payload_b64)

    # CREDIT SAFETY: QA never generates. It only inspects a RAW that already
    # exists. A retry must point at the same video_url, and the fingerprint
    # below lets n8n prove the bytes were identical rather than trusting it.
    attempt = int(payload.get("qa_attempt") or 1)
    expected_sha = str(payload.get("raw_sha256") or "").strip().lower()

    with tempfile.TemporaryDirectory(prefix="kp_raw_qa_") as td:
        video = Path(td) / "raw.mp4"
        download(payload.get("video_url"), video)
        raw_sha = sha256_file(video)
        raw_bytes = Path(video).stat().st_size
        result = analyze(video, payload)

    raw_mismatch = bool(expected_sha and expected_sha != raw_sha)
    if raw_mismatch:
        result["qa_pass"] = False
        result["qa_status"] = "technical_hold"
        result.setdefault("issues", []).append({
            "code": "RAW_IDENTITY_MISMATCH",
            "detail": f"Retry fetched different bytes (expected {expected_sha[:12]}, got {raw_sha[:12]}). "
                      "Holding instead of re-judging a different RAW.",
        })

    result.update({
        "short_id": payload.get("short_id",""),
        "video_url": payload.get("video_url",""),
        "category": payload.get("category",""),
        "lesson_key": payload.get("lesson_key",""),
        "title": payload.get("title",""),
        "content_mode": payload.get("content_mode","education"),
        "qa_attempt": attempt,
        "raw_sha256": raw_sha,
        "raw_bytes": raw_bytes,
        "raw_reused": bool(expected_sha) and not raw_mismatch,
        "raw_identity_verified": bool(expected_sha) and not raw_mismatch,
        "generation_triggered": False,
        "qa_generation_allowed": False,
    })
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
