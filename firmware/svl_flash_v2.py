#!/usr/bin/env python3
"""
svl_flash_v2.py - 改进版 Artemis SVL 烧录工具

改进点（针对 OMB-Nano 写 flash 时电源跌落导致复位的问题）：
  1. wait_for_packet 增加包长度合理性检查：SVL 包长度 3-64 字节，
     超范围则丢弃首字节滑动重试，跳过板子复位时输出的乱码。
  2. 帧大小降到 16 字节，进一步降低单次写 flash 电流峰值。
  3. 帧间延迟增到 0.15s，给 flash 写入和电源恢复更多时间。
  4. 单帧出错时自动重试该帧（最多 6 次），而非立即放弃。
"""

import argparse
import serial
import serial.tools.list_ports as list_ports
import sys
import time
import math
import os.path
from sys import exit

# SVL 协议命令
SVL_CMD_VER = 0x01
SVL_CMD_BL = 0x02
SVL_CMD_NEXT = 0x03
SVL_CMD_FRAME = 0x04
SVL_CMD_RETRY = 0x05
SVL_CMD_DONE = 0x06

# SVL 包长度合理性范围（有效包长度 = 1字节cmd + N字节data + 2字节CRC，最小3）
SVL_LEN_MIN = 3
SVL_LEN_MAX = 64

crcTable = (
    0x0000, 0x8005, 0x800F, 0x000A, 0x801B, 0x001E, 0x0014, 0x8011,
    0x8033, 0x0036, 0x003C, 0x8039, 0x0028, 0x802D, 0x8027, 0x0022,
    0x8063, 0x0066, 0x006C, 0x8069, 0x0078, 0x807D, 0x8077, 0x0072,
    0x0050, 0x8055, 0x805F, 0x005A, 0x804B, 0x004E, 0x0044, 0x8041,
    0x80C3, 0x00C6, 0x00CC, 0x80C9, 0x00D8, 0x80DD, 0x80D7, 0x00D2,
    0x00F0, 0x80F5, 0x80FF, 0x00FA, 0x80EB, 0x00EE, 0x00E4, 0x80E1,
    0x00A0, 0x80A5, 0x80AF, 0x00AA, 0x80BB, 0x00BE, 0x00B4, 0x80B1,
    0x8093, 0x0096, 0x009C, 0x8099, 0x0088, 0x808D, 0x8087, 0x0082,
    0x8183, 0x0186, 0x018C, 0x8189, 0x0198, 0x819D, 0x8197, 0x0192,
    0x01B0, 0x81B5, 0x81BF, 0x01BA, 0x81AB, 0x01AE, 0x01A4, 0x81A1,
    0x01E0, 0x81E5, 0x81EF, 0x01EA, 0x81FB, 0x01FE, 0x01F4, 0x81F1,
    0x81D3, 0x01D6, 0x01DC, 0x81D9, 0x01C8, 0x81CD, 0x81C7, 0x01C2,
    0x0140, 0x8145, 0x814F, 0x014A, 0x815B, 0x015E, 0x0154, 0x8151,
    0x8173, 0x0176, 0x017C, 0x8179, 0x0168, 0x816D, 0x8167, 0x0162,
    0x8123, 0x0126, 0x012C, 0x8129, 0x0138, 0x813D, 0x8137, 0x0132,
    0x0110, 0x8115, 0x811F, 0x011A, 0x810B, 0x010E, 0x0104, 0x8101,
    0x8303, 0x0306, 0x030C, 0x8309, 0x0318, 0x831D, 0x8317, 0x0312,
    0x0330, 0x8335, 0x833F, 0x033A, 0x832B, 0x032E, 0x0324, 0x8321,
    0x0360, 0x8365, 0x836F, 0x036A, 0x837B, 0x037E, 0x0374, 0x8371,
    0x8353, 0x0356, 0x035C, 0x8359, 0x0348, 0x834D, 0x8347, 0x0342,
    0x03C0, 0x83C5, 0x83CF, 0x03CA, 0x83DB, 0x03DE, 0x03D4, 0x83D1,
    0x83F3, 0x03F6, 0x03FC, 0x83F9, 0x03E8, 0x83ED, 0x83E7, 0x03E2,
    0x83A3, 0x03A6, 0x03AC, 0x83A9, 0x03B8, 0x83BD, 0x83B7, 0x03B2,
    0x0390, 0x8395, 0x839F, 0x039A, 0x838B, 0x038E, 0x0384, 0x8381,
    0x0280, 0x8285, 0x828F, 0x028A, 0x829B, 0x029E, 0x0294, 0x8291,
    0x82B3, 0x02B6, 0x02BC, 0x82B9, 0x02A8, 0x82AD, 0x82A7, 0x02A2,
    0x82E3, 0x02E6, 0x02EC, 0x82E9, 0x02F8, 0x82FD, 0x82F7, 0x02F2,
    0x02D0, 0x82D5, 0x82DF, 0x02DA, 0x82CB, 0x02CE, 0x02C4, 0x82C1,
    0x8243, 0x0246, 0x024C, 0x8249, 0x0258, 0x825D, 0x8257, 0x0252,
    0x0270, 0x8275, 0x827F, 0x027A, 0x826B, 0x026E, 0x0264, 0x8261,
    0x0220, 0x8225, 0x822F, 0x022A, 0x823B, 0x023E, 0x0234, 0x8231,
    0x8213, 0x0216, 0x021C, 0x8219, 0x0208, 0x820D, 0x8207, 0x0202,
)


def get_crc16(data):
    crc = 0x0000
    data = bytearray(data)
    for ch in data:
        tableAddr = ch ^ (crc >> 8)
        CRCH = (crcTable[tableAddr] >> 8) ^ (crc & 0xFF)
        CRCL = crcTable[tableAddr] & 0x00FF
        crc = CRCH << 8 | CRCL
    return crc


def wait_for_packet(ser, verbose=False, total_timeout=3.0):
    """等待有效 SVL 包，自动跳过不合理字节（板子复位输出的乱码）"""
    packet = {'len': 0, 'cmd': 0, 'data': 0, 'crc': 1, 'timeout': 1}

    deadline = time.time() + total_timeout
    # 先把已有的串口数据读到一个缓冲区
    buf = b''

    while time.time() < deadline:
        # 补充缓冲区
        if len(buf) < 2:
            chunk = ser.read(2 - len(buf))
            if chunk:
                buf += chunk
            else:
                # 短暂等待更多数据
                time.sleep(0.005)
                continue

        if len(buf) < 2:
            continue

        # 尝试解析包长度
        pkt_len = int.from_bytes(buf[:2], byteorder='big', signed=False)

        # 包长度合理性检查
        if pkt_len < SVL_LEN_MIN or pkt_len > SVL_LEN_MAX:
            if verbose:
                print(f"  [skip] 丢弃字节 {buf[0]:#04x}（包长度 {pkt_len} 越界）")
            # 丢弃首字节，滑动窗口
            buf = buf[1:]
            continue

        # 包长度合理，读取完整 payload
        while len(buf) < 2 + pkt_len and time.time() < deadline:
            chunk = ser.read(2 + pkt_len - len(buf))
            if chunk:
                buf += chunk
            else:
                time.sleep(0.005)

        if len(buf) < 2 + pkt_len:
            if verbose:
                print(f"  [debug] payload 不完整：期望 {pkt_len}，收到 {len(buf)-2}")
            # 丢弃首字节重试
            buf = buf[1:]
            continue

        payload = buf[2:2 + pkt_len]
        buf = buf[2 + pkt_len:]

        packet['timeout'] = 0
        packet['len'] = pkt_len
        packet['cmd'] = payload[0]
        packet['data'] = payload[1:pkt_len - 2]
        packet['crc'] = get_crc16(payload)

        if verbose:
            print(f"  [ok] cmd={packet['cmd']:#x}, data={packet['data'].hex()}, crc={packet['crc']:#06x}")

        return packet

    if verbose and buf:
        print(f"  [debug] 超时，残留数据: {buf.hex()}")
    return packet


def send_packet(ser, cmd, data):
    data = bytearray(data)
    num_bytes = 3 + len(data)
    payload = bytearray(cmd.to_bytes(1, 'big'))
    payload.extend(data)
    crc = get_crc16(payload)
    payload.extend(bytearray(crc.to_bytes(2, 'big')))

    chunk_size = 64
    header = num_bytes.to_bytes(2, 'big')
    ser.write(header)
    for i in range(0, len(payload), chunk_size):
        ser.write(bytes(payload[i:i + chunk_size]))
    ser.flush()


def trigger_bootloader_rts(ser, verbose=False):
    if verbose:
        print("\n==> 触发 DTR 脉冲序列")

    ser.dtr = True
    time.sleep(0.05)
    ser.dtr = False
    time.sleep(0.01)
    ser.dtr = True
    time.sleep(0.01)
    ser.dtr = False
    time.sleep(0.01)
    time.sleep(0.2)

    if verbose:
        print("==> DTR 脉冲完成")


def phase_setup(ser, verbose=False):
    print('\nPhase:\tSetup')
    ser.reset_input_buffer()
    print('\tCleared startup blip')

    ser.write(b'U')
    packet = wait_for_packet(ser, verbose=verbose, total_timeout=3.0)
    if packet['timeout'] or packet['crc']:
        print('\tFailed to enter bootload phase')
        return False

    print('\tGot SVL Bootloader Version: ' +
          str(int.from_bytes(packet['data'], 'big')))
    print("\tSending 'enter bootloader' command")
    send_packet(ser, SVL_CMD_BL, b'')
    return True


def phase_bootload(ser, binfile_path, verbose=False, frame_size=16, frame_delay=0.15):
    """传输固件数据（超保守参数：16字节帧 + 150ms延迟）"""
    startTime = time.time()
    resend_max = 6
    resend_count = 0

    print('\nPhase:\tBootload')
    print(f'\t超保守模式：帧 {frame_size} 字节，延迟 {frame_delay*1000:.0f}ms')

    with open(binfile_path, mode='rb') as binfile:
        application = binfile.read()
        total_len = len(application)
        total_frames = math.ceil(total_len / frame_size)
        curr_frame = 0
        progressChars = 0

        print(f'\thave {total_len} bytes to send in {total_frames} frames')
        print('[', end='', flush=True)

        bl_done = False
        bl_succeeded = True

        while (bl_done == False) and (bl_succeeded == True):
            packet = wait_for_packet(ser, verbose=verbose, total_timeout=4.0)
            if packet['timeout']:
                print('\n\t[timeout] 等待响应超时')
                bl_succeeded = False
                bl_done = True
                break
            if packet['crc']:
                print('\n\t[crc] CRC 校验失败')
                bl_succeeded = False
                bl_done = True
                break

            if packet['cmd'] == SVL_CMD_NEXT:
                curr_frame += 1
                resend_count = 0
            elif packet['cmd'] == SVL_CMD_RETRY:
                print('\n\tRetrying...')
                resend_count += 1
                if resend_count >= resend_max:
                    print(f'\t达到最大重试次数 {resend_max}，放弃')
                    bl_succeeded = False
                    bl_done = True
                    break
            else:
                print(f'\n\t[unknown] 未知 cmd={packet["cmd"]:#x}')
                bl_succeeded = False
                bl_done = True
                break

            if curr_frame <= total_frames:
                frame_data = application[
                    ((curr_frame - 1) * frame_size):((curr_frame - 1 + 1) * frame_size)
                ]
                percentComplete = curr_frame * 100 / total_frames
                percentCompleteInChars = math.ceil(percentComplete / 100 * 50)
                while progressChars < percentCompleteInChars:
                    progressChars += 1
                    print('#', end='', flush=True)
                if percentComplete == 100:
                    print("]", end='')

                send_packet(ser, SVL_CMD_FRAME, frame_data)
                time.sleep(frame_delay)
            else:
                send_packet(ser, SVL_CMD_DONE, b'')
                bl_done = True

        if bl_succeeded:
            print('\n\tUpload complete')
            endTime = time.time()
            bps = total_len / (endTime - startTime)
            if verbose:
                print(f'\tNominal bootload bps: {round(bps, 2)}')
                print(f'\tTotal time: {round(endTime - startTime, 1)}s')
        else:
            print('\n\tUpload failed')

        return bl_succeeded


def main():
    parser = argparse.ArgumentParser(
        description='Artemis SVL 烧录工具 v2（改进容错版）'
    )
    parser.add_argument('port', help='串口设备路径')
    parser.add_argument('-f', '--binfile', required=True, help='.bin 文件路径')
    parser.add_argument('-b', '--baud', type=int, default=115200, help='波特率（默认 115200）')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('-t', '--timeout', type=float, default=0.5, help='单次读取超时（秒）')
    parser.add_argument('--no-reset', action='store_true', help='跳过 DTR 复位脉冲')
    parser.add_argument('--frame-size', type=int, default=16, help='帧大小（默认 16 字节）')
    parser.add_argument('--frame-delay', type=float, default=0.15, help='帧间延迟（默认 0.15s）')

    args = parser.parse_args()

    if not os.path.exists(args.binfile):
        print(f"[ERROR] 文件不存在: {args.binfile}")
        sys.exit(1)

    print('\nArtemis SVL Bootloader v2 (改进容错版)')
    print(f"帧大小: {args.frame_size} 字节, 帧间延迟: {args.frame_delay*1000:.0f}ms")

    num_tries = 3
    bl_success = False
    entered_bootloader = False

    for attempt in range(num_tries):
        print(f"\n--- 尝试 {attempt + 1}/{num_tries} ---")
        try:
            with serial.Serial(args.port, args.baud, timeout=args.timeout) as ser:
                if not args.no_reset:
                    trigger_bootloader_rts(ser, verbose=args.verbose)
                entered_bootloader = phase_setup(ser, verbose=args.verbose)
                if entered_bootloader:
                    bl_success = phase_bootload(
                        ser, args.binfile, verbose=args.verbose,
                        frame_size=args.frame_size, frame_delay=args.frame_delay)
                    if bl_success:
                        break
                else:
                    if args.verbose:
                        print("  [debug] bootloader 未响应，重试")
        except serial.SerialException as e:
            print(f"[ERROR] 串口错误: {e}")
            sys.exit(1)

    if not entered_bootloader:
        print("\n[FAILED] 目标未进入 bootloader 模式")
        sys.exit(1)

    if bl_success:
        print("\n[SUCCESS] 烧录完成！")
        sys.exit(0)
    else:
        print("\n[FAILED] 烧录失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
