#!/usr/bin/env bash
# 通用串口实验：检查环境 → 打开说明 → 采集串口 → 保存并打开日志。
# bash firmware/run_experiment.sh --experiment 日期_用途 [--source real|synthetic|any] [--port /dev/cu.xxx] [--seconds 90] [--check] [--no-open]
set -euo pipefail
repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
experiment_name=""
expected_source=any
port=""
seconds=90
check_only=0
open_files=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --experiment) experiment_name="${2:?请填写实验文件夹名称}"; shift 2 ;;
        --source) expected_source="${2:?请填写real、synthetic或any}"; shift 2 ;;
        --port) port="${2:?请在--port后填写串口}"; shift 2 ;;
        --seconds) seconds="${2:?请填写监听秒数}"; shift 2 ;;
        --check) check_only=1; shift ;;
        --no-open) open_files=0; shift ;;
        -h|--help) echo '用法：bash firmware/run_experiment.sh --experiment 日期_用途 [--source real|synthetic|any] [--port /dev/cu.xxx] [--seconds 90] [--check] [--no-open]'; exit 0 ;;
        *) echo "未知参数：$1" >&2; exit 1 ;;
    esac
done
[[ -n "$experiment_name" && "$experiment_name" != */* && "$experiment_name" != .* ]] || { echo '请用--experiment指定实验文件夹，例如test1_2026-11-14_land-static'; exit 1; }
case "$expected_source" in real|synthetic|any) ;; *) echo '--source应为real、synthetic或any'; exit 1 ;; esac
experiment_dir="${OMB_EXPERIMENT_DIR:-$(dirname "$repo_dir")/omb-nano-extend/experiments/$experiment_name}"
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo '监听秒数需为正整数'; exit 1; }
[[ -f "$experiment_dir/record.md" ]] || { echo "找不到实验记录：$experiment_dir/record.md"; exit 1; }

echo '[1/3] 检查Python、串口库和实验路径'
python_bin="${OMB_PYTHON:-$HOME/Library/Caches/omb-nano-test/venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
    bootstrap_python="$(command -v python3 || true)"
    [[ -n "$bootstrap_python" ]] || { echo '请先安装Python 3'; exit 1; }
    echo '首次使用：建立独立Python环境'
    "$bootstrap_python" -m venv "$HOME/Library/Caches/omb-nano-test/venv"
    python_bin="$HOME/Library/Caches/omb-nano-test/venv/bin/python"
fi
if ! "$python_bin" -c 'import serial' 2>/dev/null; then
    echo '首次使用：安装串口库pyserial'
    "$python_bin" -m pip install pyserial
fi
echo "Python：$python_bin"
echo "实验记录：$experiment_dir/record.md"
echo '本机源码配置（板上实际配置以启动输出为准）：'
"$python_bin" - "$repo_dir/src/params.h" <<'PY'
import re,sys
text=open(sys.argv[1]).read()
for pattern in [r'^#define DEPLOYMENT_MODE\s+\d+',r'^constexpr bool use_dummy_imu_data[^;]+;',r'^constexpr bool imu_capture_only[^;]+;',r'^constexpr size_t imu_capture_samples[^;]+;']:
    for match in re.findall(pattern,text,re.M): print('  '+match)
PY
shopt -s nullglob
ports=(/dev/cu.wchusbserial* /dev/cu.usbserial* /dev/cu.usbmodem*)
shopt -u nullglob
if [[ ${#ports[@]} -gt 0 ]]; then
    printf '检测到串口：'; printf ' %s' "${ports[@]}"; echo
else
    echo '未检测到串口；连接USB后再采集。'
fi
if [[ "$check_only" == 1 ]]; then
    echo '环境检查完成，未打开串口或启动实验。'
    exit 0
fi
if [[ -z "$port" ]]; then
    if [[ ${#ports[@]} -eq 0 ]]; then
        echo '未发现串口，请检查USB连接。'; exit 1
    elif [[ ${#ports[@]} -eq 1 ]]; then
        port="${ports[0]}"
    else
        echo '多个串口，请选择：'
        for i in "${!ports[@]}"; do echo "  $((i+1))) ${ports[$i]}"; done
        read -r -p '序号：' choice
        [[ "$choice" =~ ^[1-9][0-9]*$ ]] && ((choice <= ${#ports[@]})) || exit 1
        port="${ports[$((choice-1))]}"
    fi
fi
if [[ "$open_files" == 1 ]] && command -v open >/dev/null; then
    open -t "$experiment_dir/record.md" || true
fi

echo "[2/3] 开始实验：$experiment_name"
echo '按实验record.md完成接线和摆放。'
echo "串口：${port}；监听：${seconds}秒；波特率：1000000。"
echo '开始后会通过DTR重启板子。Ctrl+C可停止。'
read -r -p '按Enter开始，输入q退出：' answer
[[ -z "$answer" ]] || exit 0
log_file="$experiment_dir/$(date +%Y%m%d_%H%M%S)_$$.log"
export OMB_LOG_FILE="$log_file"
export OMB_MODE="$experiment_name"
# 无法从电脑上的bin文件名推断板上固件版本。
export OMB_BIN='unknown (see board startup output)'
status=0
"$python_bin" -u "$repo_dir/firmware/imu_listen.py" "$port" "$seconds" || status=$?
echo '[3/3] 保存结果'
echo "日志：$log_file"
echo "数字数据：${log_file%.log}.csv"
source_seen=unknown
if [[ -f "$log_file" ]]; then
    if grep -Eq 'use_dummy_imu_data[[:space:]]*:[[:space:]]*1|WARNING: use test dummy acceleration signal' "$log_file"; then
        source_seen=synthetic
    elif grep -Eq 'use_dummy_imu_data[[:space:]]*:[[:space:]]*0|use true IMU data' "$log_file"; then
        source_seen=real
    fi
fi
result="采集退出码：${status}；数据来源：${source_seen}；预期：${expected_source}。"
if [[ "$expected_source" != any && "$source_seen" != "$expected_source" ]]; then
    result="$result 数据来源未匹配，请查看启动配置。"
    [[ "$status" != 0 ]] || status=2
fi
echo "$result"
source_commit="$(git -C "$repo_dir" rev-parse --short HEAD)"
source_state=clean
[[ -z "$(git -C "$repo_dir" status --porcelain)" ]] || source_state=modified
{
    printf '\n### %s\n\n日志：[%s](%s)。%s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$(basename "$log_file")" "$(basename "$log_file")" "$result"
    printf '\n电脑源码：`%s`（%s）；板上版本和配置见启动日志。\n' "$source_commit" "$source_state"
    printf '\n数字数据：[%s](%s)。\n' "$(basename "${log_file%.log}.csv")" "$(basename "${log_file%.log}.csv")"
    printf '\n本次日志中的配置：\n```text\n'
    if [[ -f "$log_file" ]]; then
        grep -E 'DEPLOYMENT_MODE:|use_dummy_imu_data:|using total number of samples:|imu_capture_only:|imu_capture_samples:|version :' "$log_file" | tr -d '\r' || true
    fi
    printf '\n```\n'
} >> "$experiment_dir/record.md"
if [[ "$open_files" == 1 && -f "$log_file" ]] && command -v open >/dev/null; then
    open -t "$log_file" || true
fi
exit "$status"
