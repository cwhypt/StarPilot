# 17 — smartDSU 固件对比：wocsor 旧版 vs RetroPilot ocelot 新版（及 Beartech "New SDSU"）

状态：完成（两份源码逐行阅读，2026-10-23）。
- 旧版：`wocsor/panda` 分支 `smart_dsu`，HEAD `9014acd`（2022-03-25），逻辑在 `board/main.c`（`send_spoof_acc`、`send_id`）与 `board/safety/safety_defaults.h`（`default_fwd_hook`）。README 首行："THIS CODE IS DEPRECATED. PLEASE USE THIS INSTEAD https://github.com/RetroPilot/panda/tree/master/board/smart_dsu"。eFiniLan/OneClone 的 DIY 说明引用的就是它。
- 新版：`RetroPilot/ocelot` `firmware/smart_dsu/main.c`，HEAD `94efc67`（2025-11-13）。
- Beartech "New SDSU"：`fraserxiong/panda_firmware` 分支 `New_SDSU_firmware`（2024-04-07）**只有二进制**（`sdsu.bin`、`bootstub.sdsu.bin`），无源码；README 称修复了"openpilot 0.9.6 + 原版 SDSU 固件约 70 分钟后 LKAS 报错"的 bug。据 ars510 issue #66 车主所述，它基于 ocelot `smart_dsu` + Beartech 修改（16）。下表对它的判断只能依据 issue #66 的实测行为。

## 1. 对比总表

| 方面 | wocsor 旧版 | ocelot 新版 | 对 StarPilot / 本车的影响 |
|---|---|---|---|
| 硬件 | white/grey panda（总线 0=车，2=DSU/雷达） | RetroPilot ocelot gateway/pro（CAN1=车，CAN3=DSU/雷达；CAN2 未用） | 两者都是"串在雷达与车之间"的双路 CAN 网关 |
| 维护状态 | 2022 年起**已弃用** | 2025-11 仍有提交 | — |
| **0x2FF 心跳：触发** | **事件触发**：每收到一帧 DSU/雷达 0x343 才发一帧 | **定时发送**（TIM3 中断，`send` 每两拍发一次，注释"cap this to 50Hz"），与雷达是否启动无关 | 旧版：雷达启动慢时，openpilot 指纹期可能看不到 0x2FF → 误判"没装过滤器"→ UDS 关雷达、AEB 丢失（08 §4.2）。新版：上电即有，风险低 |
| 0x2FF 内容 | 4 字节：`(雷达0x343 byte2 高 4 位) << 7`；即 byte0 bit7 = DISTANCE 按钮，MINI_CAR/ACC_TYPE 位溢出到 byte1；无计数器、无校验 | 4 字节：byte0 bit7 按钮；byte1 `ctrl_mode`；byte2 `state<<4 | counter`；byte3 Toyota 校验 | 发布分支只读 `SDSU.FD_BUTTON`（byte0 bit7，`carstate.py:268`）→ 两者都能用；DBC 的 `SDSU.STATE`（byte2 高 4 位）在旧版恒为 0，新版是真实故障码（StarPilot 目前未读） |
| 0x2FF 发送方式 | 直接写 CAN 发送邮箱 0（绕过发送队列，可能覆盖待发帧） | 经 `can_send` 队列；邮箱忙时记录漏发 | 新版更稳 |
| **拦截雷达 0x343 的条件** | 车侧最近 **250 ms** 内见过 openpilot 的 0x343（时间戳只在收到车侧帧时刷新）；或开机 2 s 内 | `op_ctrl_mode`：见到 openpilot 0x343 置位，**100 ms** 未再见到即清除 | 两者 comma 停发后都回落原车 ACC；新版回落更快 |
| 原车 0x343 透传时 | **原样转发** | **改写**：`ACC_TYPE` 位改为 01、`PERMIT_BRAKING`=1，重算校验（README：DSU 车需要；"2022 RAV4 Hybrid … slight firmware changes"） | issue #66（2025 RAV4H + Beartech New SDSU）实测 73,513/73,513 帧有此改写 → **装了新版后，不开 openpilot 纵向时的原车 ACC 行为也会变**（车主分析 `PERMIT_BRAKING` 像"允许摩擦制动"）。需确认对 TSS2 雷达 ACC 是否合适 |
| 开机伪造 0x343 | 有：DSU 侧开始有报文后 2 s 内，**每收到一帧就发一次**固定的伪 0x343（`00 00 63 c0 00 00 00 71`：ACC_TYPE=1、MINI_CAR、PERMIT_BRAKING、加速度 0），并拦截真实 0x343，用于防 DSU 晚启动导致巡航故障；README 自认 bug："comma 先开机时与其 0x343 同时发送" | 无 | 旧版在 TSS2 雷达车上的这 2 s 行为未经验证 |
| AEB 报文 0x344 / 0x283 | 原样透传 | 默认原样透传；另支持 0xF11 指令：原车 AEB **未激活**时用 openpilot 指令改写 0x344（`PRE_COLLISION_2`）实现"非 ACC 制动" | 两者默认都保留原车 AEB；StarPilot 不发 0xF11，新增功能不生效 |
| 替代 ACC 模式 | 无 | 0xF10：用 openpilot 的加速度改写**雷达自己的** 0x343 后转发（保持原车发送节奏） | StarPilot 发的是 0x343 而非 0xF10 → 走普通拦截模式 |
| 故障与健壮性 | 无状态上报；运行在 panda 固件（USB、安全模式 ALLOUTPUT） | 状态码（校验错、超时、SCE、启动中）、CAN 错误中断处理、看门狗 | 新版可被 StarPilot 监控（读 `SDSU.STATE`），目前未实现 |

## 2. 结论

- 对本车（2025 RAV4H，TSS2 雷达 ACC）最关键的两点差异：
  1. **0x2FF 是否定时发送**：新版定时 → StarPilot 开机识别可靠；旧版依赖雷达先发 0x343 → 雷达启动慢时可能识别失败，等于没装。
  2. **透传时是否改写原车 0x343**：新版（含 Beartech 实测）会改 `ACC_TYPE`/`PERMIT_BRAKING` → 影响"不开 openpilot 纵向"时的原车 ACC；旧版不改。
- 两者都：只在 openpilot 发 0x343 时拦雷达 0x343；原样放行 AEB/FCW 报文；不把雷达 0x343 转发出来（都不能做 min 融合）。
- 有实车记录的组合是 **2025 RAV4H + Beartech New SDSU（ocelot 系）**；OneClone 若确为 wocsor 旧版，没有同车型记录，且有开机识别风险。

## 3. 可延伸的 StarPilot 改动（加入 TODO）

- 读取 `SDSU.STATE`：对新版过滤器上报的故障（超时/校验错）给出告警（F 系列，装过滤器后）。

## 4. 来源

- https://github.com/wocsor/panda/tree/smart_dsu （`board/main.c`、`board/safety/safety_defaults.h`、`README.md`）
- https://github.com/RetroPilot/ocelot/tree/main/firmware/smart_dsu （`main.c`、`README.md`）
- https://github.com/fraserxiong/panda_firmware/tree/New_SDSU_firmware
- https://github.com/ds-sebastian/ars510-radar/issues/66
