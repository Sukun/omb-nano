#!/usr/bin/env python3
"""imu_listen.py - DTR 复位板子后用 1Mbps 监听串口，捕获 IMU 输出"""
import serial, time, sys

PORT = '/dev/cu.wchusbserial1410'
BAUD = 1000000
DURATION = 45
LOG = '/Users/sukeng/Downloads/buoy/firmware/imu_output.log'

ser = serial.Serial(PORT, BAUD, timeout=1)
print(f'[INFO] {PORT} @ {BAUD} bps')

# DTR 复位脉冲（触发板子重启）
ser.dtr = True; time.sleep(0.05)
ser.dtr = False; time.sleep(0.01)
ser.dtr = True; time.sleep(0.01)
ser.dtr = False; time.sleep(0.01)
time.sleep(0.3)
print('[INFO] DTR reset done, listening...')

start = time.time()
with open(LOG, 'w') as f:
    while time.time() - start < DURATION:
        data = ser.read(512)
        if data:
            text = data.decode('utf-8', errors='replace')
            print(text, end='', flush=True)
            f.write(text)
            f.flush()

ser.close()
print(f'\n[INFO] done, log -> {LOG}')
