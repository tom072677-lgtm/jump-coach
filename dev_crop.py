"""Experiment: does cropping to the person + upscaling improve pose tracking on a
small-subject clip? Finds the broad-jump interval, crops to the person's bbox over
that window, upscales, writes a cropped clip for re-analysis."""
import sys
import subprocess
import numpy as np
import analyze_jump as A

npz, src = sys.argv[1], sys.argv[2]
d = np.load(npz)
fps = float(d["fps"]); w, h = int(d["size"][0]), int(d["size"][1])
norm = A.interpolate_gaps(d["norm"], d["valid"])
world = A.interpolate_gaps(d["world"], d["valid"])
T = len(norm)
com = A.center_of_mass(norm)
com[:, 0] = A.butter_lp(com[:, 0], fps); com[:, 1] = A.butter_lp(com[:, 1], fps)
foot_y = np.nanmax(norm[:, A.FOOT_PTS, 1], axis=1)
head_y = np.nanmin(norm[:, [A.L_EAR, A.R_EAR, A.NOSE], 1], axis=1)
ankle_y = np.nanmax(norm[:, [A.L_ANK, A.R_ANK], 1], axis=1)
subj_h = float(np.nanmedian(ankle_y - head_y))


def rp(x, win, q):
    return np.array([np.nanpercentile(x[max(0, i - win):i + win + 1], q) for i in range(len(x))])


win = max(3, int(1.0 * fps))
ground = rp(foot_y, win, 80)
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
scale, _ = A.auto_scale_px_per_m(norm, world, ~airborne)

best = None
for (s, e) in intervals:
    ft = (e - s) / fps
    if ft < 0.2 or ft > 1.5:
        continue
    dx = abs(com[e, 0] - com[s, 0]) / scale if (scale and np.isfinite(scale)) else 0.0
    if best is None or dx > best[2]:
        best = (s, e, dx)

print(f"subj_h={subj_h:.0f}px ({subj_h/h*100:.0f}%)  scale={scale:.1f}  best_jump={best}")
if best is None:
    print("no jump interval"); sys.exit(0)

s, e, dx = best
b = int(0.7 * fps)
s2, e2 = max(0, s - b), min(T - 1, e + b)
xs = norm[s2:e2 + 1, :, 0]; ys = norm[s2:e2 + 1, :, 1]
x0, x1 = float(np.nanmin(xs)), float(np.nanmax(xs))
y0, y1 = float(np.nanmin(ys)), float(np.nanmax(ys))
bw, bh = x1 - x0, y1 - y0
pad = 0.18
x0 = max(0, x0 - pad * bw); x1 = min(w, x1 + pad * bw)
y0 = max(0, y0 - pad * bh); y1 = min(h, y1 + pad * bh)
cw, ch = int(x1 - x0), int(y1 - y0)
ts, te = s2 / fps, e2 / fps
print(f"crop {cw}x{ch} @ ({int(x0)},{int(y0)})  window {ts:.1f}-{te:.1f}s  (person was {subj_h:.0f}px tall, "
      f"crop makes it ~{960*subj_h/ch:.0f}px after upscale)")

out = "samples/shorts_crop.mp4"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ss", f"{ts:.3f}", "-to", f"{te:.3f}",
                "-vf", f"crop={cw}:{ch}:{int(x0)}:{int(y0)},scale=-2:960", "-r", f"{fps:.4f}", "-an", out],
               check=True)
print(f"wrote {out}")
