# 30 — 移植到官方 StarPilot 7.0（`404ef33a9`）

状态：**代码已移植，未在车上/设备上跑过测试**（云端容器是 x86，仓库里的 `.so` 是 aarch64，测试要在 comma 设备上跑）。
分支：`rav4h-alpha-long-radar-on-7.0`（基于 `starpilothq/openpilot` `stable` 404ef33a9）。旧分支 `claude/quirky-goodall-sumqk9` 停止维护：它的基线与 AGNOS 20.5.5 用户空间不兼容（见 29）。

## 1. 行为

- **默认不变**：不设 `ToyotaRadarMode`（或为 0）时，7.0 行为逐位不变——Alpha Long + 无过滤器 = 雷达视为不存在。
- **显式开启**：`ToyotaRadarMode` = `1`（或 `2`）+ Alpha Long（`DISABLE_RADAR`）+ RAV4 TSS2 2022/2023：
  - 打开 ARS510 ACC 目标接收器（`ARS510_RADAR`，`radarUnavailable=False`）；
  - T2：雷达私有总线（bus 1）`PCS_STATE` 0x100 的 `PCS_ALARM` → `carState.stockFcw` → `EventName.fcw`（屏幕 + 声音 + 仪表 FCW 图标）。首帧 `FF FF`（POWER_UP）忽略，0.5 s 无帧自动清除。
- 复用现有参数键 `ToyotaRadarMode`（新增键需要重编 `params_pyx.so`，所以没加）。设置：`echo -n 1 > /data/params/d/ToyotaRadarMode`（重启 openpilot 生效）。
- 只在 `DISABLE_RADAR` 时生效：不开 Alpha Long 时 7.0 本来就自动用 ACC 目标，T2 不启用（原车 FCW 还在，避免重复）。

## 2. 改了什么

| 文件 | 内容 |
|---|---|
| `opendbc/dbc/toyota_tss2_ars510.dbc` | 旧分支版本：`TARGET_ID` 6 bit；PCS_STATE(0x100)、EVENT_STATE(0x196) 等 |
| `car/toyota/ars510_acc_target.py` | LAT_DIST 回绕修复（校验字节 CRC）；0x196 故障位连续 ≥2 周期 → `radarUnavailableTemporary`；点字段改为 7.0 的 `pt.deprecated.*` |
| `car/toyota/ars510_preferences.py` | `configure_radar(cp, alpha_long_radar=False)`；`radar_pcs_fcw(cp)` |
| `starpilot/vehicle_preferences.py` | 读取 `ToyotaRadarMode` ∈ {1,2} → `toyota_radar_alpha_long`，传给两处 `configure_radar` |
| `car/toyota/carstate.py` | `Bus.radar` 的 PCS_STATE 可选解析器（频率 nan，缺失不影响 canValid）、`update_radar_pcs_alarm` |
| `selfdrive/car/car_events.py` | Toyota：`CS.stockFcw and radar_pcs_fcw` → `EventName.fcw`（`stockFcw` 本身无告警） |
| `selfdrive/controls/lib/radar_brake_cap.py` + `tools/longitudinal/{replay_radar_brake_cap,probe_required_decel,check_t2_radar_fcw}.py` | B/C 离线工具，路径改为 `openpilot.` 前缀；**B 仍未接入规划器**（与旧分支一致） |
| `selfdrive/controls/radard.py` | T5 的去重部分：`RadarD(dedupe_radar=...)`，仅 Toyota + `ARS510_RADAR` 时由 `main` 打开。ARS510 约 16 Hz、radard 按 `modelV2` 20 Hz 轮询，约每 5 次有 1 次读到同一条 `radarTracks`，原先会被卡尔曼滤波用两次；现在同一条记录只喂一次（新轨迹首次出现除外）。其他车型逐位不变 |
| `longitudinal_mpc_lib/long_mpc.py`、`longitudinal_planner.py` | 雷达确认的前车也能触发规划器 FCW（仅 `radar_pcs_fcw`：Toyota + ARS510 + Alpha Long，默认关）。原先 MPC 的 `crash_cnt` 只在视觉置信度 `modelProb > 0.9` 时累加；现在该条件放宽为「> 0.9，或 radard 已把雷达轨迹与视觉前车匹配（`radar=True`，`modelProb > 0.5`）」。置信度 ≤ 0.9 的新路径额外要求 TTC < 4 s 且接近速度 ≥ 0.5 m/s；> 0.9 的原路径不变。**只改 StarPilot 规划器**，`starpilot/longitudinal/upstream/` 的备用规划器没动 |
| 测试 | `test_ars510_pcs_fcw.py`（新，按 7.0 的 `VehicleStartupPreferences` 流程构造参数）；`test_ars510.py` 增加校验字节、故障位、LAT_DIST 回绕用例；`test_radard_dedupe.py`、`test_radar_confirmed_fcw.py`（新） |

## 3. 旧分支有而 7.0 没有的 RAV4 相关项（按代码核对）

| 项 | 7.0 | 处理 |
|---|---|---|
| Alpha Long 下使用 ARS510 | 被排除 | **已移植（opt-in）** |
| LAT_DIST 回绕修复 / TARGET_ID 6 bit / 雷达故障位 | 无 | **已移植** |
| T2 雷达 PCS 报警 → FCW | 无 | **已移植** |
| B/C 离线工具 | 无 | **已移植** |
| `ToyotaRadarMode=2`（ACC 目标 + 摘要车，`ars510_acc_summaries.py`） | 无（7.0 有未启用的全对象表 `ars510_radar.py`） | **未移植**：它占用的标志位与 7.0 的 `ARS510_FUSED` 冲突。目前 2 等同 1 |
| RAV4 TSS2 纵向调参（`longitudinal_vehicle_tunes.py`：提前减速、远距跟车斜率、起步后追赶限制、前车蠕行） | 在 `openpilot/` 下未找到任何对应实现；7.0 的规划器已大幅重构（570 行 vs 旧 3240 行） | **未移植**：需要适配新规划器架构，单独做，默认关 |
| 低速（<4 m/s）雷达前车兜底 | 同样存在（radard） | 无需移植 |

7.0 有而旧分支没有：2025 RAV4 雷达 CAN 过滤器识别、0.11.3 基线、新驾驶模型等（见 28）。

## 4. 已知限制

- **radard 的 `aRel` 没做**：Mode 1 的点不带 `aRel`（NaN），要用就得改成读雷达 REL_ACCEL 并换算成前车加速度，会改变跟车行为，没有回放数据先不动。

- RAV4 **2022**（力矩控制）+ Alpha Long + 本选项：7.0 的 AOL 资格检查对这个组合直接判不合格（现有测试明确断言），本选项会让该车失去 7.0 的 AOL 力矩路径。2023（角度控制，本车）不受影响。
- **V2 未回答**：Alpha Long 下雷达私有总线（0x100/0x196/0x235…）是否还在发，仍需上车看 `check_t2_radar_fcw.py` / `probe_required_decel.py`。不在发时：ACC 目标静默退化成纯视觉，T2 永不触发（不报错）。
- T2 只告警不刹车；PCS_ALARM 语义来自 ars510 作者记录，待实车验证。

## 5. 设备上验证

```
cd /data/openpilot_rav4          # 官方 7.0 副本 + 本补丁
python -m pytest -q opendbc_repo/opendbc/car/toyota/tests/test_ars510.py \
  opendbc_repo/opendbc/car/toyota/tests/test_ars510_pcs_fcw.py \
  openpilot/selfdrive/controls/tests/test_radar_brake_cap.py \
  openpilot/tools/longitudinal/tests \
  openpilot/starpilot/car/toyota/tests/test_radar_preferences.py \
  openpilot/starpilot/aol/tests/test_toyota.py \
  openpilot/selfdrive/controls/tests/test_radard_dedupe.py \
  openpilot/selfdrive/controls/tests/test_radar_confirmed_fcw.py \
  openpilot/selfdrive/controls/tests/test_subaru_planner_fcw.py \
  openpilot/selfdrive/controls/tests/test_voacc.py
```
