#!/usr/bin/env python3
"""
artemis_svl_with_reset.py

这是 SparkFun artemis_svl 的包装脚本，添加了 RTS 脉冲控制来触发 Artemis 的 bootloader。

原始 artemis_svl 不会主动控制 RTS 信号，依赖用户手动按复位按钮或串口打开时的默认 RTS 变化。
对于没有复位按钮的 PCB，需要主动产生 RTS 脉冲来触发 RC 电路（C35/C36）复位芯片并拉高 BOOT 引脚。

电路原理：
- CH340 RTS → C35 (100nF) → NRESET (R14 100kΩ 上拉到 VCC)
- CH340 RTS → C36 (100nF) → BOOT   (R20 220kΩ 下拉到 GND)

触发序列：
1. RTS = LOW  (产生下降沿 → NRESET 拉低 → 复位)
2. 等待 10ms (让复位完成)
3. RTS = HIGH (产生上升沿 → BOOT 拉高 → 进入 bootloader)
4. 等待 150ms (让 bootloader 启动)
5. 调用原始 artemis_svl 进行烧录
"""

import argparse
import serial
import subprocess
import sys
import time
import os

SVL_PATH = os.path.expanduser(
    "~/Library/Arduino15/packages/SparkFun/hardware/apollo3/1.2.1/tools/artemis/macosx/artemis_svl"
)


def trigger_bootloader(port, baud=115200):
    """
    通过 RTS 脉冲触发 Artemis 进入 bootloader 模式。

    电路：
    - RTS 下降沿 → C35 耦合 → NRESET 拉低 → 复位
    - RTS 上升沿 → C36 耦合 → BOOT 拉高 → 进入 bootloader
    """
    print(f"\n==> 触发 bootloader: {port}")
    try:
        # 打开串口（不指定 timeout，因为我们只是控制信号）
        ser = serial.Serial(port=port, baudrate=baud, timeout=0.1)

        # 初始状态：RTS = HIGH
        ser.rts = True
        time.sleep(0.05)  # 50ms 稳定

        # 产生下降沿：RTS = LOW → 触发 NRESET 拉低 → 复位
        ser.rts = False
        time.sleep(0.01)  # 10ms 保持复位

        # 产生上升沿：RTS = HIGH → 触发 BOOT 拉高 → 进入 bootloader
        ser.rts = True
        time.sleep(0.01)  # 10ms

        # 现在释放 RTS，让 BOOT 回到正常状态（由 R20 下拉）
        # 但 bootloader 已经锁存，不需要 BOOT 保持高电平
        ser.rts = False

        # 等待 bootloader 启动（SVL 需要约 150ms）
        time.sleep(0.2)

        ser.close()
        print("==> RTS 脉冲已发送，bootloader 应该已启动")
        return True
    except Exception as e:
        print(f"[ERROR] 触发 bootloader 失败: {e}")
        return False


def run_artemis_svl(port, binfile, baud, verbose, timeout):
    """调用原始 artemis_svl 工具进行烧录"""
    cmd = [SVL_PATH, port, "-f", binfile, "-b", str(baud)]
    if verbose:
        cmd.append("-v")
    cmd.extend(["-t", str(timeout)])

    print(f"\n==> 运行 artemis_svl: {' '.join(cmd)}")
    return subprocess.run(cmd).returncode


def main():
    parser = argparse.ArgumentParser(
        description="Artemis SVL 烧录工具（带 RTS 复位脉冲）"
    )
    parser.add_argument("port", help="串口设备路径，如 /dev/cu.wchusbserial1410")
    parser.add_argument("-f", "--binfile", required=True, help="要烧录的 .bin 文件路径")
    parser.add_argument("-b", "--baud", type=int, default=115200, help="波特率（默认 115200）")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    parser.add_argument("-t", "--timeout", type=float, default=0.5, help="通信超时（秒）")
    parser.add_argument("--no-reset", action="store_true", help="跳过 RTS 复位脉冲（调试用）")

    args = parser.parse_args()

    if not os.path.exists(args.binfile):
        print(f"[ERROR] 文件不存在: {args.binfile}")
        sys.exit(1)

    if not os.path.exists(SVL_PATH):
        print(f"[ERROR] artemis_svl 工具不存在: {SVL_PATH}")
        sys.exit(1)

    # 步骤 1：触发 bootloader
    if not args.no_reset:
        if not trigger_bootloader(args.port, args.baud):
            sys.exit(1)

    # 步骤 2：立即运行 artemis_svl
    # 需要快速切换，因为 BOOT 脉冲窗口只有约 22ms（R20*C36 时间常数）
    # 但实际上 bootloader 一旦启动会一直等待，所以有足够时间
    rc = run_artemis_svl(args.port, args.binfile, args.baud, args.verbose, args.timeout)

    if rc == 0:
        print("\n[SUCCESS] 烧录完成")
    else:
        print(f"\n[FAILED] 烧录失败，退出码: {rc}")
        print("\n提示：")
        print("  1. 确认没有其他程序占用串口（如 screen）")
        print("  2. 尝试不同的波特率：-b 115200 或 -b 921600")
        print("  3. 重新插拔 USB-C 线")
        print("  4. 如果仍然失败，可能需要使用 SWD 调试器（U19 接口）")

    sys.exit(rc)


if __name__ == "__main__":
    main()
