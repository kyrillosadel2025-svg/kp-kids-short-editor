#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KP Kids story contracts and deterministic beat-order repair.

Why this module exists
----------------------
The original pipeline treated "wrong story" as a single axis: was the ANSWER
disclosed too early? That misses a second, independent failure that is just as
damaging for a preschool quiz:

    the child is invited to answer AFTER the character has already answered.

Example: the boy asks "can you find the apple?", points at the apple, and only
then says "your turn - point at it". The reveal is not early; the INTERACTION is
late. Vision correctly reports CORRECT_REVEAL, and the old gate passed the video
through with the interaction wasted.

So story order is modelled as a contract, and beat order is audited separately
from reveal safety.

Two contracts are supported
---------------------------
reveal_first        HOOK QUESTION THINK REVEAL REINFORCE CHILD_TURN PAYOFF
                    Teaching/explaining. The character answers, then the child
                    repeats or practises.

interaction_first   HOOK QUESTION THINK CHILD_TURN REVEAL REINFORCE PAYOFF
                    Find-it / point-at-it / choose-one. The child must get a
                    real chance to answer BEFORE the character answers.

Reveal modes
------------
appearance  The answer object is absent and then appears. Seeing it early is a
            spoiler.
gesture     The answer object is legitimately on screen the whole time (a fruit
            bowl, a row of shapes). Only the ACT of identifying it - pointing,
            holding, circling, highlighting, naming - is the reveal.

This distinction matters: a find-the-apple episode has the apple visible in
frame one by design. Judged as `appearance` it is a PERSISTENT_EARLY_REVEAL and
is blocked forever; judged as `gesture` it is a normal, repairable video.
"""

import re

# --------------------------------------------------------------------------
# Beat vocabulary
# --------------------------------------------------------------------------
STORY_LABEL_ALIASES = {
    "OPENING": "HOOK", "INTRO": "HOOK", "HOOK": "HOOK", "GREETING": "HOOK", "SETUP": "HOOK",
    "QUESTION": "QUESTION", "ASK": "QUESTION", "CHALLENGE": "QUESTION", "PROMPT": "QUESTION",
    "THINK": "THINK", "THINKING": "THINK", "PAUSE": "THINK", "COUNTDOWN": "THINK", "WAIT": "THINK",
    "REVEAL": "REVEAL", "ANSWER": "REVEAL", "RESULT": "REVEAL", "SOLUTION": "REVEAL",
    "ANSWER_REVEAL": "REVEAL", "REVEAL_ANSWER": "REVEAL", "POINT": "REVEAL", "POINTING": "REVEAL",
    "REINFORCE": "REINFORCE", "REINFORCEMENT": "REINFORCE", "EXPLAIN": "REINFORCE", "RECAP": "REINFORCE",
    "CHILD_TURN": "CHILD_TURN", "CHILD TURN": "CHILD_TURN", "INTERACTION": "CHILD_TURN",
    "YOUR_TURN": "CHILD_TURN", "YOUR TURN": "CHILD_TURN", "INVITE": "CHILD_TURN", "CTA": "CHILD_TURN",
    "VIEWER_TURN": "CHILD_TURN", "AUDIENCE_TURN": "CHILD_TURN", "PARTICIPATION": "CHILD_TURN",
    "PAYOFF": "PAYOFF", "REWARD": "PAYOFF", "CLOSING": "PAYOFF", "OUTRO": "PAYOFF", "CELEBRATION": "PAYOFF",
}

STORY_CONTRACTS = {
    "reveal_first": ["HOOK", "QUESTION", "THINK", "REVEAL", "REINFORCE", "CHILD_TURN", "PAYOFF"],
    "interaction_first": ["HOOK", "QUESTION", "THINK", "CHILD_TURN", "REVEAL", "REINFORCE", "PAYOFF"],
}
DEFAULT_CONTRACT = "reveal_first"

# Formats where the child is meant to answer before the character does.
_INTERACTION_FIRST_HINTS = (
    "find", "spot", "point", "choose", "pick", "select", "which", "where",
    "guess", "search", "hunt", "seek", "look for", "can you",
    "لاقي", "لاقى", "هات", "شاور", "اختار", "فين", "مين", "دور على",
)
_REVEAL_FIRST_HINTS = (
    "teach", "learn", "explain", "show", "introduce", "repeat after",
    "count with", "sing", "story",
)

REPAIR_MIN_CONFIDENCE = 0.82
SEGMENT_MIN_SECONDS = 0.18
SEGMENT_MAX_COUNT = 10
SEGMENT_OVERLAP_EPSILON = 0.005
# A cut may move at most this far to land in a silence. Beyond it the boundary is
# not "clean" and we would be guessing where a word ends.
BOUNDARY_SNAP_TOLERANCE = 0.45


def normalize_story_label(value):
    s = str(value or "").strip().upper().replace("-", "_")
    s = re.sub(r"\s+", " ", s)
    if s in STORY_LABEL_ALIASES:
        return STORY_LABEL_ALIASES[s]
    return STORY_LABEL_ALIASES.get(s.replace(" ", "_"), s.replace(" ", "_"))


def _f(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Contract + reveal-mode resolution
# --------------------------------------------------------------------------
def resolve_story_contract(payload):
    """Pick the story contract for this episode.

    An explicit payload value always wins, so an author can override the guess.
    Otherwise the episode format / question wording decides.
    """
    explicit = str(payload.get("story_contract") or "").strip().lower().replace("-", "_")
    if explicit in STORY_CONTRACTS:
        return explicit, "explicit_payload_value"

    haystack = " ".join(str(payload.get(k) or "") for k in (
        "episode_format", "interaction_type", "question_line", "challenge_line",
        "reveal_type", "hook_type", "topic", "title", "dialogue_timeline",
    )).lower()

    has_invite = bool(payload.get("interaction_type") or payload.get("challenge_line")) or any(
        k in haystack for k in ("your turn", "child turn", "invite", "دورك", "شاور")
    )
    looks_findit = any(k in haystack for k in _INTERACTION_FIRST_HINTS)
    looks_teach = any(k in haystack for k in _REVEAL_FIRST_HINTS)

    if looks_findit and not looks_teach:
        return "interaction_first", "format_is_find_or_choose"
    if looks_findit and has_invite:
        return "interaction_first", "find_format_with_child_invite"
    return DEFAULT_CONTRACT, "default_reveal_first"


def resolve_reveal_mode(payload):
    """`gesture` when the answer object is on screen by design, else `appearance`."""
    explicit = str(payload.get("reveal_mode") or "").strip().lower()
    if explicit in ("gesture", "appearance"):
        return explicit, "explicit_payload_value"

    start_policy = str(payload.get("start_policy") or "").strip().lower()
    if start_policy == "visible-neutral":
        return "gesture", "start_policy_visible_neutral"
    if start_policy == "withhold-result":
        return "appearance", "start_policy_withhold_result"

    contract, _ = resolve_story_contract(payload)
    if contract == "interaction_first":
        # Find-it episodes need the candidate objects present to be findable.
        return "gesture", "find_format_requires_visible_candidates"
    return "appearance", "default_appearance"


def contract_order_map(contract):
    beats = STORY_CONTRACTS.get(contract, STORY_CONTRACTS[DEFAULT_CONTRACT])
    return {label: i for i, label in enumerate(beats)}


def contract_rule_text(contract):
    beats = STORY_CONTRACTS.get(contract, STORY_CONTRACTS[DEFAULT_CONTRACT])
    return " -> ".join(beats)


# --------------------------------------------------------------------------
# Boundary snapping
# --------------------------------------------------------------------------
def snap_to_silence(t, silences, duration, tolerance=BOUNDARY_SNAP_TOLERANCE):
    """Move a cut point into the nearest silence so no word is chopped.

    Returns (snapped_time, distance_moved, landed_in_silence).
    """
    t = max(0.0, min(float(t), duration))
    if not silences:
        return t, 0.0, False
    for a, b in silences:
        if a - 1e-6 <= t <= b + 1e-6:
            return t, 0.0, True
    best, best_d = t, None
    for a, b in silences:
        for cand in (a, b, (a + b) / 2.0):
            d = abs(cand - t)
            if d <= tolerance and (best_d is None or d < best_d):
                best, best_d = cand, d
    if best_d is None:
        return t, 0.0, False
    return max(0.0, min(best, duration)), best_d, True


# --------------------------------------------------------------------------
# The audit
# --------------------------------------------------------------------------
def _clean_beats(story_segments, duration):
    beats = []
    for i, seg in enumerate(story_segments or []):
        if not isinstance(seg, dict):
            continue
        label = normalize_story_label(seg.get("label") or seg.get("phase") or seg.get("type"))
        a = _f(seg.get("start_sec", seg.get("start", seg.get("source_start"))))
        b = _f(seg.get("end_sec", seg.get("end", seg.get("source_end"))))
        if a is None or b is None or label not in STORY_LABEL_ALIASES.values():
            continue
        a = max(0.0, min(a, duration))
        b = max(0.0, min(b, duration))
        if b - a < SEGMENT_MIN_SECONDS:
            continue
        beats.append({"label": label, "source_start": a, "source_end": b, "source_index": i})
    beats.sort(key=lambda x: x["source_start"])
    return beats


def _merge_touching(beats, gap=0.08):
    """Collapse consecutive same-label beats so we never cut inside one phase."""
    out = []
    for b in beats:
        if out and out[-1]["label"] == b["label"] and b["source_start"] - out[-1]["source_end"] <= gap:
            out[-1]["source_end"] = max(out[-1]["source_end"], b["source_end"])
        else:
            out.append(dict(b))
    return out


def audit_story_order(story_segments, payload, duration, silences=None):
    """Compare the beat order against the episode's contract and plan a repair.

    The plan is built HERE, deterministically, rather than trusted from the
    model. Vision is good at saying "this stretch is the child's turn"; it is not
    reliable at producing a safe edit decision list.
    """
    contract, contract_reason = resolve_story_contract(payload)
    order_map = contract_order_map(contract)
    report = {
        "contract": contract,
        "contract_reason": contract_reason,
        "contract_order": STORY_CONTRACTS[contract],
        "status": "ORDER_UNKNOWN",
        "confidence": 0.0,
        "reason": "no_usable_story_segments",
        "source_order": [],
        "target_order": [],
        "repair_plan": {},
        "violations": [],
    }

    beats = _merge_touching(_clean_beats(story_segments, duration))
    if len(beats) < 2:
        return report
    report["source_order"] = [b["label"] for b in beats]

    known = [b for b in beats if b["label"] in order_map]
    if len(known) < 2:
        report["reason"] = "no_contract_beats_identified"
        return report

    # --- which pairs are out of contract order? -------------------------------
    violations = []
    for i, cur in enumerate(known):
        for nxt in known[i + 1:]:
            if order_map[nxt["label"]] < order_map[cur["label"]]:
                violations.append({
                    "earlier": cur["label"], "later": nxt["label"],
                    "earlier_at": round(cur["source_start"], 3),
                    "later_at": round(nxt["source_start"], 3),
                })
    report["violations"] = violations

    if not violations:
        report.update(status="ORDER_OK", confidence=0.95,
                      reason="beat_order_already_matches_contract",
                      target_order=[b["label"] for b in beats])
        return report

    # --- coverage: can we rebuild the whole video from these beats? -----------
    covered = sum(b["source_end"] - b["source_start"] for b in beats)
    coverage = covered / duration if duration > 0 else 0.0
    overlapped = any(
        nxt["source_start"] < cur["source_end"] - SEGMENT_OVERLAP_EPSILON
        for cur, nxt in zip(beats, beats[1:])
    )
    if overlapped:
        report.update(status="ORDER_UNREPAIRABLE", reason="story_beats_overlap_in_source")
        return report
    if coverage < 0.80:
        report.update(status="ORDER_UNREPAIRABLE",
                      reason=f"beats_cover_only_{coverage:.2f}_of_source")
        return report
    if len(beats) > SEGMENT_MAX_COUNT:
        report.update(status="ORDER_UNREPAIRABLE", reason="too_many_segments_to_reorder_safely")
        return report

    # --- fill the gaps so no footage is silently dropped ----------------------
    filled = []
    cursor = 0.0
    for b in beats:
        if b["source_start"] - cursor > SEGMENT_MIN_SECONDS:
            # Attach orphan footage to the following beat rather than discarding it.
            b = dict(b, source_start=cursor)
        elif b["source_start"] > cursor:
            b = dict(b, source_start=cursor)
        filled.append(b)
        cursor = b["source_end"]
    if duration - cursor > SEGMENT_MIN_SECONDS:
        filled[-1] = dict(filled[-1], source_end=duration)
    elif duration > cursor:
        filled[-1] = dict(filled[-1], source_end=duration)

    # --- work out which boundaries are ACTUALLY cut ---------------------------
    # Two beats that stay neighbours in the output are never cut apart, so their
    # shared boundary does not have to be clean. Only the seams where the output
    # order breaks source adjacency become real cuts, and only those must land in
    # silence. Demanding a clean boundary everywhere rejects perfectly repairable
    # videos because of a seam that is never actually sliced.
    order_of = {id(b): (order_map.get(b["label"], 99), b["source_start"]) for b in filled}
    output_sequence = sorted(filled, key=lambda b: order_of[id(b)])
    output_position = {id(b): i for i, b in enumerate(output_sequence)}

    real_cuts = []
    for i in range(len(filled) - 1):
        left, right = filled[i], filled[i + 1]
        if output_position[id(right)] != output_position[id(left)] + 1:
            real_cuts.append(i)

    if not real_cuts:
        report.update(status="ORDER_OK", confidence=0.9,
                      reason="reorder_would_not_cut_anything")
        return report

    # --- snap only those cuts into a silence ----------------------------------
    silences = silences or []
    snapped = [dict(b) for b in filled]
    snap_distances = []
    dirty_boundaries = []
    for i in real_cuts:
        cut = snapped[i]["source_end"]
        new_cut, moved, clean = snap_to_silence(cut, silences, duration)
        if not clean:
            dirty_boundaries.append(round(cut, 3))
            continue
        left_span = new_cut - snapped[i]["source_start"]
        right_span = snapped[i + 1]["source_end"] - new_cut
        if left_span < SEGMENT_MIN_SECONDS or right_span < SEGMENT_MIN_SECONDS:
            dirty_boundaries.append(round(cut, 3))
            continue
        snap_distances.append(moved)
        snapped[i]["source_end"] = new_cut
        snapped[i + 1]["source_start"] = new_cut

    report["cut_points"] = [round(snapped[i]["source_end"], 3) for i in real_cuts]
    report["dirty_cut_points"] = dirty_boundaries

    snapped = [b for b in snapped if b["source_end"] - b["source_start"] >= SEGMENT_MIN_SECONDS]
    if len(snapped) < 2:
        report.update(status="ORDER_UNREPAIRABLE", reason="snapping_collapsed_the_segments")
        return report

    # --- reorder to the contract, stable within a beat ------------------------
    ordered = sorted(snapped, key=lambda b: (order_map.get(b["label"], 99), b["source_start"]))
    output = [
        {
            "label": b["label"],
            "start_sec": round(b["source_start"], 4),
            "end_sec": round(b["source_end"], 4),
            "order": position,
        }
        for position, b in enumerate(ordered)
    ]
    report["target_order"] = [s["label"] for s in output]

    # A reorder that changes nothing is not a repair.
    if report["target_order"] == [b["label"] for b in snapped]:
        report.update(status="ORDER_OK", confidence=0.9,
                      reason="contract_order_already_satisfied_after_merge")
        return report

    # --- confidence ------------------------------------------------------------
    # Start from beat coverage, penalise unclean cuts and long snap distances.
    confidence = 0.60 + min(0.22, (coverage - 0.80) * 1.1)
    confidence += 0.12 if not dirty_boundaries else -0.25 * len(dirty_boundaries)
    if snap_distances:
        confidence -= min(0.10, (sum(snap_distances) / len(snap_distances)) * 0.20)
    if len(violations) == 1:
        confidence += 0.08          # a single clear swap is the safest case
    if len(real_cuts) <= 2:
        confidence += 0.05          # fewer real cuts, fewer ways to be wrong
    if str(payload.get("story_contract") or "").strip():
        confidence += 0.05          # the author declared the contract
    confidence = round(max(0.0, min(0.99, confidence)), 3)
    report["confidence"] = confidence

    if dirty_boundaries:
        report.update(status="ORDER_UNREPAIRABLE",
                      reason=f"cut_points_{dirty_boundaries}_do_not_land_in_silence")
        return report
    if confidence < REPAIR_MIN_CONFIDENCE:
        report.update(status="ORDER_UNREPAIRABLE",
                      reason="order_repair_confidence_below_threshold")
        return report

    report.update(
        status="ORDER_REPAIRABLE",
        reason="beat_order_violates_contract_and_is_cleanly_separable",
        repair_plan={
            "action": "reorder_segments",
            "repair_kind": "beat_order",
            "confidence": confidence,
            "contract": contract,
            "reason": "reorder_beats_to_" + contract,
            "output_segments": output,
        },
    )
    return report
