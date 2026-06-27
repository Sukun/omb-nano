#!/usr/bin/env python3
"""imu_listen.py - DTR 复位板子后用 1Mbps 监听串口，捕获 IMU 输出

用法：
  python3 imu_listen.py                              # 自动检测串口，60 秒
  python3 imu_listen.py /dev/cu.wchusbserial1410     # 指定串口
  python3 imu_listen.py /dev/cu.wchusbserial1410 90  # 指定串口 + 90 秒

环境变量（由 run_all_steps.sh 自动传入，也可手动设置）：
  OMB_BIN   固件文件名（写入日志头）
  OMB_MODE  运行模式（skip-compile / skip-flash / full）
"""
import serial, time, sys, os, glob, re
from datetime import datetime

BAUD = 1000000
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 60
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(SCRIPT_DIR, 'imu_output.log')

# 串口选择：命令行参数 > 环境变量 > 自动检测
if len(sys.argv) > 1:
    PORT = sys.argv[1]
elif os.environ.get('OMB_PORT'):
    PORT = os.environ['OMB_PORT']
else:
    candidates = sorted(glob.glob('/dev/cu.wchusbserial*') + glob.glob('/dev/cu.usbserial*'))
    if not candidates:
        print('[ERROR] 未检测到串口。用法: python3 imu_listen.py /dev/cu.xxx')
        sys.exit(1)
    PORT = candidates[0]

# 日志头信息
BIN_NAME = os.environ.get('OMB_BIN', 'unknown')
RUN_MODE = os.environ.get('OMB_MODE', 'manual')
START_TS = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

ser = serial.Serial(PORT, BAUD, timeout=1)
print(f'[INFO] {PORT} @ {BAUD} bps, listening {DURATION}s')

# 写日志头
header = f"""{'=' * 60}
# OMB-Nano IMU/SD 测试日志
# 时间: {START_TS}
# 串口: {PORT}
# 波特率: {BAUD}
# 监听时长: {DURATION}s
# 固件: {BIN_NAME}
# 模式: {RUN_MODE}
{'=' * 60}
"""

with open(LOG, 'w') as f:
    f.write(header)
print(header, end='')

# DTR 复位脉冲（触发板子重启）
ser.dtr = True; time.sleep(0.05)
ser.dtr = False; time.sleep(0.01)
ser.dtr = True; time.sleep(0.01)
ser.dtr = False; time.sleep(0.01)
time.sleep(0.3)
print('[INFO] DTR reset done, listening...')

# 采集数据
sample_count = 0
sd_wrote = False
all_text = []

start = time.time()
with open(LOG, 'a') as f:
    while time.time() - start < DURATION:
        data = ser.read(512)
        if data:
            # 过滤二进制/null 字节，只保留可打印文本
            text = data.decode('utf-8', errors='replace')
            # 移除 null 字节和其他控制字符（保留换行和制表符）
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
            if text.strip():
                print(text, end='', flush=True)
                f.write(text)
                f.flush()
                all_text.append(text)
                # 统计 IMU 样本数
                if '| nmeas' in text:
                    sample_count = max(sample_count, text.count('| nmeas'))
                # 检测 SD 写入
                if 'SD: wrote' in text or 'TEST.TXT' in text:
                    sd_wrote = True

ser.close()

# 写日志摘要
elapsed = time.time() - start
full_text = ''.join(all_text)
# 提取最后一个 nmeas 序号
nmeas_nums = re.findall(r'nmeas (\d+)', full_text)
last_nmeas = int(nmeas_nums[-1]) + 1 if nmeas_nums else 0
# 提取 acc_D 值
acc_vals = re.findall(r'\|\s+([\d.]+)\s*$', full_text, re.MULTILINE)
acc_summary = ''
if acc_vals:
    acc_floats = [float(v) for v in acc_vals if v != 'nan']
    if acc_floats:
        acc_min = min(acc_floats)
        acc_max = max(acc_floats)
        acc_mean = sum(acc_floats) / len(acc_floats)
        acc_summary = f'# acc_D: min={acc_min:.2f} max={acc_max:.2f} mean={acc_mean:.2f} (n={len(acc_floats)})'

footer = f"""
{'=' * 60}
# 采集摘要
# 实际时长: {elapsed:.1f}s
# IMU 样本数: {last_nmeas}
# SD 卡写入: {'成功 (TEST.TXT)' if sd_wrote else '未检测到'}
{acc_summary}
# 日志路径: {LOG}
{'=' * 60}
"""

with open(LOG, 'a') as f:
    f.write(footer)
print(footer, end='')
