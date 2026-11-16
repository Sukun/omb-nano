#!/usr/bin/env python3
"""OMB-Nano BLE 采集接收端（Bleak Central，协议 v1）。

流程：发现并连接板子 → 订阅 Data 通知 → 写 START（Write With Response）→
按 BEGIN/DATA/END 重组并校验 600 条九列 CSV → 在独占会话目录内确认正式文件。

协议常量的唯一权威是 src/ble_capture_engine.h（namespace ble_capture）。
本文件只在 CLI 接收端复制这些常量；tests/test_ble_receiver.py 会读取该头文件、
编译固件编码器并做字节级比对，防止两边漂移。

一次运行对应板端一次启动：板端 DONE 之后不再接受第二次 START，本工具也不做
重连续接。协议解析、重组、验证与文件确认逻辑与真实 Bleak 连接分离，主机模拟
测试见 tests/test_ble_receiver.py；模拟结果不代表真机 BLE 性能。

用法：
  python3 -m venv .venv-ble-receiver
  .venv-ble-receiver/bin/pip install -r firmware/requirements-ble-receiver.txt
  .venv-ble-receiver/bin/python firmware/ble_capture_receive.py
  .venv-ble-receiver/bin/python firmware/ble_capture_receive.py --address <地址> --out ~/ble_captures

退出码：0=完整实验且正式 CSV 已确认；1=环境/设备选择失败；2=协议异常；
3=采集不完整（缺 END、缺片、数量/状态不符、断连、超时、中断）；4=文件安全/确认失败。
"""

import argparse
import asyncio
import math
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 协议 v1 常量。权威定义：src/ble_capture_engine.h（namespace ble_capture）。
ADVERTISED_NAME = "OMB-Nano"
SERVICE_UUID = "4F4D424E-4E41-4E4F-824C-850000000001"
# Control：BLEWrite（write with response）only。
CONTROL_CHARACTERISTIC_UUID = "4F4D424E-4E41-4E4F-824C-850000000002"
# Data：BLENotify only，单条通知最多 20 字节。
DATA_CHARACTERISTIC_UUID = "4F4D424E-4E41-4E4F-824C-850000000003"

PROTOCOL_VERSION = 0x01

# START (host -> Control): 0x01 + version + 8 字节 run_id
# BEGIN (Data):            0x11 + version + 8 字节 run_id + uint16 expected_count
# DATA  (Data):            0x12 + uint16 seq + uint8 fragment_index + uint8 fragment_count
#                          + 1..15 字节 CSV
# END   (Data):            0x13 + 8 字节 run_id + uint16 actual_count + uint8 capture_status
# 多字节字段一律小端。
OPCODE_START = 0x01
OPCODE_BEGIN = 0x11
OPCODE_DATA = 0x12
OPCODE_END = 0x13

RUN_ID_LENGTH = 8
START_LENGTH = 2 + RUN_ID_LENGTH
BEGIN_LENGTH = 2 + RUN_ID_LENGTH + 2
DATA_HEADER_LENGTH = 1 + 2 + 1 + 1
END_LENGTH = 1 + RUN_ID_LENGTH + 2 + 1

MAX_NOTIFICATION_LENGTH = 20
MAX_FRAGMENT_PAYLOAD = MAX_NOTIFICATION_LENGTH - DATA_HEADER_LENGTH
RECORD_BUFFER_LENGTH = 256
EXPECTED_SAMPLE_COUNT = 600
CAPTURE_STATUS_OK = 0
CAPTURE_STATUS_FAILED = 1

# 实时性判定裕量：T_first 至少比板端时间跨度短这么多秒才算“充分裕量”。
REALTIME_MARGIN_SECONDS = 1.0

DEFAULT_OUT = Path(__file__).resolve().parent / "ble_captures"


class ReceiverError(Exception):
    def __init__(self, message, exit_code=1):
        super().__init__(message)
        self.exit_code = exit_code


class ProtocolError(Exception):
    """通知不符合协议 v1。"""


class RowError(Exception):
    """一条重组后的记录不是合法的九列 CSV。"""


class FileSafetyError(Exception):
    """独占会话目录或正式文件的安全前提不成立。"""


@dataclass(frozen=True)
class Begin:
    run_id: bytes
    expected_count: int


@dataclass(frozen=True)
class Data:
    seq: int
    index: int
    count: int
    payload: bytes


@dataclass(frozen=True)
class End:
    run_id: bytes
    actual_count: int
    status: int


@dataclass
class CaptureResult:
    complete: bool = False
    verified: bool = False
    end_received: bool = False
    actual_count: int | None = None
    capture_status: int | None = None
    records: int = 0
    protocol_violations: int = 0
    queue_overflow: int = 0
    reasons: list = field(default_factory=list)
    realtime: str = "未确认"
    exit_code: int = 3
    session_dir: Path | None = None
    partial_path: Path | None = None
    final_path: Path | None = None
    diagnostics_path: Path | None = None


def encode_start(run_id):
    if len(run_id) != RUN_ID_LENGTH:
        raise ValueError(f"run_id 必须是 {RUN_ID_LENGTH} 字节")
    return bytes([OPCODE_START, PROTOCOL_VERSION]) + bytes(run_id)


def parse_notification(payload):
    """把一条通知解析为 Begin/Data/End；结构或长度不合法时抛 ProtocolError。"""
    if not payload:
        raise ProtocolError("空通知")
    opcode = payload[0]
    if opcode == OPCODE_BEGIN:
        if len(payload) != BEGIN_LENGTH:
            raise ProtocolError(f"BEGIN 长度 {len(payload)} != {BEGIN_LENGTH}")
        if payload[1] != PROTOCOL_VERSION:
            raise ProtocolError(f"BEGIN 协议版本 {payload[1]} 不受支持")
        return Begin(payload[2:2 + RUN_ID_LENGTH],
                     int.from_bytes(payload[10:12], "little"))
    if opcode == OPCODE_DATA:
        if not DATA_HEADER_LENGTH + 1 <= len(payload) <= MAX_NOTIFICATION_LENGTH:
            raise ProtocolError(
                f"DATA 长度 {len(payload)} 不在 "
                f"{DATA_HEADER_LENGTH + 1}..{MAX_NOTIFICATION_LENGTH}")
        seq = int.from_bytes(payload[1:3], "little")
        index = payload[3]
        count = payload[4]
        body = payload[DATA_HEADER_LENGTH:]
        if count == 0:
            raise ProtocolError("DATA fragment_count 为 0")
        if index >= count:
            raise ProtocolError(f"DATA fragment_index {index} >= fragment_count {count}")
        if not 1 <= len(body) <= MAX_FRAGMENT_PAYLOAD:
            raise ProtocolError(f"DATA 负载长度 {len(body)} 不在 1..{MAX_FRAGMENT_PAYLOAD}")
        return Data(seq, index, count, body)
    if opcode == OPCODE_END:
        if len(payload) != END_LENGTH:
            raise ProtocolError(f"END 长度 {len(payload)} != {END_LENGTH}")
        return End(payload[1:1 + RUN_ID_LENGTH],
                   int.from_bytes(payload[9:11], "little"),
                   payload[11])
    if opcode == OPCODE_START:
        raise ProtocolError("收到只应由主机发送的 START 操作码")
    raise ProtocolError(f"未知操作码 0x{opcode:02X}")


def parse_record_row(raw, seq):
    """校验一条原始九列 CSV 记录，返回 (板端 ms, 七个浮点值)。"""
    if not raw.endswith(b"\n"):
        raise RowError("记录不以 '\\n' 结束")
    try:
        text = raw[:-1].decode("ascii")
    except UnicodeDecodeError:
        raise RowError("记录不是 ASCII 文本")
    if "\r" in text:
        raise RowError("记录含 CR")
    if "\n" in text:
        raise RowError("记录含多余换行（一个 seq 只能对应一行）")
    fields = text.split(",")
    if len(fields) != 9:
        raise RowError(f"列数 {len(fields)} != 9")
    if not fields[0].isdigit():
        raise RowError("ms 字段不是十进制整数")
    if not fields[1].isdigit():
        raise RowError("sequence 字段不是十进制整数")
    if int(fields[1]) != seq:
        raise RowError(f"行内 seq {fields[1]} != 通知头部 seq {seq}")
    values = []
    for text_value in fields[2:]:
        try:
            value = float(text_value)
        except ValueError:
            raise RowError(f"浮点字段无法解析: {text_value!r}")
        if not math.isfinite(value):
            raise RowError(f"浮点字段不是有限数值: {text_value!r}")
        values.append(value)
    return int(fields[0]), values


class _Record:
    __slots__ = ("count", "fragments", "poisoned", "complete")

    def __init__(self):
        self.count = None
        self.fragments = {}
        self.poisoned = False
        self.complete = False


class NotificationQueue:
    """有界通知队列；回调只做入队，溢出必须被记录而不是静默丢弃。"""

    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("队列容量必须 >= 1")
        self.capacity = capacity
        self.overflow_count = 0
        self._queue = queue.Queue(maxsize=capacity)

    def offer(self, payload, arrival):
        try:
            self._queue.put_nowait((payload, arrival))
        except queue.Full:
            self.overflow_count += 1
            return False
        return True

    def get(self, timeout):
        return self._queue.get(timeout=timeout)

    def depth(self):
        return self._queue.qsize()


def make_notification_handler(notifications, clock=time.monotonic):
    """Bleak 回调：标记到达时间后立刻入队，不做解析或文件操作。"""

    def handler(_characteristic, data):
        notifications.offer(bytes(data), clock())

    return handler


class CaptureSession:
    """协议状态机 + 分片重组 + CSV 校验 + 独占目录文件安全。

    与 Bleak 无关：测试直接喂构造好的通知序列。全部公开方法都在锁内执行，
    回调线程/主线程并发调用是安全的。
    """

    WAIT_BEGIN = "WAIT_BEGIN"
    RECEIVING = "RECEIVING"
    FINALIZING = "FINALIZING"
    FINISHED = "FINISHED"

    def __init__(self, run_id, base_dir, *, expected_count=EXPECTED_SAMPLE_COUNT,
                 clock=time.monotonic, now=datetime.now):
        if len(run_id) != RUN_ID_LENGTH:
            raise ValueError(f"run_id 必须是 {RUN_ID_LENGTH} 字节")
        self.run_id = bytes(run_id)
        self.expected_count = expected_count
        self._clock = clock
        self.base_dir = Path(base_dir).expanduser()
        stamp = now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = self.base_dir / f"{stamp}_{self.run_id.hex()}"
        self.partial_path = self.session_dir / "capture.partial"
        self.final_path = self.session_dir / "capture.csv"
        self.diagnostics_path = self.session_dir / "diagnostics.log"

        self.state = self.WAIT_BEGIN
        self.start_submitted_at = None
        self.begin_arrival = None
        self.end_arrival = None
        self.first_record_arrival = None
        self.end = None

        self._lock = threading.RLock()
        self._opened_at = clock()
        self._finished = False
        self.result = None
        self._partial = None
        self._diagnostics = None
        self._file_error = None
        self._confirm_error = None

        self._records = {}
        self._row_ms = {}
        self._valid_records = 0
        self._written_rows = 0
        self._next_write_seq = 0
        self._write_order_ok = True
        self._violations = []
        self._queue_overflow_seen = 0
        self._bytes_written = 0
        self._notifications = 0
        self._post_end_notified = False

        self._open_exclusive_files()

    # -- 文件安全 ---------------------------------------------------------
    def _open_exclusive_files(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(self.session_dir)  # 排他创建：已存在就失败，绝不复用
        except FileExistsError as exc:
            raise FileSafetyError(f"会话目录已存在，拒绝复用: {self.session_dir}") from exc
        self._partial = open(self.partial_path, "xb")
        try:
            self._diagnostics = open(self.diagnostics_path, "x", encoding="utf-8")
        except FileExistsError as exc:
            self._partial.close()
            raise FileSafetyError(f"诊断日志已存在: {self.diagnostics_path}") from exc
        self._diag(f"# run_id={self.run_id.hex()} expected_count={self.expected_count}")
        self._diag(f"# 会话目录={self.session_dir}")
        self._diag(f"# .partial={self.partial_path}")

    def _diag(self, text):
        if self._diagnostics is None:
            return
        self._diagnostics.write(f"[{self._clock() - self._opened_at:9.3f}s] {text}\n")
        self._diagnostics.flush()

    def _protocol_error(self, text):
        self._violations.append(text)
        self._diag(f"协议错误: {text}")

    def _fail_file(self, text):
        self._file_error = text
        self._violations.append(text)
        try:
            self._diag(f"文件错误: {text}")
        except OSError:
            pass

    def note_start_submitted(self, monotonic_time):
        with self._lock:
            self.start_submitted_at = monotonic_time
            self._diag("已记录主机 START 提交单调时间")

    def note_queue_overflow(self, count):
        with self._lock:
            if count <= self._queue_overflow_seen:
                return
            self._queue_overflow_seen = count
            self._violations.append(f"通知队列溢出 {count} 次，主机消费不足")
            self._diag(f"队列溢出: 已丢弃 {count} 条通知（主机消费不足）")

    # -- 通知处理 ---------------------------------------------------------
    @property
    def ended(self):
        return self.state == self.FINALIZING

    def handle_notification(self, payload, arrival=None):
        with self._lock:
            if self._finished:
                return
            if self.ended:
                if not self._post_end_notified:
                    self._post_end_notified = True
                    self._protocol_error(
                        f"END 之后仍收到通知: {bytes(payload).hex(' ')}")
                return
            if arrival is None:
                arrival = self._clock()
            self._notifications += 1
            try:
                message = parse_notification(payload)
            except ProtocolError as exc:
                self._protocol_error(f"{exc}: {bytes(payload).hex(' ')}")
                return
            if isinstance(message, Begin):
                self._on_begin(message, arrival)
            elif isinstance(message, Data):
                self._on_data(message, arrival)
            else:
                self._on_end(message, arrival)

    def _on_begin(self, message, arrival):
        if self.state != self.WAIT_BEGIN:
            self._protocol_error("重复 BEGIN")
            return
        if message.run_id != self.run_id:
            self._protocol_error(
                f"BEGIN run_id {message.run_id.hex()} != 本轮 {self.run_id.hex()}")
            return
        if message.expected_count != self.expected_count:
            self._protocol_error(
                f"BEGIN expected_count {message.expected_count} != {self.expected_count}")
            return
        self.state = self.RECEIVING
        self.begin_arrival = arrival
        self._diag(f"BEGIN run_id 匹配，expected_count={message.expected_count}")

    def _on_data(self, message, arrival):
        if self.state != self.RECEIVING:
            self._protocol_error(f"BEGIN 之前收到 DATA: seq={message.seq}")
            return
        if message.seq >= self.expected_count:
            self._protocol_error(f"DATA seq {message.seq} 超出 0..{self.expected_count - 1}")
            return

        record = self._records.get(message.seq)
        if record is None:
            record = self._records[message.seq] = _Record()
        if record.complete:
            self._protocol_error(f"seq {message.seq} 已完整，仍收到分片")
            return
        if record.poisoned:
            return
        if record.count is None:
            record.count = message.count
        elif record.count != message.count:
            record.poisoned = True
            self._protocol_error(
                f"seq {message.seq} fragment_count 矛盾: "
                f"{record.count} vs {message.count}")
            return
        existing = record.fragments.get(message.index)
        if existing is not None:
            record.poisoned = True
            if existing == message.payload:
                self._protocol_error(
                    f"seq {message.seq} 分片 {message.index} 重复")
            else:
                self._protocol_error(
                    f"seq {message.seq} 分片 {message.index} 内容冲突")
            return
        record.fragments[message.index] = message.payload
        if len(record.fragments) == record.count:
            self._complete_record(message.seq, record, arrival)

    def _complete_record(self, seq, record, arrival):
        raw = b"".join(record.fragments[index] for index in range(record.count))
        if len(raw) > RECORD_BUFFER_LENGTH:
            record.poisoned = True
            self._protocol_error(
                f"seq {seq} 记录长度 {len(raw)} 超过固件缓冲 {RECORD_BUFFER_LENGTH}")
            return
        try:
            ms, _values = parse_record_row(raw, seq)
        except RowError as exc:
            record.poisoned = True
            self._protocol_error(f"seq {seq} CSV 无效: {exc}")
            return
        record.complete = True
        self._row_ms[seq] = ms
        self._valid_records += 1
        self._diag(f"记录 seq={seq} ms={ms} len={len(raw)}")
        if self.first_record_arrival is None:
            self.first_record_arrival = arrival
            self._diag(f"第一条完整合法记录 seq={seq} 到达")
        self._write_record(seq, raw)

    def _write_record(self, seq, raw):
        # 按完成顺序立即写盘：缺片时后续记录仍被保留，不丢已取得的数据。
        if seq != self._next_write_seq:
            self._write_order_ok = False
            self._diag(
                f"记录 seq={seq} 先于 seq={self._next_write_seq} 完成，"
                "完成顺序与 seq 不一致")
            self._next_write_seq = max(self._next_write_seq, seq + 1)
        else:
            self._next_write_seq = seq + 1
        if self._file_error is not None:
            return
        try:
            self._partial.write(raw)
            self._partial.flush()
        except OSError as exc:
            self._fail_file(f"写入 .partial 失败: {exc}")
            return
        self._bytes_written += len(raw)
        self._written_rows += 1

    def _on_end(self, message, arrival):
        if self.state != self.RECEIVING:
            self._protocol_error("BEGIN 之前收到 END")
            self.end = message
            self.end_arrival = arrival
            self.state = self.FINALIZING
            return
        if message.run_id != self.run_id:
            self._protocol_error(
                f"END run_id {message.run_id.hex()} != 本轮 {self.run_id.hex()}")
            return
        if message.actual_count > self.expected_count:
            self._protocol_error(
                f"END actual_count {message.actual_count} > {self.expected_count}")
        if message.status not in (CAPTURE_STATUS_OK, CAPTURE_STATUS_FAILED):
            self._protocol_error(f"END capture_status {message.status} 不是协议值")
        self.end = message
        self.end_arrival = arrival
        self.state = self.FINALIZING
        self._diag(
            f"END 收到 actual_count={message.actual_count} status={message.status} "
            f"到达={arrival - self._opened_at:.3f}s")

    # -- 结束与判定 -------------------------------------------------------
    def _close_partial(self):
        if self._partial is None:
            return self._file_error is None
        handle, self._partial = self._partial, None
        try:
            if self._file_error is None:
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, ValueError, AttributeError) as exc:
            self._fail_file(f"flush/fsync .partial 失败: {exc}")
        try:
            handle.close()
        except OSError as exc:
            self._fail_file(f"关闭 .partial 失败: {exc}")
        return self._file_error is None

    def _missing_sequences(self):
        return [seq for seq in range(self.expected_count) if seq not in self._row_ms]

    def _clock_continuous(self):
        previous = None
        for seq in range(self.expected_count):
            ms = self._row_ms.get(seq)
            if ms is None:
                continue
            if previous is not None and ms < previous:
                return False, seq
            previous = ms
        return True, None

    def _realtime_evidence(self, complete):
        if self.first_record_arrival is None:
            return "未确认", None, None
        if self.end_arrival is not None and self.first_record_arrival > self.end_arrival:
            return "未通过", None, None
        if not complete or self.start_submitted_at is None:
            return "未确认", None, None
        t_first = self.first_record_arrival - self.start_submitted_at
        span_ms = self._row_ms[self.expected_count - 1] - self._row_ms[0]
        t_capture = span_ms / 1000.0
        if t_first + REALTIME_MARGIN_SECONDS <= t_capture:
            return "通过", t_first, t_capture
        return "未确认", t_first, t_capture

    def _evaluate(self):
        reasons = []
        if self.end is None:
            reasons.append("缺少 END")
        else:
            if self.end.actual_count != self.expected_count:
                reasons.append(
                    f"actual_count {self.end.actual_count} != {self.expected_count}")
            if self.end.status != CAPTURE_STATUS_OK:
                reasons.append(f"capture_status {self.end.status} 不是 0（成功）")
        if self._valid_records != self.expected_count:
            reasons.append(
                f"合法完整记录 {self._valid_records} != {self.expected_count}")
        missing = self._missing_sequences()
        if missing:
            reasons.append(
                f"缺片/不完整记录 {len(missing)} 条（如 {missing[:10]}）")
        if self._written_rows != self.expected_count:
            reasons.append(
                f".partial 已写 {self._written_rows} 条 != {self.expected_count}")
        if not self._write_order_ok:
            reasons.append("记录未按 seq 顺序写入")
        continuous, bad_seq = self._clock_continuous()
        if not continuous:
            reasons.append(f"板端 ms 在 seq={bad_seq} 处倒退")
        if self._violations:
            reasons.append(f"协议/文件异常 {len(self._violations)} 项")
        if self._queue_overflow_seen:
            reasons.append(f"通知队列溢出 {self._queue_overflow_seen} 次")
        if self._file_error is not None:
            reasons.append(f"文件操作失败: {self._file_error}")
        return not reasons, reasons

    def _confirm(self):
        try:
            if os.path.lexists(self.final_path):
                raise FileSafetyError(f"目标文件已存在，拒绝覆盖: {self.final_path}")
            os.replace(self.partial_path, self.final_path)
            size = self.final_path.stat().st_size
            if size != self._bytes_written:
                raise FileSafetyError(
                    f"确认后文件大小 {size} != 写入字节 {self._bytes_written}")
        except (OSError, FileSafetyError) as exc:
            self._confirm_error = str(exc)
            self._diag(f"正式文件确认失败: {exc}")
            return False
        self._diag(f"正式文件确认完成: {self.final_path} bytes={self._bytes_written}")
        return True

    def finish(self, reason=""):
        with self._lock:
            if self._finished:
                return self.result
            self._finished = True
            if self.state != self.FINALIZING:
                self._diag(f"结束原因: {reason or '未知'}")
            elif reason:
                self._diag(f"结束原因: {reason}")
            file_ok = self._close_partial()
            complete, reasons = self._evaluate()
            if not file_ok:
                complete = False
            realtime, t_first, t_capture = self._realtime_evidence(complete)
            verified = False
            if complete:
                verified = self._confirm()
                if not verified:
                    reasons.append(f"正式文件确认失败: {self._confirm_error}")

            self.state = self.FINISHED
            self._diag(f"协议违规 {len(self._violations)} 项 / 通知 {self._notifications} 条")
            for text in self._violations:
                self._diag(f"  - {text}")
            self._diag(
                f"记录 valid={self._valid_records} written={self._written_rows} "
                f"bytes={self._bytes_written}")
            if 0 in self._row_ms and self.expected_count - 1 in self._row_ms:
                first_ms = self._row_ms[0]
                last_ms = self._row_ms[self.expected_count - 1]
                self._diag(
                    f"板端 ms: ms[0]={first_ms} ms[{self.expected_count - 1}]={last_ms} "
                    f"span={(last_ms - first_ms) / 1000.0:.3f}s")
            if t_first is not None:
                self._diag(
                    f"实时性: T_first={t_first:.3f}s T_capture={t_capture:.3f}s "
                    f"判定={realtime}")
            else:
                self._diag(f"实时性: 判定={realtime}")
            self._diag("结果: " + ("COMPLETE" if verified else "INCOMPLETE"))
            for text in reasons:
                self._diag(f"  ! {text}")

            if complete and verified:
                exit_code = 0
            elif self._file_error is not None or self._confirm_error is not None:
                exit_code = 4
            elif self._violations:
                exit_code = 2
            else:
                exit_code = 3

            self.result = CaptureResult(
                complete=bool(complete),
                verified=verified,
                end_received=self.end is not None,
                actual_count=None if self.end is None else self.end.actual_count,
                capture_status=None if self.end is None else self.end.status,
                records=self._valid_records,
                protocol_violations=len(self._violations),
                queue_overflow=self._queue_overflow_seen,
                reasons=reasons,
                realtime=realtime,
                exit_code=exit_code,
                session_dir=self.session_dir,
                partial_path=self.partial_path,
                final_path=self.final_path if verified else None,
                diagnostics_path=self.diagnostics_path,
            )
            try:
                self._diagnostics.flush()
                self._diagnostics.close()
            except OSError:
                pass
            self._diagnostics = None
            return self.result


def run_pump(session, notifications, *, connected=lambda: True,
             interrupted=lambda: False, clock=time.monotonic,
             begin_timeout=20.0, end_timeout=180.0, poll_interval=0.05):
    """接收主循环：消费队列；END/断连/超时/中断时结束并返回结果。"""
    start = session.start_submitted_at or clock()
    begin_deadline = start + begin_timeout
    end_deadline = start + end_timeout
    while True:
        session.note_queue_overflow(notifications.overflow_count)
        if session.ended:
            return session.finish("END")
        if interrupted():
            return session.finish("用户中断")
        if not connected():
            return session.finish("连接断开")
        now = clock()
        if session.state == CaptureSession.WAIT_BEGIN and now >= begin_deadline:
            return session.finish("等待 BEGIN 超时")
        if now >= end_deadline:
            return session.finish("等待 END 超时")
        try:
            payload, arrival = notifications.get(poll_interval)
        except queue.Empty:
            continue
        session.handle_notification(payload, arrival)


async def select_device(BleakScanner, args):
    if args.address:
        return args.address
    devices = await BleakScanner.discover(timeout=args.scan_timeout)
    matches = [device for device in devices if (device.name or "") == ADVERTISED_NAME]
    if not matches:
        raise ReceiverError(
            f"未发现广播名为 {ADVERTISED_NAME} 的设备；确认板子已上电并烧录 BLE 采集固件，"
            "或用 --address 指定。")
    if len(matches) > 1:
        listing = "、".join(f"{device.address}" for device in matches)
        raise ReceiverError(f"发现多个 {ADVERTISED_NAME}（{listing}），请用 --address 指定。")
    return matches[0]


async def run_ble_session(args, session):
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise ReceiverError(
            "缺少 bleak。按 firmware/requirements-ble-receiver.txt 创建 "
            ".venv-ble-receiver 后运行本脚本。") from exc

    device = await select_device(BleakScanner, args)
    notifications = NotificationQueue(args.queue_size)
    handler = make_notification_handler(notifications)
    try:
        async with BleakClient(device) as client:
            # 板端只在 Data 已订阅时接受 START，所以先订阅再写 START。
            await client.start_notify(DATA_CHARACTERISTIC_UUID, handler)
            session.note_start_submitted(time.monotonic())
            await client.write_gatt_char(
                CONTROL_CHARACTERISTIC_UUID, encode_start(session.run_id), response=True)
            return await asyncio.to_thread(
                run_pump, session, notifications,
                connected=lambda: client.is_connected,
                begin_timeout=args.begin_timeout,
                end_timeout=args.end_timeout)
    except ReceiverError:
        raise
    except Exception as exc:  # BleakError 等运行期异常
        raise ReceiverError(f"BLE 连接/接收失败: {exc}", exit_code=3) from exc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="通过 BLE 接收 OMB-Nano 采集协议 v1 的 600 条九列 CSV。")
    parser.add_argument("--address", help="设备地址/UUID；默认扫描广播名 OMB-Nano")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help=f"会话根目录，默认 {DEFAULT_OUT}")
    parser.add_argument("--scan-timeout", type=float, default=10.0, help="扫描秒数（默认 10）")
    parser.add_argument("--begin-timeout", type=float, default=20.0,
                        help="START 后等待 BEGIN 的秒数（默认 20）")
    parser.add_argument("--end-timeout", type=float, default=180.0,
                        help="START 后等待 END 的总秒数（默认 180）")
    parser.add_argument("--queue-size", type=int, default=1024,
                        help="通知队列容量（默认 1024，溢出会被记录）")
    return parser.parse_args(argv)


def print_summary(result):
    state = "COMPLETE（正式 CSV 已确认）" if result.verified else "INCOMPLETE"
    print(f"结果: {state}")
    print(f"  记录: {result.records}  协议违规: {result.protocol_violations}  "
          f"队列溢出: {result.queue_overflow}")
    if result.end_received:
        print(f"  END: actual_count={result.actual_count} status={result.capture_status}")
    else:
        print("  END: 未收到")
    print(f"  实时性证据: {result.realtime}")
    for reason in result.reasons:
        print(f"  - {reason}")
    print(f"  会话目录: {result.session_dir}")
    print(f"  诊断日志: {result.diagnostics_path}")
    if result.verified:
        print(f"  正式 CSV: {result.final_path}")
    else:
        print(f"  .partial: {result.partial_path}")
    print("说明: 本工具不做真实 BLE 性能判定之外的推断；实时性结论仅基于主机单调时间。")


def main(argv=None):
    args = parse_args(argv)
    run_id = os.urandom(RUN_ID_LENGTH)
    try:
        session = CaptureSession(run_id, Path(args.out))
    except (FileSafetyError, OSError) as exc:
        print(f"[ERROR] 无法创建独占会话目录: {exc}", file=sys.stderr)
        return 4
    print(f"run_id={run_id.hex()}  会话目录={session.session_dir}")
    error_code = None
    try:
        result = asyncio.run(run_ble_session(args, session))
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断：保留 .partial 与诊断日志。", file=sys.stderr)
        result = session.finish("用户中断")
    except ReceiverError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        error_code = exc.exit_code
        result = session.finish(f"接收失败: {exc}")
    except OSError as exc:
        print(f"[ERROR] 主机 I/O 失败: {exc}", file=sys.stderr)
        error_code = 4
        result = session.finish(f"主机 I/O 失败: {exc}")
    print_summary(result)
    return error_code if error_code is not None else result.exit_code


if __name__ == "__main__":
    sys.exit(main())
