# PLAN — 절대 지표 신뢰 게이트 (스케일 불일치 · 추적 품질)

## Goal (왜)

샘플 4개를 실측한 결과, **추적 품질이 나쁘거나 스케일이 틀린 클립에서도 앱이 소수점 둘째 자리까지
확신에 찬 수치 코칭을 내보낸다.** 실측 근거:

| 클립 | pose_detection_pct | scale_cv | g_implied | events_reliable | 실제 출력 |
|---|---|---|---|---|---|
| slj_00001 | 100.0 | 0.147 | **1.01** | False | ✅ 정상 차단됨 |
| slj_00002 | 100.0 | 0.088 | **11.5** | True | "이지 속도 3.41 m/s — 양호" |
| slj_00003 | 98.1 | 0.209 | 8.83 | True | "이지 속도 3.22 m/s — 양호" |
| slj_00004 | **68.5** | **0.62** | 10.85 | True | "이지 속도 4.19 m/s — 양호" |

두 가지 구멍:

1. **추적 품질이 판정에 전혀 안 쓰인다.** `pose_detection_pct`와 `scale_uncertainty_cv`는 계산·출력만
   되고 어떤 게이트에도 안 들어간다. slj_00004는 **세 프레임 중 하나꼴로 사람을 못 찾았는데도**
   (68.5%) 통과해 절대 수치를 단언했다.
2. **`events_reliable`의 g 허용폭 [7, 12]가 절대 지표에는 너무 넓다.** slj_00002는 g=11.5로 통과하지만
   이는 **스케일이 17% 틀렸다**는 뜻이다(코드가 이미 `distance_consistency_bias_pct = -14.7`로 자체
   계산 중). 실제로 `--scale-px-per-m 191.7`(= 163.5 × 11.5/9.81)로 강제하니 g=9.81, 편향 -0.0%가
   되고 거리가 2.14 m → 1.83 m로 **17% 바뀐다.** 즉 [7,12]를 통과해도 거리는 ±15%까지 틀릴 수 있다.

## 핵심 물리 — 왜 "자동 보정"을 하면 안 되는가 (이 계획의 근거)

포물선 곡률에서 나오는 관계식은 하나뿐이다:

```
g_implied = a_px[px/s²] · k² / scale        (k = 실제fps / 컨테이너fps, scale = px/m)
```

`g_implied ≠ 9.81`이면 **`k`가 틀렸거나 `scale`이 틀렸거나 둘 다 틀렸다**는 뜻인데,
**식 하나에 미지수 둘이라 영상만으로는 어느 쪽인지 판별할 수 없다.**

- 기존 슬로우모션 경로는 *scale이 맞다*고 가정하고 k를 보정한다.
- 이번에 scale을 보정하면 *k가 맞다*고 가정하는 것이다.
- **둘 다 가정이며, 영상 안에는 이를 가르는 정보가 없다.**

게다가 g가 9.81에서 벗어나는 원인은 이 둘 말고도 있다: 이벤트 오검출, 카메라 팬/줌,
면외(out-of-plane) 이동, CoM 추정 노이즈, VFR 영상.

&gt; **따라서 이 계획은 스케일을 자동 보정하지 않는다.** 대신 **불일치를 정직하게 노출하고,
&gt; 영향받는 지표만 선택적으로 차단한다.** 사용자가 `--scale-px-per-m`을 주거나 화면에 길이를 아는
&gt; 물체를 넣으면 미지수가 하나 줄어 문제가 실제로 해결된다 — 그 경로를 안내한다.

(참고: 이전 PLAN 리뷰에서 Codex가 "보정했으니 g가 정상 → 신뢰 가능"은 순환 논리라고 지적했다.
이 계획은 그 지적을 일반화해 적용한 것이다.)

## 결정

1. **스케일 자동 보정 없음.** 중력에서 역산한 대안 스케일은 *진단값으로만* 출력한다.
2. **지표를 스케일 의존성으로 2분한다.**
   - **스케일 불변(항상 표시 가능)**: 도약 각도, 체공 시간, 모든 관절 각도, 팔 스윙 범위,
     반동 최저 무릎각, 이벤트 프레임. — 각도는 `atan2(vy, vx)`라 스케일이 약분되고,
     시간은 애초에 스케일과 무관하다. (slj_00002에서 스케일을 17% 바꿔도 각도 34.8°는 불변임을 실측 확인)
   - **스케일 의존(불일치 시 숨김)**: `takeoff_speed_mps`, `takeoff_vx/vy_mps`,
     `estimated_distance_com_m`, `estimated_distance_foot_m`, `apex_com_height_m`,
     `landing_com_forward_drift_m`, `landing_com_vs_foot_m`.
3. **추적 품질이 나쁘면 전부 차단**하고 재촬영을 안내한다(기존 `events_reliable=False` 경로 재사용).

## 접근 (단계 + verify)

&gt; **Codex 리뷰 반영판.** 1차 계획의 세 결함을 고쳤다: (a) NaN을 수동 스케일의 증거로 쓰던 것 →
&gt; `scale_src` 명시 판정, (b) 문자열 후처리 게이팅 → 생성 단계 분기, (c) 복잡한 우선순위 →
&gt; 식별가능성 기준 단일 규칙.

### A. 신뢰도 계산을 헬퍼 하나로 중앙화 — `analyze_jump.py`

기존 플래그(`events_reliable`, `landing_metric_reliable` 등)는 **의미도 값도 건드리지 않는다.**
그 위에 얹는 순수 함수를 하나 만든다. 순수 함수라 단위 테스트가 쉽다.

```python
G_STD          = 9.81
G_TOL          = 1.5     # m/s²  → g ∈ [8.31, 11.31]
POSE_DET_MIN   = 90.0    # %
SCALE_CV_MAX   = 0.25
WINDOW_DET_MIN = 85.0    # % — 이지~착지 구간 국소 검출률

def compute_reliability(*, det_pct, window_det_pct, scale_cv, scale_is_manual: bool,
                        g_implied, slowmo_detected: bool, slowmo_fit_r2,
                        events_reliable: bool) -> dict:
    """추적 품질과 절대 스케일 신뢰도를 판정한다. 부작용 없음, I/O 없음."""
```

&gt; **2차 리뷰 반영**: 스케일 출처를 문자열(`"manual"`)이 아니라 **`scale_is_manual: bool`** 로 받는다.
&gt; 오타·신규 문자열이 조용히 "자동"으로 흘러가는 실패를 원천 차단한다. `scale_src` 문자열은
&gt; 콘솔·JSON 표시용으로만 남긴다.

1. **`tracking_reliable`** — 랜드마크를 실제로 따라갔는가
   ```python
   cv_ok = True if scale_is_manual else (finite(scale_cv) and scale_cv <= SCALE_CV_MAX)
   tracking_reliable = (finite(det_pct) and det_pct >= POSE_DET_MIN
                        and finite(window_det_pct) and window_det_pct >= WINDOW_DET_MIN
                        and cv_ok)
   ```
   - **NaN을 통과 사유로 쓰지 않는다.** `scale_is_manual`일 때만 cv를 면제하고,
     자동 스케일에서 cv가 NaN이면 그건 **추정 실패**이므로 **불통과**다. (Codex 지적 1)
   - **`window_det_pct` 신설**: 전체 평균이 95%여도 이지·착지 구간이 깨지면 위험하다는 지적 반영.
     `[takeoff-2, landing+2]` 구간의 검출률을 따로 계산한다.
   - *Verify*: slj_00004 → False(det 68.5). slj_00001/2/3 → True.

2. **`absolute_metrics_reliable`** — 절대 길이/속도를 믿을 수 있는가

   판정 기준은 **식별가능성**이다. `g_implied = a_px·k²/scale`은 식 하나에 미지수 둘(`k`, `scale`)이라,
   둘 중 하나가 외부에서 고정돼야만 나머지가 풀린다.
   ```python
   SLOWMO_R2_MIN = 0.95
   if not (tracking_reliable and events_reliable):
       ok, reason = False, "추적/이벤트 불안정"
   elif slowmo_detected:
       # k를 g로부터 유도했으므로 g는 더 이상 독립 검증이 아니다.
       # 사용자가 scale을 단언한 경우에만 미지수가 하나로 줄어 식별된다.
       # 단, k 자체의 적합 품질도 함께 요구한다. (2차 리뷰 지적 2)
       ok = scale_is_manual and finite(slowmo_fit_r2) and slowmo_fit_r2 >= SLOWMO_R2_MIN
       reason = "슬로우모션 + 사용자 지정 스케일에 의존" if ok else "슬로우모션이라 스케일 검증 불가"
   else:
       # k=1 가정. g가 9.81 근처면 scale이 맞다는 독립 증거가 된다.
       ok = finite(g_implied) and abs(g_implied - G_STD) <= G_TOL
       reason = "중력 검증 통과" if ok else f"중력 검증 {g_implied:.2f} vs 9.81"
   ```
   - 슬로우모션 + 자동 스케일 = 미지수 2, 식 1 → **식별 불가 → False**. 순환 논리 차단.
   - 슬로우모션 + 수동 스케일 = 미지수 1 → 식별 가능하지만, **k는 사용자가 준 스케일이 맞다는
     전제 위에서만 옳다.** 스케일이 틀리면 k가 그 오차를 그대로 흡수한다. 그래서 통과시키되
     `reliability_reason`에 **"사용자 지정 스케일에 의존"** 을 남겨 근거를 숨기지 않는다.
     `slowmo_fit_r2`는 진단 필드로도 출력해 게이트를 사후 검증 가능하게 한다.
   - *Verify*: slj_00002(g=11.5) → **False**. `--scale-px-per-m 191.7` 주면 g=9.81 → **True**.

3. **진단 필드 추가** (출력 전용, 어떤 계산에도 미사용)
   - `scale_from_gravity_px_per_m = scale * g_implied / G_STD` (g가 유한하고 &gt;0일 때만, 아니면 None)
   - `tracking_reliable`, `absolute_metrics_reliable`, `reliability_reason`(사람이 읽는 사유 문자열)
   - `window_detection_pct`
   - 기존 `distance_consistency_bias_pct`는 그대로 유지
   - *Verify*: slj_00002 → `scale_from_gravity_px_per_m ≈ 191.7`

4. **입력 하드닝**: `det_pct`·`window_det_pct`·`scale_cv`·`g_implied`·`slowmo_fit_r2`가
   None/NaN/inf여도 예외 없이 **불통과**로 떨어진다. `scale <= 0`이나 fps 결측은 기존 경로가
   이미 막고 있으므로 그대로 두되, 진단 필드 계산에서 0 나눗셈이 나지 않게 가드한다.

5. **`window_det_pct` 구간 계산 하드닝** (2차 리뷰 지적 3)
   ```python
   lo = max(0, min(takeoff, landing) - 2)
   hi = min(T, max(takeoff, landing) + 3)      # 뒤집힘·범위초과 모두 흡수
   window_det_pct = 100.0 * valid[lo:hi].mean() if hi > lo else float("nan")
   ```
   - `takeoff`/`landing`이 None이거나 순서가 뒤집혔거나 영상 범위를 벗어나도 예외 없이
     NaN → `tracking_reliable=False`로 안전하게 떨어진다.

### B. 피드백 게이팅 — 생성 단계에서 분기 (문자열 후처리 금지)

핵심 원칙: **수치를 만든 뒤 지우지 않는다. 못 믿을 수치는 애초에 만들지 않는다.** (Codex 지적 2)

5. `coaching()`을 세 갈래로 정리한다. 기존 문장 생성 로직은 그대로 두고 **호출 여부만** 나눈다.
   ```
   tracking_reliable == False  →  재촬영 안내 1줄만 반환 (기존 events 분기보다 앞)
   events_reliable   == False  →  기존 메시지 그대로 (변경 없음)
   그 외                        →  scale-invariant 코칭은 항상 생성
                                   absolute 코칭은 absolute_metrics_reliable일 때만 생성
   ```
   - 각도 코칭도 `tracking_reliable and events_reliable` 아래에서만 생성된다. `vx`가 0에 가까우면
     `atan2`가 불안정하다는 지적 반영 — 각도는 스케일에 둔감할 뿐 **무조건 안전하지는 않다**.
   - 재촬영 안내:
     ```
     ⚠️ 영상에서 몸을 안정적으로 찾지 못했어요(검출률 68%). 측면에서 전신이 프레임 안에 끝까지
        들어오게, 밝은 곳에서 카메라를 고정하고 다시 촬영해 주세요.
     ```

6. **절대 지표 집합을 상수로 한 곳에 정의**하고, 이 집합을 쓰는 문장 생성기는 전부
   `absolute_metrics_reliable` 아래에 둔다. 일부만 남기지 않는다. (Codex 지적)
   ```python
   ABSOLUTE_METRIC_KEYS = frozenset({
       "takeoff_speed_mps", "takeoff_vx_mps", "takeoff_vy_mps",
       "estimated_distance_com_m", "estimated_distance_foot_m",
       "apex_com_height_m", "landing_com_forward_drift_m", "landing_com_vs_foot_m",
   })
   ```
   - `landing_com_vs_foot_m`(착지 버킷)과 `apex_com_height_m`도 절대 길이임에 주의 —
     1차 계획에서 놓칠 뻔한 항목이다.

7. `absolute_metrics_reliable == False`면 안내 한 줄을 맨 앞에 붙인다:
   ```
   📏 화면 속 크기 기준이 흔들려(중력 검증 11.5 m/s², 기준 9.81) 속도·거리는 최대 ±17% 틀릴 수
      있어 숨겼어요. 각도와 타이밍은 크기와 무관하므로 그대로 유효합니다.
      정확한 거리를 원하면 화면 안에 1 m 자를 함께 찍거나 --scale-px-per-m 을 지정해 주세요.
   ```
   - **실질 손실을 인정한다**: 이지속도는 문헌상 기록을 지배하는 변수라(속도 변동 ±25 cm vs
     각도 변동 ±5 cm) "속도 양호" 같은 판정을 못 하게 되는 것이 뼈아프다. 그래도 틀린 수치를
     단언하는 것보다 낫고, 각도·신전·반동·팔스윙·착지 자세 코칭은 전부 살아남는다.
   - *Verify*: slj_00002 → "이지 속도 3.41 m/s — 양호" 사라지고 안내 노출.
     각도 35°·무릎 신전·팔스윙 97° 코칭은 유지.

8. `photo_feedback()`도 같은 헬퍼 결과를 받아 동일 규칙으로 분기한다(takeoff/mid/landing 세 버킷 모두).

### C. 요약 출력 — `analyze_jump.py` 콘솔 + `summary.json`

7. 콘솔 요약에 신뢰 상태 한 줄 추가. 값이 숨겨졌음을 사용자가 알아야 한다:
   ```
   absolute_metrics_reliable       : False  (g=11.5 vs 9.81 → 스케일 불일치 17%)
   ```
   - JSON은 필드 추가만 하고 **기존 필드는 하나도 제거하지 않는다**(웹앱·비교 스크립트 호환).
   - *Verify*: `compare.py`, `web_app.py`가 기존 키로 계속 동작.

## 임계값 근거와 한계 (정직하게)

| 상수 | 값 | 근거 | 한계 |
|---|---|---|---|
| `POSE_DET_MIN` | 90 % | 샘플 4개가 100/100/98.1/68.5로 갈림 | **n=4로 정한 값.** 통계적 근거 아님 |
| `SCALE_CV_MAX` | 0.25 | 샘플이 0.088/0.147/0.209/0.62 | 동일 |
| `G_TOL` | 1.5 m/s² | ±15 % 거리 오차에 대응 | 임의 선택. 더 조이면 대부분 클립이 탈락 |

**이 세 값은 잠정치다.** 지면반력기 실측 데이터가 생기면 "절대 지표가 실제로 몇 % 틀렸는가"를
기준으로 재보정해야 한다. 모듈 상단 상수로 두어 한 곳에서 바꿀 수 있게 한다.

## 비목표 (이번에 하지 않는 것)

- 스케일 자동 보정 (위 물리 논증 참조)
- 카메라 팬 보정 — slj_00002에서 체공 중 배경이 7.1 px 흘러 수평 속도가 **3.6 %** 과소평가됨을
  광류로 실측했다. 유의미하지만 이번 범위 밖이고, 수직(dy≈0)은 영향이 없어 g 진단은 오염되지 않는다.
- 임계값의 통계적 보정 (실측 데이터 필요)
- 웹 UI 개편 — 백엔드 필드만 추가하고 UI는 후속

## 신규/변경 파일

| 파일 | 변경 |
|---|---|
| `analyze_jump.py` | 상수 5개 + `ABSOLUTE_METRIC_KEYS`, 순수 함수 `compute_reliability()` 신설, 진단 필드 4개, `coaching()`·`photo_feedback()` 생성 단계 분기, 콘솔 요약 |
| `tests/test_reliability.py` | **신규** — `compute_reliability()` 단위 테스트 + 게이팅 통합 테스트 |
| `tests/conftest.py` | 기존 3개 픽스처에 `scale_src`·`window_detection_pct` 추가 (전부 게이트 통과해야 함) |

## 테스트 계획

**먼저 회귀부터.** 기존 `JUMP_A`(g 8.83, cv 0.209, det 98.1) · `JUMP_B`(9.53, 0.124, 100) ·
`JUMP_BAD`(9.7, 0.15, 95)는 전부 새 게이트를 통과하므로 기존 테스트는 깨지지 않아야 한다.
단 "통과 여부"만 보지 말고 **메시지 개수와 핵심 문구도 회귀 검증**한다. (Codex 지적)

`compute_reliability()` 단위 테스트 (순수 함수라 영상 없이 검증 가능):

| # | 입력 | 기대 |
|---|---|---|
| 1 | `det=68.5` | `tracking_reliable=False` |
| 2 | `scale_cv=0.62, scale_src="auto(limb)"` | `tracking_reliable=False` |
| 3 | `scale_cv=NaN, scale_src="auto(limb)"` | **`False`** — NaN은 통과 사유가 아님 |
| 4 | `scale_cv=NaN, scale_src="manual"` | `True` (cv 면제) |
| 5 | `det=100, window_det=70` | `False` — 국소 검출률 게이트 |
| 6 | `g=11.5, scale_src="auto(limb)"` | `absolute=False` |
| 7 | `g=9.81, scale_src="auto(limb)"` | `absolute=True` |
| 8 | `g=11.5, scale_src="manual"` | `absolute=False` (허용폭 밖) |
| 9 | `slowmo=True, g=9.81, scale_src="auto(limb)"` | **`absolute=False`** — 순환 논리 차단 |
| 10 | `slowmo=True, g=9.81, scale_src="manual"` | `absolute=True` — 식별 가능 |
| 11 | `g=None` / `inf` / `NaN` | 예외 없이 `absolute=False` |
| 12 | `scale_src="unexpected"` | 자동으로 간주(보수적) |
| 13 | `events_reliable=False` | `absolute=False` |
| 14 | **경계값** `det=90.0, window=85.0, cv=0.25, \|g-9.81\|=1.5` | 전부 **통과**(≤/≥ 포함 관계 고정) |
| 15 | `slowmo=True, manual, r2=0.90` | `absolute=False` — k 적합 품질 미달 |
| 16 | `takeoff`/`landing` 뒤집힘·범위초과 | 예외 없이 `window_det=NaN` → `tracking=False` |
| 17 | `scale_is_manual=True, scale<=0 / None / inf` | 진단 필드 `None`, 예외 없음 |

게이팅 통합 테스트:
14. `tracking_reliable=False` → `coaching()`이 재촬영 안내 **1줄만** 반환
15. `absolute=False` → 코칭에 **`\d+\.\d+\s*m/s`·`\d+\.\d+\s*m` 패턴이 0건**, `°` 패턴은 &gt;0건
    (단순 `"m" in s` 검사는 안내문의 `m/s²`까지 걸리므로 정규식으로 실제 수치 문구만 본다 — Codex 지적)
16. `absolute=False` → `photo_feedback()`의 세 버킷 모두 절대 수치 0건
17. `absolute=True` → 기존과 동일한 문장 집합
18. `scale_from_gravity_px_per_m == scale * g / 9.81`, `g` 비유한이면 `None`
19. 기존 5개 테스트 파일 전부 통과

## 성공 기준

- [ ] `pytest` 전체 통과 (기존 + 신규)
- [ ] slj_00004 → 재촬영 안내만, 수치 코칭 0건
- [ ] slj_00002 → 각도·관절 코칭 유지, 속도·거리 수치 0건, 안내 노출
- [ ] slj_00002 + `--scale-px-per-m 191.7` → 전 지표 정상 노출
- [ ] slj_00003 → 기존과 동일하게 동작 (회귀 없음)
- [ ] `summary.json` 기존 키 전부 보존 (`compare.py`·`web_app.py` 호환)

---

## 구현 후 Codex 코드 리뷰 결과 (2026-08-22)

`codex review --uncommitted`는 git repo가 아니라 사용 불가 → 변경 코드를 인라인으로 넣어 리뷰.
지적 8건 중 실제 결함 2건을 수정했고, 3건은 오판, 3건은 범위 밖으로 남긴다.

### 수정함
| # | 지적 | 조치 |
|---|---|---|
| 2 | `compute_reliability()` 슬로우모션 분기가 `g_implied`의 유한성을 검사하지 않아, NaN·inf·0에서도 수동 스케일이면 통과 | `_finite(g_implied) and g_implied > 0` 추가. 회귀 테스트 `test_non_finite_gravity_blocks_the_slowmo_path_too` 신설 |
| 6 | 코칭 텍스트는 수치를 숨기는데 **웹 지표 카드는 그대로 표시** → UI가 자기모순 | `templates/index.html`의 `card()`에 `ABS_KEYS` 게이트 추가. 기존 `landing_metric_reliable === false` 패턴을 그대로 따르고 `=== false` 비교로 구버전 요약 호환 유지 |
| 7 | 슬로우모션 보정 후 `flight_time_s`는 k(=g에서 유도)를 타므로 더 이상 스케일 무관이 아님 | 같은 `card()`에서 `slowmo_detected && !absolute_metrics_reliable`일 때만 체공 시간도 숨김 |

부수 개선: `web_app.py`의 `log_analysis()`에 `tracking_reliable`·`absolute_metrics_reliable`·
`window_detection_pct`를 기록. 임계값 재보정 시 신뢰 수준으로 로그를 걸러야 하기 때문.

### 오판이었던 지적
- **1. 구문 오류** — 리뷰에 넘긴 발췌본의 인코딩이 깨진 것. `ast.parse` 통과, 실제 파일 정상.
- **3. 슬로우모션 경로 사망** — `events_reliable = bool(events_reliable_raw or slowmo_detected)`가
  1206행에 존재. 발췌 범위에 그 줄이 안 들어가 생긴 오해.
- **8. `rnd(None, 1)` 폭발** — `rnd`가 `x is not None`으로 단락 평가. 실행으로도 확인
  (`slowmo_fit_r2: None` 정상 출력).

### 범위 밖으로 남김 (별도 판단 필요)
- **4. `photo_feedback()`의 mid·landing 버킷이 `events_reliable`을 직접 보지 않음.**
  `arm_swing_reliable`은 `run_analysis`에서 `events_reliable and _vis_ok(...)`로 만들어져
  간접 게이팅되지만, `landing_metric_reliable`은 `(landing - takeoff) >= 2`라는 약한 조건만 본다.
  **이번 변경이 만든 문제가 아니라 기존 동작**이라 손대지 않았다. 고칠지 별도 결정 필요.
- 임계값 4개의 통계적 보정 — 지면반력기 실측 데이터가 나와야 가능.
- 카메라 팬 보정 — slj_00002에서 수평 속도 3.6% 과소평가를 광류로 실측했으나 별건.

### 최종 검증
- `pytest` **135 passed** (기존 104 + 신규 31), 회귀 0건
- `analyze_jump.py`·`web_app.py`·`compare.py` `ast.parse` 통과, `index.html` 스크립트 `node --check` 통과
- `summary.json` 기존 42개 키 전부 보존, 6개 추가
- 지표 카드 6종 목록 그대로 유지 (편집 중 항목 유실 없음)


# 도약 세 순간과 최초 착지 피드백 구현 계획

## 목표와 범위

사용자가 2026-10-29 요청한 종합점수 제거, 도약 세 순간·공중·최초 착지의 상세 피드백을 구현한다. 원본은 `work/JumpCoach_before_phase_feedback_20261029`에 보존했으며 이 작업사본에서 테스트 후 변경 파일만 바탕화면 JumpCoach에 반영한다. 현재 프로젝트는 Git 저장소가 아니므로 Git 생성·커밋·worktree 생성은 하지 않는다.

## 확인된 원인

- 착지 사진은 `landing_frame`이 아니라 이후 0.6초의 최소 무릎각 프레임을 선택하여 후기 흡수 자세를 보여준다.
- 기존 착지 평가는 창 끝 무게중심과 주측 뒤꿈치를 비교하여 최초 접촉의 다리 뻗기를 표현하지 못한다.
- 총점이 `compare.py`, `web_app.py`, 화면 및 로그에 연결되어 있다.
- 자동 검출의 발 높이 임계와 마지막 공중 프레임 전달은 실제 신발 접촉과 시간차를 만들 수 있다.

## 데이터 계약

- `contact_events.refine_contact_events(norm, raw_vis, valid, com, fps, size, takeoff, apex, landing, video=None)`는 `takeoff_frame`(첫 공중), `pre_takeoff_frame`(마지막 지지), `landing_frame`, `contact_point_px`, `contact_landmark`, `contact_side`, `contact_kind`, `reliable`, `method`, `uncertainty_frames`, `note`를 반환한다. 신발 뒤꿈치·앞부분/양발을 고려하며 불확실하면 접촉 확정값으로 취급하지 않는다.
- `metrics.moments`는 `load`, `drive`, `takeoff`, `mid`, `landing` 키를 사용한다. 각 항목은 `label`, `phase`(`takeoff`/`mid`/`landing`), `frame`, `time_s`(분석영상 시간), `available`, `reliable`, `measurements`([{label,value,unit}]), `observations`, `cues`, `notes`를 갖는다.
- `metrics.phase_feedback`는 `takeoff`, `mid`, `landing`의 `label`, `observations`, `cues`, `notes`를 갖는다. `photo_feedback`는 기존 3구간 good/bad 구조로 파생할 수 있지만 총점은 생성하지 않는다.
- `artifacts`에는 기존 annotated/plots/summary 외에 위 다섯 순간 이미지가 들어간다. Web API `shots`는 순간 정보와 URL을 연결하고 화면에서 도약 3장·공중 1장·착지 1장으로 묶는다. 비교에서도 같은 상세 내용을 열람한다.
- 착지 간격은 최초 접촉점이 추정 무게중심/골반보다 진행방향으로 앞선 양을 양수로 한다. 픽셀과 길이 환산값을 구별하고, 길이 환산 신뢰도 실패 시 m/cm를 보류한다. 후기 진행은 별도 관찰이며 거리 손실·안전·근력·좋은 자세를 확정하지 않는다.

## 실행 순서와 검증

1. 접촉 검출: 뒤꿈치/앞부분/양발 중 먼저 닿는 경우, 후기 흡수와 분리, 좌우 반전, 결측·가림의 실패 테스트를 먼저 실행한다. 독립 contact_events 모듈만 구현하고 실제 체육관 영상의 접촉 전후 연속프레임으로 검토한다.
2. 도약/공중/착지 분석: 하강 말기, 전방 팔스윙 추진, 마지막 지지 프레임을 순서대로 선정한다. 짧은 영상에서 억지로 3개를 생성하지 않는다. 순간 값은 해당 프레임과 같은 정의로 읽으며 주변·착지 후 프레임으로 섞지 않는다. 몸통 기울기, 무릎·고관절 포함각, 팔 타이밍, 공중 다리 이동, 최초접촉 간격과 후기 움직임을 기술한다. 자동검출/추적/스케일별 신뢰도와 불확실성을 표시한다.
3. 점수 제거와 화면: form_score, breakdown 및 최고 자세 순위와 관련 로그 생성을 제거한다. 비교는 측정 가능한 값과 구간별 관찰을 제공한다. 신뢰할 수 없는 절대값은 비교에서도 보류한다. 입력 검증·최대5영상·영상/그래프·종료 기능을 유지한다.
4. 통합: 전체 pytest, Python 구문, 실제 단일·비교 업로드, 생성 이미지/영상/JSON 검증, 브라우저 화면 검토를 수행한다. 원본 변경 여부를 해시로 확인하고 수정 파일만 반영한다.

## 담당 파일

- 접촉 담당: `contact_events.py`, `tests/test_contact_events.py`.
- 구간 분석/통합 담당: `phase_feedback.py`, `analyze_jump.py`, 관련 구간 테스트, 설명 문서.
- 웹/점수 담당: `compare.py`, `web_app.py`, `templates/index.html`, 점수/비교의 기존 테스트와 신규 API 테스트.

## 검토 초점

- 전체 흐름에서 총점·자세순위가 다시 생성되지 않는가.
- 뒤꿈치가 아닌 발끝 또는 반대발 최초접촉도 다루는가.
- 좌우 진행방향에서 착지 간격의 부호가 일치하는가.
- 접촉/각도 추적이 실패한 영상에 확정적인 자세 평가를 내리지 않는가.
- 짧은 준비·긴 슬로모션·접촉 이후가 없는 영상에서 오류 없이 미확인 항목을 설명하는가.

## 실행 기록

- 변경 전 전체 검사: `python -X utf8 -m pytest -q` → 135 passed.
- 사용자 요청과 첨부 예시를 구현 범위로 사용하며 재승인 대기 없이 진행한다.


계획 독립 검토 반영: PHASE_FEEDBACK_PLAN.md 실행 기록 참조. 최초 접촉·후기 이동 신뢰도와 새 간격 키 분리, 기존 코칭 단정 제거.


# 2026-10-29 결과 화면 간소화

사용자 요청: 추정거리, 반복 신뢰도·해석 주의 문구를 화면에서 제거하고 동작 관찰과 다음 행동을 짧게 전달한다.

1. 단계별 사진과 교정 코멘트 한 개를 기본 화면으로 만든다. 숫자·프레임·그래프는 접어서 제공하고 반복되는 관찰/연습/주의 목록은 렌더링하지 않는다.
2. 단일/비교 화면의 추정거리·최고거리·신뢰도 안내를 제거한다. 내부 계산과 수치 유효성 검사는 유지하여 불확실한 값을 노출하지 않는다.
3. 코멘트는 확인한 같은 프레임·단계 간 변화에 근거한다. 읽을 수 없는 동작에 교정을 지어내지 않고 짧은 재촬영 안내를 보여준다. 착지 후보는 제목에서만 구별한다.
4. 단일·비교 렌더링, 스크립트 구문, 기존 분석 검사, 실제 브라우저의 데스크톱·모바일을 확인한다. 백업은 work/before_clean_ui_20261029.

검증 완료: 전체 자동검사 114개 통과. 실제 브라우저에서 첨부 체육관 영상의 단일 업로드, 샘플 2개 비교 업로드, 사진 5장/10장, 수치 펼침, 0.5배속, 휴대폰 폭 390px에서 가로 넘침 없음, 스크립트 오류 없음 확인. 기존 결과 예시 링크도 새 화면과 실제 새 코멘트로 갱신. 증거: work/clean_ui_verification.json 및 clean_ui_*.png.
