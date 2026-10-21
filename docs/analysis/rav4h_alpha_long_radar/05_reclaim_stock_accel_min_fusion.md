# 05 — 能否"回收"原车雷达 ACCEL_CMD，并与视觉取 min？

状态：完成（静态阅读；用户尚未购买 comma，无实车数据，结论只靠代码）。前置：01–04。
触发：用户提出"Alpha Long 是代码层面的事，为什么不能回收？想取 雷达 ACC_CONTROL 加速度 与 视觉加速度 的较小值"。

## 1. 结论

1. **意图完全合理**：`a_cmd = min(a_openpilot, a_stock_radar)` 是单调安全的（只会更保守）；红绿灯等"视觉想刹"的场景由 openpilot 一侧保留，天然被 min 覆盖。
2. **但"纯软件回收"在 RAV4 上做不到**——不是 Alpha Long 的代码选择，而是物理拓扑：
   - `ACCEL_CMD` 是**雷达 ECU 内部的 ACC 控制器**算出来、由雷达自己发到车内 bus 0 的 `ACC_CONTROL`(0x343)。openpilot 不参与这个计算。
   - Alpha Long 要自己发 0x343。同一条车内总线上同一个 ID 有两个发送者 → PCM 会交替收到两份指令，不可控。所以必须让雷达闭嘴：`DISABLE_RADAR` → UDS `COMMUNICATION_CONTROL ENABLE_RX_DISABLE_TX`（`interface.py:229-234`）。雷达一闭嘴，**ACC 控制器不再输出，没有任何东西可以"回收"**。
   - 对比摄像头 ACC 车型：comma 线束串在摄像头与车之间，panda 的 fwd hook 可以挡掉摄像头的 0x343 并换成自己的。panda 转发只在 bus 0↔2（`toyota.h:576-583`，`toyota_fwd_hook` 只对 bus 2 有逻辑）。RAV4 雷达在车内侧而不是线束后面 → 挡不住。[推断：repo 内无线束拓扑文档，但与上述固件行为一致]
3. **真正能"回收"的硬件前提：雷达经过一个 CAN 过滤/拦截设备再接车**（repo 里叫 radar CAN filter / sDSU 类，`interface.py:73-76, 161-166`、`carstate.py:91-92`、`toyota.h` 的 `LONG_FILTER` TX 集合）。此时：
   - `use_sdsu=True`，`DISABLE_RADAR` **不置位**（`interface.py:154-159`），雷达保持存活，stock 的 `ACC_CONTROL`/AEB 不必被 UDS 关掉，由过滤器隔离，openpilot 的 0x343 不与之冲突；
   - 雷达的 0x343 可经过滤器出现在 panda 的另一条总线，**代码上读到 stock `ACCEL_CMD` 是可行的**，再在 `carcontroller.py:575` 附近 `min()` 即可。
   - 这需要**额外硬件**，纯 comma 线束没有。你还没买 comma，这点要在购买前决定。

## 2. 两种硬件情形下的可行性

| 情形 | 雷达状态 | stock ACCEL_CMD 可读 | 与视觉 min | 备注 |
|------|----------|----------------------|------------|------|
| A. 仅 comma 线束（无过滤器） | 被 UDS 静默 | ✗ 不存在 | ✗ | 只能视觉；ARS510 解析读 bus 1 的数据在此情形下很可能也没有来源 [推断：解析器假定雷达总线在 panda bus 1，没有过滤硬件时雷达不在 panda 上] |
| B. comma + radar CAN filter | 存活，被隔离 | ✓（读过滤器侧总线上的 0x343） | ✓ 代码可做 | stock AEB 是否保留取决于过滤器放行策略（`docs/TOYOTA_RAV4_RADAR.md:22-25` 说"Filter-equipped … retain their existing configurations"）|

→ 修正 01/04 文档中的表述："Q1：bus 1 是否有数据"在情形 A 下大概率为"没有"，因为雷达根本不在 panda 总线上。

## 3. 情形 B 的实现草图（代码层面）

1. DBC：需要能解析被过滤器隔离出来的那条总线上的 `ACC_CONTROL.ACCEL_CMD`（现有 `toyota_new_mc_pt` 的 `ACC_CONTROL` 定义可复用，但 Bus 编号要按过滤器接线确认；`CarState` 需新增字段，例如 `starpilotCarState.stockAccelCmd`，需改 cereal custom schema）。
2. `carcontroller.py:575` 之前加：
   ```
   stock = CS.stock_accel_cmd            # 过滤器侧雷达 ACC 的指令
   if stock_valid: pcm_accel_cmd = min(pcm_accel_cmd, stock)
   ```
   并在放松方向不起作用（min 只降不升）。
3. **有效性判定是关键**：雷达 ACC 只有在它"认为自己激活"时才计算跟车加速度。Alpha Long 下 openpilot 通过 PCM 激活巡航，雷达是否同时进入 ACC 计算、输出有意义的 `ACCEL_CMD`，**代码里没有答案** [待验证，需实车]。若雷达在非激活时输出 0 或固定值，`min` 会被拉成 0（等于禁止加速）——必须只在 `ACC_TYPE`/状态位表明有效且数值为负时才参与（`min(a, stock) if stock < 0`）。
4. 设定速度差异：雷达 ACC 会朝它自己的设定速度调节；若其设定速度低于 openpilot 的，会产生持续的"要求减速"，min 会把车压低。需要把设定速度同步或只在"有前车"(`PRE_COLLISION`/跟车状态位)时采用 stock 值。
5. 风险：stock 加速度里包含 Toyota 的舒适/急刹策略；与 openpilot 的 `permit_braking`/PID 叠加后可能产生点刹，需要实车调参。

## 4. 情形 A（无过滤器）下，能用软件做到的最接近方案

- 不存在 stock 加速度，也大概率没有雷达数据；"雷达那份保险"在这个硬件组合里**不存在**。唯一的"min"是视觉路径内部各 cap 之间的 min（已有）。
- 因此若你要"雷达优先减速"这条保险，**要么买 radar CAN filter（情形 B），要么接受只有视觉**，或接受不开 Alpha Long（stock ACC，只做横向）。

## 5. 对 04 文档方案的影响

- 方案 C（"只减不增的雷达制动 cap"）在情形 B 下可以直接用 stock `ACCEL_CMD` 实现，比自己用 ARS510 目标数据重建更简单也更接近你想要的语义；在情形 A 下不可行。
- 推荐顺序更新：**先决定硬件（是否买 radar CAN filter）→ 若买，做 §3 的 min 方案（先"只记录 stock 值与 openpilot 值的差"，验证雷达 ACCEL_CMD 的有效性）→ 再启用 min。**
- 无论哪种情形，都不要声称恢复 AEB。

## 6. 未决问题

- **Q11**：你打算买 radar CAN filter 吗？是哪个型号/卖家？（决定雷达接到 panda 的哪个 bus、过滤器放行哪些 ID、AEB 是否保留，需要该产品的接线文档才能写 DBC/bus 映射。）
- **Q12**：[待验证，需实车] 过滤器方案下，Alpha Long 激活时雷达是否输出有效的 `ACCEL_CMD`、其设定速度如何与 openpilot 同步。
- **Q13**：`docs/TOYOTA_RAV4_RADAR.md` 的 ARS510 解析读 bus 1 的前提硬件到底是什么？原作者 repo（ds-sebastian/ars510-radar）可能有接线说明——我这边没有网络读取，建议你或后续 agent 去核对。
