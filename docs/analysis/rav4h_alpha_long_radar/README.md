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
| 2 | 待写 | radard 融合逻辑：雷达轨迹在何种条件下被采用 / 被丢弃 | 未开始 |
| 3 | 待写 | planner / MPC 如何消费 leadOne，是否有雷达专属的安全兜底 | 未开始 |
| 4 | 待写 | 方案对比与建议（含改动点、风险、测试） | 未开始 |

## 证据等级约定

- **[代码]**：直接读源码得出，文中给 `文件:行号`。
- **[文档]**：repo 内文档的陈述（如 `docs/TOYOTA_RAV4_RADAR.md`）。
- **[推断]**：由代码/协议合理推出但没有实车或 rlog 验证。
- **[待验证]**：需要实车 / rlog 才能确认，列在各文档末尾的"未决问题"。

## 未决问题（跨文档汇总）

见各文档末尾；汇总见 01 文档第 7 节。
