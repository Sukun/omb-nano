# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 当前项目配置（2026-11-15）

当前实物配置以 `../omb-nano-extend/buoy_build/prototype-status.md` 为准。U5未装，稳定3.3V经飞线注入VOUT/VCC、GND；USB为CH340供电通信。程序留在本仓库，新实验说明和运行记录归 `../omb-nano-extend/experiments/`。历史改型资料暂不迁移。

## 本仓库是什么

本仓库是 **OMB-Nano 极地浮标的固件源码**——基于 Arduino/C++、运行在 Apollo3 Blue（SparkFun Artemis 模块）上，通过 `arduino-cli` 编译。仓库是上游 [gauteh/omb-nano](https://github.com/gauteh/omb-nano) 的一个 fork（origin 为 `github.com/Sukun/omb-nano`），是整个两仓库工作区中**真正可编译、可烧录的代码**——姊妹仓库 `../omb-nano-extend` 不含任何源代码。

当前测试模式：`imu_capture_only=true`、`imu_capture_samples=600`、`use_dummy_imu_data=false`。每次启动采约60秒真实IMU，串口与SD同样的9列CSV，采完停留，不运行部署循环或波谱。具体格式见 `../omb-nano-extend/experiments/test1_2026-11-14_land-static/record.md`。

主机验证：`python3 -m unittest discover -s tests -p 'test_imu_capture.py' -v`，不连接设备。`tests/good-output*.txt`仍为更早的上游参考输出。编译仍用Docker；实际IMU与SD时序需真机复查。

## 命令

### 编译（Docker，唯一的构建方式）

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
cd ~/Downloads/omb-nano
docker build -t omb-nano .
docker run --rm -v "$(pwd)/src:/tracker" omb-nano \
  bash -lc "arduino-cli compile -e --output-dir build --build-cache-path /tmp/build -v -b SparkFun:apollo3:artemis ."
```

产物：`src/build/tracker.ino.bin`。根目录 `Makefile` 的 `bin:` target 封装了同一条 `arduino-cli compile` 命令（需在容器内、`src/` 目录下执行）。

当前三项实测先明确烧录 `firmware/omb_nano_imu_capture.bin`，之后用 `firmware/run_experiment.sh`。操作指南在 `../omb-nano-extend/experiments/01_Test_Guide.md`；`run_all_steps.sh`默认使用latest，保留为历史上电工具。

### 烧录

⚠️ **官方 SparkFun 的 `artemis_svl` 工具在本板上无效**——本板没有物理复位按钮，bootloader 进入依赖 CH340 的 **DTR 脉冲**触发 BOOT/NRESET 上的 RC 电路，而官方工具不发送 DTR 脉冲。必须用本仓库自带的 `firmware/svl_flash_v2.py`。

```bash
conda create -n pcb python=3.11 -y   # 一次性
conda activate pcb
pip install pyserial

python3 firmware/svl_flash_v2.py /dev/cu.wchusbserialXXXX -f firmware/omb_nano_latest.bin -b 115200 -v
```

烧录前提：USB-C 接电脑（CH340 供电+通信）；若 U5（S7V8F3）尚未焊接，需额外把外部 3.3V（如 AMS1117-3.3 模块）注入 U5 的 VOUT/GND 焊盘；当前原型机的稳定3.3V注入方式见上述配置文件，不按电源名称判断接线。

**建议先烧 `firmware/omb_nano_v0.1.0.bin` 或 `omb_nano_v0.2.0.bin`（未经修改的上游固件）验证硬件本身正常，再测试本地修改过的 `omb_nano_latest.bin`。**

### 串口监视

⚠️ **波特率陷阱**：烧录通信用 115200 bps；固件**运行时**串口波特率是 **1,000,000 bps**（`src/params.h: baudrate_debug_serial`）。监视器停在 115200 只会看到乱码。用 `firmware/imu_listen.py` 或 `firmware/hardware_test.py`，不要用 `screen`（在该波特率下不稳定）。

### CI

`.github/workflows/build.yml` 在 push/PR 到 `main` 时触发，**不使用 Dockerfile**，而是在 `ubuntu-latest` runner 上原生安装依赖，且使用的是**官方 SparkFun 板索引 URL**（与 Dockerfile 用的 URL 不同，见下方「工程细节坑点」）。CI 编译前会把 `tracker.ino` 改名为 `src.ino`（因为 checkout 目录名不是 `tracker`），本地 Docker 编译时挂载点已正确命名，不需要这步。

统一实验入口为 `firmware/run_experiment.sh --experiment <目录名> --source real`，输出在自有项目实验目录；主机检查命令见上文。

## 架构与代码结构

### 编译链关键事实

- 基础镜像 Ubuntu，`arduino-cli` + SparkFun Apollo3 core `1.2.1`。
- Dockerfile 使用的板索引 URL 是 `https://raw.githubusercontent.com/gauteh/omb-2024-nano/refs/heads/main/misc/package_sparkfun_apollo3_index.json`（本仓库 `misc/package_sparkfun_apollo3_index.json` 也有一份本地副本）——**注意这与 CI workflow、根目录 `Makefile` 的 `dependencies` target 用的官方 SparkFun URL 不同**，两者都锁定 core `1.2.1`，复现某条具体构建路径时不要混用。
- 三个库被替换为 jerabaul29 的 fork 分支以兼容 Artemis（Dockerfile 中 `rm -rf` + `git clone -b <branch>`）：`Adafruit_BusIO`（`fix/SPI_with_Artemis`）、`Adafruit_LSM6DS`（`feat/propagate_bool_flags`）、`OneWire`（`feat/Artemis_compatible`）。
- `SdFat@2.2.3` 被 Dockerfile 用 `sed` 全局打补丁：`SPI_DRIVER_SELECT` 从 0 改为 2（启用软件/位带 SPI）。**必须全局打补丁，不能只在 sketch 里定义**——否则 SdFat 各个独立编译单元会因为宏定义不一致产生 ODR 冲突。

### `src/` 文件速览

| 文件 | 作用 |
|------|------|
| `params.h` | **改动最频繁的文件**，所有功能开关/引脚定义/采样参数都在这里。`DEPLOYMENT_MODE` 当前值是 **`1`（测试模式）**——这是上电调试阶段留下的现行状态，不是默认值，真正部署前需要改回 `0`（生产模式）。改动约定是注释/取消注释，不要删除原代码。 |
| `tracker.ino` | 主程序入口（`setup()`/`loop()`）。 |
| `board_control.cpp/.h` | 上电时序、外设使能脚控制。 |
| `iridium_manager.cpp/.h` | 铱星驱动，保留编译（`<IridiumSBD.h>` 仍需链接），但 `tracker.ino` 中所有调用点都用 **`#ifdef USE_IRIDIUM`**（当前未定义）包裹——本版硬件 DNP。**如果将来重新启用铱星，方向必须是 `#ifdef` 不是 `#ifndef`**（早期草稿计划曾写反过）。 |
| `gnss_manager.cpp/.h` | GNSS（MAX-M10M），I2C IOM3。 |
| `imu_manager.cpp/.h` | IMU（ISM330DHCX），经 jerabaul29 fork 的 Adafruit LSM6DS 驱动，IOM0。**已真机验证工作正常**（见下方「已完成的工作」）。 |
| `wave_analyzer.cpp/.h` + `kiss_clang_3d.cpp/.h` | Welch 方法波浪谱分析，KISS FFT 后端。 |
| `sd_manager.cpp/.h` | **新增文件（上游没有）**，写 `GNSS.CSV`/`WAVES.CSV`，用 SdFat 的 `SoftSpiDriver`（原因见下方软件 SPI 说明），无卡时优雅降级、不阻塞其他逻辑。**已真机验证写入成功**。 |
| `sleep_manager.cpp/.h`、`watchdog_manager.cpp/.h`（32 秒看门狗）、`thermistors_manager.cpp/.h`（已通过 `#define DISABLE_ALL_THERMISTOR` 全局禁用）、`data_manager.cpp/.h`（基于 ETL vector 的内存缓存，SD 记录挂在这层之上） | 其余子系统，通常不需要改动。 |
| `time_manger.cpp` | ⚠️ **文件名拼写错误是上游真实存在的，不是文档笔误**——只有 `.cpp` 拼成了 "manger"，对应的 `time_manager.h` 拼写是正确的。grep/glob 时要用这个错误拼写，否则会漏掉这个文件。 |

更深入的处理流程细节（缓冲策略、Welch 分段参数等）见上游自带的 `docs/1.算法说明.md`，本文件不重复。

### 数据出口：四条路径，成熟度不对等

- **USB 串口**：始终可用（接线状态下），零额外代码，运行时把每次 GNSS 定位和完整 Welch 谱都 `Serial.print` 出来——是事实上的兜底方案，但仅限接线调试，深睡部署时不会输出。
- **SD 卡**：脱机部署的本地记录，无卡时优雅降级。
- **BLE**：Apollo3/Artemis 模块硬件支持，但**当前固件里没有一行 BLE 代码**，上电后不会广播任何东西；要用的话需要新写 ArduinoBLE 相关代码，且有效范围约 10m（近场）。
- **铱星**：已按上文方式移除/可恢复，是唯一的全球实时远程出口路径。

## 已完成的工作——不要重做

以下提交已完成并推送到 `main`，如有疑问以 `git log --oneline` 为准，不要轻信某一份文档的快照描述：

- **`b7d81fe`（Phase 0，修复既有 bug，恢复可编译基线）**：上游 `src/` 原样克隆后**根本编译不过**——`params.cpp:4` 有一处未闭合括号的语法错误（`firmware/` 下的预编译 `.bin` 早于这次修复）。同时修复了 `wave_analyzer.cpp` 的 `clear_working_wave_packet()` 中一处数组越界写（用常量下标而非循环变量 `i`），以及 3 处较小问题（`gnss_manager.cpp` 的死代码、`time_manger.cpp` 的误导性注释、`DEPLOYMENT_MODE==2` 缺字段的编译保护）。两处刻意未动的"疑似问题"：Tz/Tc 是否反向（当时未改；后续b5b700c已修复并新增Tp）、`float_mean_filter` 在极端超时下的除零（上游既有行为）。
- **`a0cde26`（Phase 1，去铱星）**：用上述 `#ifdef USE_IRIDIUM` 方式从调用路径中移除铱星，二进制体积减小约 11.7KB（未引用代码被 linker 剔除）。
- **`409e04b`（Phase 2，新增 SD 卡记录）**：新增 `sd_manager.cpp/.h` + Dockerfile 的 SdFat 补丁，二进制增大约 7.5KB。
- **`347ec25`、`6e60f92`、`288d8e0`（2026-06-26/27，超出文档记录的 3 个 Phase 之外的上电调试提交）**：在 `tracker.ino`/`params.h` 中直接加入无天线 IMU 测试模式；**IMU 已真机确认工作正常**（静止时垂直加速度约 9.76 m/s²，符合重力加速度，验证了焊接质量）；**SD 卡写入已真机确认成功**（此前"未检测到 SD 卡"的现象其实是测试流程跳过了 GNSS/波谱循环导致 SD 写入调用点从未被执行到，不是硬件故障）；烧录工具链重构，删除了 `svl_flash.py`/`artemis_svl_with_reset.py`/`flash_macos.sh`，替换为 `svl_flash_v2.py`/`run_all_steps.sh`。

明确区分："编译通过"（Phase 0–2，提交前均经 Docker 单独验证）不等于"真机验证"（IMU 和 SD 卡截至 2026-06-27 已真机确认，GNSS 锁星和完整部署周期尚未）。

## 工程细节坑点

- **DTR 脉冲是 bootloader 进入的唯一可靠方式**——本板无物理复位键，详见上方「烧录」一节。
- **U5 未焊接时需要外部注入 3.3V**到其 VOUT/GND 焊盘，同时 USB-C 必须接好；当前原型机供电以prototype-status.md为准，其他接法需重新核对。
- **SD 卡引脚不在任何 Apollo3 硬件 IOM 的 SPI pad 上**（CS=D27, MOSI=D26, SCK=D28, MISO=D25, CD=D22 是任意 GPIO 组合），因此必须用软件 SPI（`SoftSpiDriver`），且补丁必须全局打（见上文）。
- **烧录波特率 115200 ≠ 运行波特率 1,000,000**，串口监视器配错波特率是最常见的"看起来失败"原因。
- **`time_manger.cpp` 文件名拼写错误是真实的**，不要"顺手"改成 `time_manager.cpp`，否则会与 `time_manager.h` 的引用关系断开。
- **Dockerfile 与 CI/Makefile 用的板索引 URL 不同**（前者 `gauteh/omb-2024-nano`，后者官方 SparkFun），复现构建路径时注意区分，不要互相替换。
- **`#ifdef USE_IRIDIUM` 方向不要写反**——是 `#ifdef`（宏未定义则剔除调用），不是 `#ifndef`。
- **根目录 `Makefile` 的 `docker-run` target 会执行 `cp Makefile src/`**，产生的 `src/Makefile` 已在 `.gitignore` 中（`/src/Makefile`），这是预期行为，看到这个文件不需要处理。
- 上游固件的基础上电验证已有历史记录；本轮test1—test3使用采集版bin，不重复原先的上游→latest烧录顺序。

## 与 buoy 仓库的关系

PCB 硬件设计、BOM、组装说明、原理图级别的引脚/网络参考都在姊妹仓库 **`../omb-nano-extend`**（同一工作区，经 `buoy.code-workspace` 关联）。本仓库（`omb-nano`）不含任何硬件设计文件，只有固件。

- 涉及**哪些元件 DNP/已贴片、BOM 选型来源、外壳是否匹配、某个具体引脚在原理图上接到哪个网络**的问题 —— 去 `buoy` 仓库，具体看 `omb-nano-extend/.github/agents/pcb-hardware-expert.agent.md`（引脚级别的权威参考）和 `omb-nano-extend/buoy_build/2.pcb-procurement-and-assembly-guide.md`（组装/DNP 权威文档）。
- 涉及**固件逻辑、编译/烧录机制、代码里的引脚映射**的问题 —— 留在本仓库。
- 提示：`omb-nano-extend/buoy_build/buoy_notes.md`（研究日志）和 `omb-nano-extend/todo.md`（2026-06-25 的下一步计划，其内容大部分已在本仓库执行完成，见上方「已完成的工作」）都记录了本仓库里发生的工作，可作为叙事性的交叉印证，但本仓库自己的 `git log` 和 `docs/固件变更记录.md` 才是关于"这里到底做过什么"的权威记录。
