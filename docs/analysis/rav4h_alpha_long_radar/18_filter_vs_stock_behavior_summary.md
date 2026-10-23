# 18 — 装过滤器后与原车行为的区别（总结）

状态：完成（汇总 08、09、13、17 + 代码）。车：2025 RAV4H；过滤器假设为 smartDSU 类（wocsor 旧版或 ocelot/Beartech 新版）。

## 1. 三种工况

### A. comma 运行 + Alpha Long 开（装过滤器的目的）
| 功能 | 原车 | 装过滤器后 | 依据 |
|---|---|---|---|
| 日常加减速（ACC） | 雷达 ACC 决定 | **StarPilot 决定**；雷达 0x343 被过滤器拦掉。StarPilot 只要处于 openpilot 纵向模式就每 3 帧发 0x343（含未接管时，`carcontroller.py:482-483`）→ comma 运行期间**无法使用原车 ACC 逻辑** | 09 |
| 导航减速、红灯/停止标志停车、限速、弯道 | 无 | 有 | 13 |
| AEB（0x283 / 0x344） | 有 | **不变**（透传） | 17 |
| FCW 仪表提示（0x411） | 有 | **不变**（透传）；StarPilot 不再代发 FCW（只在 `DISABLE_RADAR` 时代发，`carcontroller.py:627`） | — |
| 仪表 PCS 故障灯 | 无 | 无（与"不装过滤器 + Alpha Long"相比，不再亮）[据 ars510 #66 / 上游文档] | 16 |
| 原车 ACC 的减速建议 | 生效 | **丢弃**，不能与 StarPilot 取 min | 10 |
| 跟车距离按钮 | 雷达 ACC 用 | 过滤器经 0x2FF 转报，StarPilot 用于切换 personality，并通过自己的 0x343 `DISTANCE` 同步 PCM 档位（`carcontroller.py:484-490`、`carstate.py:268`） | 17 |
| 雷达私有总线数据（ARS510） | — | 可用（`ToyotaRadarMode`） | 13 |
| 起步/停车、低速 | 原车 | StarPilot | — |

### B. comma 运行 + Alpha Long 关（原车纵向）
- 发布分支：识别到过滤器，但 `use_sdsu = use_sdsu and alpha_long` → 原车纵向（`interface.py:159`），StarPilot 只做横向，只在取消巡航时发 0x343。
- 过滤器透传雷达 0x343：
  - wocsor 旧版：**原样** → 与原车一致。
  - ocelot/Beartech 新版：**改写 `ACC_TYPE`=1、`PERMIT_BRAKING`=1** 后透传 → 原车 ACC 的刹车方式可能与出厂不同（#66 车主认为 `PERMIT_BRAKING` 像"允许摩擦制动"）。
- StarPilot 发取消用的 0x343 时，过滤器会在随后 250 ms（旧版）/100 ms（新版）内拦雷达 0x343——相当于强制取消，符合预期。

### C. comma 关机 / 拔掉 / 崩溃
- 过滤器在 250 ms（旧版）/ 100 ms（新版）没见到 openpilot 0x343 后恢复透传 → 原车 ACC 接管。
- 旧版：雷达开始发报文后的头 2 s 发伪造 0x343（加速度 0）并拦真实 0x343；之后与原车一致。
- 新版：持续改写 `ACC_TYPE`/`PERMIT_BRAKING`，**不完全等同出厂**。

## 2. 新增的风险 / 区别

1. **雷达线路上多了一个故障点**：过滤器掉电、接插件进水或接触不良 → 雷达与车断开 → ACC、PCS 故障。#66 车主就遇到雷达侧接插件未密封导致的故障。
2. **识别失败 = 等于没装**：StarPilot 开机指纹期没看到 0x2FF/0x2AA → 照常 UDS 关雷达 → 该次行程 AEB 丢失、PCS 灯亮。旧版固件（0x2FF 依赖雷达先发 0x343）风险更高。
3. 改动原车线束：保修、进水、日后拆除。
4. min 融合仍不可用（两版固件都不转发雷达 0x343）。

## 3. 一句话

装过滤器后：**加减速归 StarPilot，AEB/FCW 仍是原车**，仪表不再报 PCS 故障；代价是丢掉原车 ACC 的减速建议、雷达线路多一个故障点，新版固件还会轻微改变"不用 StarPilot 纵向时"的原车 ACC。
