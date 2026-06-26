#!/usr/bin/env bash
set -euo pipefail

IMAGE_NAME="omb-nano-env"
FQBN="SparkFun:apollo3:artemis"
CORE_SPEC="Sparkfun:apollo3@1.2.1"
BOARD_URL="https://raw.githubusercontent.com/gauteh/omb-2024-nano/refs/heads/main/misc/package_sparkfun_apollo3_index.json"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PORT=""
BIN_FILE=""
SKIP_DOCKER_BUILD="false"
SKIP_COMPILE="false"
REBUILD_IMAGE="false"
BAUD=""

usage() {
    cat <<'EOF'
One-click compile + flash for omb-nano on macOS.

Usage:
  ./firmware/flash_macos.sh [options]

Options:
  --port <device>          Serial port, e.g. /dev/tty.usbserial-0001
  --bin <path>             Bin file path (default: src/build/tracker.ino.bin)
  --image <name>           Docker image name (default: omb-nano-env)
  --skip-docker-build      Do not run docker build
  --rebuild-image          Always rebuild the docker image
  --skip-compile           Skip docker compile and only upload
  --baud <rate>            SVL bootloader baud rate (default: 921600, use 115200 for more stability)
  -h, --help               Show this help

Examples:
  ./firmware/flash_macos.sh
  ./firmware/flash_macos.sh --port /dev/tty.usbserial-0001
  ./firmware/flash_macos.sh --skip-compile --bin firmware/omb_nano_v0.2.0.bin
EOF
}

log_step() {
    printf "\n==> %s\n" "$1"
}

die() {
    printf "\n[ERROR] %s\n" "$1" >&2
    exit 1
}

resolve_bin_path() {
    local candidate="$1"
    if [[ -f "$candidate" ]]; then
        printf "%s" "$candidate"
        return
    fi

    if [[ -f "${REPO_ROOT}/${candidate}" ]]; then
        printf "%s" "${REPO_ROOT}/${candidate}"
        return
    fi

    die "Bin file not found: ${candidate}"
}

pick_port() {
    local ports=()
    shopt -s nullglob
    ports=(/dev/cu.wchusbserial* /dev/cu.usbserial* /dev/cu.usbmodem*)
    shopt -u nullglob

    if [[ ${#ports[@]} -eq 0 ]]; then
        die "No serial port detected. Check USB cable/CH340 driver, then try again."
    fi

    if [[ ${#ports[@]} -eq 1 ]]; then
        printf "%s" "${ports[0]}"
        return
    fi

    printf "Detected multiple serial ports:\n"
    local i
    for i in "${!ports[@]}"; do
        printf "  [%s] %s\n" "$i" "${ports[$i]}"
    done
    printf "Choose index: "
    local idx
    read -r idx

    if ! [[ "$idx" =~ ^[0-9]+$ ]] || (( idx < 0 || idx >= ${#ports[@]} )); then
        die "Invalid port selection"
    fi

    printf "%s" "${ports[$idx]}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            PORT="$2"
            shift 2
            ;;
        --bin)
            BIN_FILE="$2"
            shift 2
            ;;
        --image)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --skip-docker-build)
            SKIP_DOCKER_BUILD="true"
            shift
            ;;
        --rebuild-image)
            REBUILD_IMAGE="true"
            shift
            ;;
        --skip-compile)
            SKIP_COMPILE="true"
            shift
            ;;
        --baud)
            BAUD="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

if [[ "$SKIP_COMPILE" == "false" ]]; then
    if ! command -v docker >/dev/null 2>&1; then
        die "docker not found. Install Docker Desktop first, or pass --skip-compile to upload a prebuilt bin."
    fi

    log_step "Checking Docker daemon"
    docker info >/dev/null 2>&1 || die "Docker daemon is not running. Start Docker Desktop first."

    if [[ "$SKIP_DOCKER_BUILD" == "false" ]]; then
        if [[ "$REBUILD_IMAGE" == "true" ]]; then
            log_step "Rebuilding docker image: ${IMAGE_NAME}"
            docker build -t "$IMAGE_NAME" "$REPO_ROOT"
        else
            if docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
                log_step "Docker image exists: ${IMAGE_NAME}"
            else
                log_step "Building docker image: ${IMAGE_NAME}"
                docker build -t "$IMAGE_NAME" "$REPO_ROOT"
            fi
        fi
    fi

    log_step "Compiling firmware in docker"
    docker run --rm \
        -v "${REPO_ROOT}:/tracker" \
        "$IMAGE_NAME" \
        bash -lc "cd /tracker/src && arduino-cli compile -e --output-dir build --build-cache-path /tmp/build -v -b ${FQBN} ."
fi

if [[ -z "$BIN_FILE" ]]; then
    BIN_FILE="${REPO_ROOT}/src/build/tracker.ino.bin"
else
    BIN_FILE="$(resolve_bin_path "$BIN_FILE")"
fi

[[ -f "$BIN_FILE" ]] || die "Compiled bin not found at ${BIN_FILE}"

if ! command -v arduino-cli >/dev/null 2>&1; then
    die "arduino-cli not found on host. Install it first (for example: brew install arduino-cli)."
fi

log_step "Ensuring SparkFun Apollo3 core is installed on host"
if arduino-cli core list | grep -qi "sparkfun:apollo3"; then
    printf "Core already installed.\n"
else
    arduino-cli core update-index --additional-urls "$BOARD_URL"
    arduino-cli core install "$CORE_SPEC" --additional-urls "$BOARD_URL"
fi

if [[ -z "$PORT" ]]; then
    log_step "Detecting serial port"
    PORT="$(pick_port)"
fi

[[ -e "$PORT" ]] || die "Serial port not found: ${PORT}"

log_step "Uploading firmware"

# OMB-Nano PCB 没有物理复位按钮，CH340 的 DTR 脉冲用于触发 RC 电路进入 bootloader。
# 原始 artemis_svl 不发送 DTR 脉冲，必须使用 svl_flash.py。
SVL_FLASH="${SCRIPT_DIR}/svl_flash.py"
BAUD_VAL="${BAUD:-115200}"

# macOS 串口设备：cu.* 比 tty.* 更适合 pyserial
PORT_CU="${PORT/tty./cu.}"

printf "Port : %s\n" "$PORT_CU"
printf "Bin  : %s\n" "$BIN_FILE"
printf "Baud : %s\n" "$BAUD_VAL"
printf "Tool : svl_flash.py (with DTR pulse)\n"

if [[ ! -f "$SVL_FLASH" ]]; then
    die "svl_flash.py not found at ${SVL_FLASH}"
fi

# 检查 pyserial 是否可用
if ! python3 -c "import serial" 2>/dev/null; then
    printf "pyserial not found. Trying conda pcb environment...\n"
    if command -v conda >/dev/null 2>&1; then
        eval "$(conda shell.bash hook)"
        conda activate pcb 2>/dev/null || die "Failed to activate conda env 'pcb'. Run: conda create -n pcb python=3.11 -y && conda activate pcb && pip install pyserial"
    else
        die "pyserial not installed and conda not available. Install with: pip3 install pyserial"
    fi
fi

python3 "$SVL_FLASH" "$PORT_CU" -f "$BIN_FILE" -b "$BAUD_VAL" -v
upload_status=$?

if [[ $upload_status -ne 0 ]]; then
    die "svl_flash.py failed with exit code ${upload_status}. Check: 1) USB-C connected, 2) external 3.3V to U5 VOUT, 3) correct serial port."
fi

printf "\nFlash complete. Open serial monitor at 1000000 bps to verify logs.\n"
