# 15 — 上游更新检查（2026-10-23）

状态：完成。方法：`git ls-remote` / `git fetch upstream`（firestar5683/StarPilot）。

## 1. 结果概览

| 引用 | 上次（10-21） | 本次（10-23） | 结论 |
|------|--------------|---------------|------|
| 发布分支 `StarPilot` | `823a5d5` | `823a5d5` | **无更新**，本分支无需合并 |
| PR #230 head | `b66ba75` | `b66ba75` | 无更新；发布分支未合入 → 仍为 OPEN 状态（推断） |
| 开发分支 `SecretGoodStarPilot` | `2abc15c` | `d28aa48` | **+435 提交**，多数为其他车型/测试/CI；Toyota 过滤器支持有重大进展 |
| cwhypt fork `StarPilot` | `823a5d5` | `823a5d5` | 与上游发布分支一致 |

## 2. 开发分支中与本车相关的更新

### 2.1 2025 RAV4 Hybrid + 雷达 CAN filter 的实车证据（最重要）
- 新测试数据 `openpilot/starpilot/car/toyota/tests/data/rav4_2025_filter_discovery.json`："Anonymized 2025 RAV4 Hybrid discovery evidence"。
  - 平台 `TOYOTA_RAV4_TSS2_2023`；`fwdRadar` 固件解码为 `\x018821F0R03100`（**与 ars510 解析器的目标固件一致**）。
  - 过滤器广播：bus 0 与 bus 2 上的 **0x2FF（767），4 字节，`00 00 04 09`**。
- 这与 smart_dsu 固件的特征（`send_id()` 发 0x2FF、DLC 4，见 08）吻合 → **有人已在 2025 RAV4 Hybrid 上实际装了这类过滤器**（具体产品未说明，推断很可能是 OneClone / smart_dsu 类）。
- 新增 `opendbc/car/toyota/radar_filter.py`（提交 `0c0d4bc8c`、`b781fa90d`、`8adbeddcb`）：
  - `hardware_supported()`：bus 0 上 0x2FF 为 4 字节（且各总线副本也为 4 字节，无 0x201 / 0x2AA）→ 认定为雷达过滤器；或 0x2AA 8 字节的传统过滤器。注释："Radar filters can use a four-byte 2FF advertisement, as observed on the 2025 RAV4. This is distinct from legacy eight-byte SDSU status."
  - 识别后：`RADAR_CAN_FILTER` 标志、`LONG_FILTER` 安全位、**不置 `DISABLE_RADAR`**；Alpha Long 开 → openpilot 纵向，安全参数 `0x1449`；关 → 原车纵向 `0x1649`。
  - `carstate`：有过滤器时不再把被拦截的 `ACC_CONTROL` 当成必需输入，同时保留原车 PCS/AEB 观测。
  - `toyota.h`：`0x1449` 时禁用雷达诊断（不发 UDS），并按过滤器路径处理。
- 其它：`40afc1bad` 恢复 RAV4 角度横向独立控制（AOL）；`16509c380` 在 Vehicle Controls 里暴露 Alpha Longitudinal 开关；`2993c8fb0` 恢复视觉候选 follow-up 节奏。
- 开发分支的 ARS510 文件本次**无改动**（仍未含 PR #230）。

### 2.2 与发布分支的差异（静态比对）
- 发布分支对 4 字节 0x2FF 走的是通用 smartDSU 路径（`car/interfaces.py:310` 置 `SMART_DSU` → `use_sdsu`，08 §3），代码上同样不关雷达；但**没有** `radar_filter.py` 的专门识别、`toyota.h` 里也没有 `0x1449` 的专门分支（`grep 0x1049|0x1449` 无结果），且 `carstate` 走 SDSU 分支读 0x2FF 的 `FD_BUTTON`。
- 能否在发布分支上正常工作仍是推断（08）；开发分支的这组改动是作者针对这台实车做的"qualified"支持，可信度更高。本地缺 `capnp` 等依赖，未能运行 `get_params` 实测。

## 3. 对计划的影响

- 08 的"OneClone 是否兼容 2025 RAV4H"疑问：**出现正面证据**（同固件雷达 + 4 字节 0x2FF 过滤器已被实车记录并被正式支持）。仍需向卖家确认是同一产品。
- 若将来装过滤器：优先使用包含 `radar_filter.py` 的版本（开发分支，或等其进入发布分支），而不是依赖发布分支的通用 smartDSU 路径。
- 不装过滤器的 T1–T5 不受影响。
