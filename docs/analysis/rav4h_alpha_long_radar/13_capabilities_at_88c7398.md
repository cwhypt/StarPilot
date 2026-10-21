# 13 — 按当前 StarPilot 代码（含 88c7398 ARS510 集成，HEAD 823a5d5）能做到 / 做不到什么

状态：完成（一手代码）。车：RAV4 Hybrid 2025（`TOYOTA_RAV4_TSS2_2023`）。未包含上游 StarPilot#230。

## 1. 三个开关

- 硬件：是否装 radar CAN filter（在 bus 0 发 0x2FF/0x2AA，08）。
- `ExperimentalLongitudinalEnabled`（Alpha Long）。
- `ToyotaRadarMode`：0 Off(默认) / 1 ACC Target / 2 Fused Tracks（重启生效，Galaxy → Vehicle → RAV4 Radar）。与 Alpha Long 相互独立（`interface.py:25-31` 在 `_get_params` 之后单独置位；`test_ars510.py` 覆盖 alpha_long 真/假）。

## 2. 配置矩阵

| # | 过滤器 | Alpha Long | 纵向谁控 | StarPilot 纵向功能（导航减速、红灯/停止标志 Force Stop、限速、弯道） | 原车 AEB | 原车 FCW | 雷达数据进 StarPilot |
|---|---|---|---|---|---|---|---|
| A | 无 | 关 | 原车 ACC | ✗ | ✓ | ✓（仪表；StarPilot 不另报，`events.py:519` 空）| Mode 1/2 时 ✓，仅用于显示/横向辅助类（不控纵向）|
| B | 无 | 开 | StarPilot（UDS 关雷达） | ✓ | ✗（仪表 PCS 故障，据上游） | ✗；StarPilot 只代发 FCW 显示位（`carcontroller.py:627`），由 planner/model FCW 触发 | Mode 1/2 时 ✓（bus 1，据上游在 UDS 关闭后仍发） |
| C | 有 | 开 | StarPilot（雷达不关） | ✓ | ✓ | ✓ | Mode 1/2 时 ✓ [推断，过滤器不碰 bus 1] |
| D | 有 | 关 | 原车 ACC（过滤器透传） | ✗ | ✓ | ✓ | 同 A |

## 3. 现在就能做到（C 配置）

1. StarPilot 的全部纵向功能（导航、Force Stop、Speed Limit Controller、Curve Speed、CEM 等）——只依赖 `openpilotLongitudinalControl=True`。
2. 原车 AEB/PCS 仍在；`stockAeb` 被解析（`carstate.py:129`）→ `car_specific.py:254` → "BRAKE! Stock AEB" 最高优先级告警 + 禁止接管（`events.py:605-611`）。
3. 原车 FCW 仪表提示（雷达 0x411 透传）。
4. 识别过滤器、雷达不被禁用、Alpha Long 生效（`interface.py:73-76,154-171`）。
5. 雷达数据进入 radard（Mode 1/2）：
   - 与视觉 lead 匹配时，用雷达的距离/速度替代视觉估计 → 跟车更准，并启用 RAV4 专用雷达跟车调参（`is_toyota_rav4_tss2_radar_follow_lead`）、跳过一系列 vision_* 谨慎 cap（03 §3）。
   - 车速 < 4 m/s 时，正前方雷达目标即使视觉没确认也会被采用（低速防撞，02）。
6. 雷达掉线检测：Mode 1/2 下雷达停发 >0.5 s → `radarTempUnavailable` 告警（`selfdrived.py:685`）。
7. comma 失效时（过滤器 250 ms 没看到 openpilot 的 0x343）自动回落原车 ACC 指令 [据 smart_dsu 固件转述]。

## 4. 现在做不到

1. **取 min(原车 ACC_CONTROL, StarPilot)**：OneClone/smart_dsu 不转发雷达 0x343；StarPilot 也无读取代码（10）。
2. **"雷达觉得该减速"的独立兜底**（中间地带）：radard 视觉先行——视觉概率 < `LeadDetectionThreshold`(0.35) 或测距差 > 25% 时、且车速 ≥ 4 m/s，雷达看到的车**不进入规划**（02 §2-3）。需要 04 方案 C（未实现）。
3. **原车 AEB 与 StarPilot 的协同**：`stockAeb` 只产生告警，不触发 StarPilot 退出或主动减速（事件无 SOFT/IMMEDIATE_DISABLE）；AEB 期间 StarPilot 的 0x343 可能仍请求加速，由车辆制动系统仲裁 [推断，未验证]。
4. Mode 1 已知问题（据上游 StarPilot#230 描述，未在本 repo 修复）：`TARGET_ID` 应为 6 bit（本 repo DBC 为 `60|5`）、`LAT_DIST` 回绕。可能导致目标切换识别错误、横向位置错误 → 雷达与视觉匹配失败而退回视觉。
5. Mode 2（Fused）：新目标需存活 ~3.6 s 才发布（切入车初期不可见），>60 m 只发雷达 ACC 目标；上游已计划用 "ACC Target + Summaries" 取代（#230）。
6. Mode 1 单点、约 16 Hz，radard 20 Hz 运行时同一测量会被 Kalman 吃两次（02 §4），`REL_ACCEL`/`IN_PATH`/`CLASS` 未使用。
7. 无过滤器（B）时无法恢复原车 AEB/FCW。
8. 指纹时序风险：过滤器的 0x2FF 依赖雷达已开始发 0x343；雷达启动延迟时可能被判为无过滤器 → 回到 B（08 §4）。

## 5. 结论

当前代码下，**C 配置 + Mode 1** 已经实现"StarPilot 全部功能 + 原车 AEB/FCW + 雷达辅助跟车（需视觉确认）"。剩余缺口是"视觉没确认时的雷达减速兜底"与"min 融合"，以及 Mode 1 的两个解析修复。
