# 22 — commaai/opendbc#3860：官方 PR 的内容，以及它已经包含 T2 的 carstate 部分

状态：完成。来源：PR 代码（`git fetch comma refs/pull/3860/head`，一手）+ 子 agent 对 PR 页面的读取（页面被截断，约 40k/68k 字符，截断之后是摘要，下面只采用逐字部分）。2026-11-21。

## 1. PR 概况

- 标题 "Toyota: radar for radar-ACC cars from the ARS510's ACC target"；作者 ds-sebastian；目标 `commaai:master`，分支 `ds-sebastian:ars510-acc`。
- **状态：Open，未合并，页面上没有任何 comma 维护者的评论或正式 review。** 描述里称 "smasher816 helped frame this approach and reviewed it"（commit 3 里有他的 co-author trailer）。
- 10-17 从 draft 转为 ready；最后提交 10-21；标签 `DBC signals`、`car`、`toyota`。
- head：`ddcc0b07d1759c96a1d208ae1db13ad91ce23435`，6 个提交：
  1. `f68a1f0` 雷达 ACC 车（RAV4 2022-25、C-HR、Yaris）：把雷达 ACC 目标作为前车点
  2. `a608b67` DBC 文档化雷达的其他辅助帧
  3. `2569821` `TARGET_ID` 6 bit；PCS 报警、alive、boot 帧
  4. **`dea4a6a` "toyota: stockFcw from the ARS510's PCS alarm"**
  5. `c4447d8` 删除 ARS510 测试
  6. `ddcc0b0` `REQUIRED_DECEL` 为每码 0.095 m/s²；`RADAR_FAULT` 在雷达被遮挡时持续置位
- 改动 4 个文件（+259/−9）：`carstate.py`、`radar_interface.py`、`values.py`、新文件 `toyota_tss2_ars510.dbc`。**不含测试。**
- 机器人 "Car behavior report"（10-13，早于后几个提交）：190 段回放，32 changed、158 passed、0 errors，差异为 `TOYOTA_CHR_TSS2` 的 `carParams.radarUnavailable`。

## 2. 它做了什么（与 StarPilot 的 #230 对比）

| | commaai/opendbc#3860 | StarPilot 中合并的 #230 |
|---|---|---|
| 开关 | **无**，所有雷达 ACC 车型默认启用 | `ToyotaRadarMode` 0/1/2，默认关 |
| 前车点 | 雷达 ACC 目标，每个 60 ms 雷达周期一个点（以 0x239 时钟为准） | 同（Mode 1）；Mode 2 另加最多两辆移动车 |
| `TARGET_ID` 6 bit | 是 | 是 |
| `LAT_DIST` 回绕修复 | **未见**（diff 中没有） | 是（`lat_dist_wrapped`） |
| 雷达故障标志 → `radarUnavailableTemporary` | **未见** | 是（≥2 周期） |
| **`PCS_ALARM` → `stockFcw`** | **是** | **否** |
| 测试 | 删除 | 保留（502 个） |
| 摘要车 / Fused | 无 | Mode 2 |

所以 **PR #3860 有 T2 的 carstate 部分，而我们合并的 #230 没有。** 反过来 #230 有 #3860 没有的 `LAT_DIST` 修复和故障标志。

## 3. T2 的官方实现（提交 `dea4a6a`）

提交说明：雷达在 0x100（雷达总线）的 `PCS_STATE.PCS_ALARM`，与它的车内报警帧（0x411 FCW）同时发；"With the radar disabled for openpilot longitudinal there's no 0x411 to read, but the radar bus keeps running, so read the alarm there too."

```python
# carstate.update
if self.CP.flags & ToyotaFlags.RADAR_ACC:
  pcs = can_parsers[Bus.radar].vl["PCS_STATE"]
  ret.stockFcw = ret.stockFcw or bool(pcs["PCS_ALARM"] and pcs["POWER_UP"] == 0)

# get_can_parsers
if CP.flags & ToyotaFlags.RADAR_ACC:
  # not required (nan), so a quiet radar bus never makes CAN invalid
  parsers[Bus.radar] = CANParser(DBC[...][Bus.radar], [("PCS_STATE", float('nan'))], 1)
```
DBC 新增信号 `POWER_UP`（`15|8`）：上电后的第一帧为 `FF FF`（POWER_UP=255，所有位置 1），此时 `PCS_ALARM` 位也是 1，必须过滤。

**证据强度（重要）：**
- PR 描述的实测证据只有一条路线：RAV4 2022 on sunnypilot，18 分钟接入，"lead radar-backed 99 % of the time the camera had one within 80 m"——这是**前车目标**的证据。
- **没有任何一条记录说明在 Alpha Long（雷达被 UDS 关闭）下 `PCS_ALARM` 确实被发出过。** DBC 里 "seen in 8 stock alarms on 7 cars" 是原车 ACC 工作时的观察。
- PR 描述也没提 AEB、CAN 过滤器。
- 官方 diff 只设 `stockFcw`；**没有任何屏幕提示或仪表改动**。

## 4. 对 T2 的影响

1. 不用从零设计 carstate 部分；可参考官方代码，但**不能直接合并 PR**：它基于 comma 的 opendbc 结构，StarPilot 的 `carstate.update(self, can_parsers, starpilot_toggles)` 签名不同，且我们的 `RadarInterface` 是另一套（`ars510_acc_target.py`）。
2. **仍需把它变成可见提示。** 在我们的代码里 `EventName.stockFcw: {}`（`events.py:519`）是空事件；`selfdrive/car/car_specific.py:252-253` 只是 `events.add(stockFcw)`。关了雷达后原车 0x411 不存在，`stockFcw` 设成 True 屏幕上也不会有任何东西。要触发 `EventName.fcw`（`events.py:614-620`，"BRAKE!" + 声音；`carcontroller.py:474,627` 会据此点亮仪表 FCW 图标）。
3. 必须带上 `POWER_UP` 信号与首帧过滤（我们合并的 #230 DBC 没有该信号，需要补）。
4. 解析器设为可选（`nan` 频率），避免静默的雷达总线让 CAN 失效。
5. 仍加开关，默认关（信号在 Alpha Long 下的行为没有验证）。

## 5. 勘误：关于 #230 的状态

我在 12、19、20 号文档和与用户的对话中多次写"PR #230 仍 OPEN"，**这是我没有核实的断言**：PR 的开关状态无法从 git 引用得到（`refs/pull/230/head` 无论开闭都存在）。本次子 agent 读到 #3860 页面里的交叉引用记录为 "StarPilotHQ/StarPilot#230 (closed)"（页面截断前的部分，未单独核实）。`firestar5683/StarPilot` 与 `StarPilotHQ/StarPilot` 是同一个仓库（分支与 PR 引用完全一致，仓库更名）。**可以确定的只有：#230 的提交没有进入 `stable` 或 `testing`。** #230 是否被关闭、是否被替代，未知。

## 6. 来源

- https://github.com/commaai/opendbc/pull/3860（head `ddcc0b0`）
- https://github.com/ds-sebastian/ars510-radar
