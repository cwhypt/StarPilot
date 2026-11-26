# TODO（7.0 分支，rav4h-alpha-long-radar-on-7.0）

更新：2026-11-25。行程分析笔记在 `D:\StarPilot\comma_routes\_notes\`（Windows）/ 用户 Mac 的 `~/Downloads/comma_routes/_notes/`（每趟一份，先读那里）。

| # | 任务 | 状态 | 依据 |
|---|---|---|---|
| P1 | **0003 低速放宽 LTA 角速度上限**：Python 侧 `ANGLE_LIMITS` 与 panda `toyota.h` `TOYOTA_ANGLE_STEERING_LIMITS` 必须同时生效。先确认：设备能否编译 panda 固件、bootstub 是否接受本机签名（debug/release key）、pandad 刷写流程、刷后核对固件签名、回退到原固件的方法。确认后作为单独一次更新，不和别的改动混合 | 待做（阻塞：固件路线未验证） | 0003 补丁；31 §4；_notes 0a/0b/0d（turn exceeds limit、低速转弯跟不上） |
| P2 | **热点（GPS 地理围栏）**：从已分析行程的事件点生成热点清单（`/data/rav4_hotspots.json`），车上进入热点时只做更保守的动作（提前 Experimental + 限速、禁止向标记侧变道、提前提示手动转弯、单独停车偏移）；GPS 不可靠时不动作 | 进行中（第一步：提取坐标）。已提取 3 个候选（坐标只放在本地 `_notes/hotspots/rav4_hotspots.draft.json`，不进仓库）：① 0000000f rt170 车库出口转弯后红灯（夜间模型漏看）`hidden_light` 限 10 mph；② 00000010 rt145 左转后紧接红灯（要进右转道）`hidden_light` 限 10 mph；③ 00000010 rt219–224 最右车道分叉、模型被拉进岔路 `exit_notice+no_lane_change_right`。全部 `enabled=false`，先影子日志验证进入/退出时刻 | 本次讨论；_notes 0f/10 |
| P3 | **AOL 下转向临时故障不连带退出纵向**。已查明（0d rt185.7 rlog）：185.685 `steerFaultTemporary` → 185.695 card 已按只退转向处理（lat=0, long=1）→ 185.700 另一处 AOL 检查加 `controlsMismatch`（立即退出）；panda 正常（controlsAllowed=1、无 fault）；`aolAxisState.faultReason` 为 none。原因：`starpilot/aol/temporary_steering.py` / `transport_pause.py` 的"临时 EPS 故障保留"按品牌白名单（Hyundai/Honda/Ford/Subaru/GM/Mazda/VW/...），**没有 Toyota**。要做：给 Toyota 加资格（含 panda 原生 aolSafetyWire 握手是否支持），先用设备跑 selfdrived 回放定位具体分支。本地缺 aarch64 以外的 acados 求解器，AOL 测试只能在设备上跑。注意：0006（state 0 ≤1 s）已覆盖 0d 这一次；8 rt1291（EPS 25→9）应由 update-5 提前释放 LTA 避免——先看新行程是否还出现 | 进行中（定位完成，待设备测试环路） | _notes 8、0d |
| P4 | T4 B 刹车上限：等出现 `vision_missed=true` 的片段再评估接入 | 只记录 | 32 §3 |
| P5 | 装雷达 CAN 过滤器时：`radar_accel`（update-8）改为也在 `RADAR_CAN_FILTER` + Alpha Long 下启用；确认 T2/健康提示的门控 | 待硬件 | 对话 2026-11-24 |
| P6 | 跟踪上游 stable：每次发布后重打本分支提交 | 持续 | T7 |

已完成（摘要）：update-1～10（7.0 移植、雷达 FCW/健康提示、LTA 越界释放、Faster Takeoffs 窗口、驾驶员发力释放 LTA、红灯轻点油门只挪动、雷达 aRel、0001/0002/0004、0006（LTA 待机容忍 1 s）、0007（路沿过近禁止变道））；工具 `phone_pull.sh`、`gh_update.sh`。
