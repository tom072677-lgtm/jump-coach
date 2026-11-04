# PLAN — Multi-video comparison & coaching (≤5 clips)

## Goal
User uploads 2–5 jumps (same athlete, similar camera setup). The app analyzes
each, then COMPARES them: which had the best record (distance), which had the
best FORM SCORE, what problems are COMMON across all clips, per-clip strengths/
weaknesses, and an overall coaching summary that prioritizes the common problems.

Crucially: **distance (record) and form score are SEPARATE.** A lower-distance clip
can earn a higher form score, and the UI must explain WHY (so the user isn't
confused) — because form score = repeatability / potential, distance = that day's
realized result.

## Why relative comparison is valid without absolute scale
Same athlete + similar camera setup → scale error cancels across clips. The
comparison relies on scale-free metrics (joint angles, arm-swing range,
countermovement depth, symmetry, triple-extension timing) and flight time (exact
from frame count). Absolute distance stays approximate and is shown as such.

## Form score (0–100) — distance-independent
Weighted blend of posture/technique quality, NOT distance:
- takeoff speed (relative, vs the athlete's own best in the set)   ~25%
- takeoff angle within ~20–38° flat optimum                         ~15%
- countermovement depth (~90° knee)                                 ~10%
- triple-extension / hip-knee-ankle extension at takeoff            ~15%
- arm-swing range (bigger, synchronized)                            ~15%
- landing knee flexion (brings heels forward, absorbs)              ~10%
- left/right symmetry where observable                              ~10%
Each sub-metric → 0–100 via distance-to-ideal-range; weighted sum = form score.
Distance is reported ALONGSIDE but is NOT in the score (per user's philosophy).
Show a one-line rationale per sub-metric (why this clip scored what it did).

## Comparison outputs
- Table: clip | distance(approx) | form score | each sub-metric.
- "최고 기록" (max distance) and "최고 자세 점수" (max form) — explicitly may differ;
  when they differ, generate the "낮은 기록·높은 점수" explanation.
- Common problems: sub-metrics below threshold in MOST/ALL clips → top fix priority.
- Per-clip: 1 strength + 1 weakness.
- Overall coaching: common problems first, then per-clip notes.

## Integration
- web_app.py: new `POST /compare` accepting up to 5 files (reuse validation,
  UUID dirs). Run analyze_auto on each (sequential; they're independent so could
  parallelize later). Build comparison from the metrics dicts.
- New `compare.py` (pure functions): `form_score(metrics)->(score, breakdown)`,
  `compare(list_of_metrics)->report`. Testable on saved summary.json files.
- New template `compare.html` (or extend index): multi-file picker, results table,
  best-record vs best-form callout with explanation, common-problems list, per-clip
  cards, each clip's trimmed annotated video.
- Keep single-clip `/analyze` unchanged.

## Edge cases
- 1 clip uploaded → just single analysis (no comparison) or prompt for ≥2.
- A clip fails analysis (no jump / too small) → exclude from comparison, list it
  as "분석 실패" with reason; still compare the rest.
- Different camera setups (scale cv differs a lot between clips) → warn that
  absolute-distance comparison is unreliable, but form metrics still comparable.
- All clips fail → clear message.
- Mixed: some auto-trimmed, some whole-clip — fine, metrics are per-jump.
- Very different fps clips → metrics are normalized (deg, seconds) so OK.
- Form score needs ≥1 valid sub-metric; guard None/NaN sub-metrics (skip & renormalize weights).

## Success criteria (verify)
- [ ] `compare.py` form_score + compare are pure & tested on summary.json files.
- [ ] /compare with 2–3 clips returns a table, best-record vs best-form, common
      problems, per-clip cards, and the "low record / high score" explanation when
      they diverge.
- [ ] Failing clip excluded gracefully, others still compared.
- [ ] Single /analyze path unchanged (no regression).
- [ ] UI clearly separates 기록(거리) and 자세 점수 with per-metric rationale.

## Review revisions (codex — needs revision → applied)
- **Drop takeoff speed from form score** (it tracks distance → would re-create the
  confusion). Form score = pure technique: angle, countermovement depth, triple
  extension, arm swing, landing flexion. Speed/distance shown separately.
- **Exact metric keys** from summary.json: takeoff_angle_deg,
  countermovement_knee_min_deg, knee_angle_at_takeoff_deg, hip_angle_at_takeoff_deg,
  arm_swing_range_deg, landing_knee_min_deg, estimated_distance_foot_m,
  scale_uncertainty_cv, pose_detection_pct, flight_gravity_check_mps2.
- **Common problem rule**: a sub-metric is common if "개선필요" in `>= ceil(0.6*n)` clips.
- **Ties**: best_record / best_form = first on tie (deterministic).
- **Coverage/confidence**: form score needs ≥3 valid sub-metrics else flagged low-confidence.
- **Distance-comparability warning** (concrete): warn if `max(scale_cv)-min(scale_cv) > 0.3`
  across clips, or any clip's pose_detection_pct < 80.
- **Same-athlete is user-provided context** — note it, don't claim to verify.
- **Partial success**: failed clips excluded + listed with reason; compare the rest;
  if <1 valid → clear message.
- **Band labels** (우수/양호/개선필요) alongside 0-100 per metric.
- v1 backend-first (compare.py pure fns) tested on summary.json; UI = extend index
  with multiple-file input + a comparison results block.

## Perfect-mode
- Implement compare.py as pure functions; unit-check on several real summary.json.
- Adversarial review of the scoring (gaming, ties, NaN, single-metric dominance).
- Verify on real multi-clip set (reuse available clips as stand-ins for one athlete).
