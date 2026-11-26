# TODO（7.0 分支，rav4h-alpha-long-radar-on-7.0）

更新：2026-11-26。行程分析笔记在 `D:\StarPilot\comma_routes\_notes\`（Windows）/ 用户 Mac 的 `~/Downloads/comma_routes/_notes/`（每趟一份，先读那里）。

| # | 任务 | 状态 | 依据 |
|---|---|---|---|
| P1 | **0003 低速放宽 LTA 角速度上限**：Python 侧 `ANGLE_LIMITS` 与 panda `toyota.h` `TOYOTA_ANGLE_STEERING_LIMITS` 必须同时生效。先确认：设备能否编译 panda 固件、bootstub 是否接受本机签名（debug/release key）、pandad 刷写流程、刷后核对固件签名、回退到原固件的方法。确认后作为单独一次更新，不和别的改动混合 | 待做（阻塞：固件路线未验证） | 0003 补丁；31 §4；_notes 0a/0b/0d（turn exceeds limit、低速转弯跟不上） |
| P2 | **热点（GPS 地理围栏）**：从已分析行程的事件点生成热点清单（`/data/rav4_hotspots.json`），车上进入热点时只做更保守的动作（提前 Experimental + 限速、禁止向标记侧变道、提前提示手动转弯、单独停车偏移）；GPS 不可靠时不动作 | 进行中（第一步：提取坐标）。已提取 3 个候选（坐标只放在本地 `_notes/hotspots/rav4_hotspots.draft.json`，不进仓库）：① 0000000f rt170 车库出口转弯后红灯（夜间模型漏看）`hidden_light` 限 10 mph；② 00000010 rt145 左转后紧接红灯（要进右转道）`hidden_light` 限 10 mph；③ 00000010 rt219–224 最右车道分叉、模型被拉进岔路 `exit_notice+no_lane_change_right`。全部 `enabled=false`，先影子日志验证进入/退出时刻 | 本次讨论；_notes 0f/10 |
| P3 | **AOL 下转向临时故障不连带退出纵向**。已查明（0d rt185.7 rlog）：185.685 `steerFaultTemporary` → 185.695 card 已按只退转向处理（lat=0, long=1）→ 185.700 另一处 AOL 检查加 `controlsMismatch`（立即退出）；panda 正常（controlsAllowed=1、无 fault）；`aolAxisState.faultReason` 为 none。原因：`starpilot/aol/temporary_steering.py` / `transport_pause.py` 的"临时 EPS 故障保留"按品牌白名单（Hyundai/Honda/Ford/Subaru/GM/Mazda/VW/...），**没有 Toyota**。要做：给 Toyota 加资格（含 panda 原生 aolSafetyWire 握手是否支持），先用设备跑 selfdrived 回放定位具体分支。本地缺 aarch64 以外的 acados 求解器，AOL 测试只能在设备上跑。注意：0006（state 0 ≤1 s）已覆盖 0d 这一次；8 rt1291（EPS 25→9）应由 update-5 提前释放 LTA 避免——先看新行程是否还出现 | 进行中（定位完成，待设备测试环路） | _notes 8、0d |
| P2a | **热点影子日志现状**（0f/10/11，详见 `_notes/hotspots_shadow_0f_10_11.md`）：三趟 `spots: 0`，`/data/rav4_hotspots.json` 未拷上车，GPS 部分没测到；导航规则正常记录。离线回放：H12（禁止左变道）两趟都会挡住正常的进左转道变道，0007 已覆盖，改为不处理；H08/H13 夜间经过无问题，继续攒数据 | 待用户拷文件 | 0f/10/11 qlog |
| P2b | **影子规则补强**（目标：车能处理的静默，处理不了的提前通知）：① 导航转弯前限速（90° 转弯进路口前约 10–12 mph，`cruise_ceiling`）；② 出弯限加速（大转角/出弯 2 s 内约 0.5 m/s² 并保持 Experimental）；③ 用 `nextManeuver` 提前看下一个导航动作（11 rt80.6 只剩 31 m 才触发）；④ 接手提醒提前约 10 s / 150 m；⑤ fork/off ramp 在 400–800 m 外提醒选车道（驾驶员打灯后自动变道）；⑥ 两车道左转前、还没出现左转道时，自动左变道改为需要推方向盘确认（模型分不清左侧是不是对向车道） | 待做（先不急） | 0f rt170/216、10 rt145/297、11 rt81.5 |
| P7 | **岔路注入意图**：导航 fork/off ramp/"靠左/靠右"时，在岔口前约 200–300 m 给模型注入 `keepLeft`/`keepRight` desire（仍由模型开车，路沿/导流区照样看）。先影子模式只记录"会注入"的时刻，确认时机后在一个已知岔路、驾驶员扶方向盘时实测；不做无灯自动变道，不改模型路径输出。当前模型对 keep 意图的反应强弱未知，可借 P8 离线回放先测 | 待做 | 10 rt297（需用户给视频时间确认位置） |
| P8 | **模型对比**：A. 用现有 Model Manager 的随机模型（randomizer + 排除列表），每次启动换一个已安装模型；补：每趟日志里记下所用模型 id，分析时按模型分组统计问题点。成本低，但每趟路况/光线不同，需要很多趟才有结论。B. 设备停车时离线回放：保留热点路段的全分辨率视频（`phone_pull` 内容选 4，或只留对应段），用 modeld 回放让各个模型跑同一段，对比红灯 shouldStop 时刻、出弯路径、岔路走向；同一输入下公平对比，也能离线测 P7 的 keep 意图。结论用来选默认模型；不在行驶中按路段切换模型（换模型要重启 modeld） | 待做（先 A 后 B） | 本次讨论 |
| P4 | T4 B 刹车上限：等出现 `vision_missed=true` 的片段再评估接入 | 只记录 | 32 §3 |
| P5 | 装雷达 CAN 过滤器时：`radar_accel`（update-8）改为也在 `RADAR_CAN_FILTER` + Alpha Long 下启用；确认 T2/健康提示的门控 | 待硬件 | 对话 2026-11-24 |
| P6 | 跟踪上游 stable：每次发布后重打本分支提交 | 持续 | T7 |

已完成（摘要）：update-1～10（7.0 移植、雷达 FCW/健康提示、LTA 越界释放、Faster Takeoffs 窗口、驾驶员发力释放 LTA、红灯轻点油门只挪动、雷达 aRel、0001/0002/0004、0006（LTA 待机容忍 1 s）、0007（路沿过近禁止变道））；工具 `phone_pull.sh`、`gh_update.sh`。
