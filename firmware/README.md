# OMB-Nano 固件工具链

当前test1—test3请按[三项实测指南](../../omb-nano-extend/experiments/01_Test_Guide.md)：明确烧录omb_nano_imu_capture.bin，再运行run_experiment.sh。实验输出在omb-nano-extend根目录experiments/；下方latest与run_all_steps.sh保留为历史上电调试工具，不作为本轮默认入口。

本目录包含 OMB-Nano 极地浮标 PCB 板的编译、烧录、测试工具。

## 环境要求

```bash
# 1. Conda 环境（pyserial，用于烧录和测试脚本）
conda create -n pcb python=3.11
conda activate pcb
pip install pyserial

# 2. Docker Desktop（用于编译固件，镜像名 omb-nano:latest）
#    Dockerfile 在项目根目录 ~/Documents/omb-nano/Dockerfile

# 3. CH340 USB 驱动（macOS 自带，板子通过 USB-C 连接）
```

## 文件清单

| 文件 | 用途 |
|------|------|
| `run_all_steps.sh` | 一键全流程：编译→烧录→IMU/SD 测试（历史流程） |
| `svl_flash_v2.py` | SVL 烧录工具（容错乱码+小帧+重试） |
| `imu_listen.py` | IMU 串口监听（DTR 复位+1Mbps，自动检测串口） |
| `hardware_test.py` | 硬件测试工具（替代 screen） |
| `diagnose_bootloader.py` | bootloader 通信诊断（排障用） |

### 预编译固件（.bin）

| 文件 | 大小 | 版本 | 说明 |
|------|------|------|------|
| `omb_nano_v0.1.0.bin` | 134496 B | v0.1.0 | **OMB 原版出厂固件**，未做任何修改，用于验证板子上电正常 |
| `omb_nano_v0.2.0.bin` | 134536 B | v0.2.0 | 上游更新版本，未做任何修改，建议第一步烧录此版本验证硬件 |
| `omb_nano_latest.bin` | 132648 B | latest | **我们的修改版**（TEST-MODE）：DEPLOYMENT_MODE=1、跳过 GNSS 循环、启用 IMU 调试块、新增 SD 卡 `write_test_file()` 写入测试。每次编译后自动覆盖 |

> **烧录顺序建议**：先用 `v0.2.0.bin` 验证板子上电正常 → 再烧录 `omb_nano_latest.bin` 测试 IMU 和 SD 卡功能。

## 实验1：桌面复查

运行 `bash firmware/run_experiment.sh --experiment test1_2026-11-14_land-static --source real`：检查环境、打开实验记录、按Enter默认监听90秒，收到完成标记后提前结束、保存并打开日志。说明和输出均放在 [实验1文件夹](../../omb-nano-extend/experiments/test1_2026-11-14_land-static/record.md)。仅检查环境加 `--check`。脚本不编译或烧录，板上配置以启动输出为准。

## 历史上电调试入口

```bash
cd ~/Documents/omb-nano
bash firmware/run_all_steps.sh
```

串口会自动检测。如果检测到多个，会提示选择。也可手动指定：

```bash
ls /dev/cu.wchusbserial14* # 查看串口编号
bash firmware/run_all_steps.sh --port /dev/cu.wchusbserial1410
```

跳过编译（已有 bin，只烧录+测试）：

```bash
bash firmware/run_all_steps.sh --skip-compile
```

跳过烧录（已烧录好，只看 IMU/SD 结果）：

```bash
bash firmware/run_all_steps.sh --skip-flash
```

## 手动分步操作

### 1. 编译（Docker，输出到 firmware/omb_nano_latest.bin）

```bash
cd ~/Documents/omb-nano
/Applications/Docker.app/Contents/Resources/bin/docker run --rm \
    -v "$(pwd):/tracker" "omb-nano:latest" \
    bash -lc "mkdir -p /tmp/tracker && cp -r /tracker/src/* /tmp/tracker/ && \
    cd /tmp/tracker && arduino-cli compile -e --output-dir /tracker/firmware \
    --build-cache-path /tmp/build -b SparkFun:apollo3:artemis ."
# arduino-cli 输出名为 tracker.ino.bin，重命名为 latest
mv firmware/tracker.ino.bin firmware/omb_nano_latest.bin
```

### 2. 烧录（约 7 分钟）

```bash
/opt/anaconda3/envs/pcb/bin/python3 firmware/svl_flash_v2.py /dev/cu.wchusbserial1410 \
    -f firmware/omb_nano_latest.bin -b 115200 -v --frame-size 32 --frame-delay 0.1
```

> **当前原型机**：U5未装，用户确认稳定3.3V经飞线接VOUT/VCC、GND；USB为CH340供电通信。电源转换器型号待登记，不能假定为AMS1117。见 [实物配置](../../omb-nano-extend/buoy_build/prototype-status.md)。

### 3. IMU + SD 卡数据采集（默认60秒）

```bash
/opt/anaconda3/envs/pcb/bin/python3 firmware/imu_listen.py
```

串口自动检测，也可手动指定：`python3 firmware/imu_listen.py /dev/cu.xxx`

输出格式：`{时间ms} | nmeas {序号} | {acc_D}`，acc_D 为垂直加速度（m/s²），静止时约 9.81。

旧监听器日志保存到：`firmware/imu_output.log`，每次覆盖。自动选择多个候选串口中的第一个，故多设备时必须手动指定。

2026-11-14：本次及后续实验说明和运行证据归 [自有项目测试区](../../omb-nano-extend/experiments/test1_2026-11-14_land-static/record.md)。`run_experiment.sh` 已支持将采集日志直接保存到实验文件夹。

### 4. 排障诊断

```bash
/opt/anaconda3/envs/pcb/bin/python3 firmware/diagnose_bootloader.py /dev/cu.wchusbserial1410
```

## 常见问题

### 烧录失败 "Failed to enter bootload phase"
- 检查外部 3.3V 是否接入 U5 VOUT/GND 焊盘
- 检查 USB-C 是否连接（CH340 通信需要）
- 用 `lsof /dev/cu.wchusbserial*` 检查串口是否被 screen 占用

### 烧录失败 "Error receiving packet"（数据传输出错）
- 使用 `svl_flash_v2.py`（改进版，自动跳过乱码字节）
- 降低帧大小：`--frame-size 16 --frame-delay 0.15`
- 检查 3.3V 电源稳定性（写 flash 时电流峰值可能导致复位）

### 串口乱码（screen 命令）
- 固件运行波特率 1,000,000 bps，screen 不稳定
- 改用 `imu_listen.py` 或 `hardware_test.py --monitor`

### SD 卡未检测到
- 检查 SD 卡是否为 FAT32 格式
- 检查 CARD1 焊接（引脚：CS=27, MOSI=26, SCK=28, MISO=25）
- 串口日志关键词：`SD: ready`（成功）/ `SD: begin failed`（失败）

## 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 固件运行波特率 | 1,000,000 bps | `params.h: baudrate_debug_serial` |
| 烧录波特率 | 115,200 bps | SVL bootloader 通信 |
| DEPLOYMENT_MODE | 1（测试） | `params.h`，100 样本约 10 秒 |
| IMU 采样率 | 10 Hz | 199 样本约 20 秒 |
| SD 卡文件系统 | FAT32 | SdFat 库，软件 SPI |

## 验证采集数据

新固件每次启动采600个10Hz真实IMU样本，不算波谱，采完停留。`imu_listen.py`输出诊断.log与同名数字.csv；SD在采集结束后生成IMU0000.CSV等文件。字段和操作见 [test1记录](../../omb-nano-extend/experiments/test1_2026-11-14_land-static/record.md)。

逐行比较：`python3 firmware/imu_listen.py --compare computer.csv IMU0000.CSV`。

当前采集固件：`firmware/omb_nano_imu_capture.bin`，95936字节；Docker amd64编译通过，尚未烧录。不要用旧的latest文件代替本次采集固件。Apple Silicon构建镜像时使用 `docker build --platform linux/amd64 -t omb-nano:latest .`。
