# 10 — 为什么装 OneClone 过滤器后仍做不到 min(雷达, StarPilot)？缺口在哪？怎么补？

状态：完成（基于 08/09 + 本 repo 代码）。

## 1. 为什么做不到：数据到不了 comma

```
雷达 ──(雷达侧线)── [过滤器 panda] ──(车侧 bus 0)── 车内各 ECU …… comma 线束(在前挡摄像头处)
```

- 雷达的 0x343（含 `ACCEL_CMD`）先到过滤器；smart_dsu 固件在 openpilot 发 0x343 期间**直接丢弃**它（08 §2），不复制、不改 ID 转发。
- comma 只接在车侧总线上（经摄像头处线束），**永远看不到**被丢弃的那份 0x343。
- StarPilot 代码里也没有任何地方读取"雷达的 ACCEL_CMD"（01 §3）。

→ 不是物理上不可能，而是**这款固件没把数据送出来**。过滤器本身其实收到了它。

## 2. 缺口具体是什么

| 区间 | 谁在管 | 说明 |
|------|--------|------|
| 正常跟车 | StarPilot（视觉为主，可选 ARS510 雷达校正） | 正常 |
| **中间地带**：视觉漏检/低估闭合速度，雷达 ACC 本会"中等减速"（例如 -0.5 ~ -2 m/s²） | **无人** | 雷达那份 `ACCEL_CMD` 被丢；StarPilot 不知道；AEB 尚未达到触发条件 |
| 碰撞迫近 | 原车 FCW（先警告）→ AEB（重刹） | 来得晚、刹得猛，是最后防线 |

典型场景：逆光/夜间/大车/静止车尾，视觉 lead 概率低于阈值或测距偏大（02 §6）。

## 3. 补缺口的三种办法

### A. 改过滤器固件：把雷达 0x343 镜像到车侧（真正的 min）
- 固件改动：丢弃雷达 0x343 的同时，以新 ID（如 0x2AA，与 Smartype/canfilter 的 `F_MIRROR_ACC_MSG` 相同）把原数据发到车侧 bus 0。约几十行 C，在 smart_dsu 的 `default_fwd_hook` 里已有 `send_id()` 可参照。
- StarPilot 改动：DBC 加一条消息；`carstate` 解析为 `stockAccelCmd`；`carcontroller.py:575` 前 `pcm_accel_cmd = min(pcm_accel_cmd, stock)`（仅在有效且为负时）。0x2AA 出现在指纹里还会额外置 `RADAR_CAN_FILTER`（`interfaces.py:308`），可作为"支持镜像"的标志。
- 难点 / 风险：需要能刷写过滤器（是 panda 克隆板，理论可刷，需卖家确认）；雷达在被拦截状态下输出的 `ACCEL_CMD` 是否有意义（它朝 PCM 设定速度调节，StarPilot 若因限速/偏移想开得比 PCM 设定速度快，min 会把车压慢；雷达 ACC 的内部状态未验证）→ 先只记录，后启用（05 §3）。

### B. 换硬件：Smartype/canfilter 类
- 原生镜像 0x2AA，但要求 StarPilot 把 ACC 指令发到 0x2A2，需改 StarPilot + panda 安全白名单（08 §5）。无现成售卖信息。

### C. 不动硬件：用雷达**感知数据**自己算制动上限（04 方案 C）
- comma 在 bus 1 可读 ARS510 的 ACC 目标（距离、相对速度、相对加速度，据上游 ars510-radar，05 更正）。
- 在 planner 末端加"只减不增"的 cap：`a_cap = -(closing²)/(2·max(d - d_min, 1))`，限幅 [-2.5, 0]，数据过期失效。
- 语义接近"雷达觉得该减速就减速"，但数值不是原车的 `ACCEL_CMD`；与过滤器（保 AEB）**可以叠加**。

## 4. 推荐

OneClone 过滤器（保 AEB/FCW） + 方案 C（软件补中间地带），两者都不需要改过滤器固件；方案 A 作为进阶（需确认过滤器可刷写）。
