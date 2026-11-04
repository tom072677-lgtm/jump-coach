구현 방향은 타당하지만 **needs revision**입니다. 핵심 아이디어인 “자동 보정하지 않고 신뢰도 플래그로 수치 코칭을 게이트한다”는 맞습니다. 다만 지금 계획에는 게이트 정의와 출력 제거 방식에서 버그가 생길 여지가 있습니다.
System.Management.Automation.RemoteException
**1. Problems or Risks**
System.Management.Automation.RemoteException
- `scale_cv is NaN => manual scale이므로 통과` 가정이 위험합니다. NaN은 수동 스케일뿐 아니라 스케일 추정 실패, 입력 누락, 계산 오류에서도 나올 수 있습니다. 반드시 `scale_src == "manual"` 같은 명시적 소스와 함께 판단해야 합니다.
- `absolute_metrics_reliable` 로직이 `scale_src`, `slowmo_detected`, `g_implied` 사이에서 복잡합니다. 특히 `manual + slowmo_detected`일 때 우선순위가 애매합니다.
- `events_reliable`의 기존 의미를 건드리지 않는다고 했지만, 새 플래그가 기존 출력 흐름 앞에 들어가면 실제 동작은 달라질 수 있습니다. 기존 테스트가 “통과”하는지만 보지 말고 기존 메시지 개수/핵심 문구 회귀도 봐야 합니다.
- `m/s`, `m` 문구를 문자열 기준으로 제거하면 취약합니다. 안내 문구의 `m/s²`, 단위 설명, 또는 유효한 비수치 문장이 같이 사라질 수 있습니다. 가능하면 “수치 기반 코칭 항목”을 생성 단계에서 분기해야 합니다.
- 각도는 스케일에는 둔감하지만 항상 안전한 것은 아닙니다. `vx`가 작거나 이벤트 프레임이 틀리면 `atan2(vy, vx)`도 불안정합니다. `tracking_reliable && events_reliable` 없이는 각도 코칭도 막는 게 맞습니다.
- `landing_com_vs_foot_m`, `apex_com_height_m`, `estimated_distance_*_m`, drift 계열은 전부 절대 길이 수치입니다. `absolute_metrics_reliable == False`일 때 일부만 “주의”가 아니라 원칙적으로 모두 게이트해야 합니다.
System.Management.Automation.RemoteException
**2. Missing Edge Cases**
System.Management.Automation.RemoteException
- `g_implied`, `scale`, `scale_cv`, `pose_detection_pct`가 NaN/inf/None인 경우.
- `scale <= 0`, fps/container fps가 0 또는 누락된 경우.
- `scale_src` 값이 `"manual"` / `"auto(limb)"` 외 다른 문자열일 경우.
- `slowmo_detected=True`이면서 `scale_src="manual"`인 경우의 기대 동작.
- detection pct는 전체 프레임 평균일 수 있습니다. 전체는 95%여도 takeoff/landing 주변 프레임이 깨지면 코칭은 위험합니다. 가능하면 critical window detection도 고려해야 합니다.
- `events_reliable=True`지만 takeoff/landing frame이 누락되거나 순서가 뒤집힌 경우.
- `distance_consistency_bias_pct` 계산이 불가능한 경우에도 summary 출력이 깨지지 않아야 합니다.
- 수치 제거 테스트에서 `m` 단순 포함 여부만 보면 너무 넓습니다. “3.41 m/s”, “2.14 m” 같은 실제 절대 수치 문구가 사라졌는지로 검증하는 편이 낫습니다.
System.Management.Automation.RemoteException
**3. Simpler Alternatives**
System.Management.Automation.RemoteException
- 작은 helper 하나로 게이트를 중앙화하는 게 더 단순합니다.
  - `compute_reliability(...) -> {tracking_reliable, absolute_metrics_reliable, reason, scale_from_gravity_px_per_m}`
  - `coaching()`과 `photo_feedback()`은 이 결과만 사용.
- `absolute_metrics_reliable`는 우선 단순하게 시작해도 됩니다:
  ```python
  tracking_reliable
  and events_reliable
  and not slowmo_detected
  and finite(g_implied)
  and abs(g_implied - 9.81) <= G_TOL
  ```
  단, 사용자가 명시한 `--scale-px-per-m`가 있고 slowmo가 아니라면 통과 가능. 이 정도가 계획보다 이해하기 쉽습니다.
- 문장 필터링보다 생성 분기가 낫습니다. 예: 수치 코칭 함수는 `absolute_metrics_reliable`이 false면 호출하지 않고, scale-invariant 코칭만 생성.
System.Management.Automation.RemoteException
**4. Overall Verdict**
System.Management.Automation.RemoteException
**needs revision**
System.Management.Automation.RemoteException
방향은 승인 가능하지만, 구현 전에 다음 세 가지는 고쳐야 합니다.
System.Management.Automation.RemoteException
1. `NaN scale_cv == manual scale` 가정을 제거하고 명시적 `scale_src` 기준으로 판단.
2. 절대 수치 항목을 문자열 후처리가 아니라 생성 단계에서 게이트.
3. `absolute_metrics_reliable`의 우선순위를 단순하고 테스트 가능한 형태로 재정의.
System.Management.Automation.RemoteException
이 세 가지를 반영하면 계획은 구현 가능한 수준입니다.
SUCCESS: The process with PID 15876 (child process of PID 22732) has been terminated.
SUCCESS: The process with PID 22732 (child process of PID 43932) has been terminated.