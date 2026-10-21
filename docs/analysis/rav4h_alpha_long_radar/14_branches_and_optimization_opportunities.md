# 14 — StarPilot 各分支对比，以及对 RAV4H 2025（暂不装过滤器）值得做的优化

状态：完成（一手 git 分析；上游仓库 firestar5683/StarPilot，取于 2026-10-21）。
前提：用户**暂不装过滤器** → 配置 A（原车 ACC）或 B（Alpha Long，雷达被 UDS 关闭，无原车 AEB/FCW）。见 13。

## 1. 分支格局

上游 `firestar5683/StarPilot` 约 90 个分支，cwhypt fork 是它的镜像。按最后提交时间，2026-10 仍活跃的只有下列几类：

| 分支 | 性质 | 与发布分支关系 | 对本车意义 |
|------|------|---------------|-----------|
| `StarPilot`（= `Dom`，HEAD `823a5d5`，10-15） | **发布分支**：大块合并提交（"Waffles"、"Green Garden"） | — | 用户实际会装的版本；含 ARS510 集成（`88c7398`）和 RAV4 专用纵向调参 |
| `SecretGoodStarPilot`（SGSP，HEAD `2abc15c`，10-20） | **开发分支**，细粒度提交，非常活跃（10-20 单日几十个提交） | 与发布分支**无共同祖先**（独立历史）；树结构大改：代码移到 `openpilot/` 子目录，功能模块化（`starpilot/curve_speed/`、`starpilot/longitudinal/`、`starpilot/aol/` …） | 下一代版本；见 §3 风险 |
| `SGSP-Backup*`、`SGSP-aolTest` | SGSP 的快照 / AOL 实验 | 基于 SGSP | 无 |
| `fix/cem-lead-departure-launch`、`fix/curve-live-input-freshness`、`fix/launch-from-stop`（whoisdomi，10-15/16） | 针对 SGSP 的修复 PR 分支 | 基于 SGSP | 通用驾驶质量（CEM 前车起步、弯道控制器数据新鲜度、起步加速）；launch-from-stop 主要是 Ioniq 6 + 低速转弯加速上限 |
| `bluescreensonly*`、`NikTest`（9-28 ~ 10-06） | 基于旧发布的实验：UI、SLC、平滑弯道、"force stop handoff" | 基于发布分支 | 实验性，不建议直接用 |
| `test_updates`、`hippos`、`boris`、`galaxybt`、`RoadScore`、`diagnostics/*` | 更新器、回放、UI、Galaxy 蓝牙、诊断 | — | 与纵向/雷达无关 |
| `canfd-steer-authority`、`ioniq6-highway-smoothing`、GM/Honda/Ford 等 | 其他车型 | — | 无 |
| PR `#230`（ds-sebastian，`refs/pull/230/head`，2 个提交，基于发布 `823a5d5`） | ARS510 解析器更新 | **可直接合入发布分支**（ahead 2 / behind 0） | **高**，见 §2 |

## 2. 对 RAV4H 2025 有价值的改动 / 机会（按优先级）

### P1 — 移植 PR #230（低风险，直接可合）
- Mode 1 修复：`TARGET_ID` 改为 6 bit（`61|6`）；`LAT_DIST` 13 bit 回绕用 0x237 校验字节检测并补 40.96 m；雷达自报故障（0x196 byte1 bit0 连续 ≥2 周期）→ `radarUnavailableTemporary`。
- Mode 2 改为 "ACC Target + Summaries"（`ars510_acc_summaries.py`）：ACC 目标 + 雷达自己 tracker 的最多 2 个 40 m 内的移动车；为点填 `aRel`（0x235 REL_ACCEL）、`yvRel`。删除旧 Fused（`ars510_radar.py`）。
- 上游基准从 `fb5b29c` 升到 `7ff9485`。
- 文档推荐 Mode 1。

### P2 — 新发现：雷达私有总线上的"原车安全决策"信号（PR #230 的 DBC 已描述，代码未使用）
| 报文 (bus 1) | 信号 | 含义（DBC 注释，据作者实车记录） |
|---|---|---|
| 0x100 `PCS_STATE`，10 Hz | `PCS_ALARM` | 与车内总线原车 PCS 报警（0x283 PCSNOCHG、0x411 FCW）**同一帧开始**，直到结束；7 台车 8 次原车报警均出现，其他时候从不出现 |
| 0x196 `EVENT_STATE` | `REQUIRED_DECEL` (m/s²) | 雷达对"事件目标"计算的**所需减速度**（前车移动/前车停止公式，1.75 m 余量），仅在短 TTC 状态激活（约 0.5 % 的行驶周期） |
| 0x195 `EVENT_OBJECT` | `OBJECT_DIST`、`OBJECT_SPEED`、`OBJECT_ACCEL`、`EGO_TRAVEL_TO_TTC` | 该事件目标（通常是 ACC 车） |
| 0x196 | `RADAR_FAULT` | 雷达自报被遮挡/物体列表丢失 |

据上游 docs/15："On bus 1 … the radar keeps sending its object list, ACC target and **threat states** at full rate"——即 Alpha Long（UDS 关雷达车内发送）下这些仍可读 [二手转述，未实车验证]。

→ 这让**不装过滤器**也能拿回一部分"原车雷达的安全判断"：
- **P2a 雷达 FCW**：`PCS_ALARM` → StarPilot 的 FCW 告警（只提示，不控车）。风险极低，直接弥补配置 B 丢失的原车 FCW。
- **P2b 雷达所需减速度兜底（≈ 你最初要的 min）**：`REQUIRED_DECEL` 有效且新鲜时，`a_cmd = min(a_starpilot, REQUIRED_DECEL)`（只减不增），受 Toyota `ACCEL_MIN`（`CarControllerParams`）限制，不等于原车 AEB 的制动力。需先"只记录"验证误报，再启用。上游把"中继雷达 AEB 决策"标为 theoretical / fork-only，需要自己的安全评审。

### P3 — 只减不增的雷达 cap（04 方案 C）
- 数据：Mode 1 的 ACC 目标（距离、相对速度、REL_ACCEL）。覆盖 P2b 之前的"中间地带"（P2b 只在短 TTC 才激活）。
- 与 P2b 叠加：先 cap 温和减速，P2b 紧急兜底。

### P4 — radard 改进（发布分支）
- 雷达 `aRel`（PR #230 已填）目前 radard 不读（`radard.py:606` 只取 dRel/yRel/vRel/measured）→ 前车加速度仍靠 Kalman 推算。可让 Track 使用雷达 `aRel`。
- Mode 1 单点约 16 Hz、radard 20 Hz → 同一测量被 Kalman 吃两次；参照 Honda Bosch 的新鲜度去重（`radard.py:601-625`）对 ARS510 也启用。
- 视觉门限（0.35 / 25 % 距离）对 ARS510 ACC 目标可放宽或允许独立 lead（04 方案 A/B），需回放数据支撑。

### P5 — 观察项：开发分支 SGSP 对本车的风险
- SGSP 的 `radard.py` 回到接近官方实现：视觉概率阈值**写死 0.5**（发布分支为可调 `LeadDetectionThreshold`，默认 0.35），去掉了 preferred-track 连续性 → 雷达更难被采用。
- SGSP 中**找不到 RAV4 专用纵向调参**（发布分支 `longitudinal_vehicle_tunes.py` 有 65 处 RAV4 相关，含 `is_toyota_rav4_tss2_radar_follow_lead`；SGSP 只有 AOL/横向/radar_preferences 的 RAV4 代码）。
- SGSP 的 ARS510 仍是旧 Fused 版本 + 新增 `ars510_preferences.py`，未含 #230。
- 结论：SGSP 若成为下一个发布版，本车纵向行为可能变化/回退；在其稳定前建议留在发布分支，并在其上做 P1-P4。

### 不建议 / 低优先
- `fix/launch-from-stop`：主要针对 Ioniq 6；其中"低速转弯加速上限 1.7 m/s²"放开是通用改动，但基于 SGSP 结构，无法直接搬到发布分支。
- 官方 openpilot 的 `A_CRUISE_MIN -0.5`、"降低设定速度去延迟"（SGSP 已同步）：发布分支有自己的 `A_CRUISE_MIN = -1.0` 和巡航逻辑，属于调参风格选择，不是本车问题。
- `bluescreensonly*` 实验分支：未合入任何主线，不跟。

## 3. 建议的实施顺序（不装过滤器）

1. 在本分支合入 PR #230（P1），跑 `opendbc_repo/opendbc/car/toyota/tests/test_ars510.py`。
2. P2a：雷达 PCS_ALARM → FCW 告警（只告警）。
3. P2b + P3：只记录模式（日志里同时写 StarPilot 的 aTarget、雷达 REQUIRED_DECEL、cap 值），实车收集后再决定是否启用。
4. P4 radard 小修。
5. 持续跟踪 SGSP，在它发布前评估 RAV4 调参与雷达门限是否回退。

## 4. 局限

- 所有雷达私有总线信号的语义来自 ars510-radar 作者的 DBC 注释与文档（二手），不同固件/车可能不同；用户无车无 comma，均需实车 rlog 验证。
- SGSP 分析只看了与纵向/雷达/Toyota 相关的提交与文件，未全面审阅。
