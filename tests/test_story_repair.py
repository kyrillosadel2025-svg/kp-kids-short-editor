#!/usr/bin/env python3
"""KP Kids story-repair acceptance tests.

Run:  python3 tests/test_story_repair.py

Covers the delivery checklist:
  1  python syntax compile (editor + QC)
  2  YAML / JSON syntax
  3  FFmpeg multi-segment reorder WITH audio
  4  persistent-spoiler test must block
  5  repairable early-reveal test must reorder QUESTION before REVEAL
  6  UNCERTAIN must hold, never cut
  7  no paid image/video generation from QA or QA retry
  8  retry QA reuses the exact same RAW
  9  plan validation rejects overlap / reveal-first / low confidence / out-of-range
 10  fenced-JSON parsing regression (the V7 bug that faked SEMANTIC_VISION_ERROR)
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EDITOR = ROOT / "short_editor_V14_story_repair.py"
QC = ROOT / "raw_video_qc_V8_story_repair.py"
WORKFLOW = ROOT / "qa-raw-short_V8_story_repair.yml"

PASSED, FAILED = [], []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append((name, detail))
        print(f"  FAIL  {name}  {detail}")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------
# 1. Syntax
# --------------------------------------------------------------------------
def test_compile():
    print("\n[1] Python syntax compile")
    for path in (EDITOR, QC):
        r = subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                           capture_output=True, text=True)
        check(f"compiles: {path.name}", r.returncode == 0, r.stderr.strip()[:300])


# --------------------------------------------------------------------------
# 2. YAML / JSON
# --------------------------------------------------------------------------
def test_yaml():
    print("\n[2] Workflow YAML / JSON syntax")
    try:
        import yaml
    except ImportError:
        check("pyyaml available", False, "pip install pyyaml")
        return
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    check("workflow parses as YAML", isinstance(data, dict))
    # 'on' is parsed by YAML 1.1 as boolean True
    trigger = data.get("on", data.get(True))
    check("workflow has workflow_dispatch trigger",
          isinstance(trigger, dict) and "workflow_dispatch" in trigger)

    steps = data["jobs"]["qa"]["steps"]
    names = [s.get("name") for s in steps]
    check("has credit safety guard step", "Credit safety guard" in names, str(names))
    check("callback runs on always()",
          any(s.get("name") == "Callback n8n" and s.get("if") == "always()" for s in steps))
    check("QA step tolerates failure so callback still fires",
          any(s.get("id") == "qa" and s.get("continue-on-error") is True for s in steps))

    # Every embedded python heredoc in the workflow must itself be valid python.
    for step in steps:
        body = step.get("run") or ""
        if "<<'PY'" in body:
            code = body.split("<<'PY'", 1)[1].rsplit("PY", 1)[0]
            code = "\n".join(line[10:] if line.startswith(" " * 10) else line
                             for line in code.splitlines())
            try:
                compile(code, f"{step.get('name')}::heredoc", "exec")
                ok, err = True, ""
            except SyntaxError as e:
                ok, err = False, str(e)
            check(f"embedded python valid: {step.get('name')}", ok, err)


# --------------------------------------------------------------------------
# Fixture: build a synthetic 15s RAW whose beats are visually distinguishable.
# --------------------------------------------------------------------------
def build_raw(path, order):
    """order: list of (label, colour, seconds, tone_hz). Rendered with audio."""
    with tempfile.TemporaryDirectory() as td:
        parts = []
        for i, (label, colour, secs, hz) in enumerate(order):
            part = Path(td) / f"p{i}.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", f"color=c={colour}:s=360x640:d={secs}:r=24",
                "-f", "lavfi", "-i", f"sine=frequency={hz}:duration={secs}:sample_rate=48000",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                "-shortest", str(part),
            ], check=True, capture_output=True)
            parts.append(part)
        listing = Path(td) / "list.txt"
        listing.write_text("".join(f"file '{p}'\n" for p in parts), encoding="utf-8")
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy", str(path),
        ], check=True, capture_output=True)
    return path


def probe(path):
    r = subprocess.run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", str(path)
    ], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    streams = d.get("streams", [])
    return {
        "duration": float(d["format"]["duration"]),
        "has_audio": any(s["codec_type"] == "audio" for s in streams),
        "has_video": any(s["codec_type"] == "video" for s in streams),
    }


def mean_hue(path, at):
    """Sample one frame and return its dominant channel, to prove ordering."""
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "f.png"
        subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=8:8", str(png)
        ], check=True, capture_output=True)
        r = subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(png), "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-"
        ], capture_output=True, check=True)
        raw = r.stdout
        n = len(raw) // 3
        rr = sum(raw[0::3]) / n
        gg = sum(raw[1::3]) / n
        bb = sum(raw[2::3]) / n
        return max((("red", rr), ("green", gg), ("blue", bb)), key=lambda x: x[1])[0]


# --------------------------------------------------------------------------
# 3 + 5. Multi-segment reorder with audio, QUESTION before REVEAL
# --------------------------------------------------------------------------
def test_reorder(editor, workdir):
    print("\n[3/5] FFmpeg multi-segment reorder with audio (repairable early reveal)")
    # Broken story: the REVEAL (red) is generated BEFORE the QUESTION (green).
    raw = build_raw(workdir / "broken.mp4", [
        ("HOOK",     "blue",  3.0, 300),
        ("REVEAL",   "red",   4.0, 700),
        ("QUESTION", "green", 4.0, 500),
        ("PAYOFF",   "white", 4.0, 900),
    ])
    src = probe(raw)
    check("fixture RAW has audio", src["has_audio"])

    payload = {
        "qa_semantic_story": {
            "status": "EARLY_REVEAL_MOVABLE",
            "confidence": 0.91,
            "reason": "reveal precedes question",
        },
        "repair_required": True,
        "story_repair_plan": {
            "action": "reorder_segments",
            "confidence": 0.91,
            "output_segments": [
                {"label": "HOOK",     "start_sec": 0.0,  "end_sec": 3.0,  "order": 0},
                {"label": "QUESTION", "start_sec": 7.0,  "end_sec": 11.0, "order": 1},
                {"label": "REVEAL",   "start_sec": 3.0,  "end_sec": 7.0,  "order": 2},
                {"label": "PAYOFF",   "start_sec": 11.0, "end_sec": 15.0, "order": 3},
            ],
        },
    }

    report = editor.validate_semantic_repair_plan(payload, src["duration"])
    check("plan validates", report["valid"], report["reason"])
    if not report["valid"]:
        return

    out = workdir / "repaired.mp4"
    result = editor.apply_semantic_story_repair(raw, out, report)
    check("reorder applied", result["applied"], result.get("reason", ""))

    fixed = probe(out)
    check("repaired file keeps its audio", fixed["has_audio"])
    check("repaired file keeps its video", fixed["has_video"])
    check("repaired duration preserved",
          abs(fixed["duration"] - src["duration"]) < 0.4,
          f"{fixed['duration']:.2f} vs {src['duration']:.2f}")

    labels = [s["label"] for s in result["segments"]]
    check("QUESTION precedes REVEAL in plan order",
          labels.index("QUESTION") < labels.index("REVEAL"), str(labels))

    # Prove it on pixels, not just on the plan.
    q_colour = mean_hue(out, 5.0)    # QUESTION window 3.0-7.0
    r_colour = mean_hue(out, 9.0)    # REVEAL window   7.0-11.0
    check("pixel check: question segment is green at 5s", q_colour == "green", q_colour)
    check("pixel check: reveal segment is red at 9s", r_colour == "red", r_colour)

    # No duplicated dialogue: total kept audio must not exceed the source.
    total = sum(s["source_end"] - s["source_start"] for s in result["segments"])
    check("no duplicated source material", total <= src["duration"] + 0.15,
          f"kept {total:.2f}s of {src['duration']:.2f}s")

    # Times recomputed after the reorder.
    payload2 = editor.apply_semantic_repair_time_metadata(payload, result)
    qt, rt = payload2.get("question_time"), payload2.get("reveal_time")
    check("question_time recomputed", qt is not None)
    check("reveal_time recomputed", rt is not None)
    check("recomputed reveal is after recomputed question", qt is not None and rt > qt,
          f"q={qt} r={rt}")


# --------------------------------------------------------------------------
# 4. Persistent spoiler must block
# --------------------------------------------------------------------------
def test_persistent_blocks(editor, workdir):
    print("\n[4] Persistent spoiler must block")
    payload = {
        "qa_semantic_story": {
            "status": "PERSISTENT_EARLY_REVEAL",
            "confidence": 0.95,
            "reason": "answer on screen from frame 1",
        },
        "repair_required": True,
        "story_repair_plan": {
            "action": "reorder_segments",
            "confidence": 0.97,
            "output_segments": [
                {"label": "QUESTION", "start_sec": 0.0, "end_sec": 5.0, "order": 0},
                {"label": "REVEAL",   "start_sec": 5.0, "end_sec": 10.0, "order": 1},
            ],
        },
    }
    report = editor.validate_semantic_repair_plan(payload, 15.0)
    check("persistent spoiler is never repairable", not report["valid"], report["reason"])
    check("rejection names persistence",
          "persistent" in report["reason"], report["reason"])

    raw = build_raw(workdir / "persistent.mp4", [("ALL", "red", 15.0, 440)])
    out = workdir / "persistent_out.mp4"
    try:
        editor.edit_video(raw, out, payload)
        blocked, code = False, ""
    except editor.StoryBlock as e:
        blocked, code = True, e.code
    except Exception as e:
        blocked, code = False, f"wrong exception {type(e).__name__}: {e}"
    check("edit_video blocks a persistent spoiler", blocked, code)
    check("block code is PERSISTENT_EARLY_REVEAL", code == "PERSISTENT_EARLY_REVEAL", code)
    check("no output file was produced", not out.exists())


# --------------------------------------------------------------------------
# 6. UNCERTAIN must hold, never cut
# --------------------------------------------------------------------------
def test_uncertain_holds(editor, workdir):
    print("\n[6] UNCERTAIN must hold for review")
    payload = {
        "qa_semantic_story": {"status": "UNCERTAIN", "confidence": 0.4,
                              "reason": "cannot see the object clearly"},
        "story_repair_plan": {
            "action": "reorder_segments", "confidence": 0.95,
            "output_segments": [
                {"label": "QUESTION", "start_sec": 0.0, "end_sec": 5.0, "order": 0},
                {"label": "REVEAL",   "start_sec": 5.0, "end_sec": 10.0, "order": 1},
            ],
        },
    }
    report = editor.validate_semantic_repair_plan(payload, 15.0)
    check("UNCERTAIN never yields a valid repair", not report["valid"], report["reason"])

    raw = build_raw(workdir / "uncertain.mp4", [("A", "blue", 15.0, 440)])
    out = workdir / "uncertain_out.mp4"
    try:
        editor.edit_video(raw, out, payload)
        blocked, code = False, ""
    except editor.StoryBlock as e:
        blocked, code = True, e.code
    except Exception as e:
        blocked, code = False, f"wrong exception {type(e).__name__}: {e}"
    check("edit_video holds on UNCERTAIN", blocked, code)
    check("block code is STORY_STATUS_UNCERTAIN", code == "STORY_STATUS_UNCERTAIN", code)
    check("UNCERTAIN is flagged for human review, not regeneration",
          editor.StoryBlock("STORY_STATUS_UNCERTAIN", "x").to_dict()["human_review_required"] is True)

    # Vision ERROR is a technical hold, also not a regeneration trigger.
    err_payload = {"qa_semantic_story": {"status": "ERROR", "reason": "kie timeout"}}
    try:
        editor.edit_video(raw, workdir / "err_out.mp4", err_payload)
        code = ""
    except editor.StoryBlock as e:
        code = e.code
    check("vision ERROR becomes a technical hold", code == "SEMANTIC_QA_ERROR", code)
    check("vision ERROR does not request regeneration",
          editor.StoryBlock("SEMANTIC_QA_ERROR", "x").to_dict()["regeneration_required"] is False)


# --------------------------------------------------------------------------
# 9. Plan validation rejects unsafe plans
# --------------------------------------------------------------------------
def test_plan_rejections(editor, qc):
    print("\n[9] Unsafe plans are rejected")

    def plan(segments, conf=0.9, status="EARLY_REVEAL_MOVABLE"):
        return {
            "qa_semantic_story": {"status": status, "confidence": conf},
            "story_repair_plan": {"action": "reorder_segments",
                                  "confidence": conf, "output_segments": segments},
        }

    cases = [
        ("overlapping source ranges", plan([
            {"label": "QUESTION", "start_sec": 0.0, "end_sec": 6.0, "order": 0},
            {"label": "REVEAL",   "start_sec": 4.0, "end_sec": 10.0, "order": 1},
        ]), "overlap"),
        ("reveal still before question", plan([
            {"label": "REVEAL",   "start_sec": 0.0, "end_sec": 5.0, "order": 0},
            {"label": "QUESTION", "start_sec": 5.0, "end_sec": 10.0, "order": 1},
        ]), "reveal_before_question"),
        ("confidence below 0.82", plan([
            {"label": "QUESTION", "start_sec": 0.0, "end_sec": 5.0, "order": 0},
            {"label": "REVEAL",   "start_sec": 5.0, "end_sec": 10.0, "order": 1},
        ], conf=0.70), "confidence"),
        ("segment beyond source duration", plan([
            {"label": "QUESTION", "start_sec": 0.0,  "end_sec": 5.0,  "order": 0},
            {"label": "REVEAL",   "start_sec": 12.0, "end_sec": 40.0, "order": 1},
        ]), "outside_source"),
        ("discards most of the story", plan([
            {"label": "QUESTION", "start_sec": 0.0, "end_sec": 0.6, "order": 0},
            {"label": "REVEAL",   "start_sec": 1.0, "end_sec": 1.6, "order": 1},
        ]), "discards"),
        ("no reveal segment", plan([
            {"label": "HOOK",     "start_sec": 0.0, "end_sec": 5.0, "order": 0},
            {"label": "QUESTION", "start_sec": 5.0, "end_sec": 12.0, "order": 1},
        ]), "reveal"),
        ("plan attached to CORRECT_REVEAL is ignored", plan([
            {"label": "QUESTION", "start_sec": 0.0, "end_sec": 5.0, "order": 0},
            {"label": "REVEAL",   "start_sec": 5.0, "end_sec": 12.0, "order": 1},
        ], status="CORRECT_REVEAL"), "no_repair_needed"),
    ]
    for name, payload, _hint in cases:
        report = editor.validate_semantic_repair_plan(payload, 15.0)
        check(f"editor rejects: {name}", not report["valid"], report["reason"])

    # The QC-side validator must agree with the editor.
    ok, reason, _segs, _c = qc.validate_repair_plan({
        "action": "reorder_segments", "confidence": 0.9,
        "output_segments": [
            {"label": "REVEAL",   "start_sec": 0.0, "end_sec": 5.0, "order": 0},
            {"label": "QUESTION", "start_sec": 5.0, "end_sec": 12.0, "order": 1},
        ],
    }, 15.0)
    check("QC rejects reveal-before-question", not ok, reason)

    ok, reason, segs, conf = qc.validate_repair_plan({
        "action": "reorder_segments", "confidence": 0.9,
        "output_segments": [
            {"label": "HOOK",     "start_sec": 0.0,  "end_sec": 3.0,  "order": 0},
            {"label": "QUESTION", "start_sec": 7.0,  "end_sec": 11.0, "order": 1},
            {"label": "REVEAL",   "start_sec": 3.0,  "end_sec": 7.0,  "order": 2},
            {"label": "PAYOFF",   "start_sec": 11.0, "end_sec": 15.0, "order": 3},
        ],
    }, 15.0)
    check("QC accepts the safe reorder plan", ok, reason)
    if ok:
        check("QC projects output timestamps",
              all("output_start" in s and "output_end" in s for s in segs))
        times = qc.projected_event_times(segs)
        check("QC projects reveal after question",
              times["reveal_time"] > times["question_time"], str(times))


# --------------------------------------------------------------------------
# 7 + 8. Credit safety
# --------------------------------------------------------------------------
def test_credit_safety(qc):
    print("\n[7/8] Credit safety: QA never generates, retry reuses the same RAW")
    qc_src = QC.read_text(encoding="utf-8")
    editor_src = EDITOR.read_text(encoding="utf-8")
    wf_src = WORKFLOW.read_text(encoding="utf-8")

    generation_markers = [
        "nano-banana", "nano_banana", "createTask", "generateImage",
        "grok-imagine", "image-to-image", "/v1/images", "text2video",
    ]
    for marker in generation_markers:
        check(f"QC never calls generation API: {marker!r}",
              marker not in qc_src)
        check(f"editor never calls generation API: {marker!r}",
              marker not in editor_src)

    check("QC declares generation_triggered False", '"generation_triggered": False' in qc_src)
    check("editor declares generation_triggered False", '"generation_triggered"] = False' in editor_src
          or 'story_telemetry["generation_triggered"] = False' in editor_src)
    check("workflow guards against generation keys", "Generation-triggering keys" in wf_src)
    check("workflow QA failure is a technical_hold, not a fail",
          '"qa_status": "technical_hold"' in wf_src)

    # RAW identity: the same bytes must hash the same; different bytes must not.
    with tempfile.TemporaryDirectory() as td:
        a = Path(td) / "a.bin"
        b = Path(td) / "b.bin"
        a.write_bytes(b"identical-raw-bytes" * 5000)
        shutil.copyfile(a, b)
        check("same RAW yields same fingerprint", qc.sha256_file(a) == qc.sha256_file(b))
        b.write_bytes(b"different-raw-bytes" * 5000)
        check("different RAW yields different fingerprint", qc.sha256_file(a) != qc.sha256_file(b))


# --------------------------------------------------------------------------
# 10. Fenced-JSON regression (the V7 parser bug)
# --------------------------------------------------------------------------
def test_json_parsing(qc):
    print("\n[10] Vision JSON parsing regression")
    samples = [
        ('{"status":"CORRECT_REVEAL","confidence":0.9}', "bare json"),
        ('```json\n{"status":"CORRECT_REVEAL","confidence":0.9}\n```', "fenced json"),
        ('```\n{"status":"CORRECT_REVEAL","confidence":0.9}\n```', "bare fence"),
        ('Here is the result:\n{"status":"CORRECT_REVEAL","confidence":0.9}\nHope that helps.',
         "prose-wrapped json"),
        ('{"reason":"the answer is {hidden}","status":"CORRECT_REVEAL"}', "braces inside a string"),
    ]
    for text, label in samples:
        try:
            parsed = qc._json_from_model_text(text)
            ok = parsed.get("status") == "CORRECT_REVEAL"
            err = ""
        except Exception as e:
            ok, err = False, str(e)
        check(f"parses {label}", ok, err)

    try:
        qc._json_from_model_text("no json here at all")
        ok = False
    except RuntimeError:
        ok = True
    check("still raises on genuinely missing JSON", ok)


# --------------------------------------------------------------------------
def main():
    print("=" * 66)
    print("KP Kids story-repair acceptance tests")
    print("=" * 66)

    test_compile()
    test_yaml()

    editor = load(EDITOR, "kp_editor")
    qc = load(QC, "kp_qc")

    with tempfile.TemporaryDirectory(prefix="kp_tests_") as td:
        workdir = Path(td)
        test_reorder(editor, workdir)
        test_persistent_blocks(editor, workdir)
        test_uncertain_holds(editor, workdir)

    test_plan_rejections(editor, qc)
    test_credit_safety(qc)
    test_json_parsing(qc)

    print("\n" + "=" * 66)
    print(f"PASSED {len(PASSED)}   FAILED {len(FAILED)}")
    if FAILED:
        print("\nFailures:")
        for name, detail in FAILED:
            print(f"  - {name}: {detail}")
    print("=" * 66)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
