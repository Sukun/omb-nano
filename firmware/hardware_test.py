#!/usr/bin/env python3
"""
hardware_test.py - OMB-Nano 硬件测试工具

替代 screen 命令，用于执行 TODO-next-version.md 中的测试 1-3：
  测试 1：串口监视器验证（启动日志）
  测试 2：SD 卡数据写入验证
  测试 3：IMU 数据验证（需修改固件跳过 GNSS 循环）

使用方法：
  conda activate pcb
  python3 firmware/hardware_test.py                    # 交互式选择测试
  python3 firmware/hardware_test.py --port /dev/cu.wchusbserial1430 --test 1
  python3 firmware/hardware_test.py --monitor           # 纯串口监视器模式
"""

import argparse
import glob
import os
import signal
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("[ERROR] pyserial 未安装。请运行：pip install pyserial")
    sys.exit(1)

# ============================================================
# 固件运行时串口参数
# ============================================================
BAUDRATE = 1_000_000  # params.h: baudrate_debug_serial = 1000000

# 预期启动日志关键词（按出现顺序）
BOOT_MARKERS = [
    ("SD: init",           "SD 卡初始化"),
    ("SD: ready",          "SD 卡就绪"),
    ("SD: begin failed",   "SD 卡失败（无卡或焊接问题）"),
    ("baudrate_debug_serial", "参数打印"),
    ("attempt initial fix",   "GNSS 定位尝试"),
    ("success initial GNSS fix", "GNSS 定位成功"),
    ("initial GNSS fix fail",  "GNSS 定位失败（无天线正常）"),
    ("initial wave spectrum",  "波浪测量开始"),
    ("start the IMU",          "IMU 启动"),
    ("ISM330DHCX Found!",      "IMU 找到"),
    ("Failed to find ISM330DHCX", "IMU 未找到（焊接问题）"),
    ("SD: logged",             "SD 卡写入成功"),
    ("ready to sleep",         "进入睡眠（主循环开始）"),
]


# ============================================================
# 自动检测串口
# ============================================================
def auto_detect_port():
    """自动检测 CH340 串口设备"""
    patterns = [
        "/dev/cu.wchusbserial*",
        "/dev/cu.usbserial*",
        "/dev/cu.usbmodem*",
    ]
    ports = []
    for pattern in patterns:
        ports.extend(glob.glob(pattern))
    if not ports:
        print("[ERROR] 未检测到串口设备。请检查 USB-C 连接和 CH340 驱动。")
        sys.exit(1)
    if len(ports) == 1:
        return ports[0]
    print("检测到多个串口设备：")
    for i, p in enumerate(ports):
        print(f"  [{i}] {p}")
    idx = input("选择序号: ").strip()
    try:
        return ports[int(idx)]
    except (ValueError, IndexError):
        print("[ERROR] 无效选择")
        sys.exit(1)


# ============================================================
# 串口连接
# ============================================================
def open_serial(port, timeout=1.0):
    """打开串口连接"""
    try:
        ser = serial.Serial(port, BAUDRATE, timeout=timeout)
        print(f"[INFO] 已连接 {port} @ {BAUDRATE} bps")
        return ser
    except serial.SerialException as e:
        print(f"[ERROR] 无法打开串口: {e}")
        sys.exit(1)


# ============================================================
# 日志记录
# ============================================================
class Logger:
    def __init__(self, log_path=None):
        self.log_file = None
        if log_path:
            self.log_file = open(log_path, "w", encoding="utf-8")
            print(f"[INFO] 日志保存到: {log_path}")

    def log(self, text, prefix=""):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {prefix}{text}" if prefix else f"[{timestamp}] {text}"
        print(line)
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    def close(self):
        if self.log_file:
            self.log_file.close()


# ============================================================
# 通用的串口行读取器
# ============================================================
def read_lines(ser, callback, duration, logger):
    """持续读取串口数据，按行调用 callback"""
    start_time = time.time()
    line_buffer = b""
    try:
        while time.time() - start_time < duration:
            chunk = ser.read(256)
            if not chunk:
                continue
            line_buffer += chunk
            while b"\n" in line_buffer:
                line, line_buffer = line_buffer.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    logger.log(text)
                    callback(text)
    except KeyboardInterrupt:
        logger.log("\n[INFO] 用户中断监听")


# ============================================================
# 测试 1：串口监视器验证（启动日志）
# ============================================================
def test1_boot_log(ser, logger, duration=30):
    """测试 1：验证板子上电后的启动日志"""
    logger.log("=" * 60)
    logger.log("测试 1：串口监视器验证（启动日志）")
    logger.log("=" * 60)
    logger.log(f"监听时长: {duration} 秒")
    logger.log("请确保：1) 外部 3.3V 已接入  2) USB-C 已连接  3) 板子已上电")
    logger.log("如果板子已上电，按 Ctrl+C 退出后重新上电再运行此测试")
    logger.log("-" * 60)

    found_markers = set()

    def callback(text):
        for marker, desc in BOOT_MARKERS:
            if marker in text and marker not in found_markers:
                found_markers.add(marker)
                logger.log(f"  ✅ 检测到: {desc}", prefix=">>> ")

    read_lines(ser, callback, duration, logger)

    # 汇总
    logger.log("-" * 60)
    logger.log("启动日志检测结果：")
    for marker, desc in BOOT_MARKERS:
        status = "✅" if marker in found_markers else "  "
        logger.log(f"  {status} {desc}")

    logger.log("-" * 60)
    if "SD: ready" in found_markers:
        logger.log("✅ SD 卡初始化成功")
    elif "SD: begin failed" in found_markers:
        logger.log("❌ SD 卡初始化失败（检查：SD 卡是否插入、CARD1 焊接）")
    elif "SD: init" not in found_markers:
        logger.log("⚠️  未检测到 SD 卡初始化日志（板子可能已经过了启动阶段）")

    if "ISM330DHCX Found!" in found_markers:
        logger.log("✅ IMU 初始化成功")
    elif "Failed to find ISM330DHCX" in found_markers:
        logger.log("❌ IMU 未找到（检查：U6 ISM330DHCX 焊接、I2C 总线）")

    if "attempt initial fix" in found_markers:
        if "success initial GNSS fix" in found_markers:
            logger.log("✅ GNSS 定位成功")
        elif "initial GNSS fix fail" in found_markers:
            logger.log("⚠️  GNSS 定位失败（无天线时正常，板子会循环重试）")
        else:
            logger.log("⏳ GNSS 正在尝试定位（无天线会一直循环）")

    if "ready to sleep" in found_markers:
        logger.log("✅ 板子已进入主循环（所有初始化通过）")

    logger.log("=" * 60)
    return found_markers


# ============================================================
# 测试 2：SD 卡数据写入验证
# ============================================================
def test2_sd_card(ser, logger, duration=60):
    """测试 2：验证 SD 卡是否成功写入数据"""
    logger.log("=" * 60)
    logger.log("测试 2：SD 卡数据写入验证")
    logger.log("=" * 60)
    logger.log("前提条件：")
    logger.log("  1. microSD 卡已插入（FAT32 格式）")
    logger.log("  2. GNSS 天线已接（否则不会写 GNSS.CSV）")
    logger.log("  3. 或修改固件跳过 GNSS，直接测试 SD 初始化")
    logger.log(f"监听时长: {duration} 秒")
    logger.log("-" * 60)

    events = {
        "init": False, "ready": False, "failed": False,
        "logged_gnss": False, "logged_wave": False,
        "not_ready": False, "cannot_open": False,
    }

    def callback(text):
        if "SD: init" in text:
            events["init"] = True
        elif "SD: ready" in text:
            events["ready"] = True
            logger.log("  ✅ SD 卡就绪", prefix=">>> ")
        elif "SD: begin failed" in text:
            events["failed"] = True
            logger.log("  ❌ SD 卡初始化失败", prefix=">>> ")
        elif "SD: logged GNSS fix" in text:
            events["logged_gnss"] = True
            logger.log("  ✅ GNSS 数据已写入 SD 卡 (GNSS.CSV)", prefix=">>> ")
        elif "SD: logged wave" in text:
            events["logged_wave"] = True
            logger.log("  ✅ 波谱数据已写入 SD 卡 (WAVE.CSV)", prefix=">>> ")
        elif "SD: not ready" in text:
            events["not_ready"] = True
            logger.log("  ⚠️ SD 卡未就绪，跳过写入", prefix=">>> ")
        elif "SD: cannot open" in text:
            events["cannot_open"] = True
            logger.log("  ❌ SD 卡文件打开失败", prefix=">>> ")

    read_lines(ser, callback, duration, logger)

    # 汇总
    logger.log("-" * 60)
    logger.log("SD 卡测试结果：")
    if events["ready"]:
        logger.log("  ✅ SD 卡初始化成功")
    elif events["failed"]:
        logger.log("  ❌ SD 卡初始化失败")
        logger.log("     可能原因：1) 无卡  2) FAT32 格式不对  3) CARD1 焊接问题  4) SPI 引脚焊接")
    elif events["init"]:
        logger.log("  ⏳ SD 卡正在初始化...")
    else:
        logger.log("  ⚠️ 未检测到 SD 卡相关日志")

    if events["logged_gnss"]:
        logger.log("  ✅ GNSS.CSV 写入成功")
    if events["logged_wave"]:
        logger.log("  ✅ WAVE.CSV 写入成功")
    if events["not_ready"]:
        logger.log("  ❌ SD 卡未就绪，无法写入")
    if events["cannot_open"]:
        logger.log("  ❌ 文件打开失败，SD 卡可能已满或文件系统损坏")

    logger.log("-" * 60)
    logger.log("提示：拔出 SD 卡用电脑读卡器检查以下文件：")
    logger.log("  GNSS.CSV - 格式: 时间戳,纬度,经度")
    logger.log("  WAVE.CSV - 格式: 时间戳,波谱编号,Hs,Tz,Tc,max_value,...")
    logger.log("=" * 60)
    return events


# ============================================================
# 测试 3：IMU 数据验证
# ============================================================
def test3_imu(ser, logger, duration=120):
    """测试 3：验证 IMU (ISM330DHCX) 数据输出"""
    logger.log("=" * 60)
    logger.log("测试 3：IMU 数据验证")
    logger.log("=" * 60)
    logger.log("前提条件：")
    logger.log("  1. 固件已修改：跳过 GNSS 定位循环（#define TEST_MODE_NO_ANTENNA）")
    logger.log("  2. 已重新编译并烧录修改后的固件")
    logger.log("  3. 外部 3.3V 供电稳定（IMU 采样时电流会增大）")
    logger.log(f"监听时长: {duration} 秒")
    logger.log("-" * 60)
    logger.log("预期流程：")
    logger.log("  SD: init → SD: ready")
    logger.log("  → start the IMU → ISM330DHCX Found!")
    logger.log("  → use true IMU data → IMU 数据采集")
    logger.log("  → 波谱分析 → SD: logged wave")
    logger.log("-" * 60)

    events = {
        "imu_start": False, "imu_found": False, "imu_failed": False,
        "imu_data": False, "wave_start": False, "wave_packet": False,
        "sd_logged_wave": False, "welch": False,
    }
    imu_data_lines = []

    def callback(text):
        if "start the IMU" in text:
            events["imu_start"] = True
            logger.log("  ✅ IMU 启动中...", prefix=">>> ")
        elif "ISM330DHCX Found!" in text:
            events["imu_found"] = True
            logger.log("  ✅ IMU (ISM330DHCX) 找到！", prefix=">>> ")
        elif "Failed to find ISM330DHCX" in text:
            events["imu_failed"] = True
            logger.log("  ❌ IMU 未找到！检查 U6 焊接和 I2C 总线", prefix=">>> ")
        elif "use true IMU data" in text:
            events["imu_data"] = True
            logger.log("  ✅ IMU 数据采集开始", prefix=">>> ")
        elif "initial wave spectrum" in text:
            events["wave_start"] = True
            logger.log("  ✅ 波浪测量开始", prefix=">>> ")
        elif "start print wave packet" in text:
            events["wave_packet"] = True
            logger.log("  ✅ 波谱数据包打印中...", prefix=">>> ")
        elif "SD: logged wave" in text:
            events["sd_logged_wave"] = True
            logger.log("  ✅ 波谱数据已写入 SD 卡 (WAVE.CSV)", prefix=">>> ")
        elif "welch" in text.lower() or "spectrum" in text.lower():
            events["welch"] = True

        # 收集波谱相关数据行
        if any(kw in text for kw in ["Hs:", "Tz:", "Tc:", "max_value:", "frq:", "entry"]):
            imu_data_lines.append(text)

    read_lines(ser, callback, duration, logger)

    # 汇总
    logger.log("-" * 60)
    logger.log("IMU 测试结果：")
    if events["imu_found"]:
        logger.log("  ✅ IMU (ISM330DHCX) 初始化成功")
    elif events["imu_failed"]:
        logger.log("  ❌ IMU 未找到")
        logger.log("     可能原因：1) U6 ISM330DHCX 焊接问题  2) I2C 总线问题  3) IMUPwr 引脚问题")
    elif events["imu_start"]:
        logger.log("  ⏳ IMU 正在启动...（可能卡在 I2C 检测）")
    else:
        logger.log("  ⚠️ 未检测到 IMU 启动日志")
        logger.log("     可能原因：固件卡在 GNSS 循环中，需修改固件跳过 GNSS")

    if events["imu_data"]:
        logger.log("  ✅ IMU 数据采集成功")
    if events["wave_packet"]:
        logger.log("  ✅ 波谱分析完成")
    if events["sd_logged_wave"]:
        logger.log("  ✅ 波谱数据已写入 SD 卡")

    if imu_data_lines:
        logger.log("-" * 60)
        logger.log("捕获的波谱数据摘要：")
        for line in imu_data_lines[:20]:
            logger.log(f"  {line}")
        if len(imu_data_lines) > 20:
            logger.log(f"  ...（共 {len(imu_data_lines)} 行）")

    logger.log("-" * 60)
    logger.log("提示：拔出 SD 卡检查 WAVE.CSV 文件是否包含波谱数据")
    logger.log("=" * 60)
    return events


# ============================================================
# 纯串口监视器模式
# ============================================================
def monitor_mode(ser, logger):
    """纯串口监视器模式，实时显示所有串口输出"""
    logger.log("=" * 60)
    logger.log("串口监视器模式（Ctrl+C 退出）")
    logger.log("=" * 60)

    start_time = time.time()
    line_buffer = b""
    try:
        while True:
            chunk = ser.read(256)
            if not chunk:
                continue
            line_buffer += chunk
            while b"\n" in line_buffer:
                line, line_buffer = line_buffer.split(b"\n", 1)
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    elapsed = time.time() - start_time
                    print(f"[{elapsed:8.3f}] {text}")
    except KeyboardInterrupt:
        logger.log("\n[INFO] 退出监视器")


# ============================================================
# 主函数
# ============================================================
def main():
    parser = argparse.ArgumentParser(
        description="OMB-Nano 硬件测试工具（替代 screen）"
    )
    parser.add_argument("port", nargs="?", help="串口设备路径（不指定则自动检测）")
    parser.add_argument("--test", type=int, choices=[1, 2, 3], help="运行指定测试（1=启动日志, 2=SD卡, 3=IMU）")
    parser.add_argument("--monitor", action="store_true", help="纯串口监视器模式")
    parser.add_argument("--duration", type=int, default=None, help="监听时长（秒）")
    parser.add_argument("--log", action="store_true", help="保存日志到文件")
    args = parser.parse_args()

    # 检测串口
    port = args.port or auto_detect_port()

    # 日志
    log_path = None
    if args.log:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(script_dir, f"test_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
    logger = Logger(log_path)

    # 打开串口
    ser = open_serial(port)

    # Ctrl+C 优雅退出
    def signal_handler(sig, frame):
        logger.log("\n[INFO] 用户中断")
        ser.close()
        logger.close()
        sys.exit(0)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        if args.monitor:
            monitor_mode(ser, logger)
        elif args.test == 1:
            test1_boot_log(ser, logger, duration=args.duration or 30)
        elif args.test == 2:
            test2_sd_card(ser, logger, duration=args.duration or 60)
        elif args.test == 3:
            test3_imu(ser, logger, duration=args.duration or 120)
        else:
            # 交互式选择
            print("\n" + "=" * 60)
            print("OMB-Nano 硬件测试工具")
            print("=" * 60)
            print(f"串口: {port}")
            print(f"波特率: {BAUDRATE} bps")
            print("=" * 60)
            print("请选择测试：")
            print("  [1] 启动日志验证（30 秒）")
            print("  [2] SD 卡数据写入验证（60 秒）")
            print("  [3] IMU 数据验证（120 秒，需修改固件）")
            print("  [m] 纯串口监视器模式")
            print("  [q] 退出")
            print("-" * 60)
            choice = input("选择: ").strip().lower()

            if choice == "1":
                test1_boot_log(ser, logger, duration=30)
            elif choice == "2":
                test2_sd_card(ser, logger, duration=60)
            elif choice == "3":
                test3_imu(ser, logger, duration=120)
            elif choice == "m":
                monitor_mode(ser, logger)
            elif choice == "q":
                pass
            else:
                print("无效选择")

    finally:
        ser.close()
        logger.close()
        print("[INFO] 串口已关闭")


if __name__ == "__main__":
    main()
