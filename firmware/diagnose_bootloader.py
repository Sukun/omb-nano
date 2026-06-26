#!/usr/bin/env python3
"""
diagnose_bootloader.py - 诊断 Artemis bootloader 响应

测试不同的方法来触发和检测 SVL bootloader。
"""

import serial
import time
import sys


def test_method(port, method_name, baud, trigger_func):
    """测试一种触发方法"""
    print(f"\n{'='*60}")
    print(f"测试方法: {method_name}")
    print(f"波特率: {baud}")
    print(f"{'='*60}")

    try:
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.5)
        time.sleep(0.1)

        # 执行触发函数
        trigger_func(ser)

        # 清空缓冲区
        ser.reset_input_buffer()

        # 发送波特率检测字符 'U'
        print("发送 'U' 字符...")
        ser.write(b'U')

        # 等待响应
        time.sleep(0.3)

        # 读取响应
        response = ser.read(64)
        print(f"收到 {len(response)} 字节响应")
        if response:
            print(f"  原始数据: {response.hex()}")
            print(f"  ASCII: {response}")

            # 检查是否是 SVL 版本包（2字节长度 + payload）
            if len(response) >= 2:
                pkt_len = int.from_bytes(response[:2], byteorder='big')
                print(f"  如果是 SVL 包，长度字段 = {pkt_len}")

        ser.close()
        return len(response) > 0

    except Exception as e:
        print(f"错误: {e}")
        return False


def trigger_rts_low_high(ser):
    """RTS: HIGH → LOW → HIGH"""
    print("触发: RTS HIGH → LOW (10ms) → HIGH (10ms) → LOW")
    ser.rts = True
    time.sleep(0.05)
    ser.rts = False
    time.sleep(0.01)
    ser.rts = True
    time.sleep(0.01)
    ser.rts = False
    time.sleep(0.2)


def trigger_rts_high_low(ser):
    """RTS: LOW → HIGH → LOW"""
    print("触发: RTS LOW → HIGH (10ms) → LOW (10ms)")
    ser.rts = False
    time.sleep(0.05)
    ser.rts = True
    time.sleep(0.01)
    ser.rts = False
    time.sleep(0.2)


def trigger_dtr_pulse(ser):
    """DTR 脉冲"""
    print("触发: DTR 脉冲")
    ser.dtr = True
    time.sleep(0.05)
    ser.dtr = False
    time.sleep(0.01)
    ser.dtr = True
    time.sleep(0.01)
    ser.dtr = False
    time.sleep(0.2)


def trigger_both(ser):
    """同时 RTS 和 DTR"""
    print("触发: RTS + DTR 同时脉冲")
    ser.rts = True
    ser.dtr = True
    time.sleep(0.05)
    ser.rts = False
    ser.dtr = False
    time.sleep(0.01)
    ser.rts = True
    ser.dtr = True
    time.sleep(0.01)
    ser.rts = False
    ser.dtr = False
    time.sleep(0.2)


def trigger_none(ser):
    """不触发，直接测试"""
    print("触发: 无（仅打开串口）")
    time.sleep(0.2)


def listen_only(ser):
    """只监听，不发送"""
    print("监听模式：不发送任何数据，只读取")
    time.sleep(0.5)
    data = ser.read(256)
    print(f"收到 {len(data)} 字节")
    if data:
        print(f"  原始数据: {data.hex()}")
        try:
            print(f"  ASCII: {data.decode('ascii', errors='replace')}")
        except:
            pass


def main():
    if len(sys.argv) < 2:
        print("用法: python3 diagnose_bootloader.py <port>")
        print("示例: python3 diagnose_bootloader.py /dev/cu.wchusbserial1410")
        sys.exit(1)

    port = sys.argv[1]

    # 测试 1：监听启动数据（不触发）
    print("\n" + "="*60)
    print("测试 1: 监听板子启动输出（不发送任何数据）")
    print("="*60)
    try:
        ser = serial.Serial(port=port, baudrate=115200, timeout=0.5)
        time.sleep(0.1)
        print("打开串口，等待 1 秒...")
        time.sleep(1)
        data = ser.read(256)
        print(f"收到 {len(data)} 字节")
        if data:
            print(f"  原始数据: {data.hex()}")
            try:
                print(f"  ASCII: {data.decode('ascii', errors='replace')}")
            except:
                pass
        ser.close()
    except Exception as e:
        print(f"错误: {e}")

    # 测试 2：不同波特率监听
    for baud in [9600, 115200, 921600, 1000000]:
        print(f"\n--- 波特率 {baud} ---")
        try:
            ser = serial.Serial(port=port, baudrate=baud, timeout=0.5)
            time.sleep(0.1)
            time.sleep(0.5)
            data = ser.read(256)
            print(f"收到 {len(data)} 字节")
            if data:
                print(f"  原始数据: {data.hex()}")
                try:
                    decoded = data.decode('ascii', errors='replace')
                    print(f"  ASCII: {decoded}")
                except:
                    pass
            ser.close()
        except Exception as e:
            print(f"错误: {e}")

    # 测试 3：不同触发方法 + 发送 'U'
    methods = [
        ("无触发", 115200, trigger_none),
        ("RTS HIGH→LOW→HIGH", 115200, trigger_rts_low_high),
        ("RTS LOW→HIGH→LOW", 115200, trigger_rts_high_low),
        ("DTR 脉冲", 115200, trigger_dtr_pulse),
        ("RTS + DTR 同时", 115200, trigger_both),
    ]

    for name, baud, func in methods:
        test_method(port, name, baud, func)

    # 测试 4：921600 波特率
    print("\n" + "="*60)
    print("测试 4: 921600 波特率 + RTS 脉冲")
    print("="*60)
    test_method(port, "RTS HIGH→LOW→HIGH @ 921600", 921600, trigger_rts_low_high)

    print("\n" + "="*60)
    print("诊断完成")
    print("="*60)
    print("\n如果所有测试都收到 0 字节响应：")
    print("  1. 检查 CH340 RTS 是否真的产生信号（用万用表或示波器）")
    print("  2. 检查 C35, C36, R14, R20 焊接")
    print("  3. 芯片可能确实没有 bootloader")


if __name__ == '__main__':
    main()
