"""양발 신발 접촉 후보. 포즈 좌표와 신발 바닥의 픽셀은 구별한다."""
from pathlib import Path

import cv2
import numpy as np

SHOE = np.array([29, 31, 30, 32])  # 왼발 뒤/앞, 오른발 뒤/앞; 발목 제외


def _shoe_bottom(frame, background, points):
    """고정 카메라에서 발 주변 변화 영역의 하단. 분리가 안 되면 None."""
    if not np.isfinite(points).all():
        return None
    length = float(np.linalg.norm(points[1] - points[0]))
    if length < 5:
        return None
    h, w = frame.shape[:2]
    x0 = max(0, int(points[:, 0].min() - .4 * length))
    x1 = min(w, int(points[:, 0].max() + .4 * length) + 1)
    y0 = max(0, int(points[:, 1].min() - .5 * length))
    y1 = min(h, int(points[:, 1].max() + 1.1 * length) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    diff = cv2.absdiff(frame[y0:y1, x0:x1], background[y0:y1, x0:x1])
    mask = (diff.max(axis=2) > 35).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    candidates = []
    for k in range(1, count):
        x, y, cw, ch, area = stats[k]
        if area < max(8, .04 * length * length) or cw < .3 * length:
            continue
        # A component at the ROI edge cannot establish a complete shoe boundary.
        if y + ch >= y1 - y0 - 1:
            continue
        px = points[:, 0] - x0
        py = points[:, 1] - y0
        if not np.any((px >= x - .2 * length) & (px <= x + cw + .2 * length)
                      & (py >= y - .4 * length) & (py <= y + ch + .4 * length)):
            continue
        yy, xx = np.where(labels == k)
        bottom = int(yy.max())
        near = yy >= bottom - 1
        candidates.append((area, [float(np.median(xx[near]) + x0), float(bottom + y0)]))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def _image_refinement(video, norm, visible, takeoff, start, end, floor_xy):
    """국소 발 ROI를 확인한다. 배경/지면 실패 시 확정 결과를 만들지 않는다."""
    if not video or not Path(video).is_file():
        return None
    cap = cv2.VideoCapture(str(video))
    try:
        reference = max(0, takeoff - 3)
        cap.set(cv2.CAP_PROP_POS_FRAMES, reference)
        ok, background = cap.read()
        if not ok or background.shape[1::-1] != tuple(floor_xy[2]):
            return None
        # Calibrate the visible sole/landmark offset in the last supported pose.
        # The later frame supplies empty background at the original foot position.
        cap.set(cv2.CAP_PROP_POS_FRAMES, end - 1)
        ok, later_frame = cap.read()
        if not ok:
            return None
        offsets = []
        for pair in ([29, 31], [30, 32]):
            if visible[reference, pair].all():
                bottom = _shoe_bottom(background, later_frame, norm[reference, pair, :2])
                if bottom is not None:
                    offset = bottom[1] - float(norm[reference, pair, 1].max())
                    length = np.linalg.norm(norm[reference, pair[1], :2] - norm[reference, pair[0], :2])
                    if -2 <= offset <= length:
                        offsets.append(offset)
        if not offsets:
            return None
        sole_offset = float(np.median(offsets))
        observations = []
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        for i in range(start, end):
            ok, frame = cap.read()
            if not ok:
                break
            for side, pair in (("L", [29, 31]), ("R", [30, 32])):
                if not visible[i, pair].all():
                    continue
                point = _shoe_bottom(frame, background, norm[i, pair, :2])
                if point is not None:
                    ground = floor_xy[0] * point[0] + floor_xy[1] + sole_offset
                    observations.append((i, side, point, ground - point[1]))
        # Require an observed gap before a boundary reaches the support band.
        for i, side, point, gap in observations:
            prev = [v for v in observations if v[0] == i - 1 and v[1] == side]
            later = [v for v in observations if v[0] == i + 1 and v[1] == side]
            tol = max(2., .003 * floor_xy[2][1])
            if prev and later and prev[0][3] > tol and gap <= tol and later[0][3] <= tol:
                return i, side, point
        return None
    finally:
        cap.release()


def refine_contact_events(norm, raw_vis, valid, com, fps, size,
                          takeoff, apex, landing, video=None):
    """첫 공중/최초 접촉 후보를 반환한다. fps는 분석 영상의 시간 기준이다.

    heel/toe는 신발 바닥 자체가 아니며 반환값은 자동 추정이다.
    reliable은 선명한 양발 ROI를 참고할 수 있는지의
    내부 품질 판정이며 물리적 접촉의 정답을 보장하지 않는다.
    """
    norm = np.asarray(norm, dtype=float)
    raw_vis = np.asarray(raw_vis, dtype=float)
    valid = np.asarray(valid, dtype=bool)
    T = len(norm)
    if not T:
        raise ValueError("접촉 분석에 영상 프레임이 필요합니다.")
    takeoff = int(np.clip(takeoff, 0, T - 1))
    apex = int(np.clip(apex, takeoff, T - 1))
    landing = int(np.clip(landing, apex, T - 1))
    result = {
        "takeoff_frame": takeoff, "pre_takeoff_frame": max(0, takeoff - 1),
        "landing_frame": landing, "contact_point_px": None,
        "contact_landmark": None, "contact_side": "unknown", "contact_kind": "unknown",
        "reliable": False, "observation_reliable": False, "takeoff_reliable": False,
        "takeoff_observed": False, "landing_observed": False,
        "method": "unresolved", "uncertainty_frames": None,
        "landing_frame_range": [apex, min(T - 1, landing + max(1, int(.25 * fps)))],
        "note": "신발 접촉을 확인할 원시 발 좌표가 부족해 기존 후보를 유지했어요.",
    }
    visible = (raw_vis >= .6) & valid[:, None] & np.isfinite(norm[:, :, :2]).all(axis=2)
    radius = max(3, int(round(.45 * fps)))
    pre = np.arange(max(0, takeoff - radius), max(1, takeoff))
    post = np.arange(min(T - 1, landing + 1), min(T, landing + max(3, int(.2 * fps)) + 1))
    # Two local support anchors allow a sloping camera projection. They are not
    # a calibrated floor: shoe markers sit above the actual bottom of the sole.
    anchors = []
    for frames in (pre, post):
        yy = norm[frames][:, SHOE, 1]
        xx = norm[frames][:, SHOE, 0]
        good = visible[frames][:, SHOE]
        if good.sum() < 3:
            continue
        level = float(np.percentile(yy[good], 85))
        low = good & (yy >= level - max(2, .005 * size[1]))
        anchors.append((float(np.median(xx[low])), float(np.median(yy[low]))))
    if not anchors:
        return result
    slope = 0.
    if len(anchors) == 2 and abs(anchors[1][0] - anchors[0][0]) > .05 * size[0]:
        slope = (anchors[1][1] - anchors[0][1]) / (anchors[1][0] - anchors[0][0])
    intercept = anchors[-1][1] - slope * anchors[-1][0]
    floor = np.full((T, 4), np.nan)
    for k, j in enumerate(SHOE):
        local = []
        for frames in (pre, post):
            observed = frames[visible[frames, j]]
            if len(observed) < 2:
                continue
            # Heel and toe landmarks sit at different heights above the sole.
            # Keep each marker's own supported height rather than merging them.
            local.append((float(np.median(norm[observed, j, 0])),
                          float(np.percentile(norm[observed, j, 1], 75))))
        if local:
            m = slope
            if len(local) == 2 and abs(local[1][0] - local[0][0]) > .05 * size[0]:
                m = (local[1][1] - local[0][1]) / (local[1][0] - local[0][0])
            floor[:, k] = m * (norm[:, j, 0] - local[-1][0]) + local[-1][1]
    gap = floor - norm[:, SHOE, 1]
    good = visible[:, SHOE]
    tolerance = max(2., .003 * size[1])
    # Heel/toe landmarks are inside the shoe, not on its sole. During heel raise
    # a supported toe marker can shift by several pixels (5 px on a 47 px shoe
    # in the regression clip). Use a local shoe-sized support band for takeoff,
    # rather than treating that shift as first flight. This is an empirical
    # observation tolerance, not a physical contact guarantee.
    takeoff_tolerance = np.full(4, tolerance)
    for pair in ((0, 1), (2, 3)):
        observed = pre[good[pre][:, pair].all(axis=1)]
        if len(observed) >= 2:
            lengths = np.linalg.norm(norm[observed, SHOE[pair[1]], :2]
                                     - norm[observed, SHOE[pair[0]], :2], axis=1)
            takeoff_tolerance[list(pair)] = max(tolerance, .15 * float(np.median(lengths)))
    airborne = good.any(axis=1) & (~good | (gap > takeoff_tolerance)).all(axis=1)
    # Walk back from the apex, rather than accepting the original large-height
    # threshold as takeoff. An unobserved frame is a boundary, not made-up flight.
    first_air = apex
    if airborne[apex]:
        while first_air > max(0, takeoff - radius) and airborne[first_air - 1]:
            first_air -= 1
        result["takeoff_frame"] = int(first_air)
        result["pre_takeoff_frame"] = max(0, int(first_air) - 1)
        # Both shoes must be seen on both sides of the boundary. Landing quality
        # cannot establish the independent takeoff boundary.
        lo, hi = max(0, first_air - 1), min(T, first_air + 2)
        result['takeoff_reliable'] = bool(first_air > 0 and good[lo:hi].all()
                                         and not airborne[first_air - 1]
                                         and airborne[first_air:hi].all())
        result['takeoff_observed'] = bool(first_air > 0 and hi == first_air + 2 and any(
            good[lo:hi][:, pair].all()
            and (gap[first_air - 1, list(pair)] <= takeoff_tolerance[list(pair)]).any()
            and (gap[first_air:hi][:, pair] > takeoff_tolerance[list(pair)]).all()
            for pair in ((0, 1), (2, 3))))
        result['takeoff_frame_range'] = [lo, min(T - 1, first_air + 1)]
    end = min(T, landing + radius + 1)
    chosen = None
    for i in range(apex + 1, end):
        touching = good[i] & (gap[i] <= tolerance)
        if not touching.any():
            continue
        # A subsequent frame confirms persistence, but never changes the time
        # to that later frame. A last-frame contact remains an uncertain candidate.
        if i + 1 < T and not (good[i + 1] & (gap[i + 1] <= tolerance)).any():
            continue
        indices = np.flatnonzero(touching)
        chosen = i, indices
        break
    if chosen is None:
        return result
    frame, indices = chosen
    before = frame - 1
    while before > apex and not good[before].all():
        before -= 1
    lower = max(apex + 1, before)
    if not good[apex + 1:frame + 1].all():
        lower = apex + 1  # an unseen other foot may have touched earlier
    uncertainty = max(1, frame - lower)
    observed_pairs = [pair for pair in ((0, 1), (2, 3))
        if frame > apex and frame + 1 < T
        and good[frame - 1:frame + 2][:, pair].all()
        and (gap[frame - 1, list(pair)] > tolerance).all()
        and (gap[frame, list(pair)] <= tolerance).any()
        and (gap[frame + 1, list(pair)] <= tolerance).any()]
    observed_indices = np.array([k for k in indices
        if any(k in pair for pair in observed_pairs)
        and gap[frame - 1, k] > tolerance
        and gap[frame + 1, k] <= tolerance], dtype=int)
    observed_contact = bool(len(observed_indices))
    if observed_contact:
        # The reported marker must itself move from air into persistent support;
        # another marker, even on the same shoe, cannot supply that evidence.
        indices = observed_indices
    j = int(SHOE[indices[np.argmin(gap[frame, indices])]])
    sides = {"L" if SHOE[k] in (29, 31) else "R" for k in indices}
    side = next(iter(sides)) if len(sides) == 1 else "both"
    result.update({
        "landing_frame": int(frame), "contact_point_px": norm[frame, j, :2].tolist(),
        "contact_landmark": j, "contact_side": side,
        "contact_kind": "heel" if j in (29, 30) else "toe",
        "method": "pose_estimate", "uncertainty_frames": int(uncertainty),
        "landing_observed": observed_contact,
        "landing_frame_range": [int(max(apex + 1, lower - 1)), int(min(T - 1, frame + 1))],
        "note": "양발 뒤꿈치·앞부분 좌표로 추정한 최초 접촉이에요. 신발 바닥 픽셀과 지면의 실제 접촉은 확인하지 못했어요.",
    })
    if not good[max(apex, frame - 1):min(T, frame + 2)].all():
        result["note"] += " 가려진 발이 먼저 닿았을 가능성도 있어요."
    pixel = _image_refinement(video, norm, visible, result["takeoff_frame"],
                              max(apex + 1, frame - max(2, int(.15 * fps))),
                              min(T, frame + max(3, int(.15 * fps))),
                              (slope, intercept, tuple(size)))
    if pixel is not None:
        fi, side, point = pixel
        clear = good[max(apex, fi - 1):min(T, fi + 2)].all()
        result.update({"landing_frame": int(fi), "contact_point_px": point,
                       "contact_landmark": None, "contact_side": side, "contact_kind": "sole",
                       "method": "image_roi_estimate", "uncertainty_frames": 2,
                       "observation_reliable": True,
                       "landing_observed": True,
                       "reliable": bool(clear and uncertainty <= 2 and fi + 1 < T),
                       "landing_frame_range": [max(apex + 1, fi - 2), min(T - 1, fi + 2)],
                       "note": "발 주변 영상의 하단 경계로 접촉 후보를 보완했어요. 지면 높이는 포즈 추정이므로 실제 최초 접촉과 차이가 날 수 있어요."})
        if not result["reliable"]:
            result["note"] += " 보이는 발의 최초 접촉 후보이며 반대발이 먼저 닿았는지는 확인하지 못했어요."
    return result
