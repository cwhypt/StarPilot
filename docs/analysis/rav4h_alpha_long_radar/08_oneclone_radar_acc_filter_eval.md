# 08 — OneClone "Toyota Radar ACC Filter"（$99）评估

状态：完成。资料来源：
- 用户粘贴的 OneClone 产品页文字与图片（`oneclone.net/product/toyota-radar-acc-filter/`，本环境无法直接访问）；
- `github.com/eFiniLan/radar_acc_filter` README（产品页文字与之相同）——该 repo **没有固件代码**，只说明用"white/grey panda + wocsor 的 smart_dsu 固件"，零件：OBD-2 母座、12520/12519 接插件、120Ω 电阻；
- `github.com/wocsor/panda` `smart_dsu` 分支固件（`board/main.c`、`board/safety/safety_defaults.h`）——子 agent 读取并引用代码，**二手转述**；
- 对比：`github.com/Smartype/canfilter`（README 中提到的"更高级"方案）。
- StarPilot 侧为本 repo 代码（一手）。

## 1. 结论

| 需求 | 该产品（smart_dsu 固件） | 依据 |
|------|------------------------|------|
| 让 StarPilot 开 openpilot 纵向且**不 UDS 关雷达** | ✓（代码层面可行） | 固件在 bus 0 发 **0x2FF**；StarPilot `0x2FF in fingerprint[0]` → `SMART_DSU` → `use_sdsu=True` → 不置 `DISABLE_RADAR`（`car/interfaces.py:308-311`，`toyota/interface.py:73-76,154-159`）|
| 保留原车 AEB（0x344/0x283）与 FCW（0x411） | ✓（固件不碰这些 ID，双向透传） | smart_dsu 转发器只对 0x343 做处理 |
| StarPilot 读到 stockAeb/stockFcw | ✓ | 未置 `DISABLE_RADAR` 时 `carstate.py:129,200` 解析 |
| comma 失效时自动回落原车 ACC | ✓ 附带收益 | 只在 250 ms 内看到车侧 0x343（openpilot 发出）时才拦雷达 0x343，否则透传 |
| **读到雷达 ACCEL_CMD 做 min 融合** | ✗ | smart_dsu 不复制/转发被拦下的雷达 0x343 |
| 明确支持 RAV4 Hybrid 2025（ARS510） | **未声明** | 只写"some new 2022 RAV4" |

**一句话**：它能实现 R3 的核心（StarPilot 功能 + 原车 AEB/FCW），但**不能**实现"取原车加速度与 StarPilot 加速度的较小值"。

## 2. smart_dsu 固件行为（二手转述，引用自子 agent 读到的代码）

- 拓扑：panda bus 0 = 车侧，bus 2 = 雷达侧。
- bus 0 → bus 2：全部转发；若是 0x343（openpilot 发的），置 `block=1` 并记时间。
- bus 2 → bus 0：`blockmsg = (block | !boot_done) && (addr == 0x343)`，其余全部转发。`block` 在 250 ms 未见车侧 0x343 后清零；上电前 2 s（`!boot_done`）发固定的伪 0x343 `{00 00 63 c0 00 00 00 71}`。
- 收到雷达 0x343 时调用 `send_id()`：在 bus 0 发 **0x2FF，DLC 4**，byte0 bit7 携带雷达 0x343 byte2 高 4 位（距离按钮）。
- 无周期心跳；0x2FF 的频率 = 雷达 0x343 的频率。

## 3. 与 StarPilot 代码的对接检查（一手）

| 检查项 | 结果 |
|--------|------|
| 识别 | 0x2FF 在指纹 bus 0 出现即可（`interfaces.py:310`），不需要 0x2AA。`RADAR_CAN_FILTER` 标志不会置（那需要 0x2AA），只影响距离按钮来源 |
| 0x2FF 解析 | DBC `SDSU` 定义 8 字节（`toyota_nodsu_pt_generated.dbc:38-40`），固件发 4 字节；`CANParser` 不校验 DLC，`FD_BUTTON`(byte0 bit7) 与 `STATE`(byte2) 都在 4 字节内 → 可解析（`can/parser.py:53-97`）|
| SDSU 报文存活检查 | `carstate.py:268` 首次访问时惰性注册 SDSU，按 1 Hz 起学习频率并参与 `can_valid`（`parser.py:157-196`）。**若雷达停发 0x343 导致 0x2FF 中断，会触发 CAN 错误** [推断] |
| acc_type | `has_SDSU` 时不读 `ACC_CONTROL.ACC_TYPE`，保持初值 1（`carstate.py:81,203`），符合 TSS2 |
| panda 安全 | `LONG_FILTER` TX 白名单（`toyota.h` `TOYOTA_COMMON_LONG_TX_MSGS_FILTER`）|
| openpilot 发 0x343 频率 | 只要 `openpilotLongitudinalControl`，每 3 帧发一次（含未接管时，`carcontroller.py:482-483`）→ comma 运行期间雷达 0x343 始终被拦 |
| Alpha Long 开关 | 仍需打开（`use_sdsu = use_sdsu and alpha_long`，`interface.py:159`）|

## 4. 风险 / 待验证

1. **车型与接插件**：12520/12519 接插件是否匹配 2025 RAV4 Hybrid 的雷达接口；ARS510 雷达是否被该固件正确处理。卖家只写 "some new 2022 RAV4"。
2. **指纹时序**：ars510 上游与 `docs/TOYOTA_RAV4_RADAR.md` 都提到雷达**启动延迟**。0x2FF 由雷达 0x343 触发，若指纹采集结束时雷达还没开始发 0x343，StarPilot 会判定"无过滤器"→ UDS 关雷达（等于没装）。需要实车看 `carParams.flags` 与日志。
3. 固件年代久、基于 white/grey panda（产品可能是克隆板），无维护；无 AEB 期间的特殊处理（与 canfilter 不同）。
4. AEB 是雷达 + 摄像头 + 制动系统的整体功能；过滤器透传报文不等于 PCS 一定无故障，需上车确认仪表无 PCS 故障灯。

## 5. 对比：Smartype/canfilter（若需要 min 融合）

- 把雷达 0x343 **镜像为 0x2AA** 发到车侧（`F_MIRROR_ACC_MSG`）→ 可读雷达 ACCEL_CMD，且 0x2AA 正是 StarPilot `RADAR_CAN_FILTER` 的识别 ID。
- 但它期望 openpilot 把 ACC_CONTROL 发在 **0x2A2**（PRE_COLLISION_2 在 0x2A3），由过滤器替换雷达 0x343；StarPilot 只发 0x343（`toyotacan.py:58`，repo 中无 0x2A2）→ 需要改 StarPilot（carcontroller + panda 安全白名单），即 README 所说"requires extensive modification on openpilot"。
- 有 AEB 激活时优先透传原车、10 次崩溃后透传等保护逻辑。
- 是否有现成售卖：未查到。

## 6. 购买建议

- 只要"StarPilot 功能 + 原车 AEB/FCW"：该产品**在代码层面可行**，先向卖家确认 §4.1，并在装车后验证 §4.2、§4.4。
- 还要"min(原车加速度, StarPilot)"：需要 canfilter 类（镜像 0x343）的硬件 + StarPilot 改动；该产品做不到。

## 7. 硬件清单（若买该产品）

comma 4 + comma 官方 Toyota harness + 本过滤器（产品图是成品线束：两端灰色接插件串接雷达、黑色盒体）。需能拆前格栅/保险杠接近雷达插头。产品页未写是否需要额外供电或转接线，问卖家。

## 8. 未决问题

- Q17：卖家确认 2025 RAV4 Hybrid（ARS510，fwdRadar `8821F0R03100`）接插件与固件兼容？
- Q18：是否接受"无 min 融合"？若必须要，是否接受改 StarPilot + 换 canfilter 类硬件？
