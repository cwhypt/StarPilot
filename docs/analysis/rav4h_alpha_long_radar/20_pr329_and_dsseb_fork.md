# 20 — 上游 PR #329 与 ds-sebastian fork：有没有 T2 的现成实现？

状态：完成（一手 git 比对，2026-11-21）。起因：用户问 #239（更正为 **#329**）或 `ds-sebastian/StarPilot` 的分支里有没有现成实现（T2：雷达 `PCS_ALARM` → FCW）。

## 1. 结论

- **没有 T2 的现成实现。** `ds-sebastian/StarPilot` 的分支与 PR #329 中，没有任何非测试 Python 代码使用 `PCS_ALARM`、`REQUIRED_DECEL`、`PCS_STATE`；DBC 里只有定义（与 #230 相同）。
- **但 #329 修的是比 T2 更根本的问题**，且**只对 `testing` 有意义，对我们当前的 `stable` + #230 不适用**（见 §3）。

## 2. 排除：#239

`firestar5683/StarPilot#239`（`40abb31b4`，Danny，10-20）是 **Honda**（"Keep longitudinal through temporary EPS faults…"），与本事无关。用户后更正为 #329。

## 3. PR #329（`3d36a08d8`，ds-sebastian，2026-11-19，OPEN，基于 `testing`）

PR 的两个提交（与 `ds-sebastian/StarPilot` 分支 `starpilot-ars510-acc-sync` 的最上面两个一致）：

1. `7d2d642f1` "RAV4 radar: ARS510 DBC from commaai/opendbc#3860"：DBC 与 #230 同步（`TARGET_ID` 61|6，补齐 0x100/0x190/0x191-0x194/0x195/0x196/0x202/0x24F/0x680 的文档）。
2. `3d36a08d8` "RAV4 radar: keep the ACC target receiver under alpha long's radar disable"。提交说明：
   - Alpha Long 无过滤器时，UDS `28 01 01`（0x750/0x0F）**只停雷达的车内总线（bus 0）输出；雷达总线（bus 1）全速继续**，ACC 目标 0x235/0x237/0x239 也不停（实证：RAV4 `8821F0R03100` 一次 26 分钟行驶；C-HR Hybrid `8821FF410500` 在 opendbc 一条 22 分钟 "openpilot longitudinal, radar disabled" 路线）。
   - **在 `testing` 里**，`ars510_preferences.configure_radar()` 遇到 `DISABLE_RADAR` 就清掉接收器并标 `radarUnavailable=True`，"所以 Alpha Long 无过滤器时只用视觉"。PR 把这段删掉，让接收器继续工作；同时 `aol.py` 的 `qualified()` 对 RAV4 2022 不再要求"必须无 openpilot 纵向"才允许 `ARS510_RADAR`。
   - 改动文件：`ars510_preferences.py`、`aol.py`、`test_ars510.py`、`test_radar_preferences.py`、`test_toyota.py`、DBC、CREDITS/THIRD_PARTY。

**这正是本分析最初的核心问题**（"Alpha Long 下原车雷达被静默，只用了视觉"）在 `testing` 上的修复。作者的结论与 05/13 号文档一致：关雷达 ≠ 关雷达私有总线。

## 4. 对我们当前代码（`stable` + #230）的影响

- 我们的 `interface.py:22-31`（合并后）：`radar_mode in (1,2)` 时直接 `ret.flags |= ARS510_RADAR`、`ret.radarUnavailable = False`，**不检查 `DISABLE_RADAR`**；`radar_interface.py` 也不检查。全树没有 `configure_radar`/`ars510_preferences`。→ **`stable` 本来就没有 #329 要修的那个问题**，所以 #329 **不需要、也无法**直接合到当前分支（它基于 `testing`，与 `stable` 无共同祖先）。
- 因此：在 `stable` 上，Alpha Long + `ToyotaRadarMode=1/2` 时雷达接收器**本来就会运行**；这一点**仍需实车确认**（接收器启动 ≠ 一定读到数据）。
- 若将来改用 `testing`：必须带上 #329，否则 Alpha Long 无过滤器时雷达被清掉（又回到"只用视觉"）。
- 这也**增强了一个信心点**：上游作者在两台车、多条路线上观察到 UDS 关闭后 bus 1 数据仍在（我们 T2 的第一个硬件前提）。但 `PCS_STATE`(0x100) 本身是否在关闭后继续发，**仍没有被任何记录单独证实**。

## 5. ds-sebastian fork 的分支

| 分支 | head | 内容 |
|---|---|---|
| `ars510-acc-profiles` | `b66ba75d`（= PR #230 head） | 我们已合并 |
| `starpilot-ars510-acc-sync` | `3d36a08d8`（= PR #329 head） | 基于 `testing` 的同步分支，除 ARS510 的两个提交外都是 `testing` 的普通提交 |
| `StarPilot` | `823a5d56` | 旧发布分支镜像 |

## 6. 对 TODO 的影响

- T2（雷达 FCW）：**无现成实现**，仍待做。
- 新增 T9：若改用 `testing`，需合并 #329。
- T6 之外的新信息：`testing` 的 radard 更严格，加上 #329 才让雷达在 Alpha Long 下有数据——两者叠加要一并评估。
