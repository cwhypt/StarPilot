# 16 — "四字节 0x2FF 雷达过滤器"的来源、文档与购买渠道

状态：完成（网络检索 + 固件源码阅读，2026-10-23）。起因：开发分支 `radar_filter.py` 注释 "The 2025 radar filter observed in the field uses a four-byte 2FF advertisement"（提交 `b781fa90d`，见 15）。

## 1. 结论

1. **4 字节 0x2FF 就是 smartDSU 固件的心跳报文。** RetroPilot ocelot 的 `firmware/smart_dsu/main.c`（`RetroPilot/ocelot` main，`94efc67`，2025-11-13）在 `TIM3_IRQ_Handler` 中周期性向车侧发送：
   ```c
   dat[0] = (distance_button) << 7U;          // 跟车距离按钮
   dat[1] = ctrl_mode;
   dat[2] = ((state & 0xFU) << 4) | (counter); // 状态 + 计数器
   dat[3] = toyota_checksum(0x2FF, dat, 4);
   to_send.RDTR = 4;  to_send.RIR = (0x2FF << 21) | 1U;
   ```
   StarPilot 测试数据里的 `00 00 04 09` 解码为：按钮 0、ctrl_mode 0、state 0（NO_FAULT）、counter 4、校验 0x09 —— 与该格式一致。
2. **2025 RAV4 Hybrid 的实车来源很可能是 ars510-radar issue #66（DustyL）** [推断：StarPilot 的数据是匿名的]：
   - 车：RAV4 2025 hybrid，`TOYOTA_RAV4_TSS2_2023`，fwdRadar `8821F0R03100`；sunnypilot `tn` 分支，comma 3X。
   - 过滤器："radar ACC filter (panda with smartDSU firmware on the radar's car-bus link)"；固件 "Beartech's 'New SDSU'"＝RetroPilot ocelot `smart_dsu` + Beartech 的修改。
   - 线束：两个 8 针接插件；雷达侧接插件曾因"unsealed wire entries"导致故障（bus 1 错误激增、雷达物体列表中断）。
   - 行为：openpilot 不发 0x343 时，过滤器把原车 0x343 的 `ACC_TYPE`、`PERMIT_BRAKING` 置 1 并重算校验（作者在 73,513/73,513 帧上确认）。与 ocelot README 的 PERMIT_BRAKING 修改描述一致。
3. **ocelot smart_dsu README** 的支持列表含 "2022 RAV4 Hybrid (tested by @sky)"（需"slight firmware changes"）；未列 2023-25。
4. **可购买的产品**（均未明确写支持 2023-25 RAV4 雷达线束；页面在本环境无法打开，信息来自搜索摘要）：
   | 产品 | 渠道 | 价格（摘要） | 备注 |
   |---|---|---|---|
   | Beartech **C-SDSU SmartDSU** | shop.beartech.ca/products/sdsu；eBay、Etsy 转售 | 约 CAD 180（转售更贵） | 固件 "New SDSU"（Discord 发布，`fraserxiong/panda_firmware` 分支 `New_SDSU_firmware` 只有二进制）；安装指南（docs.innoisle.com/beartech）列的主要是 DSU 车型，另有一行不清楚的 "RAV4 2024" |
   | RetroPilot **C-SDSU SmartDSU** | shop.retropilot.org/product/c-sdsu-smartdsu/ | 未取得 | 开源固件即 ocelot `smart_dsu` |
   | OneClone **Toyota Radar ACC Filter** | oneclone.net | USD 99 | 按 eFiniLan README 是 white/grey panda + wocsor `smart_dsu`；wocsor 版只在收到雷达 0x343 时才发 0x2FF（08） |
5. **wocsor 版 vs ocelot 版**：ocelot 版 0x2FF 为**周期发送**（与雷达 0x343 无关），降低了 08 §4.2 的"雷达启动慢 → 指纹期没有 0x2FF"风险；还支持 0xF10/0xF11（替代 ACC/AEB 控制报文），openpilot/StarPilot 未使用。

## 2. 对选型的影响（更新 07/08）

- 若买过滤器，**优先选与 issue #66 同源的 Beartech C-SDSU（New SDSU 固件）**：同车型、同雷达固件已有实车记录，且 StarPilot 开发分支的 4 字节 0x2FF 识别正是为这种设备写的。
- 必须向卖家确认：是否提供 **2023-25 RAV4 前雷达（8 针）直插线束**、接插件密封质量（issue #66 的故障教训）、固件版本。
- OneClone（wocsor 固件）同样发 4 字节 0x2FF，但心跳依赖雷达 0x343，开机时序风险更高。

## 3. 未能获取 / 待确认

- shop.beartech.ca、docs.innoisle.com、shop.retropilot.org、docs.communitypilot.co 在本环境 DNS 失败，无法读页面原文（价格、型号选项、雷达线束）。
- Beartech "New SDSU" 无源码，只能从 issue #66 与 README 推断其基于 ocelot。
- 渠道：Beartech Discord `https://discord.gg/qcMkhzFSux`；ars510-radar issue #66 可直接问 DustyL 用的具体型号与线束。

## 4. 来源

- https://github.com/ds-sebastian/ars510-radar/issues/66
- https://github.com/ds-sebastian/ars510-radar/pull/141 （0x2FF byte0 bit7 = 跟车距离按钮）
- https://github.com/ds-sebastian/ars510-radar/pull/122 （"只丢 0x343 的雷达设置"：未知存在）
- https://github.com/RetroPilot/ocelot/tree/main/firmware/smart_dsu
- https://github.com/fraserxiong/panda_firmware （分支 `New_SDSU_firmware`）
- https://shop.beartech.ca/products/sdsu ；https://shop.retropilot.org/product/c-sdsu-smartdsu/ ；https://docs.innoisle.com/beartech/sdsu-description-and-installation-guide （仅搜索摘要）
- https://community.sunnypilot.ai/t/3745 （Smart DSU 技术帖）
