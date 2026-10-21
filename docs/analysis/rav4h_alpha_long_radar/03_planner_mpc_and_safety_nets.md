# 03 — Planner / MPC 对 lead 的消费，以及"视觉无 lead"时剩下的安全网

状态：完成（静态阅读）。前置：[01](01_architecture_and_signal_path.md)、[02](02_radard_fusion_gating.md)。

## 1. 结论先行

1. planner/MPC **只看 `radarState.leadOne/leadTwo`**，不区分 lead 来自视觉还是雷达，除少数 `radar` 标志分支（见 §3）。所以"雷达建议"在这个架构里**没有独立通道**；要让雷达起作用，只能改 radard 的输出（leadOne/leadTwo）或在 planner 末端加雷达专属的 cap。
2. 与 `ACCEL_CMD` 对应的东西在 openpilot 侧是 `longitudinalPlan.aTarget → carcontrol actuators.accel → carcontroller.py:583 ACC_CONTROL.ACCEL_CMD`。雷达 ECU 原本算的那一份被 `DISABLE_RADAR` 关掉了，**没有、也无法恢复**（01 文档 §3）。
3. 视觉无 lead 时，剩下的安全网非常薄：
   - Planner FCW 要求 `modelProb > 0.9`（`long_mpc.py:138,210-219`），雷达-only 的 lead（低速 override 产生）`modelProb=0`，**不会触发 FCW**；
   - 模型自己的 `hardBrakePredicted`（`selfdrived.py:787`）仍然有效，但来自视觉；
   - stock AEB/FCW 在 `DISABLE_RADAR` 下不可用（`carstate.py:129,200`；`docs/TOYOTA_RAV4_RADAR.md:22-24`）。openpilot 只代发 `PCS_HUD` 的 FCW 显示位（`carcontroller.py:627-628`），不产生制动。
   - panda 的 `ALT_EXP_ALLOW_AEB`(`toyota.h:408,582`) 仅在 `ToyotaAutoHold` 且 `uses_toyota_auto_hold_aeb(CP)` 的车型被置位（`interface.py:178-183`），`TOYOTA_AUTO_HOLD_CARS = (TSS2_CAR - RADAR_ACC_CAR - SECOC_CAR) | {RAV4, RAV4H}`（`values.py:630-633`），`RAV4_TSS2_2023` 是 `RADAR_ACC_CAR` 且不在补充集合里 → **不适用**。

## 2. 调用链

```
plannerd.py → longitudinal_planner.update(sm)
  :2116-2117  lead_one/lead_two = sm['radarState'].leadOne/leadTwo
  :2118-2150  各种 *_follow_lead 判定（含 is_toyota_rav4_tss2_radar_follow_lead，仅在 lead.radar==True 时生效）
  :2378       self.mpc.update(sm['radarState'], v_cruise, x, v, a, j, ..., tracking_lead=lead_control_active, ...)
  :2400       self.fcw = should_publish_planner_fcw(self.mpc.crash_cnt, carState, radarState)
  :2480+      一系列 vision_* cap（只对 radar==False 的 lead 生效）
  → longitudinalPlan.aTarget / shouldStop / fcw
controlsd → carControl.actuators.accel → card → CarController.update
  carcontroller.py:493-575  rate limit → long_pid → permit_braking → 各类 limit_*
  carcontroller.py:583      create_accel_command → ACC_CONTROL (0x343) @ bus 0
```

## 3. 代码里已经"期待"雷达 lead 的地方

这些分支在 RAV4 + Alpha Long + Mode 0 下**永远不触发**（因为不存在 `radar=True` 的 lead）：

| 位置 | 作用 |
|------|------|
| `longitudinal_vehicle_tunes.py:579-601` `is_toyota_rav4_tss2_radar_follow_lead` | "Keep a credible RAV4 radar lead active through model-horizon dropouts"：雷达 lead 在 70–100 m、闭合速度 ≥0.75 m/s 时，即使模型 horizon 丢失也保持跟车。注释说明 RAV4 的调参**本来就是为有雷达输入设计的**。 |
| `longitudinal_planner.py:2124-2127` | `rav4_radar_follow` 使用上面的函数 |
| `long_mpc.py:277-` `soften_far_radar_lead_accel` | 对远处雷达 lead 的负加速度做衰减 |
| `longitudinal_planner.py` 约 30 处 `getattr(lead,"radar",False)` | 雷达 lead 绕过各种 vision_* 谨慎 cap（"vision close stop hold / settle / final guard / untracked approach lift"等） |

反过来：**视觉 lead 会被施加大量额外的谨慎 cap**（`get_vision_*`，`:2495-2531, :2893-2931`），这些 cap 是对视觉测距不确定性的补偿。若能提供可信的雷达 lead，这些 cap 会自动让位——这是 Mode 1/2 的真实收益之一，而不仅仅是"多一个冗余"。

## 4. 闭环里 stock 雷达与 openpilot 的责任对比（Alpha Long 开/关）

| 能力 | Stock ACC（Alpha Long 关） | Alpha Long（现状） |
|------|---------------------------|---------------------|
| 前车检测 | 雷达 ECU（+ 摄像头融合，Toyota 内部） | 视觉模型；可选 ARS510 解析（Mode 1/2） |
| 加速度命令 | 雷达 ECU 的 ACC → `ACC_CONTROL.ACCEL_CMD` | openpilot MPC → PID → `ACC_CONTROL.ACCEL_CMD` |
| PCS/AEB | 开启（雷达 + 摄像头） | **关闭**（雷达 TX 被禁） |
| FCW | stock `PCS_HUD.FCW` | 仅 planner/model FCW（见上）|
| 雷达掉线检测 | stock 自带 | 仅 Mode 1/2 的 `radarUnavailableTemporary`（Mode 0 为"永久不可用"，不报警）|

## 5. "雷达建议"可以被还原成什么

雷达 ECU 的 `ACCEL_CMD` 不可得。可以从 ARS510 总线数据重建的是**目标态**：

- `ACC_TARGET_1`(0x235)：`REL_SPEED`、`REL_ACCEL`（目标相对加速度）、`TARGET_ID`、`STATE`；
- `ACC_TARGET_2`(0x237)：`LONG_DIST`、`LAT_DIST`、`IN_PATH`、`IN_PATH_LEVEL`；
- `ACC_TARGET_3`(0x239)：`CLASS`(car/truck)、`PRESENT`、`TIMESTAMP`；
- `0x80`：20 目标列表（Mode 2）。

由这些量，openpilot 可以**自己**计算一个"雷达视角的需求减速度"（例如 `a_req = -v_rel²/(2·max(d - d_min, ε))`，叠加目标自身 `REL_ACCEL`），作为**只减不增**的 cap 与视觉路径取 `min`。这与 stock 雷达 ECU 的 `ACCEL_CMD` 在数值上不会相同，但语义（"前方有车时的制动建议"）等价，且有单调安全性：cap 只能让车更保守。详见 04。

## 6. 风险与限制（影响所有方案）

- **R1 误刹**：雷达对护栏/桥/静止物体、相邻车道车的误判（`ars510_radar.py` 已为此加了路径门限和年龄门限；Mode 1 没有这些过滤，但目标是雷达自己的 ACC 目标，已经过 Toyota 的 in-path 判定）。任何新增 cap 必须限幅（例如最大 -2 m/s²，且仅当 TTC 小于阈值），并在 `radarUnavailableTemporary` / 数据过期时自动失效。
- **R2 无 AEB 的现实**：没有任何软件 cap 能替代 AEB；本分析的目标是"让规划层也能看到雷达"，不是声称恢复了 AEB。文档/UI 不应暗示恢复。
- **R3 前提未验证**：见 01 的 Q1（DISABLE_RADAR 后 bus 1 是否仍有数据）。

## 7. 未决问题

- **Q8**：`carControl.hudControl.leadVisible`（`carcontroller.py:476`，用于 `MINI_CAR`/`lead` 以及 `update_permit_braking`）来自 `longitudinalPlan.hasLead`（`controlsd.py:880`，由 planner 根据 leadOne/leadTwo 状态得出）；换成雷达 lead 后 `permit_braking` 行为会改变（`TOYOTA_NO_LEAD_COAST_BRAKE_ACCEL` 分支）。需要在实车上确认不会引入点刹。
- **Q9**：panda 安全固件对 `ACC_CONTROL` 的 accel 限幅（`toyota.h`）是否对新增 cap 无影响（只会更保守，应无影响）。

## 8. 下一步

文档 04：方案对比与建议——A) 仅启用 Mode 1/2；B) radard 放行"雷达自确认 lead"；C) planner 末端雷达 cap（只减不增）；D) 一致性监控/降级与告警；并给出推荐顺序、改动点、测试计划（含离线回放）。
