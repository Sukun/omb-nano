#!/usr/bin/env python3
"""只读导出 OMB-Nano SD 卡文件（USB 串口，1 Mbps）。

用法：
  python3 firmware/sd_export.py                      # 列出 SD 根目录文件
  python3 firmware/sd_export.py --get IMU0000.CSV    # 接收并保存一个文件
  python3 firmware/sd_export.py --list --get TEST.TXT --out ~/sd_backup

工作方式：
  1. 打开串口并发送 DTR 复位脉冲（与 imu_listen.py 相同）。
  2. 等到板子打印 SDX-READY。当前采集固件每次启动都会先采 600 个 IMU 样本，
     所以这里通常要等约 60 秒；脚本不会在采集中发送命令。
  3. 发送 LIST 或 GET <name>；文件按 64 字节分块以十六进制传输，
     SDX-END 行给出字节数和 CRC32，电脑逐字节核对后才保存为正式文件。

板子只读 SD 卡，不创建、删除、重命名或覆盖任何文件。电脑端也不会覆盖
已有同名文件，除非显式加 --overwrite。
"""

import argparse
import binascii
import glob
import os
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import serial
except ImportError:
    print("[ERROR] pyserial 未安装。请运行: pip install pyserial", file=sys.stderr)
    sys.exit(1)

BAUDRATE = 1_000_000
DEFAULT_OUT = Path(__file__).resolve().parent / "sd_exports"
PORT_PATTERNS = (
    "/dev/cu.wchusbserial*",
    "/dev/cu.usbserial*",
    "/dev/cu.usbmodem*",
)


class ExportError(Exception):
    code = 4


class DeviceError(ExportError):
    """板子回了 SDX-ERR。"""

    code = 2


class FileNotFoundOnCard(ExportError):
    code = 3


class ExistingFile(ExportError):
    code = 1


class IncompleteTransfer(ExportError):
    code = 4


class ProtocolViolation(ExportError):
    code = 5


class SessionLog:
    def __init__(self, path):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8", errors="replace")

    def line(self, text):
        self._fh.write(f"[{datetime.now():%H:%M:%S}] {text}\n")
        self._fh.flush()

    def close(self):
        self._fh.close()


class LineReader:
    """在 pyserial 之上按行读取；每行都写入会话日志。"""

    def __init__(self, ser, log):
        self._ser = ser
        self._log = log
        self._buffer = bytearray()

    def readline(self, timeout):
        deadline = time.monotonic() + timeout
        while b"\n" not in self._buffer:
            data = self._ser.read(4096)
            if data:
                self._buffer.extend(data)
                continue
            if time.monotonic() >= deadline:
                return None
        raw, _, rest = self._buffer.partition(b"\n")
        self._buffer = bytearray(rest)
        text = raw.rstrip(b"\r").decode("utf-8", "replace")
        self._log.line(text)
        return text


def choose_port(explicit):
    if explicit:
        return explicit
    ports = []
    for pattern in PORT_PATTERNS:
        ports.extend(glob.glob(pattern))
    ports = sorted(set(ports))
    if not ports:
        raise ExportError("未检测到串口。请检查 USB-C 连接和 CH340 驱动，或用 --port 指定。")
    if len(ports) == 1:
        return ports[0]
    print("检测到多个串口：")
    for index, port in enumerate(ports, start=1):
        print(f"  {index}) {port}")
    try:
        choice = int(input("请选择序号: ").strip())
        return ports[choice - 1]
    except (ValueError, IndexError):
        raise ExportError("无效的串口选择。请用 --port 指定。")


def dtr_reset(ser):
    ser.dtr = True
    time.sleep(0.05)
    ser.dtr = False
    time.sleep(0.01)
    ser.dtr = True
    time.sleep(0.01)
    ser.dtr = False
    time.sleep(0.3)


def wait_ready(lines, timeout, log):
    print(f"等待板子就绪（启动采集约 60 秒，最长 {timeout:.0f} 秒）...")
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise IncompleteTransfer(
                f"{timeout:.0f} 秒内未收到 SDX-READY。"
                "确认烧录的是本次导出固件；若板子刚上电，请重试。")
        line = lines.readline(min(remaining, 5.0))
        if line is None:
            continue
        if line == "capture: IMU read failed":
            log.line("# warning: 板子本次采集报告 IMU 读取失败（导出仍可继续）")
            print("[WARN] 板子本次采集报告 IMU 读取失败，导出仍可继续。")
        if line.startswith("SDX-READY"):
            ready = line.strip().endswith("sd=1")
            if not ready:
                print("[WARN] 板子报告 SD 卡不可用，将无法列出或导出文件。")
            return ready


def send_command(ser, text, log):
    log.line(f">>> {text}")
    ser.write((text + "\n").encode("ascii"))
    ser.flush()


def raise_for_device_error(line, fallback=DeviceError):
    code = line[len("SDX-ERR "):].strip() if line.startswith("SDX-ERR ") else line
    if code.startswith("NO-SD"):
        raise DeviceError(
            "SD 卡不可用（板子启动日志应为 'SD: begin failed'）。"
            "检查卡是否插好、FAT32 格式和 CARD1 焊接。")
    if code.startswith("NOT-FOUND"):
        raise FileNotFoundOnCard("板子上没有这个文件，请先用 --list 查看文件名。")
    raise fallback(f"板子返回错误：{line}")


def request_list(ser, lines, log, io_timeout):
    send_command(ser, "LIST", log)
    files = []
    while True:
        line = lines.readline(io_timeout)
        if line is None:
            raise IncompleteTransfer("等待文件列表超时。")
        if line.startswith("SDX-ERR"):
            raise_for_device_error(line)
        if line == "SDX-LIST-BEGIN":
            continue
        if line.startswith("SDX-LIST-END"):
            declared = int(line.split()[1])
            if declared != len(files):
                raise ProtocolViolation(
                    f"列表数量不一致：板子声明 {declared}，实际收到 {len(files)}。")
            return files
        if line.startswith("SDX-FILE"):
            parts = line.split()
            if len(parts) != 3:
                raise ProtocolViolation(f"无法解析文件行：{line}")
            files.append((parts[1], int(parts[2])))


def download(ser, lines, name, out_dir, overwrite, log, io_timeout):
    target = out_dir / name
    if target.exists() and not overwrite:
        raise ExistingFile(
            f"本地已存在 {target}，未覆盖。换 --out 目录或显式加 --overwrite。")
    part = out_dir / (name + ".part")

    send_command(ser, f"GET {name}", log)

    expected = None
    received = 0
    crc = 0
    finished = None
    with open(part, "wb") as handle:
        while True:
            line = lines.readline(io_timeout)
            if line is None:
                raise IncompleteTransfer(
                    f"传输中断：已接收 {received} 字节"
                    + (f"，预期 {expected}" if expected is not None else "")
                    + f"。临时文件保留在 {part}")
            if line.startswith("SDX-ERR"):
                try:
                    raise_for_device_error(line, IncompleteTransfer)
                except FileNotFoundOnCard:
                    # 文件不在卡上：空的 .part 没有诊断价值，删掉后再报原错误。
                    part.unlink(missing_ok=True)
                    raise
            if line.startswith("SDX-BEGIN"):
                parts = line.split()
                if len(parts) != 3:
                    raise ProtocolViolation(f"无法解析起始行：{line}")
                sent_name, expected = parts[1], int(parts[2])
                if sent_name.upper() != name.upper():
                    raise ProtocolViolation(
                        f"板子开始发送 {sent_name}，但请求的是 {name}。")
                continue
            if line.startswith("SDX-DATA"):
                if expected is None:
                    raise ProtocolViolation("在 SDX-BEGIN 之前收到数据。")
                payload = line[len("SDX-DATA "):]
                try:
                    chunk = binascii.unhexlify(payload)
                except (binascii.Error, ValueError):
                    raise ProtocolViolation(f"数据行不是合法十六进制：{payload[:32]}...")
                handle.write(chunk)
                crc = binascii.crc32(chunk, crc)
                received += len(chunk)
                if received > expected:
                    raise ProtocolViolation(
                        f"收到 {received} 字节，超过声明的 {expected} 字节。")
                continue
            if line.startswith("SDX-END"):
                parts = line.split()
                if len(parts) != 3:
                    raise ProtocolViolation(f"无法解析结束行：{line}")
                finished = (int(parts[1]), int(parts[2], 16))
                break
            # 其它行是板子的启动/采集诊断，已写入日志，忽略。

    if expected is None or finished is None:
        raise IncompleteTransfer(f"传输不完整，临时文件保留在 {part}")

    declared_size, declared_crc = finished
    actual_crc = crc & 0xFFFFFFFF
    if received != expected or declared_size != expected:
        raise IncompleteTransfer(
            f"字节数不一致：收到 {received}，BEGIN 声明 {expected}，"
            f"END 声明 {declared_size}。临时文件保留在 {part}")
    if actual_crc != declared_crc:
        raise IncompleteTransfer(
            f"CRC32 不一致：本地 {actual_crc:08X}，板子 {declared_crc:08X}。"
            f"临时文件保留在 {part}")

    os.replace(part, target)
    print(f"[OK] {name}: {received} 字节，CRC32 {actual_crc:08X}")
    print(f"     保存到 {target}")
    log.line(f"# saved {target} bytes={received} crc32={actual_crc:08X}")
    return target


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="通过 USB 串口只读导出 OMB-Nano SD 卡文件（非 U 盘模式）。")
    parser.add_argument("--port", help="串口，例如 /dev/cu.wchusbserial1410；默认自动检测")
    parser.add_argument("--list", action="store_true", help="列出 SD 根目录文件（默认动作）")
    parser.add_argument("--get", metavar="NAME", help="接收一个文件，例如 IMU0000.CSV")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"保存目录，默认 {DEFAULT_OUT}")
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖电脑上已有的同名文件")
    parser.add_argument("--boot-timeout", type=float, default=150.0,
                        help="等待 SDX-READY 的秒数（默认 150，覆盖一次 60 秒启动采集）")
    parser.add_argument("--io-timeout", type=float, default=15.0,
                        help="单次等待板子响应的秒数（默认 15）")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    out_dir = Path(args.out).expanduser().resolve()
    do_list = args.list or not args.get
    log = None
    status = 0
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / f"sd_export_{datetime.now():%Y%m%d_%H%M%S}.log"
        log = SessionLog(log_path)
        log.line(f"# out={out_dir} get={args.get} list={do_list}")
        port = choose_port(args.port)
        log.line(f"# port={port} baud={BAUDRATE}")

        print(f"串口：{port}；波特率 {BAUDRATE}。")
        print("注意：打开串口会触发 DTR 复位，板子会重新执行一次启动采集。")
        with serial.Serial(port, BAUDRATE, timeout=0.2) as ser:
            dtr_reset(ser)
            lines = LineReader(ser, log)
            sd_ready = wait_ready(lines, args.boot_timeout, log)
            ser.reset_input_buffer()

            if do_list:
                files = request_list(ser, lines, log, args.io_timeout)
                print(f"SD 卡根目录（只读），共 {len(files)} 个文件：")
                for filename, size in files:
                    print(f"  {filename:<14} {size:>10} 字节")
                if not files:
                    print("  （空）")

            if args.get:
                if not sd_ready:
                    raise DeviceError("SD 卡不可用，无法导出。")
                log.line(f"# download {args.get}")
                download(ser, lines, args.get, out_dir, args.overwrite, log, args.io_timeout)
        print(f"日志：{log_path}")
    except ExistingFile as exc:
        status = exc.code
        print(f"[ERROR] {exc}", file=sys.stderr)
    except DeviceError as exc:
        status = exc.code
        print(f"[ERROR] {exc}", file=sys.stderr)
    except FileNotFoundOnCard as exc:
        status = exc.code
        print(f"[ERROR] {exc}", file=sys.stderr)
    except IncompleteTransfer as exc:
        status = exc.code
        print(f"[ERROR] {exc}", file=sys.stderr)
        print("[HINT] 串口日志和 .part 文件已保留，可用于排查；不要拔 USB/断电后重试。",
              file=sys.stderr)
    except ProtocolViolation as exc:
        status = exc.code
        print(f"[ERROR] 协议错误：{exc}", file=sys.stderr)
    except ExportError as exc:
        status = exc.code
        print(f"[ERROR] {exc}", file=sys.stderr)
    except (serial.SerialException, OSError) as exc:
        status = 4
        print(f"[ERROR] 串口错误：{exc}", file=sys.stderr)
    except KeyboardInterrupt:
        status = 130
        print("\n[INFO] 用户中止。", file=sys.stderr)
    finally:
        if log is not None:
            log.line(f"# exit status={status}")
            log.close()
    return status


if __name__ == "__main__":
    sys.exit(main())