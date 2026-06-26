#!/usr/bin/env python3
"""
svl_flash.py - 带 RTS 复位脉冲的 Artemis SVL 烧录工具

基于 SparkFun artemis_svl.py，添加了 RTS 脉冲控制来触发自动复位电路。

适用于没有物理复位按钮的 PCB（如 OMB-Nano）。

电路原理（OMB-Nano PCB）：
  CH340 RTS ──┬── C35 (100nF) ── NRESET (R14 100kΩ 上拉到 VCC)
              └── C36 (100nF) ── BOOT   (R20 220kΩ 下拉到 GND)

触发序列：
  1. RTS = LOW  (下降沿 → NRESET 拉低 → 复位)
  2. 等待 10ms
  3. RTS = HIGH (上升沿 → BOOT 拉高 → 进入 bootloader)
  4. 等待 150ms (让 bootloader 启动)
  5. 发送 'U' 字符进行波特率检测
  6. 正常 SVL 烧录流程
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
SVL_CMD_VER = 0x01    # version
SVL_CMD_BL = 0x02     # enter bootload mode
SVL_CMD_NEXT = 0x03   # request next chunk
SVL_CMD_FRAME = 0x04  # indicate app data frame
SVL_CMD_RETRY = 0x05  # request re-send frame
SVL_CMD_DONE = 0x06   # finished - all data sent

# CRC16 表（来自 SparkFun artemis_svl）
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


def wait_for_packet(ser, verbose=False):
    packet = {'len': 0, 'cmd': 0, 'data': 0, 'crc': 1, 'timeout': 1}

    # 等待足够的数据到达
    deadline = time.time() + 2.0  # 2 秒总超时
    n = b''
    while len(n) < 2 and time.time() < deadline:
        chunk = ser.read(2 - len(n))
        if chunk:
            n += chunk
            if verbose:
                print(f"  [debug] 收到字节: {chunk.hex()} (累计 {len(n)} 字节)")
        else:
            time.sleep(0.001)

    if len(n) < 2:
        if verbose:
            print(f"  [debug] 只收到 {len(n)} 字节（期望 2）, 数据: {n.hex()}")
        # 如果有 1 字节，继续等待看是否还有更多数据
        if len(n) == 1:
            extra = b''
            extra_deadline = time.time() + 1.0
            while time.time() < extra_deadline:
                chunk = ser.read(64)
                if chunk:
                    extra += chunk
                    if verbose:
                        print(f"  [debug] 额外收到: {chunk.hex()}")
                else:
                    time.sleep(0.01)
            if extra:
                print(f"  [debug] 额外数据总计: {extra.hex()}")
        return packet

    packet['len'] = int.from_bytes(n, byteorder='big', signed=False)
    if verbose:
        print(f"  [debug] 包长度字段 = {packet['len']}")

    # 读取完整 payload
    payload = b''
    while len(payload) < packet['len'] and time.time() < deadline:
        chunk = ser.read(packet['len'] - len(payload))
        if chunk:
            payload += chunk
        else:
            time.sleep(0.001)

    if len(payload) != packet['len']:
        if verbose:
            print(f"  [debug] payload 不完整：期望 {packet['len']}，收到 {len(payload)}")
        return packet

    packet['timeout'] = 0
    packet['cmd'] = payload[0]
    packet['data'] = payload[1:packet['len']-2]
    packet['crc'] = get_crc16(payload)

    if verbose:
        print(f"  [debug] cmd={packet['cmd']:#x}, data={packet['data'].hex()}, crc_calc={packet['crc']:#06x}")

    return packet


def send_packet(ser, cmd, data):
    data = bytearray(data)
    num_bytes = 3 + len(data)
    payload = bytearray(cmd.to_bytes(1, 'big'))
    payload.extend(data)
    crc = get_crc16(payload)
    payload.extend(bytearray(crc.to_bytes(2, 'big')))

    # 分块写入，避免 CH340 缓冲区溢出
    chunk_size = 64
    header = num_bytes.to_bytes(2, 'big')
    ser.write(header)
    for i in range(0, len(payload), chunk_size):
        ser.write(bytes(payload[i:i+chunk_size]))
    ser.flush()


def trigger_bootloader_rts(ser, verbose=False):
    """
    通过 DTR 脉冲触发 Artemis 进入 bootloader 模式。

    注意：虽然函数名保留 rts（历史原因），但实际使用 DTR 信号。
    诊断测试证明 DTR 脉冲（不是 RTS）能成功触发 OMB-Nano 的 bootloader。

    DTR 脉冲序列（诊断验证有效）：
    1. DTR = HIGH（初始）
    2. DTR = LOW（50ms）
    3. DTR = HIGH（10ms）
    4. DTR = LOW（10ms）
    5. 等待 200ms 让 bootloader 启动
    """
    if verbose:
        print("\n==> 触发 DTR 脉冲序列")

    # 初始状态：DTR = HIGH
    ser.dtr = True
    time.sleep(0.05)

    # 产生脉冲：DTR = LOW
    ser.dtr = False
    time.sleep(0.01)

    # DTR = HIGH
    ser.dtr = True
    time.sleep(0.01)

    # DTR = LOW
    ser.dtr = False
    time.sleep(0.01)

    # 等待 bootloader 启动
    time.sleep(0.2)

    if verbose:
        print("==> DTR 脉冲完成")


def phase_setup(ser, verbose=False):
    """波特率协商 + 获取版本 + 进入 bootloader"""
    baud_detect_byte = b'U'

    print('\nPhase:\tSetup')

    # 清除启动 blip
    ser.reset_input_buffer()
    print('\tCleared startup blip')

    # 发送波特率检测字符
    ser.write(baud_detect_byte)

    packet = wait_for_packet(ser, verbose=verbose)
    if packet['timeout'] or packet['crc']:
        print('\tFailed to enter bootload phase')
        return False

    print('\tGot SVL Bootloader Version: ' +
          str(int.from_bytes(packet['data'], 'big')))
    print("\tSending 'enter bootloader' command")

    send_packet(ser, SVL_CMD_BL, b'')
    return True


def phase_bootload(ser, binfile_path, verbose=False):
    """传输固件数据（使用小帧 + 长延迟降低电流峰值）"""
    startTime = time.time()
    frame_size = 32  # 非常小的帧：32 字节，降低写 flash 时的电流峰值
    resend_max = 4
    resend_count = 0

    print('\nPhase:\tBootload')
    print(f'\t使用小帧模式 ({frame_size} 字节) 降低电流峰值')

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
            packet = wait_for_packet(ser, verbose=verbose)
            if packet['timeout'] or packet['crc']:
                print('\n\tError receiving packet')
                bl_succeeded = False
                bl_done = True

            if packet['cmd'] == SVL_CMD_NEXT:
                curr_frame += 1
                resend_count = 0
            elif packet['cmd'] == SVL_CMD_RETRY:
                print('\n\tRetrying...')
                resend_count += 1
                if resend_count >= resend_max:
                    bl_succeeded = False
                    bl_done = True
            else:
                print('Timeout or unknown error')
                bl_succeeded = False
                bl_done = True

            if curr_frame <= total_frames:
                frame_data = application[
                    ((curr_frame - 1) * frame_size):((curr_frame - 1 + 1) * frame_size)
                ]
                percentComplete = curr_frame * 100 / total_frames
                percentCompleteInChars = math.ceil(
                    percentComplete / 100 * 50)
                while progressChars < percentCompleteInChars:
                    progressChars += 1
                    print('#', end='', flush=True)
                if percentComplete == 100:
                    print("]", end='')

                send_packet(ser, SVL_CMD_FRAME, frame_data)
                # 较长延迟让 flash 写入完成，避免电流峰值导致复位
                time.sleep(0.05)
            else:
                send_packet(ser, SVL_CMD_DONE, b'')
                bl_done = True

        if bl_succeeded:
            print('\n\tUpload complete')
            endTime = time.time()
            bps = total_len / (endTime - startTime)
            if verbose:
                print(f'\tNominal bootload bps: {round(bps, 2)}')
        else:
            print('\n\tUpload failed')

        return bl_succeeded


def main():
    parser = argparse.ArgumentParser(
        description='Artemis SVL 烧录工具（带 RTS 复位脉冲）'
    )
    parser.add_argument('port', help='串口设备路径，如 /dev/cu.wchusbserial1410')
    parser.add_argument('-f', '--binfile', required=True, help='要烧录的 .bin 文件路径')
    parser.add_argument('-b', '--baud', type=int, default=115200, help='波特率（默认 115200）')
    parser.add_argument('-v', '--verbose', action='store_true', help='详细输出')
    parser.add_argument('-t', '--timeout', type=float, default=0.5, help='通信超时（秒）')
    parser.add_argument('--no-reset', action='store_true', help='跳过 RTS 复位脉冲')

    args = parser.parse_args()

    if not os.path.exists(args.binfile):
        print(f"[ERROR] 文件不存在: {args.binfile}")
        sys.exit(1)

    print('\nArtemis SVL Bootloader (with RTS reset)')
    print(f"Script version 2.0 (基于 SparkFun SVL 1.7)")

    num_tries = 3
    bl_success = False
    entered_bootloader = False

    for attempt in range(num_tries):
        print(f"\n--- 尝试 {attempt + 1}/{num_tries} ---")

        try:
            with serial.Serial(args.port, args.baud, timeout=args.timeout) as ser:
                # 步骤 1：触发 RTS 脉冲（如果没有 --no-reset）
                if not args.no_reset:
                    trigger_bootloader_rts(ser, verbose=args.verbose)

                # 步骤 2：SVL 协议交互
                entered_bootloader = phase_setup(ser, verbose=args.verbose)

                if entered_bootloader:
                    bl_success = phase_bootload(ser, args.binfile, verbose=args.verbose)
                    if bl_success:
                        break
                else:
                    if args.verbose:
                        print("  [debug] bootloader 未响应，重试")

        except serial.SerialException as e:
            print(f"[ERROR] 串口错误: {e}")
            # 列出可用端口
            devices = list_ports.comports()
            for dev in devices:
                print(f"  {dev.device}: {dev.description}")
            sys.exit(1)

    if not entered_bootloader:
        print("\n[FAILED] 目标未进入 bootloader 模式")
        print("\n可能原因：")
        print("  1. CH340 RTS 信号没有正确触发 RC 电路")
        print("  2. C35/C36/R14/R20 焊接问题")
        print("  3. 芯片确实没有预装 SVL bootloader")
        print("\n建议：")
        print("  - 检查 C35, C36, R14, R20 焊接")
        print("  - 尝试用万用表测量 NRESET 和 BOOT 引脚电压")
        print("  - 最终方案：焊接 U19 排针，使用 SWD 调试器")
        sys.exit(1)

    if bl_success:
        print("\n[SUCCESS] 烧录完成！")
        sys.exit(0)
    else:
        print("\n[FAILED] 烧录失败")
        sys.exit(1)


if __name__ == '__main__':
    main()
