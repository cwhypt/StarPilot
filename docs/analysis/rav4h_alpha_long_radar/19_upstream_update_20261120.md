# 19 — 上游更新检查（2026-11-20）：核心问题是否被解决

状态：完成（一手 git 比对，`git fetch upstream`；本地缺 `capnp`，未运行测试）。上次检查：15（2026-10-23）。

## 1. 上游结构变化（重要）

2026-11-17 上游做了**分支迁移**（提交 `0b2061df` "Branch-Migration: StarPilot -> stable"，`Dom` → `testing`）：

| 旧 | 新 | 现状（2026-11-20） |
|---|---|---|
| `StarPilot`（发布分支，`823a5d5`） | **`stable`**（`8589b73f`，11-18） | 与旧 `StarPilot` 内容基本一致，只多一个 CI 工作流 |
| `Dom` | **`testing`**（`256924b8`，11-20） | 1206 个提交，新目录结构（代码在 `openpilot/`，`radar_filter.py` 等） |
| `SecretGoodStarPilot`（开发分支） | — | **已从上游消失**；其内容就是现在的 `testing` |
| 旧 `StarPilot` 分支 | 仍在，停在 `0b2061df`（11-17） | 不再更新 |

`StarPilot` 分支从 `823a5d5` 到 `0b2061d` 只有 35 个提交：AGNOS 版本接受、Galaxy 更新进度、GM Buick Envision 车型、README/CI/分支迁移——**没有任何 Toyota 或雷达相关提交**。

→ 本分支（基于 `823a5d5`）**无需合并**；以后要对齐应跟 `stable`（或 `testing`）。

## 2. 核心问题逐项核查

| # | 核心问题 | 10-23 状态 | 现状 | 结论 |
|---|---|---|---|---|
| 1 | PR #230（Mode 1 修复：`TARGET_ID` 6 bit、`LAT_DIST` 回绕、雷达故障标志；Mode 2 → ACC Target + Summaries） | 未合入，PR 仍 OPEN | **仍未合入** stable / testing：DBC 中 `TARGET_ID : 60\|5`，无 `lat_dist_wrapped`，无 `ars510_acc_summaries.py`；PR head 仍是 `b66ba75` | **未解决** |
| 2 | 雷达私有总线安全信号（`PCS_ALARM`、`REQUIRED_DECEL`、`RADAR_FAULT`）被使用 | 未使用（DBC 都没有） | **仍未使用**：两个渠道的 ARS510 DBC 都没有这些信号 | **未解决** |
| 3 | 不装过滤器、开 Alpha Long 时的雷达 FCW / AEB 兜底 | 无 | 无变化：`carstate` 仍在 `DISABLE_RADAR` 时跳过 `stockAeb`/`stockFcw`（stable `carstate.py:129,200`） | **未解决** |
| 4 | 雷达在 radard 中只能"校正视觉"，视觉漏检时雷达车不进规划 | stable 有可调阈值 0.35 + preferred-track；开发分支写死 0.5 | **stable：不变**（阈值 0.35、低速 override <4 m/s）。**testing：仍是 `lead_prob > .5`，不再有 `preferred_track` / `lead_detection_probability`**（简化为接近官方实现） | **未解决**；testing 对雷达更不友好 |
| 5 | 取 min(原车 ACC, StarPilot) | 做不到 | 无变化；`radar_filter.py` 仍明确"not a copy of ACC_CONTROL" | **未解决**（硬件固件限制） |
| 6 | 过滤器 `SDSU.STATE` 故障码被读取（F7） | 未读取 | **testing 已实现**：`carstate.py:300-306` 读 `SDSU.STATE`，`ret.accFaulted = … or not sdsu_fresh or sdsu_state not in (0,7)`，`sdsu_state == 7`（ocelot `STATE_AEB_CTRL`）→ `stockAeb`；`LONG_FILTER` 时 `FD_BUTTON` 也走该路径（`:270`）。stable 无 | **testing 部分解决**（仅装过滤器时有意义） |
| 7 | 2025 RAV4H + 过滤器的支持 | 开发分支新增 `radar_filter.py` | **testing 继续完善**：`c276b589`（RAV4H 踏板 + CAN 过滤器并存）、`2a7ebf19`（对应固件）、`6232db5c`（`test_toyota_rav4h_filter.py` 过滤器 profile 回归测试）、`be278565`（Camry 集群雷达横向符号修复，与 RAV4 无关） | **testing 有进展**；stable 仍无 `radar_filter.py` |
| 8 | RAV4 专用纵向调参 | stable 有；开发分支缺失 | 无新信息（本次只比对了雷达/过滤器相关路径，未重新核对调参） | 未重新核查 |

## 3. 本次新出现、与本车相关的上游动作

- testing：`479da9e7` "Restore automatic stopped launch for qualified Toyota SDSU profiles"；`013d9753` "Calm RAV4 TSS2 low-speed center corrections"（RAV4 低速居中，横向，不涉及雷达）。
- 未见：任何 ARS510 / `ToyotaRadarMode` 变更，任何 PR #230 的跟进。

## 4. 对计划的影响

1. **T1（合入 PR #230）仍然成立且没有被上游替代**；上游发布渠道已改名，应对 `stable` 做。
2. **T2/T3（雷达 FCW、`REQUIRED_DECEL`）仍是空白**，无人做。
3. **T6（放宽视觉门限）更需要**：testing 的 radard 比 stable 更严格（阈值 0.5、无 preferred-track），若 testing 将来成为发布版，RAV4 雷达更难被采用；注意 testing 的 radard 里没有 ARS510 专用逻辑。
4. 过滤器路线：testing 在 `SDSU.STATE` 读取与 RAV4H 过滤器 profile 上已有实质进展，选型结论（16、17）不变。
5. TODO 清单更新：F7 在 testing 已实现（待合入 stable）；新增 T8（跟进 `stable`/`testing` 迁移）。

## 5. 局限

- 只对 stable/testing 做了文件与关键字比对，未运行任何测试（缺 `capnp` 等依赖）。
- testing 只拉取了最近 1206 个提交的浅历史；功能判断基于该树的当前内容。
- 没有核对 PR #230 / opendbc#3860 在 GitHub 上的最新评论（当前环境没有可用的 GitHub 检索工具），只确认了 `refs/pull/230/head` 未变。

## 6. 对照 15 号文档（10-23）的各条陈述，现在是否还成立

| 15 号文档的陈述 | 2026-11-21 复核 |
|---|---|
| 发布分支 `StarPilot` 无更新（`823a5d5`） | **已过时**：现为 `0b2061df`（+35 提交，无 Toyota/雷达改动），且已迁移为 `stable` |
| PR #230 head `b66ba75`、未合入 | **仍成立**（head 不变，stable/testing 均未含）。注：PR 在 `firestar5683/StarPilot`，不是 cwhypt fork |
| 开发分支 SGSP `d28aa48`（+435） | **已过时**：该分支已从上游消失，内容即 `testing`（`256924b8`，1206 个提交） |
| 开发分支有 2025 RAV4H + 4 字节 0x2FF 过滤器实车数据（`rav4_2025_filter_discovery.json`） | **仍成立**，现位于 `testing`；`radar_filter.py` 之后只有 `795799f4`（11-08，拆分测试文件）一次改动，逻辑未变 |
| `radar_filter.py` 识别后不关雷达，开 Alpha Long → `0x1449`，关 → `0x1649` | **仍成立**（`testing` 的 `toyota.h` 含 `0x1449`，`interface.py` 引用 7 处） |
| 发布分支没有 `radar_filter.py` / `toyota.h` 没有 `0x1449`；4 字节 0x2FF 走通用 smartDSU 路径 | **仍成立于 `stable`**：无 `radar_filter.py`，`toyota.h` 无 `0x1449`，`interfaces.py:311` 仍是 `0x2FF in fingerprint[0]` → `SMART_DSU` |
| 恢复 RAV4 角度转向独立横向控制；Vehicle Controls 里的 Alpha Long 开关 | **仍在 `testing`**（`angle_control.py`、`galaxy/tests/test_alpha_vehicle_controls.py`），`stable` 没有 |
| "将来装过滤器建议用含 `radar_filter.py` 的版本" | **仍成立**，且现在具体是 **`testing`**，而不是 `stable` |

新增细节：`testing` 在 `radar_filter.py` 之外又有 `advert_filter.py`、`camera_2aa_filter.py`、`legacy_advert_filter.py` 等识别模块，但 `advert_filter.py` 明确排除了 `radar_filter.PROFILES` 里的车（`CARS = eps8_sdsu.CARS - radar_filter.PROFILES.keys()`），**不影响 RAV4 2023-25**；`rav4h_filter.py` 针对的是旧款 `TOYOTA_RAV4H`（2019-21）。
