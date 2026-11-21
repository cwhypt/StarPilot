# 28 — 官方 StarPilot 7.0（`stable`，2026-11-21）与当前分支对比

状态：**只读代码对比**，没有在设备上测性能（容器里的 `.so` 是 aarch64，跑不了 modeld/controlsd）。对比对象：`upstream/stable` 008b0cbcf（RELEASES.md 标题 "StarPilot 7.0 — Domathon development"）vs 本分支（基于旧 `stable` 8589b73f9 + #230 + 本系列）。

## 1. 先说结论

- 官方 `stable` 在 11-18 之后被**强推**成 7.0：与本分支**没有共同历史**（`git merge-base` 为空），目录也重排了（`selfdrive/`、`starpilot/` 等移到 `openpilot/` 下）。本分支的改动不能 merge，只能手工移植。
- 7.0 自己的 RELEASES.md 写明：openpilot rebase 进行中，**还不是完成的 StarPilot 版本**。
- 对你的方案（Alpha Long、无过滤器、用 ARS510 雷达）**最关键的差异**：7.0 在雷达被关闭时**主动不用** ARS510（见 §3）。

## 2. 7.0 新增/变化（与"性能"相关的）

| 方面 | 当前分支 | 7.0 | 来源 |
|---|---|---|---|
| openpilot 基线 | `COMMA_VERSION` 0.11.2（RELEASES 顶部到 0.10.3） | 0.11.3 | `openpilot/common/version.h`、RELEASES.md |
| 默认驾驶模型 | vision + temporal policy（`driving_tinygrad` 3 块） | **Cheddar (v16)**，另有 big 模型 warp（外接 GPU 用） | `openpilot/selfdrive/modeld/models/README.md` |
| 0.11.3 Autospeed | 无 | 上游说明：红绿灯停走、弯道减速、按车流调速"总是开启"，巡航速度当上限 | RELEASES.md 0.11.3（日期仍是 2026-XX-XX） |
| ARS510 雷达 | `ToyotaRadarMode` 0/1/2 手动选择；Mode 1 ACC 目标，Mode 2 加摘要车 | **自动启用**（RAV4 TSS2 2022/2023，无开关），只用 ACC 目标（`ars510_acc_target.py`） | `ars510_preferences.py` |
| ARS510 全对象表 | 无 | 新 `ars510_radar.py`：重组 0x80 的 106 帧记录，20 个对象，按 track 的卡尔曼速度滤波、与 ACC 目标融合、按横摆预测路径门控（2.5 m，静止物 1.5 m），60 m 外只发 ACC 目标 | `ars510_radar.py` |
| 全对象表是否启用 | — | **未启用**：`configure_radar()` 每次都清掉 `ARS510_FUSED`，代码里没有地方再置位 | `ars510_preferences.py:22` |
| 2025 RAV4 雷达 CAN 过滤器 | — | 支持（4 字节 0x2FF 广播识别），过滤器 + openpilot 纵向时**保留雷达** | `radar_filter.py` |
| 其余 | — | 大量新功能（Galaxy、StarPort、FLM 横向自整定、手机 App 等），测试文件 ~1584 个 vs 本分支 starpilot/ 下 101 个 | README.md |

转向：两边对 2023–25 RAV4 都是角度控制（LTA），无差异。

## 3. 对本系列方案的影响

`ars510_preferences.configure_radar()`（7.0）：

```python
if cp.flags & ToyotaFlags.DISABLE_RADAR:
  cp.radarUnavailable = True
  return False
cp.flags |= int(ToyotaFlags.ARS510_RADAR)
```

- 7.0 中 **Alpha Long + 无过滤器 = 完全没有雷达**（纯视觉），没有 `ARS510_RADAR`。本系列的 T2（0x100 PCS 报警）、B、C 都建立在"雷达关了但私有总线还在发"这一前提上，在 7.0 的设计里这个组合被排除。原因在压缩后的历史里看不到；可能是官方认为 UDS 关闭后私有总线不可靠，或只是保守。**这正是 V2（Alpha Long 下 0x100/0x196/0x237 是否还在发）要回答的问题。**
- 7.0 给出的另一条路：**装雷达 CAN 过滤器 + openpilot 纵向**。这时原车雷达不被 UDS 关闭，ARS510 ACC 目标照常用。原车 AEB 是否保留取决于过滤器实现，未核实。
- 7.0 的 `ars510_radar.py`（若以后启用）解决的正是 26 §1 里 B 覆盖不到的部分：多目标、邻道切入（路径门控 15 m 内不门控，"cut-ins stay"）、静止物处理。

## 4. 建议

1. 现阶段继续用本分支收集数据（27 的安装路径）。7.0 尚在开发中，且它不支持你的"无过滤器 + 雷达"组合。
2. 第一批行程必须跑 `probe_required_decel.py` 和 `check_t2_radar_fcw.py` 看 Alpha Long 下雷达总线是否还在发（V2）。
   - 不在发 → 7.0 的做法是对的；改走"过滤器"路线，可直接用 7.0。
   - 还在发 → 本系列有价值，可考虑把 T2/B 移植到 7.0 并向官方提 PR。
3. 不建议现在 rebase 到 7.0：无共同历史、目录重排、上游仍在频繁提交。
4. 安装注意：官方 `stable` 已是 7.0，按 27 §2 先装官方版本时装到的就是 7.0；切到本分支后就不再跟随官方。
