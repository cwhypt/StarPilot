# 26 — B 对切入/部分重叠/交叉车辆的覆盖；C 的离线探测（REQUIRED_DECEL）

状态：B 部分为**代码分析**（未用真实日志）；C 探测脚本完成，离线测试通过，**未用真实日志跑过**。前置：25、23 §3C。

## 1. B 能覆盖切入吗？——部分能

B（`selfdrive/controls/lib/radar_brake_cap.py`）只用 `liveTracks` 里**雷达自己的 ACC 目标**（trackId < 64），并要求 `|yRel| ≤ 1.2 m`、同一目标连续 ≥5 个周期、目标曾在动、正在接近。

| 场景 | B 是否起作用 | 原因 [代码] / [推断] |
|---|---|---|
| 切入车已经**大部分进入本车道**，且雷达已把它选为 ACC 目标 | ✅，约 0.3 s 后 | 雷达换目标时 `TARGET_ID` 变化，B 复位后重新数 5 个周期（`radar_brake_cap.py` `update`） |
| 切入车**部分重叠**本车（压线、车身一部分在我车道） | ❌ 大多不起作用 | RAV4 半宽约 0.93 m，普通车半宽约 0.9 m，车身开始重叠时对方中心距我中线约 1.8 m；B 的门限 1.2 m 更窄，重叠 0.6 m 以内的车被排除 |
| 切入车**还在邻道、正横向靠近** | ❌ | B 没有横向预测：Mode 1 不提供横向速度（`ars510_acc_target.py` 里 `yvRel = nan`）；B 也不看 `IN_PATH_CANDIDATE` |
| 邻道车不是雷达的 ACC 目标（Mode 2 的摘要车，trackId ≥ 64） | ❌ | B 明确排除摘要车（`SUMMARY_TRACK_ID_MIN = 64`） |
| **横穿车辆**（路口垂直方向） | ❌ | 它在本车方向上的对地速度约 0 → 被"从没动过的目标"过滤；横向扫过很快，也很难连续 5 个周期留在 1.2 m 内。DBC 的 `CLASS 5 "crossing object"` 说明雷达能识别，但 B 没有为它设计 |
| 弯道里的前车 | ⚠️ 不确定 | `yRel` 是相对车头直线的横向距离，弯道里本车道是弯的；雷达自己的 `IN_PATH` 考虑了曲率，B 的 1.2 m 直线门限没有 |

另外：**视觉通常比雷达更早看到切入**（模型直接预测前车），radard 也优先用视觉。B 的定位始终是"视觉漏看时的兜底"，不是切入的主力。

### 现在就能用回放脚本评估的

- `--param max_lat=1.8` 即可在回放里测"按车身重叠算"的门限，看多出来多少片段、是否都是真切入（不用改代码）。

### 若数据证明需要，可做的扩展（记为 T4b，未做）

1. 用 Mode 2 已有的横向速度：Mode 2 的 ACC 目标 `yvRel` = 雷达 `LAT_SPEED`，摘要车 `yvRel` = 横向位置斜率（`ars510_acc_summaries.py` 文件头说明）。预测 `y(t) = yRel + yvRel·t`，在 `t = min(TTC, 2 s)` 内进入重叠区即视为本车道目标。
2. 把 40 m 以内的摘要车也纳入，但门限更严（要求横向在靠近、闭合 TTC 短）。
3. 回放里加"切入"标记（最近 2 s 内换过目标或 `|yRel|` 在减小），单独统计。
4. 横穿车辆不建议放进 B：误刹风险高，且属于 AEB 的范围（Alpha Long 下 AEB 已没有）。C 的探测可以看雷达的事件对象是否覆盖横穿。

## 2. C 的探测：`tools/longitudinal/probe_required_decel.py`

**不需要在车上加任何代码，也不改任何行为**：rlog 本来就记录所有总线收到的原始 CAN，所以 0x195/0x196 只要在雷达总线上发了，日志里就有。探测只是离线解码。与 `ToyotaRadarMode` 无关（Mode 0 的行程也能探测；只有"B cap"一列要 Mode 1/2）。

### 用法

```bash
python tools/longitudinal/probe_required_decel.py <rlog 或路线> [...] [--json]
```

### 输出

1. 行程时长、行驶时长、配置（雷达是否被关闭 = Alpha Long 无过滤器；`ToyotaRadarMode` 是否 1/2）。
2. 雷达总线各报文频率：0x100、0x195、0x196、0x237（应约 16.7 Hz）。
3. 0x196 帧数、故障帧数、事件激活帧数及占行驶周期的比例（DBC 注释记录约 0.5%）。
4. `REQUIRED_DECEL` 原始码的分布，**分"激活"和"空闲"两栏**——用来在真实数据上确认符号和空闲值（DBC 只说是有符号 8 位、0.095 m/s²/码，正负含义没有记录）。
5. 结论：0x196 不存在 → C 在这台车上做不了；存在但从未激活 → 还没遇到短 TTC 时刻；有激活 → 列出片段，**每个都要对照视频看**。
6. 每个激活片段：时间、持续、`REQUIRED_DECEL` 范围、事件对象的距离/速度/`EGO_TRAVEL_TO_TTC`、本车速度、雷达 ACC 目标距离与相对速度、规划器 `aTarget`、B 的 cap、以及标记：雷达总线 PCS 报警、驾驶员 2 s 内踩刹车、**原车 AEB 请求**（0x283 `PRECOLLISION_ACTIVE` 且 `FORCE<0`，或 0x344 `DSS1GDRV<0`）、原车 FCW（0x411）、视觉是否有前车。

"激活"的判定：事件对象距离 > 0（DBC：空闲时为 0）或 `REQUIRED_DECEL` 原始码 ≠ 0。0x195 与 0x196 按最近一帧配对，可能差一个周期。

### 怎么用它决定是否做 C

| 问题 | 看哪 | 需要的行程 |
|---|---|---|
| Alpha Long 下 0x196 还发吗？ | 频率、结论 | Alpha Long + 无过滤器 |
| 信号含义（符号、空闲值）对吗？ | 激活/空闲两栏原始码 | 任意 |
| 它比原车 AEB 早多少、是否总是伴随 AEB/FCW？ | 原车 AEB / FCW 标记 | **关 Alpha Long**（原车纵向，0x283/0x344/0x411 在车内总线上可见） |
| 比 B 多提供了什么？ | B cap 一列 vs `REQUIRED_DECEL` | Mode 1/2 |
| 是否覆盖横穿/切入？ | 事件对象距离 vs ACC 目标距离不一致的片段 | 任意 |

若 Alpha Long 下 0x196 不发，或激活片段总是与原车 AEB 同时（没有提前量），C 就没有意义；若它稳定地比 B 更早、更准，再考虑实现（需要改 cereal，见 23 §3C）。

### 测试（离线，模拟日志）

- `tools/longitudinal/tests/test_probe_required_decel.py`：5 项——有符号解码、没有 0x196、存在但从未激活（含 16.7 Hz 频率）、一次激活片段及其全部上下文标记（PCS 报警、原车 AEB、驾驶员刹车）、同一行程中两次事件分成两个片段。
- 与 B、T2 的脚本和纯函数一起：99 项通过；ruff 通过。
- 示例输出（模拟数据，原车纵向）：
  ```
  radar-bus rates (Hz): 0x100 16.7, 0x195 16.7, 0x196 16.7, 0x237 16.7
  0x196 frames 166, fault frames 0; event active 10 (6.06% of moving cycles; DBC note: ~0.5%)
  REQUIRED_DECEL raw codes while active: 236:10
  REQUIRED_DECEL raw codes while idle: 0:156
  verdict: REQUIRED_DECEL observed: 1 episodes -- review each with the video before planning C
       4.0s +0.54s (10 cyc) req  -1.90.. -1.90 obj d  12.0 ... | PCS alarm, driver braked, car AEB
  ```
