# 12 — StarPilot 与 ars510-radar 的关系，以及上游合并状态（截至 2026-10-21）

状态：完成。来源：本 repo（一手）；ars510-radar 及各 PR 页面（子 agent 读取，二手转述，链接见文末）。

## 1. 结论

- **ars510-radar 不是要合并进 sunnypilot**，而是要进 **comma 官方 opendbc**：`commaai/opendbc#3860`（只含 `acc` 解析器，即 ACC 目标），2026-10-17 标记 ready for review，**仍为 OPEN，尚无 comma 维护者 review**。`fused`（全轨迹）明确"stays a fork branch"（上游 docs/17_upstream.md）。
- 若 #3860 被合并，sunnypilot / StarPilot 等会随 opendbc 同步自然获得；在此之前各 fork 自行集成。sunnypilot 侧没有 ars510 解析器的 PR；作者只在 `sunnypilot/opendbc#533` 提了一个"装 smartDSU / CAN filter 的 radar-ACC 车读取 SDSU 跟车距离按钮"的 PR（OPEN，未上车测试）——与我们的过滤器路线相关。
- **StarPilot 已经跟进**：上游 firestar5683/StarPilot 在 2026-10-12 提交 `88c7398`("Green Garden") 集成了解析器（基于上游 `fb5b29c`），即 `ToyotaRadarMode` 0/1/2。
- **但 StarPilot 落后于上游**：作者本人向 StarPilot 提了 `firestar5683/StarPilot#230`（2026-10-17，OPEN，未 review）：
  - Mode 1（ACC Target）修复：6-bit `TARGET_ID`、`LAT_DIST` 回绕；
  - Mode 2 由 "Fused Tracks" 改为 "ACC Target + Summaries"，删除 `ars510_radar.py`。
  - 本 repo 当前 DBC：`TARGET_ID : 60|5`（5 bit），印证尚未包含 #230。
- 上游 HEAD：`5e75ade`（2026-10-20），活跃开发中（10 月几乎每天合并 PR）。

## 2. 两个 repo 的分工对比

| | ars510-radar（上游） | StarPilot（本 repo） |
|---|---|---|
| 定位 | 雷达协议研究 + 解析器 + 多 fork 安装器（`openpilot/install.py`，支持 openpilot / sunnypilot / StarPilot / CarrotPilot） | 完整驾驶软件；已内置解析器，无需安装器 |
| 配置 | profiles：`acc`(默认)、`acc_summaries`、`fused`、`openpilot`、`raw` | `ToyotaRadarMode`：0 Off / 1 ACC Target / 2 Fused |
| 范围 | 只负责"把雷达数据变成 RadarData"；不改 radard/planner | radard 视觉先行门限、RAV4 专用 planner 调参（02/03） |
| AEB | 文档明确：雷达禁用即失去 AEB；过滤器可保留；AEB 中继标为 theoretical | 同（`DISABLE_RADAR` 路径） |
| 验证 | 车主 2022 + 一位 2025 Hybrid 车主路测；#3860 中 62 条路线约 17 h replay | 继承上游 + 单元测试 `test_ars510.py` |

## 3. 对我们方向（过滤器 + 雷达数据 + 只减不增 cap）的影响

1. **数据源选 Mode 1（ACC Target）**：这正是上游主推、准备进 comma 官方的部分，最稳定；方案 C 只需要"雷达 ACC 正在跟的那一辆"的距离/相对速度/相对加速度。
2. 实现前应先把 **#230 的 Mode 1 修复**带进来（`TARGET_ID` 位宽、`LAT_DIST` 回绕），否则目标切换判断和横向位置可能错。可等上游 StarPilot 合并后同步，或在本分支先 cherry-pick（需读 #230 diff）。
3. 方案 C 需要的 `REL_ACCEL`、`IN_PATH`、`CLASS` 在 DBC 已有但解析器未输出（02 §4）；需决定是扩展 `RadarPoint` 还是走独立消息（04 §2 方案 C）。
4. 过滤器路线下雷达不被禁用，bus 1 数据应同样存在 [推断]；距离按钮读取 StarPilot 已支持（`carstate.py:266`，SDSU `FD_BUTTON`），与 sunnypilot/opendbc#533 的目标一致。

## 4. 链接

- https://github.com/ds-sebastian/ars510-radar （HEAD `5e75ade`；docs/17_upstream.md、docs/14_forks.md、openpilot/install.py）
- https://github.com/commaai/opendbc/pull/3860
- https://github.com/firestar5683/StarPilot/pull/230
- https://github.com/sunnypilot/opendbc/pull/533
- 上游 issue #66：RAV4 Hybrid 2025 on sunnypilot 路测报告（未读正文）
