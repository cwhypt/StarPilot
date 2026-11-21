# 25 — B（T4）第一阶段：雷达"只减不增"制动上限的纯函数 + 离线回放

状态：**纯函数与回放脚本完成，离线测试通过；未接入规划器，未用真实日志跑过**。前置：23 §3B、04 方案 C。

## 1. 这一阶段做什么、不做什么

- 做：一个纯函数，根据雷达 ACC 目标算出"为了不追上前车，本车最多允许多大的加速度"；一个回放脚本，在 rlog 上跑这个函数，与规划器实际输出比较，找出"它会刹得更狠"的片段。
- **不做：接进规划器**。`plannerd`/`longitudinal_planner.py` 一行没改，车上行为与之前完全一样。
- 下一阶段（接入）要等：用真实路测 rlog 回放，看误报（"same lead"/无风险的 "vision missed"）和漏报情况。

## 2. 文件

| 文件 | 内容 |
|---|---|
| `selfdrive/controls/lib/radar_brake_cap.py` | `RadarCapParams`（全部阈值）、`required_accel()`、`RadarBrakeCap.update(t, points, v_ego, unavailable) -> CapResult(cap, reason, ...)` |
| `selfdrive/controls/tests/test_radar_brake_cap.py` | 65 项：公式手算核对、各种不产生 cap 的情况、连续周期/换目标/过期/雷达不可用复位、静止物体过滤、最近目标选择、上下限与单调性 |
| `tools/longitudinal/replay_radar_brake_cap.py` | 回放脚本 |
| `tools/longitudinal/tests/test_replay_radar_brake_cap.py` | 8 项：视觉漏看→一个片段、规划器已经刹够→无片段、同一前车但规划器偏软→"same lead"、拉开距离不产生 cap、静止目标单列、驾驶员踩刹车标记、Mode 0 警告、参数覆盖 |

## 3. 算法

输入：`liveTracks` 中 trackId < 64 的点（Mode 1 唯一的雷达 ACC 目标；Mode 2 的摘要车不用），本车速度。

1. 取最近的 ACC 目标；必须满足：`|yRel| ≤ 1.2 m`、`0.75 ≤ dRel ≤ 120 m`、同一 trackId 连续 ≥5 个雷达周期（约 0.3 s）、该目标曾被看到在动（前车速度 > 1 m/s）、接近速度 ≥0.5 m/s。
2. 安全余量 `margin = max(4 m, 0.725 s × v_ego)`（标准跟车时距 1.45 s 的一半）。
3. 假设前车匀速，所需加速度 `a = −closing² / (2 × max(dRel − margin, 1 m))`；已在余量以内仍在接近 → 取下限。
4. `a > −0.3` 不输出（规划器本来就够）；输出限幅到 `[−2.5, −0.3]`（更重的制动是 AEB 的事）。
5. 0.5 s 没有新数据、雷达报不可用、目标消失、换了目标 → 复位，`cap=None`，即退回现状。

所有阈值都在 `RadarCapParams` 里，回放时可用 `--param` 覆盖。

**已知取舍：** "从没动过的目标忽略"是为了防路边静止物体、桥、护栏造成幻影刹车，但它也会忽略"一直停着的车"（例如远远接近红灯前已停的车流）。回放脚本把这类目标**单独列出**（"ignored as never-moving while closing"），用真实数据决定要不要放开。

## 4. 回放脚本用法

```bash
python tools/longitudinal/replay_radar_brake_cap.py <rlog 或路线> [...]
python tools/longitudinal/replay_radar_brake_cap.py <rlog> --param cap_min=-2.0 --param min_cycles=8 --min-extra 0.3 --json
```

- 要 `ToyotaRadarMode` 1 或 2 的行程（`liveTracks` 里才有 ACC 目标）；Mode 0 会打印警告。必须用 rlog。
- 输出：雷达数据/接管/有目标的时长；"cap 会比规划器多刹 ≥0.2 m/s²"的片段数（每小时多少次），每个片段的时间、分类、cap 与规划器值、车速、距离、接近速度，以及之后 2 s 内驾驶员是否踩了刹车；被静止过滤忽略的片段；各原因计数。

分类（取片段内多数）：

| 分类 | 含义 | 怎么看 |
|---|---|---|
| vision missed | 当时 `radarState.leadOne` 没有前车 | **B 要补的正是这个**。看视频：真有车在接近 → 真缺口；没有 → 误报 |
| vision other lead | 有前车但不是这个目标（不同 track 且距离差 >25%/5 m） | 视觉锁错了车，看视频判断谁对 |
| same lead | 就是同一辆车，只是规划器刹得比 cap 轻 | 不是感知缺口，是两者调参差异；数量多说明 cap 太激进 |

"driver braked" 标记可以帮助判断：cap 想刹、驾驶员随后也踩了刹车，说明风险大概率是真的。

## 5. 示例（模拟日志）

```
radar data 0.3 min, engaged 0.3 min, ACC target present 0.2 min
cap would brake harder: 1 episodes (1 engaged, 180.2/h engaged)
  vision missed      1
       5.3s-16.4s vision missed      engaged     cap -2.50 vs plan  0.00 (extra 2.50) v 20.0 d  21.5 vRel  -6.0  driver braked
ignored as never-moving while closing: 0 episodes
reasons: cap 187, no ACC target 143, target too new 4
```

## 6. 测试

- 纯函数 65 项、回放 8 项全部通过；与 T2 检查脚本一起 85 项通过；ruff 通过。
- 运行条件同 24 §4（scratchpad 导入桩，`--noconftest -c /dev/null`）。

## 7. 接入规划器之前需要的数据（下一阶段的门槛）

1. 至少数小时 Mode 1 路测 rlog（含高速、城市、弯道、桥/护栏、停车场、切入）。
2. 回放结果：
   - "same lead" 每小时次数低（cap 不比规划器激进太多）；
   - 每个 "vision missed" / "vision other lead" 片段看视频确认，误报接近 0；
   - 决定静止过滤是否放开（看"ignored as never-moving"列表）。
3. 满足后再做：`plannerd.py:24` 订阅 `liveTracks`，在 `longitudinal_planner.py` 发布前对 `output_a_target` 取 `min`，加默认关闭的开关，并加"非 ARS510 车型逐位不变"的测试。
