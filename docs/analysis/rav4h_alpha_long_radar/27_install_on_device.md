# 27 — 在 comma 设备上安装这个分支（cwhypt/StarPilot `claude/quirky-goodall-sumqk9`）

状态：步骤基于代码阅读与公开文档；**未在真实设备上走过一遍**。StarPilot 官方安装说明在 wiki.firestar.link（本环境未能读取），以官方为准。

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
