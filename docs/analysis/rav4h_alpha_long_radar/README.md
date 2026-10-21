# RAV4 Hybrid 2025 + Alpha Long：原车雷达建议被静默 — 分析索引

> 给接手的 agent：先读本页，再按编号读各步文档。每一步结束都会更新本页的"进度表"和"未决问题"。

## 问题陈述（用户原话意译）

车：Toyota RAV4 Hybrid 2025（platform `TOYOTA_RAV4_TSS2_2023`，fingerprint 覆盖 2023-25），运行在 comma "gamma4" 硬件上的 StarPilot。
开启 Alpha Long（UI 里 `ExperimentalLongitudinalEnabled`，纵向由 openpilot 控制）后：

- 原车雷达在"前方有车"时算出的加速度建议（stock `ACC_CONTROL.ACCEL_CMD`）被静默，不再被使用；
- 前车信息实际上只来自视觉模型（gamma 上的 modelV2 leads），"不够保险"；
- 目标：搞清楚现状的完整信号路径，找出雷达信息丢失的位置，评估可行的补救方案（把雷达作为安全后盾 / 与视觉融合）。

## 进度表

| # | 文档 | 内容 | 状态 |
|---|------|------|------|
| 1 | [01_architecture_and_signal_path.md](01_architecture_and_signal_path.md) | repo 结构、alpha long 开启后的开关链、雷达/视觉信号路径 | 完成（纯代码阅读） |
| 2 | [02_radard_fusion_gating.md](02_radard_fusion_gating.md) | radard 融合逻辑：雷达轨迹在何种条件下被采用 / 被丢弃 | 完成（纯代码阅读） |
| 3 | [03_planner_mpc_and_safety_nets.md](03_planner_mpc_and_safety_nets.md) | planner / MPC 如何消费 leadOne，剩余安全网（FCW/AEB） | 完成（纯代码阅读） |
| 4 | [04_options_and_recommendation.md](04_options_and_recommendation.md) | 方案对比与建议、测试计划、待用户回答的问题 | 完成（未改代码） |
| 5 | [05_reclaim_stock_accel_min_fusion.md](05_reclaim_stock_accel_min_fusion.md) | 能否回收原车 ACCEL_CMD 并与视觉取 min；硬件前提；修正 01/04 的 Q1 | 完成（纯代码阅读，用户无实车数据） |
| 6 | [06_stock_safety_plus_starpilot_features.md](06_stock_safety_plus_starpilot_features.md) | 目标"原车 AEB/FCW + StarPilot 功能"；repo 内的切换机制；R1–R4 路线对比 | 完成（Reddit 帖子未能读取） |
| 7 | [07_radar_filter_hardware_checklist.md](07_radar_filter_hardware_checklist.md) | R3 路线的过滤器选型清单（bus 0 上须有 0x2AA 或 0x2FF 等，见 08 更正）| 完成（产品页无法访问，未核实） |
| 8 | [08_oneclone_radar_acc_filter_eval.md](08_oneclone_radar_acc_filter_eval.md) | OneClone 过滤器（smart_dsu 固件）评估：可保留 AEB/FCW，但不能读雷达 ACCEL_CMD | 完成（固件为二手转述） |
| 9 | [09_who_controls_what_with_filter.md](09_who_controls_what_with_filter.md) | 装过滤器后的分工：0x343 日常加减速归 StarPilot，0x283/0x344 AEB、0x411 FCW 归原车 | 完成 |
| 10 | [10_why_no_min_and_how_to_close_gap.md](10_why_no_min_and_how_to_close_gap.md) | 为什么做不到 min、中间地带缺口、三种补法（改固件镜像 / canfilter / 软件 cap） | 完成 |
| 11 | [11_ars510_vs_filter.md](11_ars510_vs_filter.md) | ars510 解析器（读数据）vs 过滤器（保 AEB）：互补而非替代 | 完成 |

> 05 对 01/04 有修正，并含一条**后续更正**：无过滤器时 stock ACCEL_CMD 取不到，但据上游 ars510-radar，雷达在 bus 1 上的目标数据在 UDS 禁用后仍存在（纯软件即可读），所以"自己重建的只减不增制动 cap"（04 方案 C）无需额外硬件；直接 min(stock ACCEL_CMD) 才需要过滤器。

## 一句话结论

Alpha Long 通过 UDS 关闭雷达 ECU，stock `ACCEL_CMD`/AEB/FCW 随之消失且无法恢复；前车感知默认 100% 来自视觉，
即使开 `ToyotaRadarMode`，radard 也只让雷达"校正"视觉已确认的 lead。推荐：先验证 bus 1 数据（Q1）→ 开 Mode 2 → 仅记录的一致性监控 →
再做"只减不增"的雷达制动 cap。详见 04。

## 证据等级约定

- **[代码]**：直接读源码得出，文中给 `文件:行号`。
- **[文档]**：repo 内文档的陈述（如 `docs/TOYOTA_RAV4_RADAR.md`）。
- **[推断]**：由代码/协议合理推出但没有实车或 rlog 验证。
- **[待验证]**：需要实车 / rlog 才能确认，列在各文档末尾的"未决问题"。

## 未决问题（跨文档汇总）

需要用户/实车回答的问题汇总在 [04 §5](04_options_and_recommendation.md)（Q1–Q5、Q10）；
实现阶段的技术问题：Q6、Q7（02 §7）、Q8、Q9（03 §7）。
