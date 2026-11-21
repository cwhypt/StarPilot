# 24 — T2 实现：雷达总线 PCS_ALARM → 会弹提示的 FCW

状态：**代码完成，离线测试通过；未上车验证**。前置：14 §2 P2a、22（commaai/opendbc#3860）、23 §2。

## 1. 做了什么

开 Alpha Long + `ToyotaRadarMode` 1 或 2 时，读取雷达私有总线（bus 1）上的 `PCS_STATE`(0x100).`PCS_ALARM`，
写入 `carState.stockFcw`，并映射到会弹出 "BRAKE! Risk of Collision" + 声音的 `EventName.fcw`。
`fcw` 事件经已有路径（`hudControl.visualAlert == fcw` → `carcontroller.py:627-628` `create_fcw_command`，仅 `DISABLE_RADAR` 时发送）同时点亮仪表 FCW 图标。

**只提醒，不刹车。**

## 2. 改动（实际值）

| 文件 | 改动 |
|---|---|
| `opendbc_repo/opendbc/dbc/toyota_tss2_ars510.dbc` | `PCS_STATE` 增加 `POWER_UP : 15\|8@0+`（与 #3860 一致）+ 注释 |
| `opendbc_repo/opendbc/car/toyota/carstate.py` | `radar_pcs_fcw(CP)` 门控函数；`get_can_parsers` 在门控为真时加 bus 1 解析器（`PCS_STATE`，频率 `nan` = 可选）；`update_radar_pcs_alarm()`；`stockFcw = 车内总线 FCW or 雷达总线报警` |
| `selfdrive/car/car_specific.py` | Toyota 分支：`CS.stockFcw and radar_pcs_fcw(CP)` → `EventName.fcw` |
| `opendbc_repo/opendbc/car/toyota/tests/test_ars510_pcs_fcw.py`（新） | 48 项：门控矩阵、其他 Toyota 车型不变、上电首帧忽略、报警开始/保持/结束、0.5 s 无帧后清除、其他总线的同 ID 忽略、雷达总线缺失不导致 CAN 无效、整条 `CarInterface.update` 路径 |
| `selfdrive/car/tests/test_car_specific.py` | 1 项：四种 flag 组合下是否产生 `fcw` |

## 3. 设计取舍

- **门控**：`ARS510_RADAR`（即 `ToyotaRadarMode` 1/2，默认 0 = 关）**且** `DISABLE_RADAR`（Alpha Long 且无过滤器）。
  - Mode 0：不加解析器，行为与之前逐位一致。
  - 装了过滤器：雷达未被关闭，原车 0x411 FCW 已在仪表上，不重复。
  - 关 Alpha Long：同上。
  - **没有另加开关**：`ToyotaRadarMode` 本身就是默认关的实验开关。若上车后误报多，再拆出独立开关。
- **位置**：照 #3860 放在 carstate（而不是 23 §2 设想的 `card.py` 里从 RadarInterface 取），优点是不碰 `card.py`、不改 RadarInterface，Mode 1 与 Mode 2 共用。
- **上电首帧**：`FF FF` 帧的 `POWER_UP=0xFF` → 该帧的 `PCS_ALARM` 被忽略。
- **过期**：carstate 100 Hz，`PCS_STATE` 10 Hz；连续 50 帧（0.5 s）无新帧则清除保持中的报警，防止雷达掉线时报警卡住。
- **CAN 有效性**：消息频率为 `nan`（`ignore_alive`），bus 1 没有数据时 `canValid`/`canTimeout` 不受影响，只是不报警。

## 4. 测试

- `opendbc_repo/opendbc/car/toyota/tests`、`opendbc/dbc`、`opendbc/can/tests`：924 passed。
- `opendbc/car/tests/test_car_interfaces.py -k toyota`：41 passed。
- `selfdrive/car/tests/test_car_specific.py`：新测试通过；`test_kia_ray_ev_allows_stock_cruise_to_enable_controls` 在**未改动的代码上同样失败**（测试桩缺 `pcmCruise`），与本改动无关。
  - 本机运行条件：repo 里的 `msgq/ipc_pyx.so`、`transformations.so` 是 aarch64 设备版，x86 容器无法加载；用 scratchpad 里的仅导入桩 + `--noconftest -c /dev/null` 运行。设备上不受影响。

## 5. 未验证 / 局限

- **最关键**：Alpha Long 下（雷达被 UDS `28 01 01` 关闭车内发送）`0x100` 是否仍在 bus 1 上发送、`PCS_ALARM` 是否仍会置位 —— **没有任何记录证实**（TODO V2/V3）。如果不发，这个功能就是永远不响，不会有副作用。
- 报警阈值即原车 FCW 阈值，是"最后一刻"的预警，不是中间地带的跟车提醒。
- 不刹车：AEB 仍然没有。
- 若上车出现误报：把 `ToyotaRadarMode` 改回 0 即可完全关闭（同时关闭雷达感知）。

## 6. 上车后怎么验证

原则：**不刻意制造危险场景**。验证分五步，前一步不过就不往下做。工具：comma connect 下载 rlog；cabana 看 CAN（加载 `toyota_tss2_ars510` DBC 看 bus 1）；PlotJuggler / `tools/lib/logreader.py` 看 `carState`、`onroadEvents`。

| 步骤 | 条件 | 看什么 | 通过标准 | 不通过意味着 |
|---|---|---|---|---|
| 0 配置 | Alpha Long 开、`ToyotaRadarMode=1`、无过滤器，重启 | `carParams.flags`；`carFw` 的 `fwdRadar` | flags 含 `ARS510_RADAR`(16384) 和 `DISABLE_RADAR`(4)；fwdRadar `8821F0R03100` | 门控没打开，T2 不会生效；fw 不同则 DBC 语义可能不同 |
| 1 静态 | 停车、READY、P 挡，录 1-2 分钟 | `can` 中 src=1、地址 0x100 | 约 10 Hz 持续存在；上电后第一帧 `FF FF`，之后 `PCS_ALARM=0`；`carState.stockFcw` 恒 False；开机没有 BRAKE 提示 | **0x100 不存在 → T2 在你的车上无效**（V2），后面不用做，也说明 C 方案没意义 |
| 2 日常驾驶 | 正常开几天（含高速、城市、停车场、弯道、桥/护栏） | 每段 0x100 频率；`stockFcw`；`onroadEvents` 里的 `fcw` | 0x100 全程约 10 Hz（尤其 openpilot 接管期间）；无危险时 `stockFcw` 从不为 True | 出现无理由报警 → 误报，记下路线和时间，`ToyotaRadarMode` 改回 0 |
| 3 报警是否同源 | **关 Alpha Long**（原车 ACC），同样日常驾驶，等自然发生的原车 FCW | bus 0 的 0x411 `FCW`/0x283，对比 bus 1 的 0x100 `PCS_ALARM` | 原车 FCW 每次出现，bus 1 `PCS_ALARM` 同帧前后置 1，结束也一致 | 两者不一致 → 信号语义与 ars510 作者的记录不同，T2 不可信 |
| 4 Alpha Long 下真实报警 | 回到 Alpha Long，靠自然发生（可能很久都没有） | 报警时刻：`stockFcw`=True、`onroadEvents` 有 `fcw`、`sendcan` src=0 地址 0x411 `FCW=1`、屏幕和仪表 | 四者同时出现，报警结束后 ≤0.5 s 清除 | 只有 `stockFcw` 没有提示 → 事件映射问题；有 `PCS_ALARM` 没 `stockFcw` → 解析/门控问题 |
| 5 故障情况 | 自然遇到的雨雪遮挡、雷达报故障 | 0x196 故障位、`radarTempUnavailable`、`stockFcw` | 雷达故障/静默时报警不会卡住（0.5 s 内清除） | 卡住 → 过期逻辑问题 |

**区分报警来源：** `fcw` 事件也可能来自视觉模型（`modelV2.meta.hardBrakePredicted`）或规划器（`longitudinalPlan.fcw`）。只有同一时刻 `carState.stockFcw=True` 的才是 T2 触发的。

**第 3 步为什么有用：** 关 Alpha Long 时雷达照常工作，原车 FCW 在 bus 0 上可见，而 rlog 同时记录所有总线的原始 CAN（不依赖 T2 门控），所以可以离线核对"原车报警 = bus 1 `PCS_ALARM`"。这一步证明信号含义正确；第 1、2 步证明 Alpha Long 下信号还在；两者都成立，T2 才算可信。

**关于 PCS 灵敏度：** 第 3 步若迟迟等不到自然报警，可考虑在车机里把 PCS 灵敏度调到"早"以增加自然触发的机会（[推断] 是否影响 Alpha Long 下 bus 1 的报警阈值未知）。仍然不要人为制造险情。

## 7. 自动检查脚本：`tools/longitudinal/check_t2_radar_fcw.py`

把 §6 的第 0–5 步自动化：给它日志，它逐步输出 PASS / FAIL / INFO / N/A，并列出每次 `fcw` 提示的来源。任何一步 FAIL 时退出码为 1。

### 用法

```bash
# 设备上（SSH 进去）
cd /data/openpilot
python tools/longitudinal/check_t2_radar_fcw.py /data/media/0/realdata/<路线>--0/rlog.zst /data/media/0/realdata/<路线>--1/rlog.zst

# 电脑上（openpilot 开发环境），整条路线或几段
python tools/longitudinal/check_t2_radar_fcw.py "<dongle_id>|<路线>"
python tools/longitudinal/check_t2_radar_fcw.py <rlog 路径> --json
```

- **必须用 rlog，不能用 qlog**：qlog 不含完整的 `can`/`sendcan`，第 1、3、4 步会全部变成"没有数据"。
- 包含第 0 段（`--0`）：`carParams` 在路线开头，没有它第 0 步会 FAIL。只有从第 0 段开始才看得到上电首帧。

### 每一步在日志里看什么

| 步骤 | 检查项 | 数据来源 | 判定 |
|---|---|---|---|
| 0 | flags、雷达固件 | `carParams.flags`、`carFw`(fwdRadar) | 有 `ARS510_RADAR`+`DISABLE_RADAR` = PASS；只缺 `DISABLE_RADAR`（原车纵向/过滤器）= INFO，可用于第 3 步；固件不是 `8821F0R03100` = INFO |
| 1 | 0x100 是否存在、频率、>0.5 s 断档、首帧、开机 10 s 内是否报警 | `can` src=1 地址 0x100；`carState.stockFcw` | 没有 0x100 = **FAIL（T2 在这台车上无效）**；频率不在 8–12 Hz = FAIL |
| 2 | 雷达报警的时间列表；接管期间 0x100 频率 | 0x100 `PCS_ALARM`（排除首帧）；`selfdriveState.enabled` | 报警列为 INFO：**需要你按列出的时间看行车视频**，判断是不是误报 |
| 3 | 原车 FCW 与雷达总线报警是否一一对应（±0.3 s） | bus 0 收到的 0x411 `FCW` vs bus 1 `PCS_ALARM` | 只在原车纵向的日志里有 0x411，Alpha Long 下为 N/A |
| 4 | 每次雷达报警 → `stockFcw` → `fcw` 事件 → openpilot 发出 0x411 `FCW=1` | `carState`、`onroadEvents`、`sendcan` src=0 0x411 | 任何一环缺失 = FAIL，并指出是哪一环（解析/门控、事件映射、仪表图标、清除延迟） |
| 5 | 雷达静默时报警是否卡住；故障次数 | `stockFcw` 与最近一帧 0x100 的间隔；0x196 `RADAR_FAULT`（连续 ≥2 帧）、`radarTempUnavailable` | 卡住 = FAIL；故障列为 INFO |
| 来源 | 每次 `fcw` 提示来自雷达（T2）、视觉模型还是规划器 | `onroadEvents` 与 `stockFcw` / `modelV2.meta.hardBrakePredicted` / `longitudinalPlan.fcw` 的重叠 | 只有标"radar (T2)"的才是 T2 触发的 |

### 推荐的使用顺序

1. 装好后第一次开车：跑一次，重点看第 0、1 步。第 1 步 FAIL 就到此为止（T2 无效）。
2. 之后每天或每次长途跑一次：看第 2 步列出的报警时刻是否都有真实风险，看第 4、5 步。
3. 偶尔关 Alpha Long 开一段：跑第 3 步，核对信号含义。

### 测试（离线，未用真实日志）

- `tools/longitudinal/tests/test_check_t2_radar_fcw.py`：12 项。模拟日志里的 `stockFcw` 由**真实的** carstate 代码（`update_radar_pcs_alarm`）根据模拟的雷达帧算出，其余部分模拟。覆盖：正常一次报警全部 PASS、安静驾驶、没有 0x100、flags/固件不对、报警中途雷达掉线、报警卡住、事件映射或仪表图标缺失、开机 10 s 内报警、原车纵向下第 3 步通过与不通过、`fcw` 来源归属。
- **变异检查**：故意去掉 carstate 的首帧过滤 → 3 项失败；去掉 0.5 s 超时 → 1 项失败；恢复后 12 项全过。说明脚本确实能抓到这两类实现错误。
- 命令行入口：用模拟的 rlog 文件经 `LogReader` 跑通，正常日志退出码 0、没有 0x100 的日志退出码 1，`--json` 正常。
- 局限：没有真实 RAV4 日志，第 3 步的 ±0.3 s、频率 8–12 Hz 等阈值是按设计值定的，拿到真实日志后可能要调。
