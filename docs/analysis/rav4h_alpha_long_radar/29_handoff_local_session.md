# 29 — 交接：从云端会话转到本地终端调试（2026-11-23）

给接手的人或 agent：先读本页，再读 [TODO](TODO.md)（唯一任务清单）和 [README](README.md)（文档索引）。所有改动都在仓库 `cwhypt/StarPilot` 的分支 `claude/quirky-goodall-sumqk9`，只推这个分支，不开 PR。

## 1. 现在的状态（一句话）

本分支（`b5584ea4e`）**已装到车机上并重启**，还没确认是否正常开机，也还没有任何行车日志。

## 2. 车和设备

| 项 | 值 |
|---|---|
| 车 | 2025 Toyota RAV4 Hybrid（指纹预期 `TOYOTA_RAV4_TSS2_2023`，角度转向 LTA，ARS510 雷达） |
| 硬件前提 | **不装雷达 CAN 过滤器**；目标是 Alpha Long + `ToyotaRadarMode`，用 T2 补原车 FCW |
| 设备 | comma，主机名 `comma-30dfecc5`，AGNOS **20.5.5** |
| 连接 | 手机热点；设备 `172.20.10.2`，Mac `172.20.10.4`；`ssh comma@172.20.10.2`（GitHub 用户 `cwhypt` 的公钥） |
| 设备上的目录 | `/data/openpilot` = 本分支（浅克隆，`grafted`，origin = `https://github.com/cwhypt/StarPilot.git`）；`/data/openpilot_official` = 原来的官方版（`starpilothq/openpilot` `stable`，`404ef33a9`，即 7.0） |
| 安装方式 | Mac 上用 Wi-Fi 浅克隆 → 用热点 `rsync -a --partial` 传到 `/data/openpilot_rav4` → 在设备上互换目录 → 重启（27） |
| 回退 | `cd /data && mv openpilot openpilot_rav4 && mv openpilot_official openpilot && sudo reboot` |

## 3. 本系列改了什么（本分支相对 `upstream/stable` 8589b73f）

| 提交 | 内容 | 文档 |
|---|---|---|
| `94fefd3f` | 合入 PR #230（ARS510 解析器、Mode 1/2、DBC 新报文） | 21 |
| `a14a23902` | **T2**：bus 1 `PCS_STATE.PCS_ALARM` → `stockFcw` → `EventName.fcw`；仅在 `ARS510_RADAR` 且 `DISABLE_RADAR` 时生效；POWER_UP 首帧过滤；50 帧超时 | 24 |
| `595719058` | `tools/longitudinal/check_t2_radar_fcw.py`：T2 上车验证脚本（第 0–5 步） | 24 §6–7 |
| `8765632c9` | **B 第一阶段**：`selfdrive/controls/lib/radar_brake_cap.py` 纯函数 + `tools/longitudinal/replay_radar_brake_cap.py` 回放；**未接入规划器** | 25 |
| `87ab9f447` | **C 探测**：`tools/longitudinal/probe_required_decel.py`，离线解码 0x195/0x196 | 26 |
| `b5584ea4e` | `launch_env.sh`：`AGNOS_ACCEPTED_VERSIONS` 加入 `20.5.5`，否则开机会把 AGNOS 刷回 19.8.1 | 27 顶部 |

车上行为的改变只有 T2（只告警、不控车）和 AGNOS 接受列表；B、C 都是离线工具。

## 4. 本地终端下一步

### 4.1 确认开机正常
```bash
ssh comma@172.20.10.2
cat /VERSION                                   # 应为 20.5.5（没被刷）
cd /data/openpilot && git log --oneline -1     # b5584ea
tmux ls                                        # 应有 comma 会话
ps aux | grep -E "manager|controlsd|modeld|card" | grep -v grep | wc -l   # >0
tmux capture-pane -pt comma -S -60 | tail -60  # 看报错
```
- 屏幕显示 AGNOS 更新：不要断电，记录屏幕内容。
- 进程数为 0 或反复重启：看 tmux 日志；不行就按 §2 回退。
- 本分支在 20.5.5 上**从未运行过**（依据：原列表已含 20.5.1/20.5.2，`.so` 是 cpython-312，与设备 Python 3.12 一致）。最可能的问题是系统库/Python 依赖差异，报错会出现在 tmux 日志里。

### 4.2 设置
1. Alpha Long **先关**。
2. Galaxy → Vehicle → RAV4 Radar (Experimental) = **1**，重启。

### 4.3 第一批行程（原车纵向，原车 AEB/FCW 在）
开一两天后：
```bash
ls /data/media/0/realdata/ | tail -3
cd /data/openpilot
R=/data/media/0/realdata/<路线>--0
python tools/longitudinal/probe_required_decel.py $R/rlog*        # C：0x196 是否存在、REQUIRED_DECEL 符号/空闲值
python tools/longitudinal/replay_radar_brake_cap.py $R/rlog*      # B：需 Mode 1/2
python tools/longitudinal/check_t2_radar_fcw.py $R/rlog*          # T2：此时第 0 步应 FAIL/N/A（未开 Alpha Long），看第 1 步 0x100 是否存在
```
必须用 rlog（qlog 没有原始 CAN）。也可以 `scp` 到 Mac 上跑（需要本仓库的 Python 环境）。

### 4.4 第二批行程（打开 Alpha Long，原车 AEB 不在，小心）
- 核心问题 **V2**：雷达被 UDS 关闭后，bus 1 上的 0x100 / 0x196 / 0x235–0x23B 是否还在发。三个脚本都会显示。
  - 还在发 → T2 有效，继续 V3（`check_t2_radar_fcw.py` 第 2–4 步）和 B 第二阶段。
  - 不发 → T2/B/C 在"无过滤器"配置下都无效；官方 7.0 正是这样设计的（28 §3），改走"装过滤器"路线。

## 5. 本地开发环境注意

- 仓库里的 `.so` 都是 aarch64 版本，在 Mac（x86 或 Apple Silicon 的 macOS）上 import `msgq`、`transformations` 会失败。云端测试时用了一个导入桩（sitecustomize，未提交），运行方式：
  ```bash
  PYTHONPATH=<桩目录>:opendbc_repo:. python -m pytest -q -c /dev/null --rootdir . --noconftest -p no:cacheprovider <测试文件>
  PYTHONPATH=opendbc_repo:. python -m pytest -n 8 opendbc_repo/opendbc/car/toyota/tests
  ```
  桩的作用只是让 `msgq.ipc_pyx`、`transformations` 可以被 import；离线工具本身不需要它们。
- 相关测试：`opendbc_repo/opendbc/car/toyota/tests/test_ars510_pcs_fcw.py`（49）、`selfdrive/car/tests/test_car_specific.py`、`selfdrive/controls/tests/test_radar_brake_cap.py`（65）、`tools/longitudinal/tests/`（25）。已知与本系列无关的失败：`test_kia_ray_ev_allows_stock_cruise_to_enable_controls`。
- 直接在设备上跑工具最省事：设备已有完整运行环境。
- 提交信息结尾按仓库约定加 Co-Authored-By / Claude-Session 行；不要在提交里写模型名。

## 6. 待做（摘要，详见 TODO）

| 优先 | 项 |
|---|---|
| 现在 | 4.1 开机确认；若失败，记录 tmux 日志再回退 |
| 有日志后 | V1（`carParams.carFw` 的 `fwdRadar`）、V2、V3、V4；决定 C 是否实现 |
| 数据满足 25 §7 后 | B 第二阶段接入规划器（默认关闭开关）；T4b 切入扩展 |
| 以后 | 是否移植到官方 7.0（28 §4）；7.0 在 Alpha Long 无过滤器时不用 ARS510 |

## 7. 已知风险

- Alpha Long 无过滤器时**没有原车 AEB**；T2 只告警，B 未接入，C 未实现。
- 雷达总线信号的含义（PCS_ALARM、REQUIRED_DECEL 符号）来自社区逆向记录，未经实车验证。
- 本分支未经 StarPilot 维护者审阅；上游 `stable` 已被强推为 7.0，与本分支无共同历史，设备不会再收到官方更新。
