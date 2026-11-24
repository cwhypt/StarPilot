# 31 — 设置快照与第一次行车（2026-11-23）

设备：comma（mici），StarPilot 7.0 + 本分支，RAV4 Hybrid 2025（`TOYOTA_RAV4_TSS2_2023`，LTA 角度转向，ARS510 雷达固件 `8821F0R03100`）。
来源：`dump_settings.py`（只读，Galaxy 同一套 `SettingsGateway`）与路线 `00000005--44e029747e`（13.7 min，夜间）。

## 1. 用户改过的设置

| 页面 | 设置 | 当前 | 默认 |
|---|---|---|---|
| Vehicle | Alpha Longitudinal | On | Off |
| Vehicle | Radar with Alpha Long (experimental)（`ToyotaRadarMode`） | On | Off |
| Long Planner | Approaching lead buffer | On | Off |
| Long Planner | Faster Takeoffs | On | Off |
| Long Planner | Enhanced Stops | On（之前曾是 Off） | On |
| Lane Centering | Lane offset | −0.1 m | 0.0 |
| Lane Changes | Automatic Lane Changes（1.0 s 延迟，每次打灯一次） | On | Off |
| Steering | Automatic Turn Initiation | On | Off |
| Sounds | Green Light / Lead Departing / Loud Blindspot | On | Off |
| PiP | 盲区摄像头（打灯、盲区警告时显示） | On | Off |

其余为默认：StarPilot 纵向规划器 On、VOACC Off、Driving Mode Automatic（停车灯/前车/慢车/停止前车条件 On）、Curve Speed Controller Off、SLC Off（显示限速牌 On）、AOL On（Pause steering below 0、Pause only while signaling Off）、Standard 跟车 1.45 s / 1.2 s、Always Allow Uploads Off。

注：`profiles/developer`（Actuator Delay 0.05 s、Max/Start Accel 3.5、Stop Accel −2.0、Stopping Rate 0.30、Start/Stop Speed 0.30）和 `torque/developer`（Actuator delay 0.38 s）是**自动默认值**，不是用户设置；对比脚本把"Automatic"和数值不同误判为已更改。

## 2. 第一次行车的发现

- 雷达：Alpha Long 下雷达私有总线全程在发（0x100 10 Hz，0x235/0x237/0x239 50 Hz，0x195/0x196 16.7 Hz，0x680 2 Hz）；停车时前车 171 点/10 s，与原车 ACC 时相同；纵向激活且有前车时 92 % 为雷达匹配前车。
- 纵向：夜间接近静止车流，视觉约 100 m 识别、雷达 76 m 匹配，最大减速约 −1.5 m/s²，停在前车后 5.8 m；无 FCW、无误刹。绿灯起步一次偏慢（前车动后约 2–3 s 驾驶员踩油门）。
- 转向：
  - EPS 的 LTA 角度上限 ±94.95°（7.0 `ANGLE_LIMITS`），路口/停车场转弯需 150–350°。规划器 35 s 想超过上限，实际发出的角度被限制；方向盘 > 90° 的 46 s 中 94 % 是驾驶员在打。
  - Automatic Turn Initiation（`signal_turn.py`：横向激活、0.1 < v < 14 m/s、aEgo ≤ −0.5、模型预测转弯、打灯）会让模型开始转弯，但受上限所限转不到底。
  - 大角度转弯后 EPS 报 LTA 暂不可用 12 次（静默）。
  - 低速（< 7 m/s，小区/停车场）车道线置信度约 0.25，路上约 0.76；低速时驾驶员 26 % 的时间在扶方向盘，路上 7 %。
  - 变道需 ≥ 20 mph（`turn_signal_guidance` 下限，界面上的 5 mph 不起作用）；一次 20 mph 以上的打灯触发了变道。
- 日志问题（已修，update-2）：`REQUIRED_DECEL` 空闲编码是 −1（−0.095 m/s²）；启动时雷达总线静默 > 15 s。

## 3. 第二次行车（`00000006--cc77e8db39`，28 min，行驶约 3 min，夜间小区/车库）

- **转弯后猛加速**：纵向激活、前方无车时，规划器 aTarget 达 2.8–3.0 m/s²，实测 2.85 m/s²，2–3 s 内 6 → 17 mph（1123 s 刚转完弯，前方停车标志；1213 s；1263 s 起步）。原因是 **Faster Takeoffs**：车速 < 5 m/s（11 mph）时把加速上限从正常的 1.6 m/s²（`A_CRUISE_MAX_VALS`）提到 `TAKEOFF_ACCEL_MAX`，到 10 m/s 才过渡回正常；低速转弯后的"再加速"也被当成起步。设定速度 28–29 km/h（约 17–18 mph）。
- **"Steering Assist Temporarily Unavailable" 11 次**：与第一次相同，均在方向盘 140–500° 的转弯后回正时（EPS LTA_STATE 5 → 9）。已修（`0f10880`，超出 LTA 角度范围时释放 LTA，回到范围内并稳定后接回）。
- **"TAKE CONTROL IMMEDIATELY"（1268 s）**：`controlsMismatch` 与 LTA 临时故障在同一帧出现，发生在起步加速中、驾驶员打方向转弯时，纵向和横向一起退出。第一次行车 294–299 s 的同一告警也是相同组合。推测为同一根因（LTA 故障引发 AOL 轴的来源失效），`0f10880` 应能避免，待实车确认。
- 书签（1032 s）：车库内停车状态，前方 10.8 m 停着的车被当作前车，规划器给 +0.21 m/s² 但车未移动，随后切到实验模式。无风险。
