# 06 — 目标：保留原车 TSS 安全（AEB/FCW）+ StarPilot 功能（导航、红灯停车）

状态：完成（静态阅读 + 1 次受限的网络检索）。前置：01–05。

## 0. 用户的最终需求

同时要：(a) 原车 TSS 的 AEB、FCW 等安全功能；(b) StarPilot 的导航减速、红灯停车等功能。
并问：Reddit 帖子 `r/Comma_ai/comments/1nrn73f`（2025 RAV4 Hybrid XSE on sunnypilot）里提到 sunnypilot 有"在 openpilot 纵向与 Toyota 纵向之间自动切换"的选项，原理是什么，StarPilot 能否做到。

**帖子正文未能读取**（环境拦截 reddit.com，未绕过）。因此下面对 sunnypilot 该选项的行为**不做断言**；仅分析 repo 里已有的类似机制，以及该需求在 RAV4 上的物理约束。待用户粘贴帖子文字后再补（见 §6 Q14）。

## 1. repo 里已有的"切换"机制（[代码]）

1. **启动期开关，不是运行期混合**：纵向模式在 `get_params` 时一次性决定（`CP.openpilotLongitudinalControl`、`safetyConfigs[0].safetyParam` 里的 `STOCK_LONGITUDINAL` 位，`interface.py:185-186`），panda 在 `toyota_init` 时据此选 TX 白名单（`toyota.h:481-515`）。运行中不能改。
2. **Hyundai 的"自动回退"**（`hyundai/interface.py:395-432`）：启动时尝试 UDS 禁用 SCC；成功 → 开 openpilot 纵向；失败（READY 模式、NRC 0x22、超时）→ 剥掉 LONG 安全位，退回 stock SCC + 仅横向，并置 `EcuDisableFailed`（`card.py:461` 使用）。这是"**按启动时 UDS 成功与否自动选择**"，不是行驶中根据场景切换。
3. **`DisableOpenpilotLongitudinal` / `disable_openpilot_long`**（`params_keys.h:327`，`car_helpers.py:400`）：只在 `not CP.alphaLongitudinalAvailable` 时生效。RAV4（`RADAR_ACC_CAR`，`alphaLongitudinalAvailable=True`）**不适用**；对 RAV4 来说"回到 stock 纵向"=关闭 Alpha Long 开关并重启。
4. **Toyota 没有任何自动切换逻辑**（grep 无结果）。

## 2. 为什么 RAV4 上"AEB + openpilot 纵向"不能靠软件切换同时得到

- AEB/FCW 是**雷达 ECU 的输出**（0x283 `PRE_COLLISION`、0x344 `PRE_COLLISION_2`、0x411 `PCS_HUD`），Alpha Long 的前提是雷达 TX 被 UDS 关闭（01 §3）。
- 上游 ars510-radar 的说明（二手转述，见 05 更正）：禁用雷达会带走 AEB，仪表出现 "Pre-Collision System Malfunction"/PCS OFF；**只有在雷达线路上加 CAN filter**，让过滤器只丢弃雷达的 0x343、其余（含 PCS/AEB）放行，才能"openpilot 纵向 + 雷达 AEB 保留"。
- 即：**同一时刻**既让 openpilot 发 0x343 又保留 AEB，在 RAV4 上要靠**硬件隔离**，软件切换做不到。

## 3. 三条路线对比

| 路线 | 导航/红灯停车等 StarPilot 纵向功能 | AEB/FCW | 硬件 | 备注 |
|------|------------------------------------|---------|------|------|
| R1. 关闭 Alpha Long（stock ACC + openpilot 横向） | ✗ 基本不可用：这些功能最终要产生减速/停车指令，stock ACC 只接受设定速度和取消 | ✓ 完整保留 | 无 | 最安全、最简单；StarPilot 只剩横向类功能 |
| R2. Alpha Long，无过滤器 | ✓ | ✗ 丢失（PCS 故障灯）；可加"雷达目标驱动的只减不增 cap"（04 方案 C，数据来自 bus 1，见 05 更正）作为**部分**补偿，但不是 AEB | 无 | 软件补偿不能替代 AEB |
| R3. Alpha Long + radar CAN filter | ✓ | ✓ 雷达 AEB 保留（据上游文档；FCW 的 0x411 是否同样放行 [待验证]） | 需要过滤器 | 唯一同时满足两个需求的方案；还能读到雷达的 `ACCEL_CMD` 做 min 融合（05 §3） |
| R4. 运行期在 R1/R2 之间切换（如"有导航/红灯需求时才关雷达"） | 部分 | 部分时段 ✗ | 无 | 需要：运行期 UDS 重新启停雷达、运行期切换 panda 安全模式（`toyota_init` 只在设置安全模式时执行）、CarParams 改变；切换瞬间的 PCS 故障灯/ACC 取消风险高。**不建议**，且 repo 内无现成基础 |

→ 要同时满足需求，**R3 是唯一合理的路线**；R1 是无硬件时的安全默认。

## 4. 对 StarPilot 的含义

- "自动切换"在 StarPilot 里**可以做启动期版本**（类似 Hyundai：检测到过滤器/UDS 禁用成功 → 开 openpilot 纵向；否则退回 stock），RAV4 上现成的信号是：`use_sdsu`（指纹 0x2AA/0x2FF）决定 `DISABLE_RADAR` 是否置位（`interface.py:154-159`）。
- 运行期混合（行驶中按场景切）当前架构做不到，也不安全。
- 无论哪条路线，红灯停车/导航都是 planner 层的功能，只依赖 `openpilotLongitudinalControl=True`；它们不依赖雷达，所以 R2/R3 都能用。

## 5. 对前面分析的更新

- 04 的方案排序应改为：**先选硬件路线（R1/R2/R3）**，再选软件补偿：
  - R3：实现 05 §3 的 min 融合，并确认 AEB/FCW 保留；
  - R2：实现 04 方案 C（雷达目标 → 只减不增 cap）作为尽力而为的补偿，并在 UI/文档中**明确声明无 AEB**；
  - R1：无需开发。

## 6. 未决问题

- **Q14**：请粘贴 Reddit 帖子正文（尤其是 sunnypilot 自动切换选项的名称、触发条件、是否保留 AEB）。
- **Q15**：过滤器是否放行雷达的 0x411（FCW 显示）与 0x283/0x344？是否会让 PCS 故障灯消失？（取决于具体产品，待你找到型号后查；上游 docs/13、docs/15 有说明，二手转述未核实。）
- **Q16**：你可接受的取舍：R1（安全，功能少）/ R2（功能全，无 AEB）/ R3（需买硬件）？
