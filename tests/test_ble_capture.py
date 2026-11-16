"""主机检查：PLAN_02 的 BLE 协议、状态机、分片与记录长度边界；不需要板子或射频。

引擎测试把 src/ble_capture_engine.cpp 直接编译为主机程序，真实 BLE 发送、IMU
与 SD 全部由测试替身注入；记录测试用最小 Arduino.h 桩编译真实的
print_capture_sample() 与 256 字节有界缓冲。
"""
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
ENGINE_SOURCE = SRC / "ble_capture_engine.cpp"
ENGINE_CHECK = REPO / "tests" / "ble_engine_check.cpp"
RECORD_CHECK = REPO / "tests" / "ble_record_check.cpp"

ARDUINO_STUB = r'''
// Minimal host stand-in for the Arduino Print API used by the capture formatter.
#ifndef Arduino_h
#define Arduino_h

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

class Print {
  public:
    virtual ~Print() {}
    virtual size_t write(uint8_t value) = 0;
    virtual size_t write(const uint8_t *buffer, size_t size) {
        size_t count = 0;
        while (size-- > 0) {
            count += write(*buffer++);
        }
        return count;
    }
    size_t print(char value) { return write(static_cast<uint8_t>(value)); }
    size_t print(const char *text) {
        size_t count = 0;
        while (*text != '\0') {
            count += write(static_cast<uint8_t>(*text++));
        }
        return count;
    }
    size_t print(unsigned int value) {
        char text[16];
        const int length = snprintf(text, sizeof text, "%u", value);
        return length > 0 ? print(static_cast<const char *>(text)) : 0;
    }
    size_t print(unsigned long value) {
        char text[24];
        const int length = snprintf(text, sizeof text, "%lu", value);
        return length > 0 ? print(static_cast<const char *>(text)) : 0;
    }
    // Mirrors Arduino's Print::printFloat so the CSV bytes match on the host.
    size_t print(double value, int digits) {
        size_t count = 0;
        if (value != value) {
            return print("nan");
        }
        if (value > 4294967040.0 || value < -4294967040.0) {
            return print("ovf");
        }
        if (value < 0.0) {
            count += print('-');
            value = -value;
        }
        double rounding = 0.5;
        for (int i = 0; i < digits; ++i) {
            rounding /= 10.0;
        }
        value += rounding;
        const unsigned long whole = static_cast<unsigned long>(value);
        double remainder = value - static_cast<double>(whole);
        count += print(whole);
        if (digits > 0) {
            count += print('.');
        }
        while (digits-- > 0) {
            remainder *= 10.0;
            const unsigned int digit = static_cast<unsigned int>(remainder);
            count += print(digit);
            remainder -= digit;
        }
        return count;
    }
    size_t print(float value, int digits) {
        return print(static_cast<double>(value), digits);
    }
};

#endif
'''


def host_compiler():
    return shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")


class CompiledCheck(unittest.TestCase):
    """编译并运行一个主机 C++ 检查程序，每个用例一个 pytest 测试。"""

    executable = None

    @classmethod
    def build(cls):
        raise NotImplementedError

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.executable = cls.build(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, case):
        result = subprocess.run([str(self.executable), case], capture_output=True, text=True,
                                timeout=120)
        self.assertEqual(result.returncode, 0,
                         f"{case} failed:\n{result.stdout}{result.stderr}")
        self.assertIn(f"PASS {case}", result.stdout)


class EngineTest(CompiledCheck):
    @classmethod
    def build(cls, root):
        compiler = host_compiler()
        if compiler is None:
            raise unittest.SkipTest("no host C++ compiler")
        executable = root / "ble_engine_check"
        command = [compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror",
                   "-I", str(SRC), str(ENGINE_CHECK), str(ENGINE_SOURCE),
                   "-o", str(executable)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError(f"engine check did not compile:\n{built.stderr}")
        return executable

    def test_start_validation(self):
        self.check("start_validation")

    def test_readiness_gate(self):
        self.check("readiness_gate")

    def test_begin_encoding(self):
        self.check("begin_encoding")

    def test_end_encoding(self):
        self.check("end_encoding")

    def test_data_encoding_and_fragmentation(self):
        self.check("data_encoding_and_fragmentation")

    def test_record_too_long_no_truncation(self):
        self.check("record_too_long_no_truncation")

    def test_local_failure_keeps_cursor(self):
        self.check("local_failure_keeps_cursor")

    def test_budget_limits_service(self):
        self.check("budget_limits_service")

    def test_tx_never_past_sample_count(self):
        self.check("tx_never_past_sample_count")

    def test_init_failure_sends_end_zero(self):
        self.check("init_failure_sends_end_zero")

    def test_init_failure_without_link(self):
        self.check("init_failure_without_link")

    def test_partial_capture_sends_end_n(self):
        self.check("partial_capture_sends_end_n")

    def test_terminal_failure_no_end_capture_continues(self):
        self.check("terminal_failure_no_end_capture_continues")

    def test_serialization_failure_no_end_capture_continues(self):
        self.check("serialization_failure_no_end_capture_continues")

    def test_begin_failure_disables_radio(self):
        self.check("begin_failure_disables_radio")

    def test_end_failure_is_terminal(self):
        self.check("end_failure_is_terminal")

    def test_drain_completes_and_sends_end(self):
        self.check("drain_completes_and_sends_end")

    def test_drain_deadline(self):
        self.check("drain_deadline")

    def test_run_without_start_is_refused(self):
        self.check("run_without_start_is_refused")


class RecordFormatTest(CompiledCheck):
    @classmethod
    def build(cls, root):
        compiler = host_compiler()
        if compiler is None:
            raise unittest.SkipTest("no host C++ compiler")
        stub = root / "stub"
        stub.mkdir()
        (stub / "Arduino.h").write_text(ARDUINO_STUB)
        executable = root / "ble_record_check"
        command = [compiler, "-std=c++11", "-O1", "-Wall", "-Wextra", "-Werror",
                   "-I", str(stub), "-I", str(SRC), str(RECORD_CHECK),
                   str(ENGINE_SOURCE), "-o", str(executable)]
        built = subprocess.run(command, capture_output=True, text=True)
        if built.returncode != 0:
            raise AssertionError(f"record check did not compile:\n{built.stderr}")
        return executable

    def test_plausible_row(self):
        self.check("plausible_row")

    def test_longest_plausible_row_fits(self):
        self.check("longest_plausible_row_fits")

    def test_capacity_boundary(self):
        self.check("capacity_boundary")

    def test_small_capacity_is_refused(self):
        self.check("small_capacity_is_refused")


class StaticInvariantTest(unittest.TestCase):
    """静态证据：唯一样本数组、未改动的科学接口、默认模式与协议身份。"""

    def test_single_600_sample_array(self):
        pattern = re.compile(r"IMUCaptureSample\s+\w+\s*\[")
        found = []
        for path in sorted(SRC.iterdir()):
            if path.suffix in (".cpp", ".h", ".ino"):
                for match in pattern.finditer(path.read_text(errors="replace")):
                    found.append((path.name, match.group(0)))
        self.assertEqual(len(found), 1, found)
        self.assertEqual(found[0][0], "imu_capture.cpp")

    def test_checked_in_default_stays_legacy(self):
        params = (SRC / "params.h").read_text()
        self.assertIn("constexpr bool imu_capture_only {true};", params)
        self.assertIn("constexpr bool ble_capture_mode {false};", params)
        self.assertIn("BLE capture and the legacy serial boot capture are exclusive", params)

    def test_protocol_identity_is_pinned(self):
        header = (SRC / "ble_capture_engine.h").read_text()
        for value in ("OMB-Nano",
                      "4F4D424E-4E41-4E4F-824C-850000000001",
                      "4F4D424E-4E41-4E4F-824C-850000000002",
                      "4F4D424E-4E41-4E4F-824C-850000000003"):
            self.assertIn(value, header)
        self.assertIn("BLEWrite", (SRC / "ble_capture.cpp").read_text())
        self.assertIn("BLENotify", (SRC / "ble_capture.cpp").read_text())

    def test_scientific_interfaces_untouched(self):
        capture = (SRC / "imu_capture.h").read_text()
        self.assertIn("struct IMUCaptureSample {", capture)
        self.assertIn("inline size_t print_capture_sample(Print &out, "
                      "const IMUCaptureSample &sample)", capture)
        manager = " ".join((SRC / "imu_manager.h").read_text().split())
        self.assertIn("bool get_new_reading(float & acc_N_inout, float & acc_E_inout, "
                      "float & acc_D_inout, float & yaw___inout, float & pitch_inout, "
                      "float & roll__inout", manager)


if __name__ == "__main__":
    unittest.main()
