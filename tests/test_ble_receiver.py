"""主机检查：PLAN_03 BLE 接收端的协议解析、重组、CSV 校验与文件安全；不需要板子。

全部通知序列都是构造出来的：这些结果只证明接收端逻辑，不代表真机 BLE 性能。
协议常量与编码还和 src/ble_capture_engine.h 的真实固件代码做字节级比对。
"""

import importlib.util
import queue
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "firmware/ble_capture_receive.py"
HEADER = REPO / "src/ble_capture_engine.h"

_SPEC = importlib.util.spec_from_file_location("ble_capture_receive", TOOL)
receiver = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = receiver
_SPEC.loader.exec_module(receiver)

RUN_ID = bytes(range(1, 9))
FLOATS = (0.0, 0.0, 9.81, 0.0, 0.0, 0.0, 9.8)


def u16(value):
    return value.to_bytes(2, "little")


def begin_packet(run_id=RUN_ID, expected=600, version=1):
    return bytes([receiver.OPCODE_BEGIN, version]) + run_id + u16(expected)


def end_packet(run_id=RUN_ID, actual=600, status=0):
    return (bytes([receiver.OPCODE_END]) + run_id + u16(actual) + bytes([status]))


def fragments(seq, raw, size=15):
    chunks = [raw[index:index + size] for index in range(0, len(raw), size)]
    return [bytes([receiver.OPCODE_DATA]) + u16(seq) + bytes([index, len(chunks)]) + chunk
            for index, chunk in enumerate(chunks)]


def row(ms, seq, values=FLOATS, width=0):
    ms_field = f"{ms:0{width}d}" if width else str(ms)
    return (ms_field + f",{seq}," + ",".join(f"{value:.6f}" for value in values)
            + "\n").encode("ascii")


def make_rows(count=600, start_ms=1_000_000, step=100):
    return [row(start_ms + index * step, index) for index in range(count)]


def record_packets(rows):
    packets = []
    for seq, raw in enumerate(rows):
        packets.extend(fragments(seq, raw))
    return packets


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def new_session(self, run_id=RUN_ID, **kwargs):
        return receiver.CaptureSession(run_id, self.root, **kwargs)

    def feed(self, session, packets, start=0.0, step=0.001):
        moment = start
        for packet in packets:
            session.handle_notification(packet, moment)
            moment += step
        return moment

    def diagnostics(self, session):
        return session.diagnostics_path.read_text(encoding="utf-8")

    def partial_lines(self, session):
        return session.partial_path.read_bytes().count(b"\n")


class NormalFlowTest(SessionTest):
    def test_normal_600_records_are_byte_identical(self):
        rows = make_rows()
        session = self.new_session()
        self.assertIn(RUN_ID.hex(), session.session_dir.name)
        session.note_start_submitted(0.0)
        packets = [begin_packet()] + record_packets(rows) + [end_packet()]
        self.feed(session, packets, start=0.01)

        result = session.finish()

        self.assertTrue(result.verified, result.reasons)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.records, 600)
        self.assertEqual(result.actual_count, 600)
        self.assertEqual(result.capture_status, 0)
        self.assertEqual(result.realtime, "通过")
        self.assertEqual(session.final_path.read_bytes(), b"".join(rows))
        self.assertFalse(session.partial_path.exists())
        text = self.diagnostics(session)
        self.assertIn("END 收到 actual_count=600 status=0", text)
        self.assertIn("判定=通过", text)
        self.assertIn("结果: COMPLETE", text)

    def test_fragment_boundaries_do_not_assume_newline_position(self):
        rows = []
        for seq in range(600):
            # 每 15 字节一个分片；末尾宽度变化让换行落在分片中间或边界上。
            rows.append(row(1_000_000 + seq * 100, seq, width=6 + seq % 7))
        self.assertTrue(any(len(raw) % 15 == 0 for raw in rows))

        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows) + [end_packet()])

        result = session.finish()
        self.assertTrue(result.verified, result.reasons)
        self.assertEqual(session.final_path.read_bytes(), b"".join(rows))

    def test_fragments_in_reverse_order_reassemble(self):
        rows = make_rows(count=3)
        session = self.new_session()
        session.note_start_submitted(0.0)
        packets = [begin_packet()]
        for seq, raw in enumerate(rows):
            packets.extend(reversed(fragments(seq, raw)))
        packets.append(end_packet(actual=3, status=1))
        self.feed(session, packets)

        result = session.finish()
        self.assertEqual(session.partial_path.read_bytes(), b"".join(rows))
        self.assertEqual(result.records, 3)
        self.assertEqual(result.protocol_violations, 0)

    def test_maximum_record_length_boundary(self):
        # ms 字段补到 190 位，整行恰好 256 字节（固件 record_buffer_length）。
        max_row = row(int("1" + "0" * 189), 0)
        too_long = row(int("1" + "0" * 190), 0)
        self.assertEqual(len(max_row), 256)
        self.assertEqual(len(too_long), 257)

        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + fragments(0, max_row)
                  + fragments(1, too_long) + [end_packet(actual=2, status=1)])
        result = session.finish()

        self.assertEqual(result.records, 1)
        self.assertEqual(session.partial_path.read_bytes(), max_row)
        self.assertIn("超过固件缓冲 256", self.diagnostics(session))
        self.assertFalse(result.verified)

    def test_second_run_does_not_touch_first_result(self):
        rows = make_rows()
        first = self.new_session(run_id=b"\x11" * 8)
        first.note_start_submitted(0.0)
        self.feed(first, [begin_packet(b"\x11" * 8)] + record_packets(rows)
                  + [end_packet(b"\x11" * 8)])
        self.assertTrue(first.finish().verified)
        original = first.final_path.read_bytes()

        second = self.new_session(run_id=b"\x22" * 8)
        second.note_start_submitted(0.0)
        self.feed(second, [begin_packet(b"\x22" * 8)] + record_packets(rows[:2])
                  + [end_packet(b"\x22" * 8, actual=2, status=1)])
        second.finish()

        self.assertEqual(first.final_path.read_bytes(), original)
        self.assertFalse(second.final_path.exists())
        self.assertNotEqual(first.session_dir, second.session_dir)

    def test_exclusive_session_directory_is_never_reused(self):
        fixed = lambda: datetime(2026, 1, 2, 3, 4, 5)  # noqa: E731
        self.new_session(now=fixed)
        with self.assertRaises(receiver.FileSafetyError):
            self.new_session(now=fixed)


class ReassemblyAnomalyTest(SessionTest):
    def test_missing_fragment_keeps_other_records_and_diagnostics(self):
        rows = make_rows()
        session = self.new_session()
        session.note_start_submitted(0.0)
        packets = [begin_packet()]
        for seq, raw in enumerate(rows):
            pieces = fragments(seq, raw)
            if seq == 10:
                pieces = pieces[1:]  # 丢掉第一个分片
            packets.extend(pieces)
        packets.append(end_packet())
        self.feed(session, packets)

        result = session.finish()

        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(self.partial_lines(session), 599)
        self.assertEqual(session.partial_path.read_bytes(),
                         b"".join(rows[:10] + rows[11:]))
        self.assertIn("缺片/不完整记录 1 条", " ".join(result.reasons))

    def test_duplicate_fragment_is_reported(self):
        rows = make_rows()
        session = self.new_session()
        session.note_start_submitted(0.0)
        packets = [begin_packet()]
        for seq, raw in enumerate(rows):
            pieces = fragments(seq, raw)
            if seq == 5:
                pieces = [pieces[0]] + pieces  # 完整记录前重复第一个分片
            packets.extend(pieces)
        packets.append(end_packet())
        self.feed(session, packets)

        result = session.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.protocol_violations, 1)
        self.assertIn("分片 0 重复", self.diagnostics(session))
        self.assertFalse(session.final_path.exists())

    def test_conflicting_fragment_count_is_reported(self):
        rows = make_rows(count=2)
        session = self.new_session()
        session.note_start_submitted(0.0)
        pieces = fragments(0, rows[0])
        contradictory = (bytes([receiver.OPCODE_DATA]) + u16(0)
                         + bytes([0, len(pieces) + 1]) + b"x")
        self.feed(session, [begin_packet()] + pieces[:1] + [contradictory]
                  + pieces[1:] + fragments(1, rows[1]) + [end_packet(actual=2, status=1)])

        result = session.finish()
        self.assertFalse(result.verified)
        self.assertIn("fragment_count 矛盾", self.diagnostics(session))
        self.assertEqual(result.records, 1)  # seq 0 被排除，seq 1 仍被保留
        self.assertEqual(session.partial_path.read_bytes(), rows[1])

    def test_invalid_csv_rows_are_refused(self):
        cases = {
            "eight_columns": b"1,0,0.0,0.0,0.0,0.0,0.0,0.0\n",
            "seq_mismatch": b"1,1,0.0,0.0,0.0,0.0,0.0,0.0,0.0\n",
            "nan_value": b"1,0,0.0,0.0,nan,0.0,0.0,0.0,0.0\n",
            "no_newline": b"1,0,0.0,0.0,0.0,0.0,0.0,0.0,0.0",
            "carriage_return": b"1,0,0.0,0.0,0.0,0.0,0.0,0.0,0.0\r\n",
            "non_numeric": b"1,0,0.0,0.0,0.0,0.0,0.0,0.0,abc\n",
        }
        for index, (name, bad_row) in enumerate(cases.items()):
            with self.subTest(name=name):
                run_id = bytes([0x30 + index]) * 8
                session = self.new_session(run_id=run_id)
                session.note_start_submitted(0.0)
                self.feed(session, [begin_packet(run_id)] + fragments(0, bad_row)
                          + [end_packet(run_id, actual=1, status=0)])
                result = session.finish()
                self.assertFalse(result.verified)
                self.assertEqual(result.records, 0)
                self.assertEqual(session.partial_path.read_bytes(), b"")
                self.assertIn("CSV 无效", self.diagnostics(session))


class ProtocolStateTest(SessionTest):
    def test_data_before_begin_is_discarded(self):
        rows = make_rows(count=1)
        session = self.new_session()
        session.note_start_submitted(0.0)
        pieces = fragments(0, rows[0])
        self.feed(session, pieces)

        self.assertEqual(session.state, session.WAIT_BEGIN)
        self.assertEqual(session.partial_path.read_bytes(), b"")
        self.assertIn("BEGIN 之前收到 DATA", self.diagnostics(session))

        result = session.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.protocol_violations, len(pieces))

    def test_wrong_run_id_begin_is_rejected(self):
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet(run_id=b"\xff" * 8)])
        self.assertEqual(session.state, session.WAIT_BEGIN)
        self.assertIn("BEGIN run_id", self.diagnostics(session))
        result = session.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.protocol_violations, 1)

    def test_duplicate_begin_is_reported(self):
        rows = make_rows(count=1)
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet(), begin_packet()]
                  + record_packets(rows) + [end_packet(actual=1, status=1)])
        result = session.finish()
        self.assertEqual(result.protocol_violations, 1)
        self.assertIn("重复 BEGIN", self.diagnostics(session))
        self.assertFalse(result.verified)

    def test_illegal_lengths_and_unknown_messages_are_rejected(self):
        messages = {
            "empty": b"",
            "start_opcode": bytes([receiver.OPCODE_START, 1]) + RUN_ID,
            "begin_short": begin_packet()[:-1],
            "end_long": end_packet() + b"\x00",
            "data_short": bytes([receiver.OPCODE_DATA]) + u16(0) + b"\x00\x01",
            "data_long": bytes([receiver.OPCODE_DATA]) + u16(0) + b"\x00\x01" + b"x" * 16,
            "unknown": b"\x7f\x00\x00",
        }
        session = self.new_session()
        session.note_start_submitted(0.0)
        for payload in messages.values():
            session.handle_notification(payload, 0.0)
        result = session.finish()
        self.assertEqual(result.protocol_violations, len(messages))
        self.assertEqual(session.partial_path.read_bytes(), b"")
        text = self.diagnostics(session)
        self.assertIn("BEGIN 长度 11", text)
        self.assertIn("END 长度 13", text)
        self.assertIn("未知操作码 0x7F", text)
        self.assertIn("空通知", text)


class EndConditionTest(SessionTest):
    def test_init_failure_end_zero_status_failed(self):
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet(), end_packet(actual=0, status=1)])
        result = session.finish()

        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.actual_count, 0)
        self.assertEqual(result.capture_status, 1)
        self.assertEqual(session.partial_path.read_bytes(), b"")
        self.assertIn("actual_count 0 != 600", " ".join(result.reasons))

    def test_partial_capture_end_n_status_failed(self):
        rows = make_rows(count=100)
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows)
                  + [end_packet(actual=100, status=1)])
        result = session.finish()

        self.assertFalse(result.verified)
        self.assertEqual(result.actual_count, 100)
        self.assertEqual(result.capture_status, 1)
        self.assertEqual(session.partial_path.read_bytes(), b"".join(rows))
        self.assertFalse(session.final_path.exists())

    def test_missing_end_keeps_partial(self):
        rows = make_rows()
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows))
        result = session.finish("等待 END 超时")

        self.assertFalse(result.verified)
        self.assertFalse(result.end_received)
        self.assertIn("缺少 END", result.reasons)
        self.assertEqual(session.partial_path.read_bytes(), b"".join(rows))
        self.assertIn("等待 END 超时", self.diagnostics(session))

    def test_actual_count_mismatch_is_incomplete(self):
        rows = make_rows()
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows)
                  + [end_packet(actual=599, status=0)])
        result = session.finish()
        self.assertFalse(result.verified)
        self.assertIn("actual_count 599 != 600", " ".join(result.reasons))

    def test_unknown_capture_status_is_protocol_error(self):
        rows = make_rows()
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows)
                  + [end_packet(actual=600, status=2)])
        result = session.finish()
        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 2)
        self.assertIn("capture_status 2", self.diagnostics(session))

    def test_non_monotonic_board_time_is_incomplete(self):
        rows = make_rows()
        rows[300] = row(500_000, 300)  # 板端 ms 倒退
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows) + [end_packet()])
        result = session.finish()
        self.assertFalse(result.verified)
        self.assertIn("倒退", " ".join(result.reasons))


class QueueAndLoopTest(SessionTest):
    def test_queue_overflow_is_recorded(self):
        rows = make_rows(count=20)
        session = self.new_session()
        session.note_start_submitted(0.0)
        notifications = receiver.NotificationQueue(capacity=4)
        packets = [begin_packet()] + record_packets(rows) + [end_packet(actual=20, status=1)]
        for packet in packets:
            notifications.offer(packet, 0.0)

        result = receiver.run_pump(session, notifications, begin_timeout=0.2,
                                   end_timeout=0.5, poll_interval=0.001)

        self.assertGreater(notifications.overflow_count, 0)
        self.assertGreater(result.queue_overflow, 0)
        self.assertIn("队列溢出", " ".join(result.reasons))
        self.assertFalse(result.verified)

    def test_begin_timeout_keeps_partial_and_diagnostics(self):
        session = self.new_session()
        session.note_start_submitted(0.0)
        result = receiver.run_pump(session, receiver.NotificationQueue(8),
                                   begin_timeout=0.05, end_timeout=0.5,
                                   poll_interval=0.005)
        self.assertEqual(result.exit_code, 3)
        self.assertTrue(session.partial_path.exists())
        self.assertIn("等待 BEGIN 超时", self.diagnostics(session))

    def test_disconnect_stops_the_session(self):
        rows = make_rows(count=10)
        session = self.new_session()
        session.note_start_submitted(0.0)
        pending = [begin_packet()] + record_packets(rows)

        class DripQueue(receiver.NotificationQueue):
            def get(self, timeout):
                if pending:
                    return pending.pop(0), 0.0
                raise queue.Empty

        result = receiver.run_pump(session, DripQueue(64), connected=lambda: bool(pending),
                                   begin_timeout=5.0, end_timeout=5.0, poll_interval=0.001)
        self.assertFalse(result.verified)
        self.assertIn("缺少 END", result.reasons)
        self.assertEqual(session.partial_path.read_bytes(), b"".join(rows))


class FileSafetyTest(SessionTest):
    def test_write_failure_keeps_partial_and_fails(self):
        rows = make_rows(count=5)
        session = self.new_session()
        session.note_start_submitted(0.0)

        class WriteFailing:
            def __init__(self, handle):
                self._handle = handle

            def __getattr__(self, name):
                return getattr(self._handle, name)

            def write(self, data):
                raise OSError("disk full")

        session._partial = WriteFailing(session._partial)
        self.feed(session, [begin_packet()] + record_packets(rows)
                  + [end_packet(actual=5, status=1)])
        result = session.finish()

        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 4)
        self.assertIn("写入 .partial 失败", self.diagnostics(session))
        self.assertTrue(session.partial_path.exists())
        self.assertFalse(session.final_path.exists())

    def test_close_failure_is_reported(self):
        rows = make_rows(count=2)
        session = self.new_session()
        session.note_start_submitted(0.0)

        class CloseFailing:
            def __init__(self, handle):
                self._handle = handle

            def __getattr__(self, name):
                return getattr(self._handle, name)

            def close(self):
                self._handle.close()
                raise OSError("close failed")

        session._partial = CloseFailing(session._partial)
        self.feed(session, [begin_packet()] + record_packets(rows)
                  + [end_packet(actual=2, status=1)])
        result = session.finish()

        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 4)
        self.assertIn("关闭 .partial 失败", self.diagnostics(session))
        self.assertTrue(session.partial_path.exists())

    def test_existing_target_is_not_overwritten(self):
        rows = make_rows()
        session = self.new_session()
        session.final_path.write_bytes(b"historical result\n")
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows) + [end_packet()])
        result = session.finish()

        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(session.final_path.read_bytes(), b"historical result\n")
        self.assertEqual(session.partial_path.read_bytes(), b"".join(rows))
        self.assertIn("目标文件已存在", self.diagnostics(session))

    def test_interrupt_keeps_partial_and_diagnostics(self):
        rows = make_rows(count=10)
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows))
        result = session.finish("用户中断")

        self.assertFalse(result.verified)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(session.partial_path.read_bytes(), b"".join(rows))
        self.assertIn("用户中断", self.diagnostics(session))
        self.assertFalse(session.final_path.exists())


class RealtimeEvidenceTest(SessionTest):
    def test_late_first_record_is_unconfirmed(self):
        rows = make_rows()  # 板端跨度 59.9 s
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows), start=60.0, step=0.0)
        session.handle_notification(end_packet(), 61.0)
        result = session.finish()

        self.assertTrue(result.complete)
        self.assertEqual(result.realtime, "未确认")
        self.assertIn("判定=未确认", self.diagnostics(session))

    def test_data_arriving_after_end_is_failed(self):
        rows = make_rows()
        session = self.new_session()
        session.note_start_submitted(0.0)
        self.feed(session, [begin_packet()] + record_packets(rows), start=10.0, step=0.0)
        session.handle_notification(end_packet(), 5.0)  # END 到达时间早于首条记录
        result = session.finish()

        self.assertTrue(result.complete)
        self.assertEqual(result.realtime, "未通过")


class ProtocolConsistencyTest(unittest.TestCase):
    def test_start_packet_encoding(self):
        packet = receiver.encode_start(RUN_ID)
        self.assertEqual(packet, bytes([0x01, 0x01]) + RUN_ID)
        self.assertEqual(len(packet), 10)
        self.assertEqual(len(packet), receiver.START_LENGTH)

    def test_constants_match_firmware_header(self):
        header = HEADER.read_text()
        for value in (receiver.ADVERTISED_NAME, receiver.SERVICE_UUID,
                      receiver.CONTROL_CHARACTERISTIC_UUID,
                      receiver.DATA_CHARACTERISTIC_UUID):
            self.assertIn(value, header)
        for name, value in (("protocol_version", receiver.PROTOCOL_VERSION),
                            ("opcode_start", receiver.OPCODE_START),
                            ("opcode_begin", receiver.OPCODE_BEGIN),
                            ("opcode_data", receiver.OPCODE_DATA),
                            ("opcode_end", receiver.OPCODE_END)):
            self.assertIn(f"{name} {{0x{value:02x}}}", header)
        self.assertIn("expected_sample_count {600}", header)
        self.assertIn("max_notification_length {20}", header)
        self.assertIn("record_buffer_length {256}", header)
        self.assertIn("start_length {2 + run_id_length}", header)
        self.assertIn("begin_length {2 + run_id_length + 2}", header)
        self.assertIn("end_length {1 + run_id_length + 2 + 1}", header)
        self.assertEqual(receiver.EXPECTED_SAMPLE_COUNT, 600)
        self.assertEqual(receiver.BEGIN_LENGTH, 12)
        self.assertEqual(receiver.END_LENGTH, 12)
        self.assertEqual(receiver.DATA_HEADER_LENGTH, 5)
        self.assertEqual(receiver.MAX_FRAGMENT_PAYLOAD, 15)
        self.assertEqual(receiver.MAX_NOTIFICATION_LENGTH, 20)

    def test_firmware_encoder_bytes_parse_in_receiver(self):
        compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            self.skipTest("no host C++ compiler")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "emit.cpp"
            executable = root / "emit"
            source.write_text(r'''
#include <cstdio>
#include "ble_capture_engine.h"
int main() {
    unsigned char run_id[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    unsigned char buffer[64];
    size_t n = ble_capture::encode_begin(buffer, sizeof buffer, run_id, 600);
    for (size_t i = 0; i < n; ++i) std::printf("%02x", buffer[i]);
    std::printf("\n");
    n = ble_capture::encode_data(buffer, sizeof buffer, 0x1234, 1, 3, "MS,0,9.8\n", 9);
    for (size_t i = 0; i < n; ++i) std::printf("%02x", buffer[i]);
    std::printf("\n");
    n = ble_capture::encode_end(buffer, sizeof buffer, run_id, 599, 1);
    for (size_t i = 0; i < n; ++i) std::printf("%02x", buffer[i]);
    std::printf("\n");
    return 0;
}
''')
            built = subprocess.run(
                [compiler, "-std=c++11", "-I", str(REPO / "src"), str(source),
                 str(REPO / "src/ble_capture_engine.cpp"), "-o", str(executable)],
                capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(executable)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            begin_hex, data_hex, end_hex = run.stdout.strip().splitlines()

        begin = receiver.parse_notification(bytes.fromhex(begin_hex))
        self.assertIsInstance(begin, receiver.Begin)
        self.assertEqual(len(bytes.fromhex(begin_hex)), receiver.BEGIN_LENGTH)
        self.assertEqual(begin.run_id, RUN_ID)
        self.assertEqual(begin.expected_count, 600)

        data = receiver.parse_notification(bytes.fromhex(data_hex))
        self.assertIsInstance(data, receiver.Data)
        self.assertEqual(data.seq, 0x1234)
        self.assertEqual(data.index, 1)
        self.assertEqual(data.count, 3)
        self.assertEqual(data.payload, b"MS,0,9.8\n")

        end = receiver.parse_notification(bytes.fromhex(end_hex))
        self.assertIsInstance(end, receiver.End)
        self.assertEqual(len(bytes.fromhex(end_hex)), receiver.END_LENGTH)
        self.assertEqual(end.run_id, RUN_ID)
        self.assertEqual(end.actual_count, 599)
        self.assertEqual(end.status, 1)


if __name__ == "__main__":
    unittest.main()
