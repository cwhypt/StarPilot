# 23 — "视觉没看到、只有雷达看到的前车"缺口：T2 与三种修复（A/B/C）的代码跨度与优缺点

状态：完成（核对了各方案要改的位置；**行数是估算**，不是实现后的实际值）。基线：本分支（`stable` + #230）。

## 0. 缺口的原因（一句话）

`radard.get_lead`（`selfdrive/controls/radard.py:328-407`）是视觉先行：视觉概率 >0.35 且与某雷达目标匹配才采用雷达；否则只有车速 <4 m/s 的正前方目标才单独成为前车。车速 ≥4 m/s 时视觉漏看的车，雷达看到也进不了规划，也触发不了规划器 FCW（要求 `modelProb>0.9`，`long_mpc.py:138,210`）。详见 02。

## 1. 先核对到的关键事实（影响方案选择）

| 事实 | 位置 | 意义 |
|---|---|---|
| plannerd 订阅了 `radarState`，**没有订阅 `liveTracks`** | `selfdrive/controls/plannerd.py:24` | B 方案只需加一个订阅，**不改 cereal 数据结构** |
| 规划器最终输出前有一串 cap（`output_a_target = min(...)`） | `longitudinal_planner.py:3121-3193`，发布于 `:3222` | B 的挂载点天然存在，一行 `min` |
| Mode 1 的 `liveTracks` 只有雷达 ACC 目标一个点；Mode 2 另有 trackId ≥64 的摘要车 | `ars510_acc_target.py:73`、`ars510_acc_summaries.py` | B/A 要只用 trackId <64 的 ACC 目标 |
| card 先 `CI.update` 得 `CS`，再 `RI.update` 得雷达数据，最后发布 `CS` | `selfdrive/car/card.py:292-299,435` | T2 可在两者之间把雷达信号写入已有的 `CS.stockFcw`，**不改数据结构** |
| Toyota 专用事件逻辑有现成位置 | `selfdrive/car/car_specific.py:134` | T2 的事件映射挂在这里 |
| `RadarPoint` / `RadarData` 没有"所需减速度"字段 | `cereal/car.capnp:300-340` | C 需要改 cereal 或绕路 |
| 已有测试文件 | `selfdrive/controls/tests/test_leads.py`、`test_longitudinal_planner.py`；`opendbc_repo/.../tests/test_ars510.py` | 测试可以直接加在这里 |

## 2. T2：雷达 `PCS_ALARM` → FCW 告警（只提醒）

- **改动面**（估算 7-9 个文件、约 150 行含测试）：
  1. `toyota_tss2_ars510.dbc`：补 `POWER_UP`（+1 行）。
  2. `ars510_acc_target.py` / `ars510_acc_summaries.py`：订阅 `PCS_STATE`（可选，频率 nan），暴露 `pcs_alarm`（各约 +12 行，可抽共用函数）。
  3. `card.py`：`RI.update` 之后 `CS.stockFcw |= RI.pcs_alarm`（约 +3 行）。
  4. `car_specific.py` Toyota 分支：`DISABLE_RADAR` 且 `stockFcw` 时加 `EventName.fcw`（约 +4 行）。
  5. 开关：`params_keys.h`、`starpilot_variables.py`、Galaxy 布局 JSON（约 +15 行）。
  6. 测试：首帧忽略、报警开始/结束、过期失效、非 ARS510 车型不变（约 +80 行）。
- **不需要改数据结构。**
- **效果：** 雷达判断要撞时，屏幕 "BRAKE!" + 声音 + 仪表 FCW 图标；**不依赖视觉**；只提醒，不刹车。
- **风险：** 低。限制：这是与原车 FCW 同阈值的"最后一刻"预警；该信号在 Alpha Long（雷达被 UDS 关闭）下是否还发，**没有任何记录证实**。

## 3. 三种修复对比

### A. radard：雷达 ACC 目标在视觉没看到时独立成为前车
- **改动面**（估算 3-4 个文件、约 170 行含测试）：`radard.py` 的 `get_lead`（约 +40）、`RadarD.__init__`/`main` 传入 ARS510 标志（约 +8）、`test_leads.py`（约 +120）。
- **要加的条件**（防幻影刹车）：仅 trackId<64 的 ACC 目标；`|yRel|` 在本车道内；距离 0.75–~120 m；连续存在 ≥N 个周期；闭合速度或车距满足阈值；静止物体单独规则。
- **优点：** 视觉漏看时车会减速；后续减速由现有 MPC 处理，舒适；之后的 RAV4 雷达调参分支（`radar==True`）自然生效。
- **风险：高。** 前车选择影响所有下游：MPC、Force Stop、CEM（`trackingLead`）、各种 `get_*` cap，雷达 lead 还会绕过一批 vision-only 谨慎 cap；误检直接变成急刹（幻影刹车）。雷达 lead 的 `modelProb=0`，仍不触发规划器 FCW。
- **能不能先只记录：** 不好做——它改的就是决策本身，只能做影子模式（并行算一份不生效）。

### B. 规划器末端"只减不增"的雷达制动 cap（T4）
- **改动面**（估算 5-6 个文件、约 250 行含测试）：新增纯函数模块（约 60 行）、`plannerd.py` 加 `liveTracks` 订阅（+1）、`longitudinal_planner.py` 在 `:3193` 之前 `min`（约 +15）、开关（约 +15）、测试（约 +120）。
- **优点：**
  - **只会让车更保守**（`min`），数据过期/无目标时 cap=None，退回现状，失效模式最安全；
  - **不改前车选择，对其他功能零影响**；
  - **不改数据结构**；
  - **可以先完全离线验证**：`liveTracks` 和 `longitudinalPlan` 都在 rlog 里，把纯函数跑在日志上，对比 `aTarget` 与 cap，**不需要在车上装任何代码**。
- **风险：中。** 减速可能比 MPC 生硬；误检同样会刹（但雷达 ACC 目标是 Toyota 自己已判定在路径内的目标，原车 ACC 也会对同样的目标刹车）；cap 的公式和限幅（草案：`-(closing²)/(2·max(d−d_min,1))`，限 [−2.5, 0]）需要实车数据调。
- **不能修复：** 预警（没有 FCW）。

### C. 读取雷达 `REQUIRED_DECEL` 做兜底（T3）
- **改动面**（估算 8-10 个文件、约 300 行含测试）：两个解析器读 0x196/0x195 原始帧（约 +30）、**cereal 数据结构新增字段或新消息**（`car.capnp` 或自定义 schema，需重新编译，影响日志兼容）、plannerd 消费、cap 函数、开关、测试。
- **优点：** 最接近"原车 AEB 之前的判断"，是雷达自己的威胁评估。
- **风险：高。**
  - 该信号只在短 TTC 才有效（约 0.5% 的行驶周期）；
  - 公式只是作者的近似（DBC 注释："floor(a_req / 0.095) … 1.75 m margin"）；
  - Alpha Long 下是否仍发送**没有任何记录**；
  - 要动 cereal。
- **作用：** 补的是"快撞了"的最后一刻，不是中间地带。

## 4. 综合对比

| | T2（预警） | A | B | C |
|---|---|---|---|---|
| 解决什么 | 视觉漏看时的**预警** | 视觉漏看时**自动减速**（决策级） | 视觉漏看时**自动减速**（兜底级） | 快撞前的**减速兜底** |
| 改数据结构 | 否 | 否 | **否** | **是** |
| 估算规模 | ~150 行 / 7-9 文件 | ~170 行 / 3-4 文件 | ~250 行 / 5-6 文件 | ~300 行 / 8-10 文件 |
| 对现有行为的影响 | 无（只加提示） | **大** | 小（只能更保守） | 小 |
| 失效模式 | 不响 | 幻影刹车 | 不介入 | 不介入 |
| 可否离线验证 | 难（报警稀少） | 难 | **容易（rlog 回放）** | 难（信号稀少） |
| 依赖未验证假设 | 是（PCS_ALARM 仍发送） | 否 | 否 | 是（REQUIRED_DECEL 仍发送） |
| 风险 | 低 | 高 | 中 | 高 |

## 5. 建议顺序

1. **T2**（低风险；默认关；先写测试）。
2. **B 的纯函数 + 离线回放脚本**（先不接进规划器）。有第一批 rlog 后评估误报、调限幅，再决定是否接入。
3. **A** 最后再考虑，且仅在 B 的数据证明"视觉漏看且雷达判断正确"确实常见时。
4. **C** 取决于 T2 上车后是否确认 0x100/0x196 在 Alpha Long 下仍在发送；若 T2 的信号一直不出现，C 基本没有意义。
