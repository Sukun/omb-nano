# OMB-Nano 固件工具链

本目录包含 OMB-Nano 极地浮标 PCB 板的编译、烧录、测试工具。

## 环境要求

```bash
# 1. Conda 环境（pyserial，用于烧录和测试脚本）
conda create -n pcb python=3.11
conda activate pcb
pip install pyserial

# 2. Docker Desktop（用于编译固件，镜像名 omb-nano:latest）
#    Dockerfile 在项目根目录 ~/Downloads/omb-nano/Dockerfile

# 3. CH340 USB 驱动（macOS 自带，板子通过 USB-C 连接）
```

## 文件清单

| 文件 | 用途 |
|------|------|
| `run_all_steps.sh` | 一键全流程：编译→烧录→IMU/SD 测试（**推荐**） |
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

## 快速开始

```bash
cd ~/Downloads/omb-nano
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
cd ~/Downloads/omb-nano
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

> **注意**：烧录需要外部 3.3V 供电（AMS1117-3.3V 模块接到 U5 的 VOUT/GND 焊盘）+ USB-C 连接。

### 3. IMU + SD 卡数据采集（45 秒）

```bash
/opt/anaconda3/envs/pcb/bin/python3 firmware/imu_listen.py
```

串口自动检测，也可手动指定：`python3 firmware/imu_listen.py /dev/cu.xxx`

输出格式：`{时间ms} | nmeas {序号} | {acc_D}`，acc_D 为垂直加速度（m/s²），静止时约 9.81。

日志保存到：`firmware/imu_output.log`

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
