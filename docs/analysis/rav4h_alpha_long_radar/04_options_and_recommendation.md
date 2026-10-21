# 04 — 方案对比与建议

状态：完成（分析阶段；**尚未改任何代码**）。前置：[01](01_architecture_and_signal_path.md)、[02](02_radard_fusion_gating.md)、[03](03_planner_mpc_and_safety_nets.md)。

## 1. 问题重述（基于前三份结论）

Alpha Long 通过 UDS 静默雷达 ECU 实现 → 原车 `ACCEL_CMD`/AEB/FCW 全部消失且不可恢复。
前车感知在默认配置下 100% 来自视觉；即使打开 `ToyotaRadarMode`，radard 也只让雷达"校正"视觉已确认的 lead（低速 <4 m/s 除外）。
因此**真正缺的是：一个独立于视觉置信度的、来自原车雷达的前车制动需求通道**。

## 2. 方案

### 方案 0 — 先打开 `ToyotaRadarMode`（零代码）
- 做法：Galaxy → Vehicle → RAV4 Radar (Experimental) 选 ACC Target(1) 或 Fused(2)，重启。
- 收益：视觉 lead 有雷达配对时，距离/速度/加速度用雷达 Track；planner 的 `radar==True` 分支（RAV4 专用 follow 调参、少了 vision 谨慎 cap）生效。
- 不解决：视觉漏检（02 §6 矩阵第 3、4 行）、切入前 3.6 s（Mode 2）。
- 前提：Q1（Alpha Long 下 bus 1 仍有数据）。**这是所有后续方案的共同前置，必须先验证。**
- 风险：低；该功能已有测试（`opendbc_repo/opendbc/car/toyota/tests/test_ars510.py`）。

### 方案 A — radard 放行"雷达自确认 lead"（独立 lead）
- 做法：在 `radard.get_lead` 里，当视觉无 lead / 匹配失败时，若存在满足下列条件的雷达 track，则作为 leadOne（`radar=True`，`modelProb=0`）：
  - 来源是雷达 ACC 目标（Mode 1 的唯一点，或 Mode 2 的 `acc_tid`）；
  - `|yRel| < ~1.2 m`，`dRel` 在 [0.75, ~120] m，`cnt >= N`（例如 ≥ 5 个周期，约 0.3 s）；
  - 满足闭合条件（`vRel < -0.5` 或 `dRel < 安全跟车距离`），避免静止路边物体；
  - 过滤静止目标（沿用 Mode 2 的 `STILL` 逻辑）。
- 优点：直接让 MPC 看到视觉漏检的车；跟车行为由现有 MPC 决定，舒适度好。
- 缺点：误检直接进入 MPC（可能急刹）；需要在 `RadarPoint` 里携带"这是雷达 ACC 目标/IN_PATH"信息（schema 或接口内过滤，Q6）；会影响所有走 radard 的车型，需严格按 `CP.carFingerprint in ARS510_RADAR_CAR ∧ ARS510 flags` 门控。
- 工作量：中（radard + radar_interface + 测试）。

### 方案 B — 放宽视觉-雷达配对门限
- 做法：对 ARS510 车型，`match_vision_to_track` 的 `dist_scale 0.25 → 0.35`、增加"雷达 ACC 目标优先"。
- 优点：改动小，解决"视觉距离偏差>25% 时雷达被拒"。
- 缺点：不解决视觉完全漏检；调参需要 rlog 数据。
- 建议：与方案 A 合并考虑，不单独做。

### 方案 C — planner 末端"雷达制动 cap"（只减不增，**推荐的安全后盾**）
- 做法：新增纯函数 `radar_brake_cap(radar_target, v_ego, ...) -> accel_cap | None`：
  - 输入：ARS510 ACC 目标（`LONG_DIST`、`REL_SPEED`、`REL_ACCEL`、`IN_PATH`、`CLASS`、`PRESENT`）+ `v_ego`；
  - 输出：满足"在 `d_min`（≈ 当前跟车距离的 50%，下限 4 m）处相对速度降到 ~0"所需的减速度 `a_req = -(closing²)/(2·max(d-d_min, 1))`，并在 `[-2.5, 0]` 内限幅（避免 stock AEB 级别的"重刹"，那应由 FCW 提醒驾驶员接管）；
  - 在 `longitudinal_planner.update` 末尾对 `output_a_target` 取 `min`（只会更保守），同一处标记 `longitudinalPlan` 里的诊断字段（日志用）。
- 数据通路：Mode 1/2 的 interface 已在 `card` 进程里解码；需要把"雷达 ACC 目标"以独立消息（扩展 `liveTracks` 或 `starpilotCarState`）送到 plannerd，**不经过** radard 的视觉配对。
- 优点：与视觉置信度完全解耦；单调安全（只减不增）；失效模式简单（数据过期 → cap=None → 回到现状）；不改变 MPC。
- 缺点：舒适度略差（cap 会在 MPC 之外压出一次制动）；需要新增跨进程字段（cereal schema 变更需要重新生成/兼容性评估）。
- 工作量：中到大。

### 方案 D — 一致性监控与告警
- 做法：雷达 ACC 目标与 `leadOne` 持续不一致（雷达目标闭合 TTC < X，而 `leadOne.status==False` 超过 T 秒）→ 触发 `EventName`（软告警，或 FCW 视觉/声音）；`radarTempUnavailable` 已存在，可复用。
- 优点：不改控制行为，风险最低，可作为方案 A/C 的数据采集阶段（只记录不干预 → 用 rlog 离线评估误报）。
- 缺点：只是提醒驾驶员。
- 工作量：小。

### 方案 E — 恢复原车雷达（不推荐）
- 不 DISABLE_RADAR 则无法发自己的 `ACC_CONTROL`（[推断] RAV4 的 ACC_CONTROL 由雷达直接发到 PCM，comma 线束不在这条路径上，无法拦截——这也是上游选择 UDS 静默的原因；repo 内无线束拓扑文档可佐证）；等于放弃 Alpha Long。

## 3. 推荐顺序

1. **验证前提（用户/实车）**：回答 Q1/Q2/Q3/Q5（见 §5）。没有 Q1 的肯定答案，A/C/D 都无从谈起。
2. **方案 0**：开启 `ToyotaRadarMode=2`（或 1），收 rlog。
3. **方案 D（仅记录）**：先加"雷达目标 vs leadOne 不一致"的日志/事件，不干预控制，用真实路测数据统计误报率与漏检场景。
4. 数据支撑后做 **方案 C**（雷达制动 cap，只减不增）作为"保险"；如需更自然的跟车再做 **方案 A**。
5. 方案 B 合并进 A。

理由：C 提供与视觉解耦的下限保护，失效模式最安全；A 改变 MPC 输入，影响面更大，宜在 D 数据验证后再做。

## 4. 测试计划（供实现阶段使用）

- 单元：`radar_brake_cap` 纯函数表驱动（距离/速度/加速度/IN_PATH/CLASS/过期），断言单调：`cap <= 0` 且在 `[-2.5, 0]`。
- 离线回放：`selfdrive/test/process_replay/` 现有框架 + 含 ARS510 的 rlog；比较启用前后 `aTarget`：只能降低，不能升高。
- 回归：`opendbc_repo/opendbc/car/toyota/tests/test_ars510.py`、`selfdrive/controls/tests/test_longitudinal_planner.py` 保持通过；对非 ARS510 车型输出逐位一致（参考 `test_radar_selection_is_opt_in...` 的写法）。
- 实车：先"仅记录"模式跑一周；再开 cap，场景含：前车急刹、切入、弯道相邻车、桥/护栏、停车场低速、雷达掉线（拔 bus 1 或 Mode 切换）。

## 5. 需要用户回答 / 提供的信息

| # | 问题 | 为什么重要 |
|---|------|------------|
| Q1 | Alpha Long 下 rlog 里 bus=1 的 0x80/0x235/0x237/0x239 是否有数据（频率）？ | 决定所有方案是否可行 |
| Q2 | 是否装了 radar CAN filter / sDSU（指纹有 0x2AA/0x2FF）？ | 影响 `DISABLE_RADAR` 是否置位（01 §7） |
| Q3 | 当前 `ToyotaRadarMode` 值？ | 若为 0，现状是预期行为，先走方案 0 |
| Q4 | "雷达建议"指 stock ACCEL_CMD / 雷达看到的前车 / stock AEB 哪一个？ | 决定做 A/C 还是仅 D |
| Q5 | `carParams.carFw` 里 `fwdRadar` 版本？（解析器针对 `8821F0R03100`） | 协议兼容性 |
| Q10 | 可接受的最大干预减速度（建议上限 -2.5 m/s²）？是否愿意先走"仅记录"阶段？ | 方案 C/D 参数 |

## 6. 不在本次范围

- 不改代码、不改 panda 固件；不声称恢复 AEB；不改 `DISABLE_RADAR` 流程。
- 若用户确认要实现，建议拆成：(1) 方案 D 的日志/事件 PR；(2) 方案 C 的 PR（含 schema 变更评估）。
