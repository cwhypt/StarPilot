# 27 — 在 comma 设备上安装这个分支（cwhypt/StarPilot `claude/quirky-goodall-sumqk9`）

状态：步骤基于代码阅读与公开文档；**未在真实设备上走过一遍**。StarPilot 官方安装说明在 wiki.firestar.link（本环境未能读取），以官方为准。

## ⚠️ 2026-11-23：先别切换（AGNOS 版本冲突）

实际设备：AGNOS **20.5.5**，官方 `starpilothq/openpilot` `stable`（7.0）。本分支 `launch_env.sh` 只接受 AGNOS `19.8.1 19.8.2 20.2-starpilot.1 20.2.1 20.5.1 20.5.2`，`launch_chffrplus.sh` 的 `agnos_init` 在版本不在列表时会按 `system/hardware/tici/agnos.json` **刷回 19.8.1**（含 xbl/abl 等引导分区）——这是降级，风险高。**已处理（用户选择不用 7.0）：** `launch_env.sh` 的接受列表加入 `20.5.5`，切换后不刷 AGNOS。依据：列表本来就含 20.5.1/20.5.2；本分支 `.so` 为 cpython-312，7.0 `.python-version` 为 3.12。未在 20.5.5 上实测，开机异常就按 §2 的回退命令换回。

可行路线：
1. 保持官方 7.0，先**关 Alpha Long** 开车收集 rlog（7.0 对 RAV4 自动启用 ARS510，正好是基线数据）；本分支只克隆到旁边目录跑离线脚本，不重启、不互换。
2. 之后二选一：把 T2 移植到 7.0（需同时改 7.0 的 `configure_radar`，它在 `DISABLE_RADAR` 时关闭 ARS510，见 28 §3）；或在本分支把 20.5.5 加入接受列表（旧用户空间跑在新 AGNOS 上，未验证）。

## 0. 这个分支能直接在设备上跑吗

- 分支 = 上游 `stable`（8589b73f）+ PR #230 + 本系列改动（T2、B 纯函数、检查/回放/探测脚本、文档）。
- `stable` 是"发布树"：`prebuilt` 标记、29 个 `.so`、panda 固件都在 git 里；`launch_chffrplus.sh:157` 看到 `prebuilt` 就**不在设备上编译**。
- 本系列改动只有 Python 和 `.dbc`（opendbc 运行时用 Python 解析 DBC），没有改 C/C++、panda 安全模式或固件 → 不需要重新编译。[推断，未上车]
- 更新：`system/updated/updated.py` 对 `origin` 执行 `git fetch origin <当前分支>`，所以设备装好后会跟随你仓库里这个分支的更新。

## 1. 前提

- 仓库 `cwhypt/StarPilot` 需要**公开**，否则设备 `git fetch` 不到（设备上没有你的 GitHub 凭据）。
- 硬件：comma 设备 + 适配 2025 RAV4 Hybrid 的 comma 官方线束（按 comma 官网车型页选择）。不装雷达过滤器（与本系列前提一致）。

## 2. 安装（推荐路径：先装官方 StarPilot，再切到这个分支）

comma 的自定义软件安装器格式是 `installer.comma.ai/<GitHub 用户>/<分支>`，[推断] 它默认找名为 `openpilot` 的仓库；而这个仓库叫 `StarPilot`、分支名带 `/`，直接用安装器可能不行。因此：

1. **装官方 StarPilot**：设备开机设置时选 "Custom Software"，按 StarPilot wiki / Discord 给出的安装地址安装。装好、开一次车，确认正常。
2. **开 SSH**：设备 设置 → Developer（开发者）→ 打开 SSH，填你的 GitHub 用户名（设备会拉你 GitHub 账号的公钥）。电脑上 `ssh comma@<设备 IP>`。
3. **切换到这个分支**（停车、熄火或 P 挡）：
   ```bash
   cd /data/openpilot
   git remote get-url origin            # 记下原地址，回退时用
   git status --short                   # 应为空
   git remote set-url origin https://github.com/cwhypt/StarPilot.git
   git fetch origin claude/quirky-goodall-sumqk9
   git checkout -B claude/quirky-goodall-sumqk9 FETCH_HEAD
   git branch --set-upstream-to origin/claude/quirky-goodall-sumqk9
   git log --oneline -1                 # 应为本分支最新提交
   ls prebuilt                          # 必须存在，否则会在设备上编译
   sudo reboot
   ```
   本地分支名必须与远端相同，否则更新程序 `git fetch origin <本地分支名>` 会失败。

   **更推荐（2026-11-23 补记）：另起一个干净目录再互换。** 官方 `stable` 现在是 7.0（28），目录结构不同，在原目录里 checkout 会留下 7.0 编译出来、被 git 忽略的文件。本分支没有 git 子模块，可以直接浅克隆：
   ```bash
   cd /data
   git clone --depth 1 -b claude/quirky-goodall-sumqk9 https://github.com/cwhypt/StarPilot.git openpilot_rav4
   ls openpilot_rav4/prebuilt            # 必须存在
   mv openpilot openpilot_official       # 保留官方版本，回退时换回来
   mv openpilot_rav4 openpilot
   sudo reboot
   ```
   回退：`cd /data && mv openpilot openpilot_rav4 && mv openpilot_official openpilot && sudo reboot`。

## 3. 开机后的设置

1. 设置里打开 Alpha Long（`ExperimentalLongitudinalEnabled`，openpilot 纵向）。
2. Galaxy → Vehicle → **RAV4 Radar (Experimental)** 选 **1（ACC Target）**；重启。
3. 验证（第一次开车后，SSH 到设备）：
   ```bash
   cd /data/openpilot
   python tools/longitudinal/check_t2_radar_fcw.py /data/media/0/realdata/<路线>--0/rlog*
   ```
   第 0 步应 PASS（`ARS510_RADAR` + `DISABLE_RADAR`），第 1 步看 0x100 是否存在（24 §6/§7）。

## 4. 建议的头几次驾驶顺序

1. 先**关 Alpha Long**、`ToyotaRadarMode=1` 开一两天：原车 ACC/AEB/FCW 都在；收集 C 探测（26 §2）和 T2 第 3 步所需的对照数据。
2. 再**开 Alpha Long**：跑 T2 检查第 0–2 步；同时跑 B 回放（25 §4）、C 探测。
3. 记住：Alpha Long 下**原车 AEB 不在**，T2 只提醒、B 尚未接入。

## 5. 回退

```bash
cd /data/openpilot
git remote set-url origin <第 2 步记下的原地址>
git fetch origin <官方分支> && git checkout -B <官方分支> FETCH_HEAD
sudo reboot
```
或在设备设置里卸载，重新用官方安装地址安装。也可以只把 `ToyotaRadarMode` 改回 0（T2 和雷达感知一并关闭）。

## 6. 风险提示

- 这是个人分支，未经 StarPilot 维护者审阅，未上车验证。
- comma 不为 fork 的数据提供支持；使用 fork 的风险自负。
