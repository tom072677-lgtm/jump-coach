"""Auto jump-segment detection (dev/diagnostic). Pure function detect_from_pose()
runs on pre-scanned pose so it's testable without re-decoding video.
Gravity is a confidence signal, NOT a hard gate (codex review)."""
import sys
import math
import numpy as np
import analyze_jump as A


def rolling_percentile(x, win, q):
    T = len(x)
    out = np.empty(T)
    for i in range(T):
        lo, hi = max(0, i - win), min(T, i + win + 1)
        out[i] = np.nanpercentile(x[lo:hi], q)
    return out


def detect_from_pose(fps, size, norm, world, valid, verbose=False):
    """Return (start_s, end_s, confidence) for the best standing-long-jump segment,
    or None if none is confident enough. Plus an info dict."""
    w, h = int(size[0]), int(size[1])
    norm = A.interpolate_gaps(norm, valid)
    world = A.interpolate_gaps(world, valid)
    T = len(norm)

    com = A.center_of_mass(norm)
    com[:, 0] = A.butter_lp(com[:, 0], fps)
    com[:, 1] = A.butter_lp(com[:, 1], fps)

    foot_y = np.nanmax(norm[:, A.FOOT_PTS, 1], axis=1)
    head_y = np.nanmin(norm[:, [A.L_EAR, A.R_EAR, A.NOSE], 1], axis=1)
    ankle_y = np.nanmax(norm[:, [A.L_ANK, A.R_ANK], 1], axis=1)
    subj_h = float(np.nanmedian(ankle_y - head_y))
    info = {"subj_h": subj_h, "subj_frac": subj_h / h, "T": T, "fps": fps}

    win = max(3, int(1.0 * fps))
    ground = rolling_percentile(foot_y, win, 80)
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

    scale, scale_cv = A.auto_scale_px_per_m(norm, world, ~airborne)
    cands = []
    for (s, e) in intervals:
        ft = (e - s) / fps
        if ft < 0.2 or ft > 1.5:
            continue
        k = max(1, int(0.1 * fps))
        pre_ground = s - k >= 0 and bool((~airborne[max(0, s - k):s]).all())
        post_ground = e + 1 + k <= T and bool((~airborne[e + 1:e + 1 + k]).all())
        if not (pre_ground and post_ground):
            continue
        vis = float(np.nanmean(norm[s:e + 1, A.FOOT_PTS, 2]))
        if vis < 0.3:
            continue
        spd, ang, vx, vy, g = A.fit_takeoff_velocity(com, scale, s, e, fps)
        g = 0.0 if g is None else g
        dx = abs(com[e, 0] - com[s, 0]) / scale if (scale and np.isfinite(scale)) else 0.0
        g_score = math.exp(-((g - 9.8) / 4.0) ** 2)        # 1 at 9.8, soft falloff
        ft_score = 1.0 if 0.3 <= ft <= 1.1 else 0.5
        dx_score = min(1.0, dx / 1.5)                       # favors broad jump over vertical
        vis_score = min(1.0, vis / 0.7)
        conf = 0.40 * g_score + 0.15 * ft_score + 0.30 * dx_score + 0.15 * vis_score
        cands.append(dict(s=s, e=e, ft=ft, g=g, dx=dx, vis=vis, conf=conf))

    if verbose:
        print(f"  subj_h={subj_h:.0f}px ({subj_h/h*100:.0f}% of frame)  scale={scale:.1f}(cv{scale_cv:.2f})  "
              f"raw_intervals={len(intervals)}  candidates={len(cands)}")
        for c in sorted(cands, key=lambda c: -c["conf"]):
            print(f"   [{c['s']/fps:5.1f}-{c['e']/fps:5.1f}s] ft={c['ft']:.2f}s g={c['g']:5.2f} "
                  f"dx={c['dx']:5.2f}m vis={c['vis']:.2f} conf={c['conf']:.2f}")

    if not cands:
        info["reason"] = "no airborne candidate"
        return None, info
    best = max(cands, key=lambda c: c["conf"])
    info["best"] = best
    if subj_h < 0.12 * h:
        info["reason"] = "subject too small"
        return None, info
    if best["conf"] < 0.35:
        info["reason"] = f"low confidence {best['conf']:.2f}"
        return None, info
    buf = int(0.7 * fps)
    return (max(0, best["s"] - buf) / fps, min(T - 1, best["e"] + buf) / fps, best["conf"]), info


if __name__ == "__main__":
    d = np.load(sys.argv[1])
    res, info = detect_from_pose(float(d["fps"]), d["size"], d["norm"], d["world"], d["valid"], verbose=True)
    print(f"  RESULT: {res}")
