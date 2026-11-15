#!/usr/bin/env python3
"""DTR reset + 1 Mbps monitor. Usage: imu_listen.py [port] [seconds=60].
OMB_LOG_FILE chooses diagnostics .log; numeric IMU rows go to sibling .csv.
"""
import codecs
import glob
import math
import os
import re
import sys
import time
from datetime import datetime

import serial


def numeric_row(line):
    fields = line.split(',')
    if len(fields) != 9 or not all(re.fullmatch(r'\d+', v) for v in fields[:2]):
        return False
    try:
        return all(math.isfinite(float(v)) for v in fields[2:])
    except ValueError:
        return False


def compare_csv(computer_path, sd_path):
    with open(computer_path, newline='') as f:
        computer = f.read().splitlines()
    with open(sd_path, newline='') as f:
        card = f.read().splitlines()
    if not computer or not all(numeric_row(row) for row in computer + card):
        print('CSV为空或含有非9列数字行，请检查文件。')
        return 1
    if computer == card:
        print(f'一致：{len(computer)}行，9列，逐行完全相同。')
        return 0
    first = next((i + 1 for i, pair in enumerate(zip(computer, card)) if pair[0] != pair[1]),
                 min(len(computer), len(card)) + 1)
    print(f'不一致：电脑{len(computer)}行，SD {len(card)}行，首个差异在第{first}行。')
    return 1


def main():
    if len(sys.argv) == 4 and sys.argv[1] == '--compare':
        return compare_csv(sys.argv[2], sys.argv[3])
    duration = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    ports = sorted(glob.glob('/dev/cu.wchusbserial*') + glob.glob('/dev/cu.usbserial*') + glob.glob('/dev/cu.usbmodem*'))
    port = sys.argv[1] if len(sys.argv) > 1 else os.environ.get('OMB_PORT')
    if not port:
        if len(ports) != 1:
            print('请指定串口：python3 firmware/imu_listen.py /dev/cu.xxx')
            return 1
        port = ports[0]
    log_path = os.environ.get('OMB_LOG_FILE', os.path.join(os.path.dirname(__file__), 'imu_output.log'))
    csv_path = os.path.splitext(log_path)[0] + '.csv'
    row_count = 0
    legacy_count = 0
    values = []
    pending = ''
    complete = False
    sd_saved = False
    capture_mode = False
    expected_rows = None
    status = 0
    decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
    start = time.monotonic()
    with open(log_path, 'w') as log, open(csv_path, 'w', newline='') as csv:
        def emit(text):
            print(text, end='', flush=True)
            log.write(text)
            log.flush()
        emit(f'# {datetime.now().astimezone().isoformat()} | {port} | 1000000 bps | {duration}s\n')
        emit(f'# mode={os.environ.get("OMB_MODE", "manual")} | board firmware: see startup\n')
        try:
            with serial.Serial(port, 1000000, timeout=1) as ser:
                ser.dtr = True; time.sleep(0.05)
                ser.dtr = False; time.sleep(0.01)
                ser.dtr = True; time.sleep(0.01)
                ser.dtr = False; time.sleep(0.3)
                emit('[INFO] DTR reset done, listening...\n')
                start = time.monotonic()
                while time.monotonic() - start < duration:
                    data = ser.read(512)
                    if not data:
                        continue
                    text = decoder.decode(data)
                    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
                    emit(text)
                    pending += text
                    while '\n' in pending:
                        line, pending = pending.split('\n', 1)
                        line = line.rstrip('\r')
                        if numeric_row(line):
                            csv.write(line + '\n')
                            csv.flush()
                            row_count += 1
                            values.append(float(line.split(',')[-1]))
                        else:
                            match = re.search(r'\| nmeas (\d+)\s*\|\s*([-+\d.eE]+)', line)
                            if match:
                                legacy_count += 1
                                values.append(float(match.group(2)))
                        capture_mode |= line == 'imu_capture_only: 1'
                        if line.startswith('imu_capture_samples: '):
                            expected_rows = int(line.split(':', 1)[1])
                        if line in ('capture: IMU read failed', 'capture: SD not saved'):
                            status = 1
                        sd_saved |= line == 'capture: SD saved'
                        complete |= line == 'capture: complete'
                    if complete:
                        break
                if pending:
                    emit('\n[INFO] 串口末尾不完整行未写入CSV\n')
        except KeyboardInterrupt:
            status = 130
            emit('\n[INFO] 用户停止采集\n')
        except (serial.SerialException, OSError) as exc:
            status = 1
            emit(f'\n[ERROR] {exc}\n')
        if capture_mode and (not complete or row_count != expected_rows):
            status = status or 1
            emit(f'\n[ERROR] 采集不完整：收到{row_count}行，预期{expected_rows}，完成标记={complete}\n')
        emit(f'\n# received numeric rows={row_count}; legacy rows={legacy_count}; elapsed={time.monotonic()-start:.1f}s\n')
        emit(f'# capture complete={complete}; SD saved message={sd_saved}\n')
        if values:
            emit(f'# acc_D: min={min(values):.6f} max={max(values):.6f} mean={sum(values)/len(values):.6f}\n')
        emit(f'# CSV: {os.path.abspath(csv_path)}\n# log: {os.path.abspath(log_path)}\n')
    return status


if __name__ == '__main__':
    sys.exit(main())
