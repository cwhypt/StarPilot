# 01 — Repo 结构与 Alpha Long 下的雷达信号路径

状态：完成（仅静态阅读代码，未跑实车 / rlog）。分支 `claude/quirky-goodall-sumqk9`，基于 commit `823a5d5`。

## 1. Repo 结构（与本问题相关的部分）

StarPilot 是 openpilot 的 fork（FrogPilot 系），主要目录：

| 路径 | 作用 |
|------|------|
| `opendbc_repo/opendbc/car/toyota/` | Toyota 车型层：`interface.py`（CarParams）、`carstate.py`、`carcontroller.py`（发 CAN）、`radar_interface.py`、`ars510_*.py`（本 fork 新增的 RAV4 雷达解析） |
| `opendbc_repo/opendbc/safety/modes/toyota.h` | panda 侧安全固件（TX 白名单、AEB/ACC 相关消息放行） |
| `opendbc_repo/opendbc/dbc/toyota_tss2_ars510.dbc` | ARS510 雷达总线 DBC（0x235/0x237/0x239/0x23B ACC target） |
| `selfdrive/car/card.py` | 进程 `card`：加载 CarInterface/RadarInterface，发布 `carState`、`liveTracks` |
| `selfdrive/controls/radard.py` | 融合雷达轨迹 + 视觉 leads，产出 `radarState.leadOne/leadTwo` |
| `selfdrive/controls/lib/longitudinal_planner.py`（3240 行） | 纵向规划，消费 `radarState` |
| `selfdrive/controls/lib/longitudinal_vehicle_tunes.py` | 按车型的纵向调参，含 `is_toyota_rav4_tss2_radar_follow_lead` |
| `selfdrive/controls/lib/longitudinal_mpc_lib/` | 纵向 MPC |
| `starpilot/` | StarPilot 自有逻辑（toggles `starpilot_variables.py`、`starpilot_planner.py`、Galaxy web UI 等） |
| `common/params_keys.h` | 参数表（`ToyotaRadarMode`、`ExperimentalLongitudinalEnabled`、`AlphaLongitudinalEnabled`） |
| `docs/TOYOTA_RAV4_RADAR.md` | 已有的 RAV4 雷达（ARS510）功能说明 |

## 2. 目标车型的识别

- RAV4 Hybrid 2023-25 → `CAR.TOYOTA_RAV4_TSS2_2023`，flags = `RADAR_ACC | ANGLE_CONTROL`（`values.py:306-313`）[代码]。
- `RADAR_ACC`：ACC 由前向雷达 ECU 计算并经 `ACC_CONTROL`(0x343) 发给 PCM，而不是由摄像头 [代码]。
- `ARS510_RADAR_CAR = {RAV4_TSS2_2022, RAV4_TSS2_2023}`（`values.py:624`）。

## 3. 开关链：Alpha Long 打开后发生了什么

```
UI/Galaxy: ExperimentalLongitudinalEnabled (bool)
  └─ system/manager/manager.py:926-928  复制为 AlphaLongitudinalEnabled
      └─ selfdrive/car/card.py:138  alpha_long_allowed
          └─ opendbc car_helpers.get_car → CarInterface.get_params(alpha_long=True)
```

在 `opendbc_repo/opendbc/car/toyota/interface.py`：

1. `interface.py:151-157`：RAV4 属于 `RADAR_ACC_CAR`，所以 `alphaLongitudinalAvailable = True`；
   没有 sDSU 时，`alpha_long and candidate in RADAR_ACC_CAR` → 置 `ToyotaFlags.DISABLE_RADAR` [代码]。
2. `interface.py:168-171`：`openpilotLongitudinalControl = ... or DISABLE_RADAR` → True [代码]。
   即：**Alpha Long 的实现方式就是把原车雷达 ECU 关掉，自己发 ACC_CONTROL**。
3. `interface.py:229-234` `init()`：对雷达 ECU（`addr=0x750, sub_addr=0xF`）发 UDS `COMMUNICATION_CONTROL / ENABLE_RX_DISABLE_TX`，让它**不再发送正常报文**；`deinit()` 时恢复 [代码]。
4. `carcontroller.py:630-632`：每 20 帧发 TesterPresent 保持静默；`carcontroller.py:627-628` 另外代发 `PCS_HUD`/FCW（`create_fcw_command`）[代码]。
5. `carstate.py:129`、`carstate.py:200`：`DISABLE_RADAR` 时**不再解析** `PRE_COLLISION`(stockAeb) 和 `PCS_HUD`(stockFcw)、不从 `ACC_CONTROL` 读 `ACC_TYPE` [代码]。
   → stock AEB/FCW 信息在 Alpha Long 下整体消失（`docs/TOYOTA_RAV4_RADAR.md:22-24` 也明确写了"Alpha Long still disables the factory AEB output"）[文档]。

**结论 A**：用户观察到的"雷达 ACCEL_CMD 被静默"是 Alpha Long 设计上的必然结果，不是 bug。雷达 ECU 的 ACC 控制器输出（含 `ACCEL_CMD`、`PERMIT_BRAKING`、`MINI_CAR` 等）从 CAN 上整个消失，`carcontroller.py:583` 里 openpilot 自己发的 `ACC_CONTROL` 取而代之。
`CarState`/`carState` 中本来就没有任何字段保存 stock `ACCEL_CMD`（`grep ACCEL_CMD` 只在 `toyotacan.py:49,64` 的发送端出现）[代码]。

## 4. 雷达"感知数据"的两条独立路径

要区分两种东西：

- (a) 雷达 ECU 内部 ACC 控制器算出的**加速度命令**（`ACC_CONTROL.ACCEL_CMD`，在 bus 0 上）——Alpha Long 下被静默，且没有任何替代来源。
- (b) 雷达的**目标/物体数据**（距离、相对速度、横向位置）——这是 openpilot 可以自己解析并喂给 radard 的原料。

### 4.1 默认状态：RAV4 完全无雷达输入

- `interface.py:145`：`radarUnavailable = Bus.radar not in DBC[candidate] or ...`；测试 `test_ars510.py:test_default_rav4_remains_vision_only` 断言默认 RAV4 `radarUnavailable == True` 且 `ars510 is None` [代码]。
- 于是 `RadarInterface.update` 走 `super().update(None)`（`radar_interface.py:73-74`），`liveTracks` 里没有 points → radard 的 `tracks` 为空 → `get_lead` 只能走 `get_RadarState_from_vision`（`radard.py:346-347`）。
- 这就是用户所说"只用了 gamma 的视觉结果"：**Alpha Long 开启 + `ToyotaRadarMode=0`（默认）时，前车信息 100% 来自视觉 modelV2.leadsV3**。

### 4.2 本 fork 新增的可选路径：`ToyotaRadarMode`

- 参数 `ToyotaRadarMode`：0=Off(默认)，1=ACC Target，2=Fused Tracks（`common/params_keys.h:807`，`starpilot_variables.py:653`，Galaxy UI 文案在 `device_settings_layout.json:3675`）。
- `interface.py:25-31`：`candidate in ARS510_RADAR_CAR and mode in (1,2)` → 置 `ARS510_RADAR`（及 `ARS510_FUSED`），并把 `radarUnavailable=False`。
- `radar_interface.py:45-49`：选择解析器：
  - Mode 1 → `ars510_acc_target.py`：读 **bus 1** 的 `ACC_TARGET_1/2/3`(0x235/0x237/0x239)，只发布**雷达 ACC 当前跟随的那一个目标**（STATE==5 且 PRESENT），每雷达周期一个点。
  - Mode 2 → `ars510_radar.py`：重组 **bus 1** `0x80` 的 742 字节物体列表（20 个槽），用 Kalman 融合 + ACC 目标锚定 + 路径门限，发布多条轨迹。
- 两者的数据都来自 **bus 1**（`RADAR_BUS = 1`），不是 bus 0 [代码]。

**这是目前 repo 里唯一能把"原车雷达判断的前车"送进 openpilot 的通道**，并且是**感知级**数据（dRel/yRel/vRel），不是 `ACCEL_CMD`。

### 4.3 [待验证] DISABLE_RADAR 之后 bus 1 上还有没有雷达数据

`docs/TOYOTA_RAV4_RADAR.md:21-25` 声称"does not … alter the existing radar-disable procedure"，并要求在 Alpha Long 配置下收 rlog 验证 [文档]。
代码里没有任何证据表明 `COMMUNICATION_CONTROL ENABLE_RX_DISABLE_TX`（只关"正常报文"的 TX）会/不会同时停掉雷达在 bus 1 上的 0x80/0x235… 报文。物理上 bus 1 是什么（雷达专用口 / 转接线）也没有在 repo 里说明。
→ 这是整个方案能否成立的**第一个硬前提**，必须由实车 rlog 回答（见第 7 节 Q1）。

## 5. 数据如何进入规划（只列到 radard，下一步展开）

```
card.py:149  RI = RadarInterface(CP)          → liveTracks (points: trackId,dRel,yRel,vRel)
radard.py:606-625  每个 point 建 Track(Kalman)
radard.py:652-662  get_lead(...) 生成 radarState.leadOne / leadTwo
longitudinal_planner.py:2116-2117  lead_one/lead_two = radarState.leadOne/leadTwo → MPC
```

`radard.get_lead`（`radard.py:328-407`）的关键行为（详见后续文档 02）：

- 只有在 **视觉 lead 概率 > `lead_detection_probability`(参数 `LeadDetectionThreshold`，默认 35 → 0.35，clip 到 0.25-0.5，`starpilot_variables.py:1365-1369`)** 时才会尝试把雷达 track 与视觉 lead 配对（`radard.py:337-341`）；配对失败则退回纯视觉 lead。
- **没有视觉 lead 时，雷达 track 只在 `v_ego < V_EGO_STATIONARY`(=4 m/s ≈ 14 km/h，`radard.py:29`) 且 `|yRel|<1`、`0.75<dRel<25` 的低速场景下才会被单独采用**（`potential_low_speed_lead`，`radard.py:209-212`、`349-399`）。
- 即使 Mode 2 打开，高速时"视觉没看到、雷达看到"的车**不会**成为 leadOne。

**结论 B**：即使把 `ToyotaRadarMode` 打开，雷达在当前架构里也只是视觉 lead 的"校正器"（提供更准的 dRel/vRel），而**不是独立的安全冗余**。这与用户"不够保险"的担心吻合——但原因不只是 ACCEL_CMD 被静默，还包括 radard 的"视觉先行"设计。

## 6. 现状小结

| 场景 | 前车来源 | 备注 |
|------|----------|------|
| 不开 Alpha Long（stock ACC） | 原车雷达 ECU 全权负责 | openpilot 只做横向 |
| Alpha Long + Mode 0（默认） | 仅视觉 | `radarUnavailable=True`，雷达零输入；stock AEB/FCW 同时丧失 |
| Alpha Long + Mode 1/2 | 视觉为主，雷达轨迹仅在与视觉 lead 匹配时替换其距离/速度；低速可单独采用 | 前提：bus 1 数据在 DISABLE_RADAR 后仍存在（未验证） |

## 7. 未决问题（需要实车/rlog 或用户确认）

- **Q1**：Alpha Long（DISABLE_RADAR 已生效）时，bus 1 上 0x80 / 0x235 / 0x237 / 0x239 是否仍以正常频率出现？（rlog 里看 `can` bus=1 的地址统计。）
- **Q2**：用户车是否装了 radar CAN filter / sDSU（`0x2AA`/`0x2FF` 指纹）？若有，`use_sdsu=True` 路径不同：`DISABLE_RADAR` 不会被置位（`interface.py:154-159`），雷达没有被 UDS 静默，而是被硬件拦截。需要用户确认硬件配置。
- **Q3**：用户当前 `ToyotaRadarMode` 是多少？（`/data/params/d/ToyotaRadarMode`）若是 0，则"只用视觉"是预期行为，第一步只需打开 Mode 2/1。
- **Q4**：用户所说"原车雷达建议"指的是哪一种：(i) stock `ACCEL_CMD` 本身；(ii) 雷达看到的前车（距离/相对速度）；(iii) stock AEB/FCW。三者可行的补救手段完全不同（见 02-04 文档）。
- **Q5**：车辆固件：ARS510 解析针对 fwdRadar `8821F0R03100`（见 `ars510_radar.py` 文件头）；用户 2025 款 hybrid 的雷达 fw 是否一致？（`CarParams.carFw` 里查 `fwdRadar`。）

## 8. 下一步

文档 02：逐条梳理 `radard.get_lead` / `match_vision_to_track` 对 RAV4 + ARS510 轨迹的采用条件，列出雷达看到车而 leadOne 不采用的所有分支，并构造最小例子验证（可用现有 `test_ars510.py`、`selfdrive/controls/tests` 的 fixture 做离线复现）。
