문서 인코딩이 깨져 있지만, 의도는 한국어 계획으로 읽었습니다.
System.Management.Automation.RemoteException
## 1. 문제 / 위험
System.Management.Automation.RemoteException
- `scale_src`를 문자열로 판정하는 설계는 취약합니다. `"manual"`, `"auto(limb)"`, `"unexpected"` 같은 문자열 대신 `scale_is_manual: bool` 또는 enum 상수를 쓰는 편이 안전합니다.
- `absolute_metrics_reliable`에서 `slowmo_detected and scale_src == "manual"`이면 바로 True로 가는 부분은 아직 약합니다. 수동 scale이 있어도 slowmo 보정값 `k`가 잘못 추정됐으면 속도/거리 절대값은 여전히 틀릴 수 있습니다.
- `g_implied`를 신뢰성 기준으로 쓰면서 동시에 slowmo 보정에도 쓰는 경로가 있다면 순환 검증 위험이 있습니다. “g로 보정한 뒤 g가 맞으니 신뢰”가 되지 않도록 `g_implied` 계산 시점과 보정 여부를 명확히 분리해야 합니다.
- 임계값 `POSE_DET_MIN=90`, `SCALE_CV_MAX=0.25`, `G_TOL=1.5`는 샘플 4개 기반이라 과적합 위험이 큽니다. 계획에 한계는 잘 적혀 있지만, 실제 코드에서는 reason 문자열에 “휴리스틱”임을 남기는 게 좋습니다.
- `tracking_reliable == False`일 때 기존 `events_reliable=False` 경로보다 먼저 차단하는 것은 맞지만, 기존 메시지/테스트가 이 우선순위 변경을 기대하는지 확인이 필요합니다.
- 정규식으로 `m/s`, `m`, `°` 포함 여부를 검사하는 통합 테스트는 한국어 문구 변경에 취약합니다. 가능하면 coaching 결과를 “metric key 기반 구조”로 검증하거나 최소한 금지 키 기준으로 검사하는 편이 낫습니다.
System.Management.Automation.RemoteException
## 2. 빠진 엣지 케이스
System.Management.Automation.RemoteException
- `window_det_pct` 계산 구간에서 `takeoff` 또는 `landing` 이벤트가 없거나 index가 뒤집힌 경우.
- landing 이후 프레임이 부족해서 `[takeoff-2, landing+2]`가 영상 범위를 벗어나는 경우.
- `det_pct=90.0`, `window_det_pct=85.0`, `scale_cv=0.25`, `abs(g-9.81)=1.5` 같은 경계값 테스트.
- `scale_src="manual"`이지만 `scale <= 0`, `scale is None`, `scale is inf`인 경우.
- `slowmo_detected=False`인데 컨테이너 FPS와 실제 FPS가 다른 VFR 영상.
- `events_reliable=True`지만 takeoff/landing 이벤트 위치가 물리적으로 말이 안 되는 경우.
- `vx`가 0에 가깝거나 음수인 경우의 각도 coaching 차단 기준.
- `scale_from_gravity_px_per_m` 계산에서 `scale`, `g_implied` 둘 중 하나라도 비정상일 때 None 처리 테스트.
System.Management.Automation.RemoteException
## 3. 더 단순한 대안
System.Management.Automation.RemoteException
현재 계획은 비교적 타당하지만 약간 넓습니다. 첫 구현은 더 작게 갈 수 있습니다.
System.Management.Automation.RemoteException
- 1차 변경:
  - `compute_reliability()` 추가
  - `tracking_reliable`, `absolute_metrics_reliable`, `reliability_reason`만 summary에 추가
  - absolute metric 문장만 차단
  - 최소 테스트 추가
System.Management.Automation.RemoteException
- 2차 변경:
  - `window_detection_pct`
  - `scale_from_gravity_px_per_m`
  - `photo_feedback()` 전체 gating
  - 콘솔 요약 개선
System.Management.Automation.RemoteException
특히 `photo_feedback()`까지 한 번에 건드리면 출력 회귀 범위가 넓어집니다. 먼저 coaching의 잘못된 절대 수치 차단을 고정하고, 같은 helper를 photo 쪽에 재사용하는 순서가 더 안전합니다.
System.Management.Automation.RemoteException
## 4. Overall verdict
System.Management.Automation.RemoteException
**needs revision**
System.Management.Automation.RemoteException
방향은 좋습니다. “자동 보정하지 않고, 신뢰 불가한 절대 수치만 차단한다”는 핵심 판단도 맞습니다.
System.Management.Automation.RemoteException
다만 구현 전 아래 3가지는 고쳐야 합니다.
System.Management.Automation.RemoteException
1. `scale_src` 문자열 판정을 enum/boolean으로 바꾸기  
2. slowmo + manual scale일 때도 `k` 신뢰 조건을 별도로 두기  
3. 경계값/이벤트 누락/window 범위 초과 테스트 추가하기  
System.Management.Automation.RemoteException
이 세 가지를 반영하면 승인 가능한 계획입니다.
SUCCESS: The process with PID 38844 (child process of PID 20632) has been terminated.
SUCCESS: The process with PID 20632 (child process of PID 25872) has been terminated.