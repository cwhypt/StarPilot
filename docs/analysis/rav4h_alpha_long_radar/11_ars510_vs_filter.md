# 11 — 为什么不能"只用 ars510-radar、不要过滤器"？

状态：完成。前置：05（更正）、08、09、10。

## 1. 两者解决的是不同的问题

| | ars510-radar（已集成为 `ToyotaRadarMode` 1/2） | radar CAN filter（如 OneClone） |
|---|---|---|
| 类型 | **软件解析器**：读雷达私有总线 bus 1 上的目标数据 | **硬件**：串在雷达与车之间，拦雷达的 0x343 |
| 给 StarPilot 什么 | 雷达看到的前车（距离/相对速度）→ radard → 规划 | 不提供数据；让 StarPilot 不必 UDS 关雷达 |
| 雷达车内总线输出（0x343/0x283/0x344/0x411） | Alpha Long 下照样被 UDS 关掉 | 保留（只拦 0x343） |
| 原车 AEB / FCW | ✗（上游 docs/15："the radar disable takes the car's AEB with it"；仪表 PCS 故障灯） | ✓ |
| 原车 `ACCEL_CMD` | ✗ | ✗（smart_dsu 不镜像，见 10） |
| 需要硬件 | 否（comma 读 bus 1，据上游） | 是 |

- 本 repo 的 `ars510_acc_target.py` / `ars510_radar.py` 就是从 ds-sebastian/ars510-radar `fb5b29c` 改编的（文件头注释），"直接用它" = 在 StarPilot 里设 `ToyotaRadarMode=1/2`。
- 它不需要过滤器，是因为它**只读不写**：读的是 bus 1，与 UDS 关闭的车内总线发送无关。

## 2. 对用户目标的覆盖

| 目标 | 仅 ars510 | 仅过滤器 | 两者都用 |
|------|-----------|----------|----------|
| StarPilot 导航/红灯停车（Alpha Long） | ✓ | ✓ | ✓ |
| 雷达看到的车进入规划 | ✓（受 radard 门限限制，02） | ✗ | ✓ |
| 中间地带兜底 | 需加 04 方案 C（软件 cap） | ✗ | 方案 C |
| 原车 AEB/FCW | ✗ | ✓ | ✓ |

## 3. 结论

- 只要求"雷达参与 + StarPilot 功能"、接受**没有原车 AEB**：只用 ars510（+ 方案 C），不买过滤器（= 06 的 R2）。
- 要原车 AEB/FCW：必须过滤器；ars510 与之互补，建议同时启用（= R3 + Mode 2 + 方案 C）。
- 注意：仅 ars510 时，`ToyotaRadarMode` 默认 0；radard 只在视觉已确认或车速 <4 m/s 时采用雷达车（02），所以"雷达兜底"要靠方案 C 才完整。
