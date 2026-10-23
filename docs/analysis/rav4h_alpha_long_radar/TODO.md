# 待做清单（RAV4 Hybrid 2025 / StarPilot 发布分支）

> 给接手的 agent：本页是唯一的任务清单。开始一项前把状态改为"进行中"，完成后改为"完成"并写上提交哈希。
> 背景见 [README](README.md)；依据主要在 13、14 号文档。
> 当前硬件前提：**暂不装过滤器**（配置 B：Alpha Long 开，雷达车内输出被 UDS 关闭，无原车 AEB/FCW）。用户尚未购买 comma，无实车数据。
> 开发基线：上游发布分支 `StarPilot`（`823a5d5`），本仓库分支 `claude/quirky-goodall-sumqk9`。

## 状态说明
`待做` / `进行中` / `完成` / `阻塞（原因）`

## 一、软件任务（按优先级）

| # | 任务 | 状态 | 风险 | 依据 |
|---|------|------|------|------|
| T1 | 合入 PR #230（ARS510 解析器更新） | 待做 | 低 | 14 §2 P1 |
| T2 | 2a：雷达 `PCS_ALARM` → StarPilot FCW 告警（只告警，不控车） | 待做 | 低 | 14 §2 P2a、§5 |
| T3 | 2b：雷达 `REQUIRED_DECEL` 兜底，**先只记录** | 待做 | 高（启用时） | 14 §2 P2b、§5 |
| T4 | 只减不增的雷达 cap（ACC 目标），**先只记录** | 待做 | 中（启用时） | 04 方案 C、10 §3C、14 P3 |
| T5 | radard 小修：读雷达 `aRel`；ARS510 测量去重 | 待做 | 低-中 | 02 §4、14 P4 |
| T6 | radard 视觉门限对 ARS510 ACC 目标放宽 / 允许独立 lead | 阻塞（需回放数据） | 中 | 02、04 方案 A/B |
| T7 | 跟踪开发分支 SGSP：发布前评估 RAV4 调参与雷达门限是否回退（10-23 已检查一次，见 15） | 待做（持续） | — | 14 §2 P5、15 |

### T1 合入 PR #230
- 来源：`firestar5683/StarPilot` `refs/pull/230/head`（提交 `6c88563c7`、`b66ba75d3`，基于 `823a5d5`，可直接合入）。
- 内容：`TARGET_ID` 6 bit；`LAT_DIST` 回绕修正；雷达故障标志（0x196）→ `radarUnavailableTemporary`；Mode 2 改为 ACC Target + Summaries（新增 `ars510_acc_summaries.py`，删除 `ars510_radar.py`）；DBC 新增 PCS_STATE / EVENT_OBJECT / EVENT_STATE 等报文。
- 验收：`pytest opendbc_repo/opendbc/car/toyota/tests/test_ars510.py` 与 `starpilot/system/the_galaxy/tests/test_device_settings_layout.py` 通过；`THIRD_PARTY_NOTICES.md` 基准提交更新为 `7ff9485`。
- 注意：上游维护者尚未 review；合入后若上游再改，需要同步。

### T2 雷达 FCW（2a）
- 输入：bus 1 `PCS_STATE`(0x100).`PCS_ALARM`，10 Hz；上电第一帧 `FF FF` 需忽略。
- 输出：StarPilot FCW 告警（屏幕 + 声音）；在 `DISABLE_RADAR` 模式下同时驱动仪表 FCW 图标（`carcontroller.py:627-628` 的 `create_fcw_command`，目前只由 `hudControl.visualAlert == fcw` 触发）。
- 数据通路需决定：在 Toyota `RadarInterface` 内解析后放进何处（`RadarData` 无此字段 → 扩展 schema 或放 `starpilotCarState`）。
- 门控：仅 `ARS510_RADAR` 标志 + `DISABLE_RADAR` 时生效（装过滤器后原车 FCW 已在，避免重复）；信号过期（>0.5 s）即失效。
- 验收：单元测试覆盖 首帧忽略 / 报警开始与结束 / 过期失效 / 非 ARS510 车型不变。
- 局限：只提示，不刹车；信号语义来自 ars510 作者记录，待实车验证。

### T3 REQUIRED_DECEL 兜底（2b）
- 输入：bus 1 `EVENT_STATE`(0x196).`REQUIRED_DECEL`（m/s²，0.095/码，仅短 TTC 时有效，约 0.5 % 行驶周期），配合 `EVENT_OBJECT`(0x195)。
- 第一阶段（只记录）：日志里同时写 StarPilot `aTarget`、`REQUIRED_DECEL`、`min()` 结果，不改控制。
- 第二阶段（启用，需用户确认 + 实车数据）：`a_cmd = min(a_starpilot, REQUIRED_DECEL)`，只减不增；受 Toyota `ACCEL_MIN` 限制；信号过期 / `RADAR_FAULT` 时失效。
- 安全：上游标为 "theoretical / fork-only"，需要自己的安全评审；不得宣称等同原车 AEB。

### T4 只减不增的雷达 cap
- 输入：Mode 1 ACC 目标的距离、相对速度、`REL_ACCEL`（T1 后可从 `aRel` 取）。
- 公式草案：`a_cap = -(closing²)/(2·max(d - d_min, 1))`，限幅 [-2.5, 0]，`d_min` ≈ 跟车距离的 50%（下限 4 m）。
- 位置：`longitudinal_planner.py` 末端对 `output_a_target` 取 min。
- 先只记录，用户确认后启用。

### T5 radard 小修
- `radard.py:606` 只取 dRel/yRel/vRel/measured：让 Track 在雷达给出有效 `aRel` 时使用它。
- ARS510 约 16 Hz、radard 20 Hz → 同一测量被 Kalman 用两次：参照 Honda Bosch 的 `radar_fresh` 去重（`radard.py:601-625`），对 ARS510 也启用。
- 验收：非 ARS510 车型行为逐位不变（现有 `selfdrive/controls/tests` 通过）。

## 二、需要实车 / 用户回答（买车载设备后）

| # | 事项 | 状态 |
|---|------|------|
| V1 | 确认 `carParams.carFw` 的 `fwdRadar` 是否为 `8821F0R03100` | 待做 |
| V2 | Alpha Long 下 bus 1 是否有 0x235/0x237/0x239、0x100、0x196 等报文 | 待做 |
| V3 | `PCS_ALARM` 与实际报警场景是否一致（不刻意制造危险） | 待做 |
| V4 | 收集 Mode 1 / Mode 2 路测 rlog（跟车、刹车、起停、弯道、切入） | 待做 |

## 三、装过滤器之后再做（目前搁置）

| # | 任务 | 状态 |
|---|------|------|
| F1 | 向卖家确认 OneClone 过滤器：2025 RAV4H 兼容、固件版本、能否重刷、是否发 0x2FF/0x2AA（07 §3、08 §8）。10-23 更新：开发分支已有 2025 RAV4H + 4 字节 0x2FF 过滤器实车证据与正式支持（15），仍需确认是同一产品 | 搁置 |
| F2 | 原车 AEB 生效时 StarPilot 协同：至少不在 AEB 期间请求加速（13 §4.3） | 搁置 |
| F3 | 改过滤器固件镜像雷达 0x343 → 实现 min(原车 ACC, StarPilot)（10 §3A） | 搁置 |
| F4 | 验证开机时序：雷达启动慢是否导致判定为无过滤器（08 §4.2） | 搁置 |
| F6 | 选型：优先 Beartech C-SDSU（New SDSU）并确认 2023-25 RAV4 雷达 8 针线束与接插件密封；或在 ars510 issue #66 问 DustyL（16） | 搁置 |
| F5 | 装过滤器时使用含 `radar_filter.py` 的版本（开发分支或其发布后），而非发布分支的通用 smartDSU 路径（15 §3） | 搁置 |
