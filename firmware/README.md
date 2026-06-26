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

| 文件 | 用途 | 推荐度 |
|------|------|--------|
| `flash_macos.sh` | 一键编译(Docker)+烧录脚本 | 推荐 |
| `svl_flash_v2.py` | 改进版 SVL 烧录工具（容错+小帧） | 推荐 |
| `svl_flash.py` | 原版 SVL 烧录工具（DTR 脉冲触发） | 备用 |
| `hardware_test.py` | 硬件测试工具（替代 screen） | 推荐 |
| `imu_listen.py` | IMU 串口监听（DTR 复位+1Mbps） | 推荐 |
| `diagnose_bootloader.py` | bootloader 通信诊断 | 排障用 |
| `artemis_svl_with_reset.py` | 带硬复位的 SVL 工具 | 排障用 |
| `omb_nano_v0.2.0.bin` | 预编译固件 v0.2.0（出厂版） | 备用 |
| `omb_nano_v0.1.0.bin` | 预编译固件 v0.1.0 | 历史版 |

## 推荐使用流程

### 第 1 步：编译 + 烧录（一键）

```bash
cd ~/Downloads/omb-nano
conda activate pcb
bash firmware/flash_macos.sh
```

`flash_macos.sh` 会自动：Docker 编译 → 检测串口 → svl_flash.py 烧录。

### 第 2 步：单独烧录（已有 .bin 文件）

```bash
conda activate pcb
# 改进版（推荐，解决写 flash 时电源跌落复位问题）
python3 firmware/svl_flash_v2.py /dev/cu.wchusbserial1410 \
    -f src/build/tracker.ino.bin -b 115200 -v \
    --frame-size 32 --frame-delay 0.1

# 原版（如果改进版不工作）
python3 firmware/svl_flash.py /dev/cu.wchusbserial1410 \
    -f firmware/omb_nano_v0.2.0.bin -b 115200 -v
```

> **注意**：烧录需要外部 3.3V 供电（AMS1117-3.3V 模块接到 U5 的 VOUT/GND 焊盘）+ USB-C 连接。

### 第 3 步：硬件测试

```bash
conda activate pcb

# 测试 1：启动日志验证（30 秒，检测 SD/IMU/GNSS 关键词）
python3 firmware/hardware_test.py --test 1

# 测试 2：SD 卡数据写入验证（60 秒）
python3 firmware/hardware_test.py --test 2

# 测试 3：IMU 数据验证（120 秒）
python3 firmware/hardware_test.py --test 3

# 纯串口监视器模式
python3 firmware/hardware_test.py --monitor

# 保存日志到文件
python3 firmware/hardware_test.py --monitor --log session.log
```

> **提示**：测试 1 需要在脚本运行期间断开再接上 3.3V 电源触发重启，才能捕获启动日志。

### 第 4 步：IMU 数据采集

```bash
conda activate pcb
# DTR 复位板子 + 1Mbps 监听 45 秒 + 自动保存到 imu_output.log
python3 firmware/imu_listen.py
```

输出格式：`{时间ms} | nmeas {序号} | {acc_D}`，acc_D 为垂直加速度（m/s²），静止时约 9.81。

### 第 5 步：排障

```bash
# bootloader 通信诊断
python3 firmware/diagnose_bootloader.py /dev/cu.wchusbserial1410
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
- 改用 `hardware_test.py --monitor` 或 `imu_listen.py`

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
