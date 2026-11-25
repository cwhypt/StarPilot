# TODO（7.0 分支，rav4h-alpha-long-radar-on-7.0）

更新：2026-11-25。行程分析笔记在用户 Mac 的 `~/Downloads/comma_routes/_notes/`（每趟一份，先读那里）。

| # | 任务 | 状态 | 依据 |
|---|---|---|---|
| P1 | **0003 低速放宽 LTA 角速度上限**：Python 侧 `ANGLE_LIMITS` 与 panda `toyota.h` `TOYOTA_ANGLE_STEERING_LIMITS` 必须同时生效。先确认：设备能否编译 panda 固件、bootstub 是否接受本机签名（debug/release key）、pandad 刷写流程、刷后核对固件签名、回退到原固件的方法。确认后作为单独一次更新，不和别的改动混合 | 待做（阻塞：固件路线未验证） | 0003 补丁；31 §4；_notes 0a/0b/0d（turn exceeds limit、低速转弯跟不上） |
| P2 | **热点（GPS 地理围栏）**：从已分析行程的事件点生成热点清单（`/data/rav4_hotspots.json`），车上进入热点时只做更保守的动作（提前 Experimental + 限速、禁止向标记侧变道、提前提示手动转弯、单独停车偏移）；GPS 不可靠时不动作 | 进行中（第一步：提取坐标） | 本次讨论 |
| P3 | **AOL 下转向临时故障不连带退出纵向**：现在 `steerFaultTemporary` 经 AOL 轴状态升级为 critical → `controlsMismatch`（TAKE CONTROL）。改为只退出转向 + 响亮提示，纵向保持 | 待定（用户未决定） | _notes 8 rt1291.6（EPS 25→9，2 s）、0d rt185.7 |
| P4 | T4 B 刹车上限：等出现 `vision_missed=true` 的片段再评估接入 | 只记录 | 32 §3 |
| P5 | 装雷达 CAN 过滤器时：`radar_accel`（update-8）改为也在 `RADAR_CAN_FILTER` + Alpha Long 下启用；确认 T2/健康提示的门控 | 待硬件 | 对话 2026-11-24 |
| P6 | 跟踪上游 stable：每次发布后重打本分支提交 | 持续 | T7 |

已完成（摘要）：update-1～10（7.0 移植、雷达 FCW/健康提示、LTA 越界释放、Faster Takeoffs 窗口、驾驶员发力释放 LTA、红灯轻点油门只挪动、雷达 aRel、0001/0002/0004、0006（LTA 待机容忍 1 s）、0007（路沿过近禁止变道））；工具 `phone_pull.sh`、`gh_update.sh`。
