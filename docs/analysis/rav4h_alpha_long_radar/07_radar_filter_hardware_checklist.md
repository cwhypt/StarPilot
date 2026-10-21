# 07 — R3 路线：radar CAN filter 硬件选型清单（按 StarPilot 代码要求）

状态：完成（代码推导）。用户候选产品：`https://oneclone.net/product/toyota-radar-acc-filter/` —— **页面在本环境无法访问（DNS 失败），搜索也无结果，产品细节未核实**。下文是"任何过滤器要在 StarPilot 上达到 R3 效果，代码要求它满足什么"。

## 1. StarPilot 如何识别过滤器（硬性条件）

- RAV4 2023-25 是 `NO_DSU_CAR`（`ToyotaTSS2PlatformConfig.init` 置 `NO_DSU`，`values.py:123-124`）。
- 识别逻辑（`car/interfaces.py:306-311`、`toyota/interface.py:73-76`）：**指纹阶段在 bus 0 上看到 0x2AA** → `RADAR_CAN_FILTER` + `SMART_DSU` 标志、`use_sdsu=True`、安全参数加 `LONG_FILTER`。
- 之后（`interface.py:154-159`）：`use_sdsu` 为真时**不置** `DISABLE_RADAR` → 不发 UDS 禁用 → 雷达保持工作；`openpilotLongitudinalControl = use_sdsu and alpha_long`，**Alpha Long 开关仍需打开**。
- 测试 `toyota/tests/test_ars510.py` 用 `fingerprint[0][0x2aa] = 8` 模拟"装了过滤器"。

→ **过滤器必须在 bus 0（车内 PT 总线）上发送 0x2AA 或 0x2FF**（更正，见 08：`interfaces.py:310` 中 `0x2FF in fingerprint[0]` 也会置 `SMART_DSU` → `use_sdsu`；0x2AA 额外置 `RADAR_CAN_FILTER`）**，且上电后足够早（在 openpilot 指纹采集窗口内）出现**。两者都不发的过滤器：StarPilot 当作没装，照样 UDS 关雷达，AEB 仍丢。这是购买前必须向卖家确认的第一项。

## 2. 功能要求

| 要求 | 原因（代码） |
|------|-------------|
| 串接在**雷达与车之间**，丢弃雷达发往车的 `ACC_CONTROL` 0x343 | openpilot 在 bus 0 发自己的 0x343（`carcontroller.py:583`），两个发送者会冲突 |
| 放行 0x283 `PRE_COLLISION`、0x344 `PRE_COLLISION_2`、0x411 `PCS_HUD` 等其余雷达报文 | 保留原车 AEB/FCW；`carstate.py:129,200` 在未置 `DISABLE_RADAR` 时会读 `stockAeb`/`stockFcw` |
| 不影响雷达 bus 1（雷达私有总线）输出 | ARS510 解析（`ToyotaRadarMode`）读 bus 1 |
| （加分）把被丢弃的雷达 0x343 转发到某处供读取 | 实现 05 §3 "min(雷达 ACCEL_CMD, openpilot)" 需要；若过滤器直接吞掉，该功能做不了，但 AEB 仍保留 |

## 3. 购买前问卖家的问题

1. 是否支持 **RAV4 / RAV4 Hybrid 2023-2025（Continental ARS510 雷达，fwdRadar `8821F0R03100`）**？
2. 是否在 bus 0 广播 **0x2AA 或 0x2FF**？（与 sunnypilot/openpilot 社区"CAN filter"检测一致即可。）
3. 被拦截的雷达 0x343 是否可在某条总线上读到？
4. 装后原车 **PCS/AEB 与 FCW 是否正常、仪表是否无 PCS 故障灯**？
5. 安装位置与方式：是否插在雷达接插件处（前格栅/保险杠后），是否需要拆格栅、是否附带转接线/延长线、供电来源。
6. 不开 openpilot 纵向时，是否完全透明（原车 ACC 正常）？

## 4. 硬件清单（R3）

- comma 设备 + comma 官方对应 RAV4 2025 Hybrid 的 Toyota harness（插在前挡摄像头处）。
- radar CAN filter（满足 §1-§2）。
- 可能需要：雷达接插件转接线/延长线、拆格栅工具（取决于产品，未核实）。
- 不需要额外 CAN 分线来读 ARS510 bus 1（据上游 ars510-radar，二手转述，05 更正）。

## 5. 软件侧（装好后）

1. 打开 Alpha Long 开关，重启；确认 `carParams` 中无 `DISABLE_RADAR` 标志、`openpilotLongitudinalControl=True`。
2. 确认 `carState.stockAeb`/`stockFcw` 字段有更新（说明雷达报文仍在被解析）。
3. 可选：`ToyotaRadarMode=2`。
4. 之后再做 05 §3 的 min 融合（取决于 §2 第 4 行能否读到雷达 0x343）。
