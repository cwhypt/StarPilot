# 21 — `ToyotaRadarMode=1` 能做什么 / PR #230 到底做了什么

状态：完成（一手读代码，基线 = `stable` 8589b73f + PR #230 b66ba75d，本分支 `94fefd3f` 之后）。**未上车验证**。
场景：RAV4 Hybrid 2025，**不装过滤器**，不做 T2。

## 1. 两个模式的前提与总览

| | A：不开 Alpha Long | B：开 Alpha Long（目标） |
|---|---|---|
| 纵向 | 原车 ACC | StarPilot |
| 横向 | StarPilot | StarPilot |
| 雷达车内总线输出（0x343/0x283/0x344/0x411） | 正常 | UDS 关闭（`DISABLE_RADAR`） |
| `ToyotaRadarMode` 的作用 | 雷达数据进 StarPilot，但纵向归原车，不参与加减速 | 雷达数据参与 StarPilot 的前车判断 |

## 2. B + `ToyotaRadarMode=1`（ACC Target）能做到

1. **StarPilot 全部纵向控制：** 跟车、起停、设定速度；TSS2 无最低巡航速度限制。
2. **依赖纵向控制权的 StarPilot 功能**：导航减速、红灯/停车标志停车（Force Stop）、限速控制、弯道降速、CEM 等。前提是 `openpilotLongitudinalControl=True`；**逐项在 RAV4 上是否生效未核对**。
3. **RAV4 专用纵向调参**（`longitudinal_vehicle_tunes.py`，含 `is_toyota_rav4_tss2_radar_follow_lead`、提前减速 cap、起步后追赶限制）。
4. **雷达 ACC 目标作为前车输入**（每个雷达周期最多 1 个点，字段 trackId/dRel/yRel/vRel；`aRel` 为 NaN，radard 也不读）：
   - 与视觉前车匹配成功（视觉概率 > 0.35，且距离差 <25% 或 5 m、横向在门限内）时，用雷达 Track 的距离/速度替换视觉估计，并让 planner 里 `radar==True` 的分支生效（跳过一系列 vision-only 谨慎 cap）。
   - 匹配失败 → 回到纯视觉。
5. **低速前车兜底**：车速 <4 m/s，正前方（|y|<1 m）0.75–25 m 内的雷达目标，不需要视觉确认就成为前车。
6. **雷达状态提示：**
   - 雷达曾有数据、之后 >0.5 s 无新周期 → `radarTempUnavailable`（**SOFT_DISABLE**，会让 openpilot 降级退出）。
   - **（#230 新增）** 雷达自报 "blocked"（0x196 byte1 bit0 连续 ≥2 周期，如雷达被泥雪遮挡）→ 同样 `radarUnavailableTemporary` → **软退出**。#230 之前没有这条。
7. **StarPilot 自己的预警（FCW）**：规划器判断 TTC<4 s 且视觉概率>0.9，或模型 `hardBrakePredicted` → 屏幕 "BRAKE! Risk of Collision" + 声音；关雷达时还会驱动仪表 FCW 图标（`carcontroller.py:627`）。

## 3. B + Mode 1 做不到（不做 T2 时）

| 项 | 结论 |
|---|---|
| 原车 AEB | **没有**（0x283/0x344 随雷达车内输出被关）。仪表有 PCS 故障灯 |
| 原车 FCW（0x411） | **没有**，同上 |
| 雷达的 `PCS_ALARM`（私有总线 0x100） | **没有被使用**：DBC 有定义，代码不读。该信号在 UDS 关闭后是否仍发送，**没有任何记录证实** |
| 雷达的 `REQUIRED_DECEL`（0x196） | 同上，未使用（T3） |
| 原车 ACC 的减速建议（0x343 `ACCEL_CMD`） | 没有 |
| 视觉没确认的前车（车速 ≥4 m/s） | 雷达看到也不进入规划，不触发 FCW、不减速 |
| 雷达目标的 `aRel`/横向速度 | 不进 radard |
| 雷达私有总线读不到数据 | **不会报警**，静默变成只用视觉（`ars510_acc_target.py:56-61`：`last_record_t` 为空时 `radarUnavailableTemporary=False`）。上车后必须自己看日志确认有 0x235/0x237/0x239 |

## 4. PR #230 做了什么（合并提交 `94fefd3f`；上游 firestar5683/StarPilot#230，仍 OPEN）

| # | 改动 | 文件 | 影响 |
|---|---|---|---|
| 1 | `TARGET_ID` 5 bit → **6 bit**（`61\|6`） | `toyota_tss2_ars510.dbc` | 雷达切换跟随目标的识别；ID≥32 时原来会混淆 |
| 2 | **`LAT_DIST` 回绕修复**：雷达的横向距离只有低 12 bit，左侧 >20.95 m（转弯、路口）会读成偏右 40.96 m；用 ACC_TARGET_2 的校验字节（线性 CRC-8）判断是否回绕并补回 | `ars510_acc_target.py`（`lat_dist_wrapped`） | 弯道/路口处目标横向位置正确，不会被 radard 当成别的车道 |
| 3 | **雷达故障标志**：0x196 byte1 bit0 连续 ≥2 周期 → `radarUnavailableTemporary` 且不发点 | 两个解析器 | 见 §2.6：新增一条软退出路径 |
| 4 | **Mode 2 由 "Fused Tracks" 改为 "ACC Target + Summaries"**：ACC 目标 + 雷达自己 tracker 的最多 2 辆 40 m 内移动车（速度来自 8 个周期距离斜率）；删除旧 Fused 解析器 `ars510_radar.py`；`ARS510_FUSED` 标志 → `ARS510_SUMMARIES` | 新增 `ars510_acc_summaries.py`、`values.py`、`interface.py`、`radar_interface.py` | Mode 2 的行为整体替换；文档推荐 Mode 1 |
| 5 | Mode 2 的点补 `aRel`（0x235 REL_ACCEL）、`yvRel`（若 RadarPoint 有这些字段） | `ars510_acc_summaries.py` | 本 repo 的 radard 不读，无实际效果 |
| 6 | **DBC 补充文档化**：PCS_STATE(0x100)、RADAR_CYCLE、TARGET_SUMMARY_A/B、EVENT_OBJECT/STATE(0x195/0x196)、RADAR_ALIVE、RADAR_BOOT、OBJECT_STREAM(0x680) | `toyota_tss2_ars510.dbc` | **只是定义，除 0x196 的故障位外都没有代码在读** |
| 7 | Galaxy 选项文案：Mode 2 改名；测试同步；上游基准 `fb5b29c` → `7ff9485` | `device_settings_layout.json`、`THIRD_PARTY_NOTICES.md` 等 | — |

**#230 没做：** 读 `PCS_ALARM` / `REQUIRED_DECEL`、任何 FCW/AEB 兜底、放宽视觉门限、取 min。
本分支验证：`test_ars510.py` 502 通过；Toyota 目录 825 通过；未上车。
