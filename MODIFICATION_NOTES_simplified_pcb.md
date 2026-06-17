# 简化版 PCB 固件修改说明（omb-nano fork）

> 本文档记录针对"简化版 PCB"对上游 gauteh/omb-nano 固件所做的修改：改了哪些文件、为何改、编译方式与产物。
> 简化版 PCB 差异、DNP 元件清单依据：`buoy/docs/2.PCB备料与组装指南.md`、`buoy/docs/4.OMB_Nano_烧录固件.md`、`buoy/docs/4.1 代码修改.md`。
> 分阶段提交，每阶段编译验证后再 commit。

---

## 硬件背景（简化版 PCB）

- **主控**：SparkFun Artemis 模块（Apollo3 Blue，Cortex-M4F），编译/烧录只作用于这颗 MCU。
- **传感器**：IMU ISM330DHCX（I2C IOM0）、GNSS MAX-M10M（I2C IOM3，外接有源天线 AHGG.202 经 U11）。
- **新增**：microSD 卡座 CARD1（SPI）。
- **供电**：电池 → U5 S7V8F3（自焊）→ 3.3V；或 USB-C → U27 HT7333-1（限流 150mA）。
- **未贴（DNP）**：铱星 RockBLOCK 9603、板载天线链 E1/U21/L3/C17-19/R22/U20、超级电容 C30/C31、SW1 干簧开关；其中仅**铱星**对应活跃固件代码需改（其余为纯硬件或上游已注释）。

---

## Phase 0 — 修复既有代码 Bug（恢复可编译基线）

> 审查上游 src/ 发现的问题。**B1 直接阻断编译**——当前 src/ 在修复前根本编译不过（预编译 bin 早于这些改动）。

| 编号 | 文件 | 问题 | 修复 |
|------|------|------|------|
| **B1** | `src/params.cpp:4` | `Serial.print(F("version : "; Serial.print(ON_VERSION);` 括号未闭合，非法 C++，阻断编译 | 改为 `Serial.print(F("version : ")); Serial.print(ON_VERSION);` |
| **B2** | `src/wave_analyzer.cpp` `clear_working_wave_packet()` | `array_pwelch[nbr_wave_packet_freqs]=0` 用常量下标 55（合法 0–54）→ 越界写 + 未清零；默认 mode 0 每次波谱执行 | 改为 `array_pwelch[i]=0` |
| **B3** | `src/params.h` DEPLOYMENT_MODE==2 块 | 缺 `total_number_of_samples`、`timeout_attempt_transmit_seconds`，选 mode 2 编译失败且报错隐晦 | 加功能解释注释 + 明确 `#error`（不补实现；默认 mode 0 不受影响） |
| **B4** | `src/wave_analyzer.cpp` `perform_welch_analysis_imu_data()` | mode 1 等小 `total_number_of_samples` 配置下，Welch 循环仍读到约第 12338 个样本 → 越界读 | 读取前加防御性 guard：样本数不足则打印并安全 return |
| **B5** | `src/gnss_manager.cpp`（setup/sleep 两处） | `return false;` 之后的 `delay(500);` 为死代码 | 删除死代码 |
| **B6** | `src/time_manger.cpp:97` | 注释"months start from 0, so deduct 1"误导（Time 库 Month 为 1-based，代码 `=month` 本就正确） | 改为正确注释 |

**未改动（避免误报）**：IMU 累加器容量充足无溢出；Tz/Tc 是否 `sqrt(m2/m0)` 反向属上游科学约定（附带既有 TODO），不擅改；`float_mean_filter` 空输入除零仅严重超时下出现，属上游行为。

**未处理**：19 处 TODO/HACK 均为注释、不影响编译，留待后期。

---

## Phase 1 — 去铱星（适配简化版 PCB，铱星模块未贴）  *(已完成，commit #2)*

- `src/params.h`：铱星参数区新增开关与说明，默认 `// #define USE_IRIDIUM`（注释/不定义）。将来真装模块时取消注释即可恢复铱星。
- `src/tracker.ino`：用 **`#ifdef USE_IRIDIUM … #endif`** 条件编译包裹**全部** `iridium_manager.*` 调用（发送/状态查询）、`any_successful_transmit` 声明、整个发送突发块；并把日志措辞改干净。保留 `#include "iridium_manager.h"` 与 `iridium_manager.cpp` 参与编译（`<IridiumSBD.h>` 库仍链接）。
  - 注意：正确方向是 `#ifdef`（宏未定义→剔除），非计划初稿误写的 `#ifndef`。
- 不改 `board_control.cpp`：其 `iridiumSleep`(p35) 写 LOW 在无模块时为无害空操作。
- 验证：编译通过，`src/build/tracker.ino.bin` 124648 字节（较 Phase 0 的 136376 减小约 11.7KB，未引用的铱星收发代码被 linker 剔除）。
- GNSS/IMU/波浪谱/睡眠/看门狗逻辑未改动，功能保留。

---

## Phase 2 — SD 卡数据记录（新增元件）  *(待执行 / 视编译难度可能推迟)*

- `src/Dockerfile`：追加 SD 库（SdFat）。
- `src/board_control.h`：SD 引脚 `SD_CS=27, SD_MOSI=26, SD_SCK=28, SD_MISO=25, SD_CD=22`（来自 PCB设计决策记录.md §六）。
- 新增 `src/sd_manager.h/.cpp`：`GNSS.CSV` / `WAVES.CSV` 追加写；每次 open→append→flush→close。
- 写入时序（U27 150mA 限额）：GPS 关断后再写、波谱算完后再写，避免与 GPS 60mA 叠加。

---

## 编译与产物

构建链 = 仓库 `Dockerfile` + `Makefile bin:` 目标。macOS（Docker Desktop 已装）：

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
cd ~/Downloads/omb-nano
docker build -t omb-nano .
docker run --rm -v "$(pwd)/src:/tracker" omb-nano \
  bash -lc "arduino-cli compile -e --output-dir build --build-cache-path /tmp/build -v -b SparkFun:apollo3:artemis ."
```

- 产物：`src/build/tracker.ino.bin`（约 134KB 量级，对照 `firmware/omb_nano_v0.2.0.bin`）。
- 烧录：`arduino-cli upload -p <PORT> -b SparkFun:apollo3:artemis --input-file src/build/tracker.ino.bin`（串口 1,000,000 bps）。
