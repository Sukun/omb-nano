#!/usr/bin/env bash
# OMB-Nano 全流程测试脚本（编译 → 烧录 → IMU/SD 测试）
# 用法：
#   cd ~/Downloads/omb-nano && bash firmware/run_all_steps.sh          # 交互式菜单
#   cd ~/Downloads/omb-nano && bash firmware/run_all_steps.sh --full   # 编译+烧录+测试
#   cd ~/Downloads/omb-nano && bash firmware/run_all_steps.sh --flash  # 烧录+测试（用现有 bin）
#   cd ~/Downloads/omb-nano && bash firmware/run_all_steps.sh --test   # 仅测试（跳过编译和烧录）
#   cd ~/Downloads/omb-nano && bash firmware/run_all_steps.sh --port /dev/cu.xxx --test
#   cd ~/Downloads/omb-nano && bash firmware/run_all_steps.sh --diagnose        # 含 bootloader 诊断
set -uo pipefail

cd "$(dirname "$0")/.."

PY="/opt/anaconda3/envs/pcb/bin/python3"
DOCKER="/Applications/Docker.app/Contents/Resources/bin/docker"
FQBN="SparkFun:apollo3:artemis"
BIN="firmware/omb_nano_latest.bin"
PORT=""
DIAGNOSE="false"
# 模式：full / flash / test
MODE=""

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port) PORT="$2"; shift 2 ;;
        --full)      MODE="full";  shift ;;
        --flash)     MODE="flash"; shift ;;
        --test)      MODE="test";  shift ;;
        # 向后兼容
        --skip-flash)     MODE="test";  shift ;;
        --skip-compile)   MODE="flash"; shift ;;
        --diagnose) DIAGNOSE="true"; shift ;;
        *) echo "未知参数: $1"; exit 1 ;;
    esac
done

# 交互式选择模式
if [[ -z "$MODE" ]]; then
    echo "========================================"
    echo "  OMB-Nano 测试流程"
    echo "========================================"
    echo "  [1] 编译 + 烧录 + 测试（完整流程）"
    echo "  [2] 烧录 + 测试（使用现有 bin）"
    echo "  [3] 仅测试（跳过编译和烧录）"
    echo "  [0] 退出"
    echo "========================================"
    printf "选择 [0-3]: "
    read -r choice
    case "$choice" in
        1) MODE="full"  ;;
        2) MODE="flash" ;;
        3) MODE="test"  ;;
        0) echo "已退出。"; exit 0 ;;
        *) echo "无效选择: $choice"; exit 1 ;;
    esac
fi

echo "[INFO] 模式: $MODE"

# 自动检测串口（如果未指定）
if [[ -z "$PORT" ]]; then
    shopt -s nullglob
    ports=(/dev/cu.wchusbserial* /dev/cu.usbserial* /dev/cu.usbmodem*)
    shopt -u nullglob
    if [[ ${#ports[@]} -eq 0 ]]; then
        echo "[ERROR] 未检测到串口。检查 USB-C 连接，或手动指定：--port /dev/cu.xxx"
        exit 1
    fi
    if [[ ${#ports[@]} -eq 1 ]]; then
        PORT="${ports[0]}"
    else
        echo "检测到多个串口："
        for i in "${!ports[@]}"; do echo "  [$i] ${ports[$i]}"; done
        printf "选择序号: "; read -r idx
        PORT="${ports[$idx]}"
    fi
fi
echo "[INFO] 串口: $PORT"

# ============================================================
# 步骤 1：编译（Docker 直接输出到 firmware/）
# ============================================================
if [[ "$MODE" == "full" ]]; then
    echo ""
    echo "========================================"
    echo "步骤 1：编译（Docker）"
    echo "========================================"
    "$DOCKER" run --rm \
        -v "$(pwd):/tracker" \
        "omb-nano:latest" \
        bash -lc "mkdir -p /tmp/tracker && cp -r /tracker/src/* /tmp/tracker/ && cd /tmp/tracker && arduino-cli compile -e --output-dir /tracker/firmware --build-cache-path /tmp/build -b ${FQBN} . 2>&1 | tail -5"
    # arduino-cli 输出名为 tracker.ino.bin（基于 sketch 名），重命名为版本号
    if [[ -f "firmware/tracker.ino.bin" ]]; then
        mv "firmware/tracker.ino.bin" "$BIN"
    fi
    if [[ ! -f "$BIN" ]]; then
        echo "[ERROR] 编译失败，未生成 $BIN"
        exit 1
    fi
    BIN_SIZE=$(stat -f%z "$BIN")
    echo "[OK] 编译完成 -> $BIN ($BIN_SIZE bytes)"
else
    echo "[INFO] 跳过编译，使用已有 $BIN"
fi

# ============================================================
# 步骤 2：烧录（svl_flash_v2.py，约 7 分钟）
# ============================================================
if [[ "$MODE" == "test" ]]; then
    echo ""
    echo "[INFO] 跳过烧录，直接测试"
else
    echo ""
    echo "========================================"
    echo "步骤 2：烧录（svl_flash_v2.py）"
    echo "========================================"
    "$PY" firmware/svl_flash_v2.py "$PORT" -f "$BIN" -b 115200 -v --frame-size 32 --frame-delay 0.1
    if [[ $? -ne 0 ]]; then
        echo "[ERROR] 烧录失败"
        exit 1
    fi
    echo "[OK] 烧录完成"
fi

# ============================================================
# 步骤 3：IMU + SD 卡数据采集（imu_listen.py，60 秒）
# ============================================================
echo ""
echo "========================================"
echo "步骤 3：IMU + SD 卡数据采集（60 秒）"
echo "========================================"
# 传入环境变量给 imu_listen.py（写入日志头）
export OMB_BIN="$BIN"
export OMB_MODE="$MODE"
"$PY" firmware/imu_listen.py "$PORT" 60
echo "[OK] 日志 -> firmware/imu_output.log"

# ============================================================
# 步骤 4：bootloader 诊断（仅 --diagnose 时执行）
# ============================================================
if [[ "$DIAGNOSE" == "true" ]]; then
    echo ""
    echo "========================================"
    echo "步骤 4：bootloader 诊断（排障用）"
    echo "========================================"
    "$PY" firmware/diagnose_bootloader.py "$PORT" 2>&1 | head -20
fi

# ============================================================
echo ""
echo "========================================"
echo "全部完成。"
echo "日志文件（绝对路径）："
echo "  $(pwd)/firmware/imu_output.log"
echo "========================================"
