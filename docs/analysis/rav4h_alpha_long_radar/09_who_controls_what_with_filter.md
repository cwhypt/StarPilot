# 09 — 装过滤器后：雷达在工作，谁在控制加减速？

状态：完成（代码 + DBC）。前置：08。

## 1. 用户的疑问

"雷达保持工作、AEB/FCW 都在，那前面有车时雷达不也会减速吗？那 Alpha Long 怎么控制？"

## 2. 关键：雷达 ECU 输出的是几条**不同**的报文，走不同的执行通道

| 报文 | ID | 内容 | 接收方 / 作用 | 过滤器（smart_dsu）处理 |
|------|----|------|---------------|------------------------|
| `ACC_CONTROL` | 0x343 (835) | `ACCEL_CMD`（m/s²）、`PERMIT_BRAKING`、`CANCEL_REQ` 等（`toyota_nodsu_pt_generated.dbc:207-215`） | 动力/混动控制（PCM/HCU），**日常跟车的加减速**，即 ACC | openpilot 在发时**拦掉雷达的**，只让 openpilot 的那份到车 |
| `PRE_COLLISION` | 0x283 (643) | `FORCE`(N)、`PRECOLLISION_ACTIVE`、`BRAKE_STATUS`（dbc:624-632） | 制动系统：**紧急制动请求（AEB）** | 透传 |
| `PRE_COLLISION_2` | 0x344 (836) | `DSS1GDRV`(m/s²)、`PCSWAR`/`PCSALM`/`PCSABK` 等（dbc:651-659） | PCS 预警 / 预制动相关 | 透传 |
| `PCS_HUD` | 0x411 (1041) | `FCW` 位、PCS 指示 | 仪表：**FCW 警告**（只提醒） | 透传 |

（更正：03/06/07 文档曾把 0x344 写成 `PRE_COLLISION`，实际 `PRE_COLLISION` 是 0x283，已修正 06/07。）

## 3. 所以分工是

- **日常"前面有车减速"= ACC = 0x343**。雷达仍会计算并发出它的 `ACCEL_CMD`，但过滤器把它拦在雷达那一侧，车只收到 StarPilot 发的 0x343（`carcontroller.py:583`）。→ 跟车、导航减速、红灯停车，全由 StarPilot（Alpha Long）控制。
- **"快撞了"= AEB = 0x283/0x344**，独立于 ACC。雷达判断碰撞迫近时直接向制动系统请求制动力，不经过 0x343，所以不受 StarPilot 的 0x343 影响。→ 这是原车安全兜底。
- **FCW = 0x411**，只是仪表/声音警告。

正常驾驶时 AEB 不会动作；只有碰撞迫近（通常是 StarPilot 没刹够、或驾驶员接管不及时）才会介入。这正是用户要的"原车兜底 + StarPilot 功能"。

## 4. StarPilot 侧如何感知 AEB

- 未置 `DISABLE_RADAR` 时，`carstate.py:129`：`stockAeb = PRECOLLISION_ACTIVE and FORCE < -1e-5`（读 0x283）；`carstate.py:200`：`stockFcw` 读 0x411。
- `selfdrived/events.py:605-611`：`stockAeb` → 最高优先级告警 "BRAKE! Stock AEB: Risk of Collision"。

## 5. 仍然存在的局限 / 待验证

- 原车 ACC 的"温和跟车减速"建议（雷达的 `ACCEL_CMD`）被丢弃，不参与控制——这就是 08 里说的"做不到 min 融合"。AEB 只在紧急时才出手，**中间地带（雷达觉得该稍微减速、StarPilot 视觉没看到车）没有兜底**。
- AEB 动作与 StarPilot 同时发出的 0x343（可能仍在请求加速）之间如何仲裁，由车的制动/动力系统决定（一般制动优先）[推断，未验证]。
- StarPilot 收到 `stockAeb` 后是否主动退出纵向控制，需要再查 `selfdrived` 的状态机（本文未展开）。
