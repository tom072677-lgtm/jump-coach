#!/usr/bin/env python3
"""
analyze_jump.py — Standing long jump video analysis (Phase-1 demo)

Pipeline:
  video → MediaPipe PoseLandmarker (Tasks API, 3D world + 2D image landmarks)
        → segmental center-of-mass (Winter/Dempster parameters)
        → Butterworth-filtered kinematics
        → automatic takeoff / landing event detection
        → takeoff angle & velocity, joint-angle curves, estimated distance
        → annotated video + plots + JSON summary + rule-based coaching feedback

Designed to run offline on any side-view jump clip. Joint angles use the 3D
world landmarks (metric, perspective-robust); the center-of-mass trajectory and
events use the 2D image landmarks (which carry global translation), auto-scaled
to meters by comparing limb lengths in world space vs image space.

Run:
  python analyze_jump.py samples/slj_00001.mp4 --out out/slj1
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

# Windows consoles default to cp949 — force UTF-8 so Korean feedback prints.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import numpy as np
import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import butter, filtfilt

from contact_events import refine_contact_events
from phase_feedback import select_moment_frames, build_phase_analysis, preparation_start

import mediapipe as mp
from mediapipe.tasks.python import vision
from mediapipe.tasks.python.core.base_options import BaseOptions

# --------------------------------------------------------------------------- #
# BlazePose 33-landmark indices
# --------------------------------------------------------------------------- #
NOSE = 0
L_EAR, R_EAR = 7, 8
L_SHO, R_SHO = 11, 12
L_ELB, R_ELB = 13, 14
L_WRI, R_WRI = 15, 16
L_HIP, R_HIP = 23, 24
L_KNE, R_KNE = 25, 26
L_ANK, R_ANK = 27, 28
L_HEEL, R_HEEL = 29, 30
L_TOE, R_TOE = 31, 32

FOOT_PTS = [L_ANK, R_ANK, L_HEEL, R_HEEL, L_TOE, R_TOE]

POSE_CONNECTIONS = [
    (L_SHO, R_SHO), (L_SHO, L_HIP), (R_SHO, R_HIP), (L_HIP, R_HIP),
    (L_SHO, L_ELB), (L_ELB, L_WRI), (R_SHO, R_ELB), (R_ELB, R_WRI),
    (L_HIP, L_KNE), (L_KNE, L_ANK), (L_ANK, L_HEEL), (L_HEEL, L_TOE), (L_ANK, L_TOE),
    (R_HIP, R_KNE), (R_KNE, R_ANK), (R_ANK, R_HEEL), (R_HEEL, R_TOE), (R_ANK, R_TOE),
]

# --------------------------------------------------------------------------- #
# Segment inertial parameters (Winter, "Biomechanics and Motor Control of
# Human Movement", from Dempster): mass as fraction of total body mass, and
# longitudinal CoM location as a fraction of segment length from the PROXIMAL
# joint. Limb segments are bilateral (left+right counted separately).
# Sum over all listed segments = 1.000.
# --------------------------------------------------------------------------- #
# (name, proximal_idx, distal_idx, mass_fraction, com_ratio_from_proximal)
SEGMENTS = [
    # Head&neck CoM ~ mid-skull; ear-midpoint is far closer than the nose
    # (the nose sits ~10 cm anterior, which would bias whole-body CoM forward).
    ("head",    L_EAR,  R_EAR,  0.081, 0.50),
    ("trunk",   "MID_SHO", "MID_HIP", 0.497, 0.50),
    ("l_thigh", L_HIP,  L_KNE,  0.100, 0.433),
    ("r_thigh", R_HIP,  R_KNE,  0.100, 0.433),
    ("l_shank", L_KNE,  L_ANK,  0.0465, 0.433),
    ("r_shank", R_KNE,  R_ANK,  0.0465, 0.433),
    ("l_foot",  L_HEEL, L_TOE,  0.0145, 0.50),
    ("r_foot",  R_HEEL, R_TOE,  0.0145, 0.50),
    ("l_uarm",  L_SHO,  L_ELB,  0.028, 0.436),
    ("r_uarm",  R_SHO,  R_ELB,  0.028, 0.436),
    ("l_farm",  L_ELB,  L_WRI,  0.016, 0.430),
    ("r_farm",  R_ELB,  R_WRI,  0.016, 0.430),
]
# Point masses (single landmark). Mass fractions: SEGMENTS sum to 0.988 and
# these hands add 0.012 → total 1.000.
POINT_MASSES = [("l_hand", L_WRI, 0.006), ("r_hand", R_WRI, 0.006)]


# --------------------------------------------------------------------------- #
# Pose extraction
# --------------------------------------------------------------------------- #
def extract_pose(video_path: str, model_path: str):
    """Run PoseLandmarker in VIDEO mode. Returns fps, (w,h), and arrays:
    norm  (T,33,3): x_px, y_px, visibility   (image space, pixels)
    world (T,33,3): x, y, z in meters        (hip-centered, 3D)
    valid (T,) bool: whether a pose was detected on that frame.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise SystemExit("영상을 열 수 없습니다. 파일이 손상되었거나 지원하지 않는 형식일 수 있어요.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    opts = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = vision.PoseLandmarker.create_from_options(opts)

    norm, world, valid = [], [], []
    idx = 0
    while True:
        ok, frame_bgr = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        ts = int(idx * 1000.0 / fps)
        res = landmarker.detect_for_video(mp_img, ts)

        if res.pose_landmarks:
            lms = res.pose_landmarks[0]
            wls = res.pose_world_landmarks[0]
            norm.append([[p.x * w, p.y * h, p.visibility] for p in lms])
            world.append([[p.x, p.y, p.z] for p in wls])
            valid.append(True)
        else:
            norm.append([[np.nan, np.nan, 0.0]] * 33)
            world.append([[np.nan, np.nan, np.nan]] * 33)
            valid.append(False)
        idx += 1
    cap.release()
    landmarker.close()

    return fps, (w, h), np.array(norm), np.array(world), np.array(valid, dtype=bool)


def interpolate_gaps(arr: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Linearly interpolate NaN gaps along time for every (landmark, coord)."""
    T = arr.shape[0]
    t = np.arange(T)
    out = arr.copy()
    if valid.sum() < 2:
        return out
    for j in range(arr.shape[1]):
        for c in range(arr.shape[2]):
            col = out[:, j, c]
            good = ~np.isnan(col)
            if good.sum() >= 2:
                out[:, j, c] = np.interp(t, t[good], col[good])
    return out


# --------------------------------------------------------------------------- #
# Kinematics
# --------------------------------------------------------------------------- #
def _pt(frame_lm, key, dims=2):
    """Resolve a landmark index or a midpoint pseudo-key to coords (single frame)."""
    if key == "MID_SHO":
        return (frame_lm[L_SHO, :dims] + frame_lm[R_SHO, :dims]) / 2
    if key == "MID_HIP":
        return (frame_lm[L_HIP, :dims] + frame_lm[R_HIP, :dims]) / 2
    return frame_lm[key, :dims]


def _pt_vec(norm_xy, key):
    """Vectorized _pt over all frames -> (T, 2)."""
    if key == "MID_SHO":
        return (norm_xy[:, L_SHO, :2] + norm_xy[:, R_SHO, :2]) / 2
    if key == "MID_HIP":
        return (norm_xy[:, L_HIP, :2] + norm_xy[:, R_HIP, :2]) / 2
    return norm_xy[:, key, :2]


def center_of_mass(norm_xy: np.ndarray) -> np.ndarray:
    """Whole-body CoM per frame in pixels via segmental model. norm_xy: (T,33,>=2).
    Vectorized over frames (numpy releases the GIL → thread-parallel friendly)."""
    num = np.zeros((norm_xy.shape[0], 2))
    tot = 0.0
    for _, prox, dist, mass, ratio in SEGMENTS:
        p = _pt_vec(norm_xy, prox); d = _pt_vec(norm_xy, dist)
        num += mass * (p + ratio * (d - p))
        tot += mass
    for _, jidx, mass in POINT_MASSES:
        num += mass * norm_xy[:, jidx, :2]
        tot += mass
    return num / tot


def joint_angle(world: np.ndarray, a: int, b: int, c: int) -> np.ndarray:
    """3D angle at joint b (a-b-c) in degrees, per frame. Uses world landmarks."""
    va = world[:, a, :] - world[:, b, :]
    vc = world[:, c, :] - world[:, b, :]
    dot = np.sum(va * vc, axis=1)
    na = np.linalg.norm(va, axis=1)
    nc = np.linalg.norm(vc, axis=1)
    cos = np.clip(dot / (na * nc + 1e-9), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def primary_side(norm: np.ndarray) -> str:
    """Pick the more visible side (sum of joint visibilities)."""
    left = norm[:, [L_HIP, L_KNE, L_ANK, L_SHO, L_ELB], 2].mean()
    right = norm[:, [R_HIP, R_KNE, R_ANK, R_SHO, R_ELB], 2].mean()
    return "L" if left >= right else "R"


_FILTER_SKIPPED = False


def butter_lp(sig: np.ndarray, fps: float, cutoff: float = 6.0, order: int = 2) -> np.ndarray:
    """Zero-lag Butterworth low-pass. Returns raw (with a one-time warning) if the
    clip is too short or the cutoff is above Nyquist."""
    global _FILTER_SKIPPED
    nyq = fps / 2.0
    if cutoff >= nyq or len(sig) <= 3 * (order + 1):
        if not _FILTER_SKIPPED:
            print(f"      [warn] low-pass skipped (fps={fps:.1f}, cutoff={cutoff} Hz, n={len(sig)}); signals are RAW")
            _FILTER_SKIPPED = True
        return sig
    b, a = butter(order, cutoff / nyq, btype="low")
    padlen = min(3 * max(len(a), len(b)), len(sig) - 1)   # avoid padlen>signal on short clips
    return filtfilt(b, a, sig, padlen=padlen)


def auto_scale_px_per_m(norm: np.ndarray, world: np.ndarray, standing: np.ndarray):
    """Estimate pixels-per-meter by comparing limb lengths in image (2D) vs world
    (3D, metric) space over standing frames, using thigh & shank (both sides).
    Returns (median_px_per_m, coeff_of_variation). This is APPROXIMATE: it assumes
    limbs are roughly fronto-parallel at stance and ignores perspective; for
    accurate absolute distances pass --scale-px-per-m or calibrate in the lab."""
    pairs = [(L_HIP, L_KNE), (R_HIP, R_KNE), (L_KNE, L_ANK), (R_KNE, R_ANK)]
    ratios = []
    for a, b in pairs:
        px = np.linalg.norm(norm[standing][:, a, :2] - norm[standing][:, b, :2], axis=1)
        m = np.linalg.norm(world[standing][:, a, :] - world[standing][:, b, :], axis=1)
        good = (m > 1e-3) & np.isfinite(px) & np.isfinite(m)
        if good.any():
            ratios.extend((px[good] / m[good]).tolist())
    if not ratios:
        return float("nan"), float("nan")
    r = np.array(ratios)
    return float(np.median(r)), float(np.std(r) / (np.mean(r) + 1e-9))


def fit_takeoff_velocity(com: np.ndarray, scale: float, takeoff: int, landing: int, fps: float):
    """Take-off velocity from the FLIGHT-phase CoM, not a single finite difference.
    Horizontal: linear fit x(t)=x0+vx·t. Vertical: quadratic fit y(t)=c+vy·t-½g·t²
    (the implied g is a physics sanity check). Returns speed, angle(deg), vx, vy, g."""
    idx = np.arange(takeoff, landing + 1)
    if len(idx) < 3:
        return (float("nan"),) * 5
    tt = (idx - takeoff) / fps
    x_m = com[idx, 0] / scale
    y_m = (com[takeoff, 1] - com[idx, 1]) / scale          # up positive, 0 at takeoff
    vx = float(np.polyfit(tt, x_m, 1)[0])                   # m/s (signed)
    a2, vy, _ = np.polyfit(tt, y_m, 2)                      # y = a2 t² + vy t + c
    g_implied = float(-2.0 * a2)
    speed = math.hypot(vx, vy)
    angle = math.degrees(math.atan2(vy, abs(vx))) if abs(vx) > 1e-6 else (90.0 if vy > 0 else None)
    return speed, angle, abs(vx), float(vy), g_implied


def med_at(arr: np.ndarray, idx: int, k: int = 2) -> float:
    """Robust value of a 1-D signal near frame idx (median of a ±k window)."""
    lo, hi = max(0, idx - k), min(len(arr), idx + k + 1)
    return float(np.nanmedian(arr[lo:hi]))


def _vis_ok(raw_vis, idxs, lo, hi, thr) -> bool:
    """True if EVERY required landmark is, on average, visible over [lo,hi). Reads the
    pre-interpolation visibility (interpolate_gaps fills the visibility channel too, so a
    post-interp read is synthetic). np.all over per-landmark means so one good landmark
    can't mask a consistently-lost one. raw_vis has 0.0 (not NaN) on undetected frames."""
    if hi <= lo:
        return False
    seg = raw_vis[lo:hi][:, idxs]
    return bool(seg.size and np.all(np.mean(seg, axis=0) >= thr))


def _subject_crop_box(norm, valid, size, occupy=0.55, margin=0.35):
    """If the subject occupies only a small region of the frame (e.g. a landscape clip
    letterboxed into a vertical 'shorts' canvas, so the person is a thin band in a tall
    frame), return an (x, y, w, h) crop box around the subject (+margin) so the analysis
    sees a larger, clearer subject. Returns None when the subject already fills the frame
    (so normally-framed clips are untouched — no regression).

    Uses robust 1-99 percentiles of ALL landmarks over EVERY detected frame, so the box is
    the stable union of the whole jump (handles the horizontal travel of a long jump). The
    crop is purely a pre-step: after cropping we re-extract pose, so all downstream coords
    and the px->m scale are recomputed in the cropped frame (self-consistent; the original
    pose is used only to size this box, then discarded)."""
    w, h = int(size[0]), int(size[1])
    if valid.sum() < 5:
        return None
    pts = norm[valid][:, :, :2]
    xs, ys = pts[:, :, 0], pts[:, :, 1]
    if np.isfinite(xs).sum() < 10 or np.isfinite(ys).sum() < 10:
        return None
    x0, x1 = np.nanpercentile(xs, 1), np.nanpercentile(xs, 99)
    y0, y1 = np.nanpercentile(ys, 1), np.nanpercentile(ys, 99)
    if not all(np.isfinite(v) for v in (x0, x1, y0, y1)):
        return None
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return None
    # Crop only when there's clearly empty margin in SOME dimension (letterbox / small subject).
    if bh >= occupy * h and bw >= occupy * w:
        return None
    mx, my = bw * margin, bh * margin
    cx0, cy0 = max(0, int(x0 - mx)), max(0, int(y0 - my))
    cx1, cy1 = min(w, int(x1 + mx)), min(h, int(y1 + my))
    cw = (cx1 - cx0) - ((cx1 - cx0) % 2)        # libx264 needs even dimensions
    ch = (cy1 - cy0) - ((cy1 - cy0) % 2)
    if cw < 80 or ch < 80 or (cw >= w and ch >= h):
        return None                              # nothing meaningful to crop
    return (cx0, cy0, cw, ch)


# --------------------------------------------------------------------------- #
# Event detection
# --------------------------------------------------------------------------- #
def detect_events(com_xy: np.ndarray, norm: np.ndarray, frame_h: int):
    """Find takeoff, apex, landing frame indices.
    Ground line ≈ high percentile of the lowest foot point; airborne when the
    feet lift clear of it. The jump is the airborne run containing the CoM apex.
    """
    foot_y = np.nanmax(norm[:, FOOT_PTS, 1], axis=1)      # lowest foot point (image y, down+)
    ground = np.nanpercentile(foot_y, 85)
    height_above = ground - foot_y                         # >0 when airborne
    thresh = 0.04 * frame_h
    airborne = height_above > thresh

    com_y = com_xy[:, 1]
    if not np.isfinite(com_y).any():
        raise SystemExit("자세를 인식하지 못했어요. 옆에서 전신이 다 보이게, 밝은 곳에서 다시 촬영해 주세요.")
    apex = int(np.nanargmin(com_y))                        # highest CoM = smallest y
    if not airborne[apex]:
        # widen near apex if the threshold just missed it
        lo, hi = max(0, apex - 3), min(len(airborne), apex + 4)
        airborne[lo:hi] = airborne[lo:hi] | (height_above[lo:hi] > thresh * 0.5)

    # contiguous airborne run containing apex
    takeoff = apex
    while takeoff > 0 and airborne[takeoff - 1]:
        takeoff -= 1
    landing = apex
    while landing < len(airborne) - 1 and airborne[landing + 1]:
        landing += 1
    landing = min(landing + 1, len(airborne) - 1)          # first ground-contact frame
    return takeoff, apex, landing, ground, airborne


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def phase_of(i, takeoff, landing):
    if i < takeoff:
        return "PREP/LOAD", (80, 180, 255)
    if i < landing:
        return "FLIGHT", (80, 255, 80)
    return "LANDING", (255, 160, 80)


def _joints_to_draw(xy, vis, core, torso_len, vis_min=0.5, reach=2.8):
    """Boolean mask (33,) of landmarks trustworthy to DRAW for one frame. Pure & testable.
    A joint is dropped when (a) it isn't finite, (b) its pre-interpolation visibility is low,
    or (c) it sits implausibly far from the body core — a background latch (motion blur) lands
    a joint well beyond any anatomical reach. `reach` is in torso (shoulder-hip) lengths: a fully
    extended real limb reaches ~2.3 torso lengths from the core, so 2.8 keeps real poses intact
    while dropping off-body flings. Falls back to a visibility-only gate if the body scale is
    unknown (so it never hides MORE than the plain visibility rule when it can't measure reach)."""
    xy = np.asarray(xy, dtype=float)
    vis = np.asarray(vis, dtype=float)
    finite = np.all(np.isfinite(xy), axis=1)
    keep = finite & (vis >= vis_min)
    if core is not None and np.all(np.isfinite(core)) and np.isfinite(torso_len) and torso_len > 0:
        dist = np.linalg.norm(xy - np.asarray(core, dtype=float), axis=1)
        keep &= (dist <= reach * torso_len)
    return keep


def render_video(video_path, out_path, norm, valid, com_xy, knee, hip,
                 takeoff, apex, landing, fps, size, raw_vis=None):
    cap = cv2.VideoCapture(video_path)
    w, h = size
    # Don't draw glitched joints. During motion blur a low-confidence extremity landmark can
    # latch onto a background object (a jacket on the ledge, a person through the window), so
    # the skeleton briefly "grabs" something off-body. Two guards (render-only — metrics are
    # unaffected): (1) hide a joint whose pre-interpolation visibility is low; (2) skip a bone
    # drawn far longer than that bone's typical (median) length — a landmark that flew to the
    # background makes an anatomically impossible bone.
    BONE_MAX = 2.5
    if raw_vis is None:
        raw_vis = np.full((len(norm), 33), 1.0)
    # Per-bone typical length (reject implausibly long bones) + body scale (reject joints that
    # sit too far from the torso core). Both computed over confidently-tracked frames.
    bone_med = {}
    for a, b in POSE_CONNECTIONS:
        L = np.linalg.norm(norm[:, a, :2] - norm[:, b, :2], axis=1)
        ok = np.isfinite(L) & (raw_vis[:, a] > 0.5) & (raw_vis[:, b] > 0.5)
        bone_med[(a, b)] = float(np.nanmedian(L[ok])) if int(ok.sum()) > 5 else float(np.nanmedian(L))
    mid_sho = (norm[:, L_SHO, :2] + norm[:, R_SHO, :2]) / 2
    mid_hip = (norm[:, L_HIP, :2] + norm[:, R_HIP, :2]) / 2
    torso_len = float(np.nanmedian(np.linalg.norm(mid_sho - mid_hip, axis=1)))
    TORSO_IDX = [L_SHO, R_SHO, L_HIP, R_HIP]
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    vw = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
    i = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if valid[i]:
            f = norm[i]
            core = np.nanmean(f[TORSO_IDX, :2], axis=0)
            keep = _joints_to_draw(f[:, :2], raw_vis[i], core if np.all(np.isfinite(core)) else None, torso_len)
            for a, b in POSE_CONNECTIONS:
                if not (keep[a] and keep[b]):                          # low-conf / off-body joint
                    continue
                pa, pb = f[a, :2], f[b, :2]
                med = bone_med.get((a, b))
                if med and med > 0 and np.linalg.norm(pa - pb) > BONE_MAX * med:   # implausibly long bone
                    continue
                cv2.line(frame, tuple(pa.astype(int)), tuple(pb.astype(int)), (245, 245, 245), 2)
            for j in range(33):
                if keep[j]:
                    cv2.circle(frame, tuple(f[j, :2].astype(int)), 3, (0, 200, 255), -1)
            c = com_xy[i]
            if np.all(np.isfinite(c)):
                cv2.circle(frame, tuple(c.astype(int)), 7, (60, 60, 255), -1)
                cv2.circle(frame, tuple(c.astype(int)), 7, (255, 255, 255), 1)

        label, color = phase_of(i, takeoff, landing)
        cv2.rectangle(frame, (0, 0), (w, 64), (0, 0, 0), -1)
        cv2.putText(frame, f"{label}", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(frame, f"knee {knee[i]:5.0f}  hip {hip[i]:5.0f} deg",
                    (12, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
        for name, fi in (("TAKEOFF", takeoff), ("APEX", apex), ("LANDING", landing)):
            if i == fi:
                cv2.putText(frame, name, (w - 220, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 3)
        vw.write(frame)
        i += 1
    cap.release()
    vw.release()


import threading

_PLOT_LOCK = threading.Lock()   # pyplot is not thread-safe — serialize plotting only


def save_event_frames(video, out, frames_wanted: dict, T: int) -> dict:
    """Save ORIGINAL (no-overlay) JPEGs at the given frame indices, for the 3 key-moment
    screenshots. frames_wanted: {key: frame_index}. T: true decoded frame count (indices are
    clamped to [0, T-1]; CAP_PROP_FRAME_COUNT is unreliable). Sequential decode (codec-robust);
    if the stream ends before a target, the last decoded frame is used. Best-effort: a per-shot
    failure is logged and skipped, never raised. Returns {key: path} for shots actually written."""
    wanted = {k: int(min(max(0, fi), max(0, T - 1))) for k, fi in frames_wanted.items()}
    targets = sorted(set(wanted.values()))
    cap = cv2.VideoCapture(str(video))
    grabbed, last, idx, ti = {}, None, 0, 0
    while ti < len(targets):
        ok, frame = cap.read()
        if not ok:
            break
        last = frame
        if idx == targets[ti]:
            grabbed[targets[ti]] = frame.copy()
            ti += 1
        idx += 1
    cap.release()

    paths = {}
    for key, fi in wanted.items():
        img = grabbed.get(fi, last)
        if img is None:
            print(f"      [warn] screenshot '{key}' (frame {fi}) — no frame decoded")
            continue
        h, w = img.shape[:2]
        if w > 1280:
            img = cv2.resize(img, (1280, int(h * 1280 / w)), interpolation=cv2.INTER_AREA)
        p = out.with_suffix(f".{key}.jpg")
        try:
            if cv2.imwrite(str(p), img) and p.exists() and p.stat().st_size > 0:
                paths[key] = str(p)
            else:
                print(f"      [warn] screenshot '{key}' write failed ({p})")
        except Exception as e:
            print(f"      [warn] screenshot '{key}' error: {e}")
    return paths


def annotate_landing_reach(path, metrics, size):
    """Overlay horizontal projections on the SAME first-contact photo (not a later pose)."""
    contact = metrics.get("landing_contact_point_px")
    com = metrics.get("landing_com_point_px")
    pelvis = metrics.get("landing_hip_point_px")
    if any(p is None or not np.isfinite(p).all() for p in (contact, com, pelvis)):
        return
    image = cv2.imread(str(path))
    if image is None:
        return
    height, width = image.shape[:2]
    ratio = np.array([width / size[0], height / size[1]])
    def pt(value):
        point = np.asarray(value) * ratio
        return (int(np.clip(point[0], 0, width - 1)), int(np.clip(point[1], 0, height - 1)))
    c, center, hip = pt(contact), pt(com), pt(pelvis)
    cv2.line(image, (c[0], min(center[1], hip[1])), c, (0, 220, 255), 2)
    for point, color, label in [(center, (60, 60, 255), "CoM"), (hip, (255, 160, 60), "HIP")]:
        cv2.line(image, point, (c[0], point[1]), color, 2)
        cv2.circle(image, point, 5, color, -1)
        cv2.putText(image, label, (max(0, point[0] - 35), max(14, point[1] - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, .55, color, 2)
    cv2.circle(image, c, 5, (0, 220, 255), -1)
    if not cv2.imwrite(str(path), image):
        print(f"      [warn] 착지 간격 표시 저장 실패: {path}")


def photo_feedback(m: dict) -> dict:
    """Compatibility view of the same phase observations; no separate quality rules."""
    phases = m.get("phase_feedback") or {}
    return {key: {"good": list(phases.get(key, {}).get("observations", [])),
                  "bad": list(phases.get(key, {}).get("cues", []))}
            for key in ("takeoff", "mid", "landing")}


def render_plots(out_path, t, knee, hip, ankle, arm, com_xy, scale, takeoff, apex, landing, fps, lo, hi):
    """lo, hi: frame window to focus the plots on (keeps the jump readable).
    pyplot is not thread-safe, so plotting is serialized with _PLOT_LOCK. Plotting is
    fast (~1s) so this barely dents the parallel /compare speedup — pose (the heavy
    part) still runs concurrently across clips."""
    with _PLOT_LOCK:
        fig, ax = plt.subplots(2, 2, figsize=(13, 8))
        ev = [(takeoff, "takeoff", "tab:green"), (apex, "apex", "tab:gray"), (landing, "landing", "tab:orange")]
        win = (t[lo], t[hi - 1])

        def marks(a, xlim=True):
            for fi, lab, col in ev:
                a.axvline(t[fi], color=col, ls="--", lw=1, label=lab)
            if xlim:
                a.set_xlim(*win)

        ax[0, 0].plot(t, knee, label="knee"); ax[0, 0].plot(t, hip, label="hip")
        ax[0, 0].plot(t, ankle, label="ankle"); marks(ax[0, 0])
        ax[0, 0].set_title("(1) Joint angles (3D)"); ax[0, 0].set_xlabel("s"); ax[0, 0].set_ylabel("deg")
        ax[0, 0].legend(fontsize=7)

        ax[0, 1].plot(t, arm, color="tab:purple"); marks(ax[0, 1])
        ax[0, 1].set_title("(2) Arm elevation (hip-shoulder-elbow)"); ax[0, 1].set_xlabel("s"); ax[0, 1].set_ylabel("deg")

        cx = (com_xy[:, 0] - com_xy[takeoff, 0]) / scale
        cy = (com_xy[takeoff, 1] - com_xy[:, 1]) / scale     # up positive
        sl = slice(lo, hi)
        ax[1, 0].plot(cx[sl], cy[sl], color="tab:red")
        ax[1, 0].scatter([cx[takeoff], cx[apex], cx[landing]], [cy[takeoff], cy[apex], cy[landing]],
                         c=["tab:green", "tab:gray", "tab:orange"], zorder=5)
        ax[1, 0].set_title("(3) CoM trajectory (jump window)"); ax[1, 0].set_xlabel("horizontal (m)"); ax[1, 0].set_ylabel("height (m)")
        ax[1, 0].axis("equal")

        ax[1, 1].plot(t, cy, label="height"); ax[1, 1].plot(t, cx, label="horizontal")
        marks(ax[1, 1]); ax[1, 1].set_title("(4) CoM displacement vs time")
        ax[1, 1].set_xlabel("s"); ax[1, 1].set_ylabel("m"); ax[1, 1].legend(fontsize=7)

        fig.tight_layout()
        fig.savefig(out_path, dpi=110)
        plt.close(fig)


# --------------------------------------------------------------------------- #
# Coaching feedback (rule-based). Thresholds are grounded in the biomechanics
# research report (Obsidian: "리서치 — 운동역학·마커리스 근거"):
#   • Wakai & Linthorne (2005): optimal take-off angle is a FLAT optimum —
#     calculated 19–27°, athlete-preferred 31–39°, loss from preferring higher
#     is only 4–14 cm. Take-off SPEED dominates (≈5× the distance effect of angle).
#   • Typical take-off speed 3.0–4.2 m/s (skilled ≈3.55, unskilled ≈2.94).
#   • Arm swing adds ≈16–21% distance (Ashby & Heegaard 2002; PMC5931160).
#   • Countermovement ≈90° knee flexion; landing should flex to bring heels forward.
# These remain to be re-validated against the SNU lab force-plate/mocap data.
# --------------------------------------------------------------------------- #
REF = {
    "takeoff_speed_mps": (3.0, 4.2),
    "takeoff_angle_deg": (20.0, 40.0),
    "cm_knee_deg": 90.0,
    "arm_swing_min_deg": 90.0,   # heuristic; matches compare.py's scoring band (documented duplication)
    "landing_knee_flexed_below": 130.0,
}

# --------------------------------------------------------------------------- #
# Reliability gates — which numbers are we allowed to say out loud?
#
# The parabola gives ONE equation with TWO unknowns:
#       g_implied = a_px[px/s²] · k² / scale        (k = true fps / container fps)
# so g ≠ 9.81 proves that k or scale (or both) is wrong, but CANNOT say which.
# We therefore never auto-correct scale; we only refuse to quote the numbers that
# depend on it. Angles and times are scale-invariant and survive.
#
# All four thresholds are HEURISTICS fitted to a handful of clips, not statistics.
# Re-calibrate them against the SNU lab force-plate data (that is the whole point
# of the validation study) and change them here — nowhere else reads raw numbers.
# --------------------------------------------------------------------------- #
G_STD = 9.81            # m/s²
G_TOL = 1.5             # m/s² → accept g ∈ [8.31, 11.31] (≈ ±15% scale error)
POSE_DET_MIN = 90.0     # % of frames with a pose, whole clip
WINDOW_DET_MIN = 85.0   # % of frames with a pose, take-off→landing window
SCALE_CV_MAX = 0.25     # limb-length scale spread; above this the scale is guesswork
SLOWMO_R2_MIN = 0.95    # vertical-parabola fit quality required to trust the slow-mo k

# Metrics whose VALUE is proportional to the pixel→meter scale. When the scale is
# unverified these must not be quoted; every other metric (angles, times, frames)
# is scale-invariant and stays. Keep this set in sync with any new metric.
ABSOLUTE_METRIC_KEYS = frozenset({
    "takeoff_speed_mps", "takeoff_vx_mps", "takeoff_vy_mps",
    "estimated_distance_com_m", "estimated_distance_foot_m",
    "apex_com_height_m", "landing_foot_ahead_of_com_m", "landing_foot_ahead_of_hip_m",
    "landing_followthrough_com_drift_m",
})


def _finite(x) -> bool:
    """True only for a real, finite number. None / NaN / inf / non-numeric → False."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def compute_reliability(*, det_pct, window_det_pct, scale_cv, scale_is_manual: bool,
                        g_implied, slowmo_detected: bool, slowmo_fit_r2,
                        events_reliable: bool) -> dict:
    """Decide (a) whether the landmarks were really tracked and (b) whether absolute
    lengths/speeds may be quoted. Pure function — no I/O, no globals, so the gates are
    unit-testable without a video. Any non-finite input fails closed (unreliable).

    scale_is_manual is a BOOLEAN on purpose: keying off a display string like
    "manual" lets a typo or a new scale source silently fall through to "trusted".
    """
    scale_is_manual = bool(scale_is_manual)

    # (a) tracking — did we actually see the body, over the whole clip AND in the
    # take-off→landing window (a 95% whole-clip rate can still hide a broken flight).
    cv_ok = True if scale_is_manual else (_finite(scale_cv) and scale_cv <= SCALE_CV_MAX)
    tracking_reliable = bool(
        _finite(det_pct) and det_pct >= POSE_DET_MIN
        and _finite(window_det_pct) and window_det_pct >= WINDOW_DET_MIN
        and cv_ok
    )

    # (b) absolute scale — see the identifiability note above.
    if not (tracking_reliable and bool(events_reliable)):
        absolute_ok = False
        if not tracking_reliable:
            reason = "몸 추적이 불안정해요"
        else:
            reason = "도약·착지 검출이 불안정해요"
    elif slowmo_detected:
        # k was DERIVED from g, so g is no longer an independent check of the scale.
        # Only a user-asserted scale reduces this to one unknown — and even then the
        # numbers inherit whatever error that asserted scale carries. Say so.
        # g must still be finite: k = sqrt(9.81/g) is meaningless otherwise, and this
        # function promises to fail closed on any non-finite input.
        absolute_ok = (scale_is_manual and _finite(g_implied) and g_implied > 0
                       and _finite(slowmo_fit_r2) and slowmo_fit_r2 >= SLOWMO_R2_MIN)
        reason = ("슬로우모션 보정 — 사용자가 지정한 스케일에 의존" if absolute_ok
                  else "슬로우모션이라 크기 기준을 검증할 수 없어요")
    else:
        absolute_ok = _finite(g_implied) and abs(g_implied - G_STD) <= G_TOL
        reason = ("중력 검증 통과" if absolute_ok else
                  (f"중력 검증 {g_implied:.2f} m/s² (기준 {G_STD})" if _finite(g_implied)
                   else "중력 검증값을 구하지 못했어요"))

    return {
        "tracking_reliable": tracking_reliable,
        "absolute_metrics_reliable": bool(absolute_ok),
        "reliability_reason": reason,
    }


def scale_from_gravity(scale, g_implied):
    """The scale the flight parabola implies, given g must be 9.81. Diagnostic ONLY —
    nothing downstream consumes it, because we cannot tell a scale error from an fps
    error (see the identifiability note). Returns None when it is not computable."""
    if not (_finite(scale) and scale > 0 and _finite(g_implied) and g_implied > 0):
        return None
    return scale * g_implied / G_STD


def coaching(m: dict) -> list[str]:
    """CLI summary derived from phase feedback, never the old score heuristics."""
    if m.get("tracking_reliable") is False:
        return ["몸을 안정적으로 찾지 못했어요. 전신과 신발이 보이는 측면 영상으로 다시 촬영해 주세요."]
    phases = m.get("phase_feedback") or {}
    if not phases:
        return ["구간별 시점 자료가 없습니다. 원영상을 다시 분석해 주세요."]
    return [f"{p['label']}: {text}" for p in phases.values()
            for text in p.get("observations", []) + p.get("cues", []) + p.get("notes", [])]


def _rolling_percentile(x, win, q):
    T = len(x)
    out = np.empty(T)
    for i in range(T):
        lo, hi = max(0, i - win), min(T, i + win + 1)
        out[i] = np.nanpercentile(x[lo:hi], q)
    return out


def detect_from_pose(fps, size, norm, world, valid, slowdown=1):
    """Locate the standing-long-jump segment from pre-extracted pose. Returns
    ((start_s, end_s, confidence), info) or (None, info). Pure function (testable
    on scanned pose). Hard gates: flight 0.2-1.5s, both-feet airborne with ground
    contact before/after, foot visibility, subject size. Confidence blends
    gravity-closeness, flight-time, horizontal travel (favors broad jump), visibility."""
    if not _finite(slowdown) or not 1 <= slowdown <= 32:
        raise ValueError('슬로모션 배율은 1~32 사이로 입력하세요.')
    event_fps = fps * slowdown
    w, h = int(size[0]), int(size[1])
    norm = interpolate_gaps(norm, valid)
    world = interpolate_gaps(world, valid)
    T = len(norm)
    com = center_of_mass(norm)
    com[:, 0] = butter_lp(com[:, 0], event_fps); com[:, 1] = butter_lp(com[:, 1], event_fps)
    foot_y = np.nanmax(norm[:, FOOT_PTS, 1], axis=1)
    head_y = np.nanmin(norm[:, [L_EAR, R_EAR, NOSE], 1], axis=1)
    ankle_y = np.nanmax(norm[:, [L_ANK, R_ANK], 1], axis=1)
    subj_h = float(np.nanmedian(ankle_y - head_y))
    if not np.isfinite(subj_h) or subj_h <= 1:        # M2: joints/subject not detected
        return None, {"reason": "피사체/관절 미검출"}
    info = {"subj_frac": round(subj_h / h, 3)}

    win = max(3, int(1.0 * event_fps))
    ground = _rolling_percentile(foot_y, win, 80)
    airborne = (ground - foot_y) > (0.12 * subj_h)

    intervals = []
    i = 0
    while i < T:
        if airborne[i]:
            j = i
            while j < T and airborne[j]:
                j += 1
            intervals.append((i, j - 1)); i = j
        else:
            i += 1

    scale, _ = auto_scale_px_per_m(norm, world, ~airborne)
    cands = []
    for (s, e) in intervals:
        ft = (e - s) / event_fps
        if ft < 0.2 or ft > 1.5:
            continue
        k = max(1, int(0.1 * event_fps))
        if not (s - k >= 0 and bool((~airborne[max(0, s - k):s]).all())):
            continue
        if not (e + 1 + k <= T and bool((~airborne[e + 1:e + 1 + k]).all())):
            continue
        vis = float(np.nanmean(norm[s:e + 1, FOOT_PTS, 2]))
        if vis < 0.3:
            continue
        _, _, _, _, g = fit_takeoff_velocity(com, scale, s, e, event_fps)
        if g is None or not np.isfinite(g):           # M1: NaN gravity → far-from-9.8
            g = 19.6
        has_scale = bool(scale) and np.isfinite(scale)
        dx = abs(com[e, 0] - com[s, 0]) / scale if has_scale else 0.0
        # The foot-height threshold starts late in the ascent. Measure the arc
        # above its endpoint chord, not the remaining rise after that threshold.
        chord = np.linspace(com[s, 1], com[e, 1], e - s + 1)
        rise = float(np.max(chord - com[s:e + 1, 1])) / scale if has_scale else 0.0
        if rise < 0.08:                               # reject straight walking/drift trajectories
            continue
        conf = (0.40 * math.exp(-((g - 9.8) / 4.0) ** 2)
                + 0.15 * (1.0 if 0.3 <= ft <= 1.1 else 0.5)
                + 0.30 * min(1.0, dx / 1.5)
                + 0.15 * min(1.0, vis / 0.7))
        if not np.isfinite(conf):                     # M1: never accept NaN confidence
            continue
        cands.append((s, e, conf))

    if not cands:
        info["reason"] = "점프 구간을 찾지 못함"
        return None, info
    s, e, conf = max(cands, key=lambda c: c[2])
    if subj_h < 0.12 * h:
        info["reason"] = "피사체가 너무 작음"
        return None, info
    if conf < 0.35:
        info["reason"] = f"신뢰도 낮음({conf:.2f})"
        return None, info
    buf = int(0.7 * event_fps)
    info["confidence"] = round(conf, 2)
    start_s = max(0, s - buf) / fps
    end_s = min(T - 1, e + buf) / fps
    return (start_s, end_s, conf, s / fps, e / fps), info   # + takeoff_s, landing_s of the jump itself


def analyze_auto(video, out_prefix, model="models/pose_landmarker_heavy.task", cutoff=6.0, scale=None):
    """Auto-detect the jump segment, trim to it, and analyze using the DETECTED
    event window (no re-detection — avoids the double-work / mismatch). Falls back
    to the whole clip if no confident jump is found. Returns (metrics, artifacts)."""
    import subprocess
    fps, size, norm, world, valid = extract_pose(video, model)

    # Pre-crop: if the subject is only a small band of the frame (e.g. a landscape clip
    # letterboxed into a vertical 'shorts' canvas), crop to it and re-extract pose so the
    # subject is large/clear. Re-extraction makes coords + px->m scale self-consistent in
    # the cropped frame. Normally-framed clips return crop=None and are untouched.
    analysis_video = video
    cropped = None
    crop = _subject_crop_box(norm, valid, size)
    if crop is not None:
        cx, cy, cw, ch = crop
        cropped = str(Path(out_prefix).with_name(Path(out_prefix).name + "_crop.mp4"))
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
                            "-vf", f"crop={cw}:{ch}:{cx}:{cy}",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", cropped], check=True)
            print(f"[auto] subject fills only part of the frame; cropped to {cw}x{ch} at ({cx},{cy}) and re-extracting pose")
            analysis_video = cropped
            fps, size, norm, world, valid = extract_pose(cropped, model)
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as ex:
            print(f"[auto] crop failed ({ex}); analyzing original frame")
            if cropped and Path(cropped).exists():
                try:
                    Path(cropped).unlink()
                except OSError:
                    pass
            cropped, analysis_video = None, video
    crop_box = crop if cropped else None

    res, info = detect_from_pose(fps, size, norm, world, valid)
    trimmed = None
    try:
        events = None
        if res is not None:
            s, e, conf, to_s, ld_s = res
            trimmed = str(Path(out_prefix).with_name(Path(out_prefix).name + "_trim.mp4"))
            try:
                # H2: -ss/-to AFTER -i = frame-accurate; -r forces fps == original (no drift)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", analysis_video,
                                "-ss", f"{s:.3f}", "-to", f"{e:.3f}", "-r", f"{fps:.4f}", "-vsync", "cfr",
                                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", trimmed], check=True)
                events = (to_s - s, ld_s - s)
            except (subprocess.SubprocessError, FileNotFoundError, OSError) as ex:  # H3
                print(f"[auto] trim failed ({ex}); analyzing whole clip")
                res = None
        if res is not None:
            print(f"[auto] jump at {s:.1f}-{e:.1f}s (conf {conf:.2f}); analyzing trimmed segment")
            metrics, artifacts = run_analysis(trimmed, out_prefix, model=model, cutoff=cutoff,
                                              scale=scale, events_sec=events)
            metrics["auto_trim"] = {"start_s": round(s, 2), "end_s": round(e, 2), "confidence": round(conf, 2)}
        else:
            print(f"[auto] no jump segment found ({info.get('reason','')}); analyzing whole clip")
            metrics, artifacts = run_analysis(analysis_video, out_prefix, model=model, cutoff=cutoff, scale=scale)
            metrics["auto_trim"] = None
            metrics.setdefault("detection_warnings", []).insert(0, f"자동 점프 검출 실패({info.get('reason','')}) — 전체 영상 분석")
        metrics["auto_crop"] = ({"x": crop_box[0], "y": crop_box[1], "w": crop_box[2], "h": crop_box[3]}
                                if crop_box else None)
        Path(artifacts["summary"]).write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
        return metrics, artifacts
    finally:                                          # H3: never leak the temp trim/crop files
        for tmp in (trimmed, cropped):
            if tmp and Path(tmp).exists():
                try:
                    Path(tmp).unlink()
                except OSError:
                    pass


def run_analysis(video, out_prefix, model="models/pose_landmarker_heavy.task", cutoff=6.0, scale=None, events_sec=None, pose_data=None, quality_window=None, slowdown=1):
    """Run the full analysis pipeline. Returns (metrics dict, artifacts dict of
    output file paths). Reused by both the CLI (main) and the web app. The
    processing body below is unchanged from the CLI version — `args` is just a
    namespace so that body needs no edits. If `events_sec=(takeoff_s, landing_s)`
    is given, those events are used directly instead of auto-detection."""
    from types import SimpleNamespace
    args = SimpleNamespace(video=video, out=out_prefix, model=model, cutoff=cutoff, scale_px_per_m=scale)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] Pose extraction: {args.video}")
    fps, size, norm, world, valid = pose_data if pose_data is not None else extract_pose(args.video, args.model)
    event_fps = fps * slowdown
    w, h = size
    T = len(norm)
    quality_lo, quality_hi = quality_window if quality_window is not None else (0,T)
    if not (0 <= quality_lo < quality_hi <= T):
        raise ValueError('점프 품질 검사 구간을 확인하세요.')
    det = valid.mean() * 100
    print(f"      {T} frames @ {fps:.1f} fps, {w}x{h}, pose detected on {det:.0f}% of frames")
    if valid.sum() < 5:
        raise SystemExit("영상에서 사람 자세를 거의 찾지 못했어요. 측면에서 전신이 보이게 다시 촬영해 주세요.")

    if events_sec is None:
        detected, _ = detect_from_pose(fps, size, norm, world, valid, slowdown=slowdown)
        if detected is None:
            raise ValueError('점프 구간을 찾지 못했습니다. 도약과 착지가 포함된 영상인지 확인하세요.')
        events_sec = (detected[3], detected[4])
        if quality_window is None:
            quality_lo = max(0, round(detected[0] * fps))
            quality_hi = min(T, round(detected[1] * fps) + 1)

    observed_norm, observed_world = norm.copy(), world.copy()
    raw_vis = norm[:, :, 2].copy()        # per-landmark visibility BEFORE interpolation fills gaps
    norm = interpolate_gaps(norm, valid)
    world = interpolate_gaps(world, valid)
    t = np.arange(T) / fps

    print("[2/5] Kinematics: CoM, joint angles, filtering")
    side = primary_side(norm[quality_lo:quality_hi])
    HIP, KNE, ANK = (L_HIP, L_KNE, L_ANK) if side == "L" else (R_HIP, R_KNE, R_ANK)
    SHO, ELB, TOE = (L_SHO, L_ELB, L_TOE) if side == "L" else (R_SHO, R_ELB, R_TOE)

    frame_com = center_of_mass(norm)
    com = frame_com.copy()
    com[:, 0] = butter_lp(com[:, 0], event_fps, args.cutoff)
    com[:, 1] = butter_lp(com[:, 1], event_fps, args.cutoff)

    knee_raw = joint_angle(world, HIP, KNE, ANK)
    hip_raw = joint_angle(world, SHO, HIP, KNE)
    ankle_raw = joint_angle(world, KNE, ANK, TOE)
    arm_raw = joint_angle(world, HIP, SHO, ELB)
    knee = butter_lp(knee_raw, event_fps, args.cutoff)
    hip = butter_lp(hip_raw, event_fps, args.cutoff)
    ankle = butter_lp(ankle_raw, event_fps, args.cutoff)
    arm = butter_lp(arm_raw, event_fps, args.cutoff)

    if events_sec is not None:
        to_s, ld_s = events_sec
        takeoff = max(0, min(T - 1, int(round(to_s * fps))))
        landing = min(T - 1, max(takeoff + 1, int(round(ld_s * fps))))
        apex = takeoff                                # H1: defined even if landing clamps to takeoff
        ground = float(np.nanpercentile(np.nanmax(norm[:, FOOT_PTS, 1], axis=1), 85))
        airborne = np.zeros(T, dtype=bool); airborne[takeoff:landing + 1] = True
        print(f"[3/5] Using pre-detected jump window (frames {takeoff}-{landing})")
    else:
        print("[3/5] Event detection: takeoff / apex / landing")
        takeoff, apex, landing, ground, airborne = detect_events(com, norm, h)
    # apex must lie strictly inside the flight phase
    if landing > takeoff:
        apex = takeoff + int(np.argmin(com[takeoff:landing + 1, 1]))
    # Refine the candidate event window against shoe contact, before deriving any metrics.
    contact = refine_contact_events(norm, raw_vis, valid, frame_com, event_fps, size,
                                    takeoff, apex, landing, video=args.video)
    takeoff, landing = contact["takeoff_frame"], contact["landing_frame"]
    quality_lo = min(quality_lo, contact['pre_takeoff_frame'])
    quality_hi = max(quality_hi, landing+1)
    apex = takeoff + int(np.nanargmin(com[takeoff:landing + 1, 1]))
    airborne = np.zeros(T, dtype=bool)
    airborne[takeoff:landing] = True
    # focus window around the jump for plotting + prep-phase metrics
    pad = int(round(0.8 * event_fps))
    lo, hi = max(0, takeoff - pad), min(T, landing + pad)
    prep0 = preparation_start(takeoff, fps, slowdown=slowdown)
    # Pixel→meter scale: manual override, else approximate auto-scale.
    scale_cv = float("nan")
    if args.scale_px_per_m:
        scale = float(args.scale_px_per_m)
        scale_src = "manual"
    else:
        standing = ~airborne
        standing[:quality_lo] = False
        standing[quality_hi:] = False
        scale, scale_cv = auto_scale_px_per_m(norm, world, standing)
        scale_src = "auto(limb)"
    if not math.isfinite(scale) or scale <= 0:
        raise SystemExit("영상 속 크기를 가늠하지 못했어요. 측면에서 전신이 보이는 영상으로 다시 시도해 주세요.")

    # Detection sanity: warn (don't crash) on implausible event geometry.
    warnings = []
    air_frac = (landing - takeoff) / T
    if landing <= takeoff or (landing - takeoff) < 2:
        warnings.append("flight phase not found / too short — events unreliable")
    if air_frac > 0.5:
        warnings.append(f"airborne fraction {air_frac:.0%} implausibly large — likely a trimmed/panning clip")
    flight_time = (landing - takeoff) / fps
    if not (0.2 <= flight_time <= 1.2):
        warnings.append(f"flight time {flight_time:.2f}s outside the typical 0.2–1.2s — check the clip/events")
    for w in warnings:
        print(f"      [warn] {w}")

    print(f"      takeoff=f{takeoff} apex=f{apex} landing=f{landing}; "
          f"scale={scale:.1f} px/m ({scale_src}, cv={scale_cv:.2f})")

    # Take-off velocity from the flight-phase CoM parabola (robust to ±1-frame
    # takeoff error and to contact-boundary noise).
    takeoff_speed, takeoff_angle, vfx, vfy, g_implied = fit_takeoff_velocity(com, scale, takeoff, landing, fps)
    raw_speed = takeoff_speed
    fit_t = np.arange(takeoff, landing + 1) / fps
    fit_y = com[takeoff:landing + 1, 1]
    trajectory_r2 = None
    if len(fit_y) >= 4 and np.isfinite(fit_y).all():
        fitted = np.polyval(np.polyfit(fit_t - fit_t[0], fit_y, 2), fit_t - fit_t[0])
        variance = float(np.sum((fit_y - fit_y.mean()) ** 2))
        trajectory_r2 = 1 - float(np.sum((fit_y - fitted) ** 2)) / variance if variance > 1e-9 else None

    # --- Slow-motion auto-correction --------------------------------------------------
    # A real jump filmed in slow motion only corrupts TIME-derivative metrics (g, speed,
    # flight time); spatial ones (distance, all angles, form score) are unaffected. Slow-mo
    # makes the container fps understate the true fps by a factor k, so g_implied ≈ 9.81/k².
    # We detect it CONSERVATIVELY and rescale the time-metrics by k. This is NOT a free pass:
    # we require an implausibly low g AND a long flight AND a CLEAN vertical parabola (R²≥0.95,
    # real CoM rise) AND a sane k — so pose noise / mis-detected events / camera pans (low g but
    # messy fit) are NOT "corrected" into false confidence. events_reliable_raw stays honest;
    # the slow-mo path's trust rests on fit quality + sane k, not on the (forced-to-9.81) g.
    g_raw = g_implied
    events_reliable_raw = bool(g_implied is not None and np.isfinite(g_implied) and 7.0 <= g_implied <= 12.0)
    slowmo_detected = False
    slowmo_factor = None
    slowmo_fit_r2 = None      # parabola R² behind the slow-mo k; exposed so the gate is auditable
    if (g_implied is not None and np.isfinite(g_implied) and 0.0 < g_implied < 3.0
            and flight_time > 1.5 and np.isfinite(vfy) and vfy > 0):
        idx = np.arange(takeoff, landing + 1)
        if len(idx) >= 4:
            tt = (idx - takeoff) / fps
            y_m = (com[takeoff, 1] - com[idx, 1]) / scale          # CoM rise (m), up positive
            coef = np.polyfit(tt, y_m, 2)
            ss_res = float(np.sum((y_m - np.polyval(coef, tt)) ** 2))
            ss_tot = float(np.sum((y_m - y_m.mean()) ** 2))
            r2 = (1.0 - ss_res / ss_tot) if ss_tot > 1e-9 else 0.0
            apex_rise = float(np.max(y_m))
            k = math.sqrt(9.81 / g_implied)
            slowmo_fit_r2 = r2
            if r2 >= SLOWMO_R2_MIN and 2.0 <= k <= 16.0 and apex_rise >= 0.10:
                slowmo_detected = True
                slowmo_factor = k
                takeoff_speed *= k; vfx *= k; vfy *= k
                g_implied = g_implied * k * k          # ≈ 9.81 by construction
                flight_time = flight_time / k
                warnings[:] = [w for w in warnings if "flight time" not in w.lower()]
                print(f"      [slowmo] ~{k:.1f}x slow motion (R²={r2:.2f}); rescaled speed/g/flight to real-time")

    com_x_to, com_x_la = frame_com[takeoff, 0], frame_com[landing, 0]
    dist_com = abs(com_x_la - com_x_to) / scale
    last_support = contact["pre_takeoff_frame"]
    toe_to = norm[last_support, TOE, 0]
    contact_point = contact.get("contact_point_px")
    dist_foot = (abs(contact_point[0] - toe_to) / scale
                 if contact.get("reliable") and contact_point is not None else None)
    apex_height = (com[takeoff, 1] - com[apex, 1]) / scale

    def rnd(x, n):
        return round(float(x), n) if x is not None and np.isfinite(x) else None

    land_end = min(T, landing + int(round(0.6 * event_fps)))
    fwd = float(np.sign(com_x_la - com_x_to))

    # Slow motion expands video time: preserve the same physical preparation window.
    preparation_factor = slowdown if slowdown > 1 else (slowmo_factor or 1)
    prep0 = preparation_start(takeoff, fps, slowdown=preparation_factor)
    pad = int(round(.8 * fps * preparation_factor))
    lo, hi = max(0, takeoff - pad), min(T, landing + pad)

    # --- Reliability gates for the prep-phase metrics (mirror the landing gate) ---
    # Each gate requires ALL landmarks the angle is built from to be really tracked (pre-interp),
    # so a hallucinated joint can't produce a confident-but-wrong score.
    # Event sanity: a physically-impossible implied gravity (g_implied way off 9.81) means takeoff/
    # apex/landing were mis-detected, so EVERY event-windowed metric (cm, arm, extension, angle, speed)
    # is garbage even when the landmarks themselves were visible. Gate them all on this.
    # Real-time clips: trust the raw gravity check. Slow-mo clips: g was rescaled to ~9.81,
    # so reliability rests on the slow-mo gates (clean parabola + sane k) instead.
    events_reliable = bool(events_reliable_raw or slowmo_detected)
    cm_window_full = (takeoff - prep0) >= int(round(0.5 * event_fps))
    cm_reliable = bool(events_reliable and cm_window_full and _vis_ok(raw_vis, [HIP, KNE, ANK], prep0, takeoff + 1, 0.6))
    arm_reliable = bool(events_reliable and _vis_ok(raw_vis, [HIP, SHO, ELB], prep0, apex + 1, 0.6))

    # Distance trust: g_implied should be ~9.81; since g_implied ∝ 1/scale, (9.81/g − 1) is the
    # rough fractional distance error. Named "consistency" because g also reflects event-timing /
    # CoM / perspective errors, not scale alone. Surfaced to the user, never silently trusted.
    dist_bias_pct = ((9.81 / g_implied - 1.0) * 100.0
                     if (g_implied is not None and np.isfinite(g_implied) and g_implied > 1.0) else None)

    # --- Detection rate INSIDE the take-off→landing window ------------------------------
    # A 95% whole-clip rate can still hide a shredded flight phase, which is exactly the
    # window every event-based metric is built from. min/max + clamping absorb reversed or
    # out-of-range events; an empty window yields NaN, which fails the gate closed.
    _lo = max(0, min(takeoff, landing) - 2)
    _hi = min(T, max(takeoff, landing) + 3)
    window_det = (100.0 * float(np.mean(valid[_lo:_hi]))) if _hi > _lo else float("nan")

    quality_det = float(valid[quality_lo:quality_hi].mean()*100)
    rel = compute_reliability(
        det_pct=quality_det,
        window_det_pct=window_det,
        scale_cv=scale_cv,
        scale_is_manual=(scale_src == "manual"),
        g_implied=g_implied,
        slowmo_detected=slowmo_detected,
        slowmo_fit_r2=slowmo_fit_r2,
        events_reliable=events_reliable,
    )
    if not rel["absolute_metrics_reliable"]:
        print(f"      [gate] 절대 수치(속도·거리·높이) 숨김 — {rel['reliability_reason']}")

    metrics = {
        "fps": rnd(fps, 2),
        "frames": T,
        "pose_detection_pct": rnd(det, 1),
        "quality_detection_pct": rnd(quality_det, 1),
        "quality_window_frames": [int(quality_lo),int(quality_hi)],
        "primary_side": side,
        "scale_px_per_m": rnd(scale, 1),
        "scale_source": scale_src,
        "scale_uncertainty_cv": rnd(scale_cv, 3),
        "detection_warnings": warnings,
        "takeoff_frame": int(takeoff), "apex_frame": int(apex), "landing_frame": int(landing),
        "flight_time_s": rnd(flight_time, 3),
        "flight_gravity_check_mps2": rnd(g_implied, 2),    # should be ≈9.8 if scale & events are sound
        "apex_com_height_m": rnd(apex_height, 3),
        "takeoff_angle_deg": rnd(takeoff_angle, 1),
        "takeoff_speed_mps": rnd(takeoff_speed, 2),
        "takeoff_vx_mps": rnd(vfx, 2), "takeoff_vy_mps": rnd(vfy, 2),
        "estimated_distance_com_m": rnd(dist_com, 2),
        "estimated_distance_foot_m": rnd(dist_foot, 2),
        "countermovement_knee_min_deg": rnd(np.nanmin(knee[prep0:takeoff + 1]), 1),
        "knee_angle_at_takeoff_deg": rnd(knee_raw[last_support], 1),
        "hip_angle_at_takeoff_deg": rnd(hip_raw[last_support], 1),
        "ankle_angle_at_takeoff_deg": rnd(ankle_raw[last_support], 1),
        "arm_swing_range_deg": rnd(np.nanmax(arm[prep0:apex + 1]) - np.nanmin(arm[prep0:apex + 1]), 1),
        "landing_knee_min_deg": rnd(np.nanmin(knee[landing:land_end]), 1) if land_end > landing else None,
        "knee_angle_at_landing_deg": rnd(knee_raw[landing], 1),     # leg reach at first contact
        "countermovement_reliable": cm_reliable,                      # knee/hip/ankle really tracked in prep
        "arm_swing_reliable": arm_reliable,                           # hip/sho/elbow really tracked in prep..apex
        "events_reliable": events_reliable,                           # takeoff/apex/landing physically plausible
        "events_reliable_raw": events_reliable_raw,                   # raw g∈[7,12], before any slow-mo rescale
        # --- Reliability gates (added; nothing above was removed) ---
        "window_detection_pct": rnd(window_det, 1),                   # detection rate over takeoff→landing
        "tracking_reliable": rel["tracking_reliable"],                # landmarks really followed the body
        "absolute_metrics_reliable": rel["absolute_metrics_reliable"],# may we quote speed/distance/height?
        "reliability_reason": rel["reliability_reason"],              # human-readable why
        "scale_from_gravity_px_per_m": rnd(scale_from_gravity(scale, g_implied), 1),  # diagnostic only
        "slowmo_fit_r2": rnd(slowmo_fit_r2, 3),                       # audit trail for the slow-mo k
        "slowmo_detected": slowmo_detected,                           # real jump filmed in slow motion
        "slowmo_factor": rnd(slowmo_factor, 1) if slowmo_factor else None,   # estimated slowdown k
        "flight_gravity_check_raw_mps2": rnd(g_raw, 2),               # g before slow-mo rescale (diagnostic)
        "distance_consistency_bias_pct": rnd(dist_bias_pct, 1),       # ~ |distance error| from gravity check
    }
    metrics["jump_direction"] = fwd
    metrics["pre_takeoff_frame"] = int(last_support)
    metrics["landing_event"] = contact
    frames = select_moment_frames(norm, raw_vis, valid, frame_com, takeoff, apex, landing, prep0, side=side)
    phase_data = build_phase_analysis(metrics, norm, raw_vis, valid, frame_com,
                                     knee_raw, hip_raw, ankle_raw, arm_raw, frames, contact, prep_start=prep0)
    metrics.update(phase_data["landing_metrics"])
    metrics["moments"] = phase_data["moments"]
    metrics["phase_feedback"] = phase_data["phase_feedback"]
    metrics["coaching"] = coaching(metrics)
    metrics["photo_feedback"] = photo_feedback(metrics)

    def observed_at(frame, ids):
        return bool(frame is not None and valid[frame] and np.isfinite(observed_norm[frame, ids]).all()
                    and np.isfinite(observed_world[frame, ids]).all()
                    and (raw_vis[frame, ids] >= .6).all())

    load = frames.get('load')
    trunk = norm[load, SHO, :2] - norm[load, HIP, :2] if load is not None else None
    raw_values = {
        'flight_time_video_s': (landing - takeoff) / fps,
        'takeoff_speed_video_mps': raw_speed, 'takeoff_angle_deg': takeoff_angle,
        'trajectory_r2': trajectory_r2,
        'landing_knee_angle_deg': knee_raw[landing],
        'knee_angle_change_apex_to_contact_deg': knee_raw[landing] - knee_raw[apex],
        'load_trunk_angle_deg': math.degrees(math.atan2(-trunk[1], abs(trunk[0]))) if trunk is not None else None,
        'load_knee_angle_deg': knee_raw[load] if load is not None else None,
    }
    if contact_point is not None:
        raw_values['landing_foot_ahead_of_hip_m'] = (contact_point[0] - norm[landing, HIP, 0]) * fwd / scale
        raw_values['landing_foot_ahead_of_com_m'] = (contact_point[0] - frame_com[landing, 0]) * fwd / scale
    metrics['raw_measurements'] = {k: float(v) if v is not None and np.isfinite(v) else None for k, v in raw_values.items()}
    com_ids = [7,8,11,12,13,14,15,16,23,24,25,26,27,28,29,30,31,32]
    primary_ids = ([8,12,14,16,24,26,28,30,32] if side == 'R'
                   else [7,11,13,15,23,25,27,29,31])
    metrics['raw_measurements'].update(
        landing_joints_visible=observed_at(landing, [HIP,KNE,ANK]),
        apex_joints_visible=observed_at(apex, [HIP,KNE,ANK]),
        load_joints_visible=observed_at(load, [SHO,HIP,KNE,ANK]),
        com_visible=observed_at(landing, com_ids),
        flight_primary_visible=all(observed_at(i,primary_ids) for i in range(takeoff,landing+1)),
        flight_com_visible=all(observed_at(i,com_ids) for i in range(takeoff,landing+1)))
    np.savez_compressed(out.with_suffix('.pose.npz'), norm=observed_norm, world=observed_world,
                        valid=valid, com=frame_com, fps=fps, size=size)

    print("[4/5] Rendering annotated video + plots + key-moment screenshots")
    render_video(args.video, out.with_suffix(".annotated.mp4"), norm, valid, com, knee, hip,
                 takeoff, apex, landing, fps, size, raw_vis=raw_vis)
    render_plots(out.with_suffix(".plots.png"), t, knee, hip, ankle, arm, com, scale,
                 takeoff, apex, landing, fps, lo, hi)
    shots = save_event_frames(args.video, out, {k: v for k, v in frames.items() if v is not None}, T)
    if shots.get("landing"):
        annotate_landing_reach(shots["landing"], metrics, size)

    print("[5/5] Writing summary")
    out.with_suffix(".summary.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")

    artifacts = {
        "annotated": str(out.with_suffix(".annotated.mp4")),
        "plots": str(out.with_suffix(".plots.png")),
        "summary": str(out.with_suffix(".summary.json")),
        "pose": str(out.with_suffix('.pose.npz')),
    }
    artifacts.update(shots)   # takeoff / mid / landing screenshot paths (only those written)
    return metrics, artifacts


def main():
    ap = argparse.ArgumentParser(description="Standing long jump video analysis (demo)")
    ap.add_argument("video")
    ap.add_argument("--model", default="models/pose_landmarker_heavy.task")
    ap.add_argument("--out", default="out/jump")
    ap.add_argument("--cutoff", type=float, default=6.0, help="Butterworth low-pass cutoff (Hz)")
    ap.add_argument("--scale-px-per-m", type=float, default=None,
                    help="Manual pixel→meter scale (overrides the approximate auto-scale)")
    ap.add_argument("--auto", action="store_true",
                    help="Auto-detect & trim the jump segment before analyzing (for long clips)")
    args = ap.parse_args()
    fn = analyze_auto if args.auto else run_analysis
    metrics, artifacts = fn(args.video, args.out, model=args.model,
                            cutoff=args.cutoff, scale=args.scale_px_per_m)

    print("\n===== JUMP ANALYSIS =====")
    for k, v in metrics.items():
        if k == "coaching":
            continue
        print(f"  {k:32s}: {v}")
    print("  --- 코칭 피드백 ---")
    for c in metrics["coaching"]:
        print(f"   • {c}")
    print(f"\nArtifacts: {artifacts['annotated']} | {artifacts['plots']} | {artifacts['summary']}")


if __name__ == "__main__":
    main()
