# 02 — radard：雷达轨迹何时被采用 / 被丢弃

状态：完成（静态阅读）。前置：[01](01_architecture_and_signal_path.md)。源文件：`selfdrive/controls/radard.py`。

## 1. 结论先行

radard 是"**视觉先行、雷达校正**"的设计，不是"雷达/视觉各自独立、取更保守者"。
对 RAV4 + Alpha Long，即使 `ToyotaRadarMode` 开到 1 或 2：

1. 雷达 track 只能**替换/修正**一个已经被视觉模型确认的 lead 的距离与速度；
2. 视觉没看到而雷达看到的车，**只有 `v_ego < 4 m/s` 时**才会成为 `leadOne`（`leadTwo` 完全没有这条通道）；
3. 因此"视觉漏检 / 低置信度"的高速场景下，雷达看到的车对规划**不可见**。这正是"不够保险"的核心。

## 2. get_lead 的决策表（`radard.py:328-407`）

输入：`tracks`（雷达轨迹）、`lead_msg`（modelV2.leadsV3[i]）、`filtered_lead_prob`（经 `lead_prob_filters` 的视觉概率）。
`thr = lead_detection_probability`（`LeadDetectionThreshold`，默认 0.35，clip 0.25–0.5）。

| # | 条件 | 结果 | 代码 |
|---|------|------|------|
| 1 | `tracks` 非空 ∧ `ready` ∧ `prob > thr` | 尝试 `match_vision_to_track`；成功 → 用**雷达 Track** 输出（`radar=True`，`modelProb=视觉概率`） | `:337-345` |
| 2 | 同上但匹配失败 | 退回**纯视觉**（`radar=False`） | `:346-347` |
| 3 | `prob <= thr`（视觉没有 lead） | `status=False`，**雷达轨迹被忽略** | `:343` |
| 4 | `low_speed_override=True`（仅 leadOne）∧ 存在 `potential_low_speed_lead` 的轨迹 | 取最近的雷达轨迹，**覆盖/补充**上面的结果（只有当它比当前 lead 更近或当前无 lead） | `:349-399` |
| 5 | leadTwo 调用 `low_speed_override=False` | leadTwo 永远不会是"视觉没看到的雷达车" | `:658-662` |

`potential_low_speed_lead`（`:209-212`）：`|yRel|<1.0 ∧ v_ego<V_EGO_STATIONARY(4.0 m/s) ∧ 0.75<dRel<25`。

## 3. 视觉-雷达配对门限（`match_vision_to_track`，`:270-305`）

1. 在所有 track 里选 `vision_track_probability`（距离/横向/速度三项 Laplace 相似度之积）最大者。
2. 必须通过 `track_matches_vision(dist_scale=0.25, dist_floor=5, vel_limit=10, y_std_scale=1, y_floor=1)`：
   - 距离：`|dRel - (vision_x - 1.52)| < max(25%·d_vis, 5 m)`（`RADAR_TO_CAMERA=1.52`）；
   - 速度：`|vRel+v_ego - v_vis| < 10` **或** 雷达绝对速度 > 3 m/s；
   - 横向：`|yRel + y_vis| < max(1.0, yStd)`。
3. 失败后，对上一帧选中的 track（`cnt>=3`）用放宽门限再试（`dist_scale=0.40, floor=8, vel 13, y_std 2, y_floor 1.5`）。
4. `human_lane_changes` 开启且换道开始时，按方向过滤 `yRel` 符号（`:274-280`）。

含义：**视觉的距离估计偏差 >25% 时，正确的雷达 track 反而被拒绝，退回错误的视觉距离**。视觉测距在远距离、坡道、夜间误差大，是最需要雷达纠正的地方，而门限恰好在此处把雷达拒之门外（此处是风险点，详见 04 文档方案 B）。

## 4. 进入 radard 之前，ARS510 解析器自己的过滤

### Mode 1（ACC Target，`ars510_acc_target.py`）
- 每个雷达周期（~60 ms，ACC_TARGET 50 Hz 报文但内容 60 ms 更新一次）**至多发布 1 个点**：`STATE==5 ∧ PRESENT`（`:50`）。
- `aRel = yvRel = NaN`，`measured=True`（`:56-57`）。`ACC_TARGET_1.REL_ACCEL` / `IN_PATH` / `IN_PATH_LEVEL` / `CLASS` 等 DBC 里已有的信号**全部未使用** [代码]。
- 注意：radard 以 ~20 Hz（modelV2）运行，而雷达点约 16 Hz；Mode 1 在两次扫描之间不发点，`RadarInterface.update` 返回 `None`（`:34-36`），`card.py` 不会发新的 `liveTracks`，radard 重复使用上一次的 `liveTracks` 数据 → 同一测量可能被 `Track.update` 的 Kalman 吃两次（只有 Honda Bosch 做了去重，见 `radard.py:601-625`）。影响程度 [推断]：轻微，但会让 `aLeadK` 估计略偏。

### Mode 2（Fused，`ars510_radar.py`）
- `PUBLISH_AGE=60`（~3.6 s）：新出现的 track 需要存活约 3.6 s 才会发布（`:27, :133`）→ **突然切入的车在前 3.6 s 不会出现在雷达轨迹里**（除非它恰好就是雷达 ACC 目标 `acc_tid`，也仍受 `age<60` 的限制，因为判断在 `:133` 对所有 tid 一视同仁）[代码]。
- `PUBLISH_STD=0.75 m/s`：新 track 速度不确定度需先收敛（`:133`）。
- `FAR_ACC_ONLY=60 m`：>60 m 只发布雷达 ACC 正在跟的那一辆（`:136-138`）。
- `PATH_GATE=2.5 m`：>15 m 且偏离 yaw 预测路径 >2.5 m 的 track 被丢（`:136-137`）。静止物体（|v|<0.5 m/s）即使 ≤15 m 也按 1.5 m 门限过滤。
- 需要 0xB4 车速（`EGO_MAX_AGE_S=0.5`）；缺失则 `vrel=NaN` 并不发布（`:127-134`）。

所以 Mode 2 的雷达轨迹本身就是**保守、滞后**的（为了不污染 radard），并不是低延迟的安全冗余。

## 5. radard 之外的两处影响

- `radar_state.radarErrors = rr.errors`（`radard.py:631`）：Mode 1/2 在雷达静默 >0.5 s 后会置 `radarUnavailableTemporary`（`ars510_acc_target.py:38`、`ars510_radar.py:219`），selfdrived 把它转为 `EventName.radarTempUnavailable`（`selfdrived.py:685-686`）。**这给了一个现成的"雷达掉线检测"信号**，可用于方案里的降级判定。
- `liveTracks.valid = not any(RD.errors.to_dict().values())`（`card.py:442`）。

## 6. 场景矩阵（Alpha Long，RAV4H 2025）

| 场景 | 视觉 | 雷达(Mode2) | leadOne |
|------|------|-------------|---------|
| 正常跟车 | ✓ | ✓ 且匹配 | 雷达 Track（好） |
| 视觉距离偏差>25% | ✓(偏) | ✓ | **视觉**（雷达被拒）|
| 视觉漏检（逆光/夜/大车） | ✗ | ✓ | **无**（v_ego≥4）|
| 视觉概率 0.3（<thr） | 弱 | ✓ | **无** |
| 突然切入（<3.6 s） | ✓ | 尚未发布 | 视觉 |
| 低速(<4 m/s)正前方 | ✗ | ✓ | 雷达（override）|
| 雷达/总线 mode=0 | ✓ | ✗ | 视觉 |

## 7. 未决问题

- **Q6**：radard 层面最小改动的"雷达独立 lead"应该放行哪些轨迹？候选：仅 Mode 1/2 的 `acc_tid`（雷达 ACC 自己确认在路径内、IN_PATH=1、CLASS∈{1,2}）。需要决定是否把 `IN_PATH`/`CLASS` 经 `RadarPoint` 带出——`RadarPoint` 结构没有这些字段（见 cereal `car.capnp`），要么扩展 schema，要么在 interface 内部先过滤再发布。
- **Q7**：Mode 1 的重复测量问题是否值得先修（给 `card`/`radard` 加"新鲜度"标志）。

## 8. 下一步

文档 03：planner/MPC 对 `leadOne` 的消费，以及在"视觉无 lead"时系统还剩哪些兜底（FCW、AEB、MPC 对 radar/vision lead 的差异化处理）。
