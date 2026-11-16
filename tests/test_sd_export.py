"""主机检查：SD 导出的列表、传输完整性、错误处理和采集兼容；不需要板子。"""
import binascii
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "firmware/sd_export.py"
CAPTURE_BOOT = (
    "imu_capture_only: 1\n"
    "imu_capture_samples: 600\n"
    "12345,0,0.000000,0.000000,9.810000,0.000000,0.000000,0.000000,9.800000\n"
    "capture: SD saved\n"
    "capture: complete\n"
    "SDX-READY sd=1\n"
    "export: LIST | GET <name> (read-only; SD not modified)\n"
)


def list_response(files):
    lines = ["SDX-LIST-BEGIN"]
    lines += [f"SDX-FILE {name} {size}" for name, size in files]
    lines.append(f"SDX-LIST-END {len(files)}")
    return "\n".join(lines) + "\n"


def get_response(name, data, crc=None, finish=True):
    lines = [f"SDX-BEGIN {name} {len(data)}"]
    for start in range(0, len(data), 64):
        lines.append("SDX-DATA " + data[start:start + 64].hex().upper())
    if finish:
        value = binascii.crc32(data) & 0xFFFFFFFF if crc is None else crc
        lines.append(f"SDX-END {len(data)} {value:08X}")
    return "\n".join(lines) + "\n"


class SDExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.out = self.root / "out"
        (self.root / "serial.py").write_text('''import json, os
class SerialException(Exception): pass
class Serial:
 def __init__(self,*a,**k):
  self.boot=os.environ.get('TEST_BOOT','').encode()
  self.pos=0
  self.responses=json.loads(os.environ.get('TEST_RESPONSES','{}'))
  self.pending=bytearray()
  self.commands=[]
  self.dtr=False
 def __enter__(self): return self
 def __exit__(self,*a): pass
 def write(self,data):
  self.commands.append(data.decode('ascii','replace').strip()); return len(data)
 def flush(self): pass
 def reset_input_buffer(self): pass
 def read(self,n):
  if self.pos < len(self.boot):
   chunk=self.boot[self.pos:self.pos+7]; self.pos+=7; return chunk
  if not self.pending and self.commands:
   self.pending += self.responses.get(self.commands.pop(0),'').encode()
  if self.pending:
   chunk=bytes(self.pending[:7]); del self.pending[:7]; return chunk
  return b''
''')
        self.env = dict(os.environ, PYTHONPATH=str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, args, boot=CAPTURE_BOOT, responses=None):
        self.env["TEST_BOOT"] = boot
        self.env["TEST_RESPONSES"] = json.dumps(responses or {})
        return subprocess.run(
            [sys.executable, str(TOOL), "--port", "fake", "--out", str(self.out)] + args,
            env=self.env, capture_output=True, text=True, timeout=20)

    def logs(self):
        return list(self.out.glob("sd_export_*.log"))

    def test_list_parses_files(self):
        result = self.run_tool(["--list"], responses={
            "LIST": list_response([("IMU0000.CSV", 54720), ("GNSS.CSV", 120)])})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("IMU0000.CSV", result.stdout)
        self.assertIn("54720", result.stdout)
        self.assertIn("GNSS.CSV", result.stdout)
        self.assertEqual(len(self.logs()), 1)
        self.assertIn("SDX-LIST-END", self.logs()[0].read_text())

    def test_list_is_the_default_action(self):
        result = self.run_tool([], responses={"LIST": list_response([("TEST.TXT", 12)])})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("TEST.TXT", result.stdout)
        self.assertIn("TEST.TXT", (self.out / self.logs()[0].name).read_text())

    def test_download_verifies_bytes_and_crc(self):
        data = bytes(range(256)) * 3 + b"line one\r\nline two\n\x00\xff"
        result = self.run_tool(["--get", "IMU0000.CSV"], responses={
            "GET IMU0000.CSV": get_response("IMU0000.CSV", data)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.out / "IMU0000.CSV").read_bytes(), data)
        self.assertFalse((self.out / "IMU0000.CSV.part").exists())
        self.assertIn("CRC32", result.stdout)

    def test_list_and_download_in_one_session(self):
        data = b"ms,seq,acc_D\n1,0,9.8\n"
        result = self.run_tool(["--list", "--get", "IMU0000.CSV"], responses={
            "LIST": list_response([("IMU0000.CSV", len(data))]),
            "GET IMU0000.CSV": get_response("IMU0000.CSV", data)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.out / "IMU0000.CSV").read_bytes(), data)

    def test_crc_mismatch_fails_and_keeps_partial(self):
        data = b"a,b,c\n1,2,3\n"
        result = self.run_tool(["--get", "IMU0000.CSV"], responses={
            "GET IMU0000.CSV": get_response("IMU0000.CSV", data, crc=0xDEADBEEF)})
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertFalse((self.out / "IMU0000.CSV").exists())
        self.assertEqual((self.out / "IMU0000.CSV.part").read_bytes(), data)
        self.assertIn("CRC32", result.stderr)

    def test_interrupted_transfer_fails_and_keeps_partial(self):
        data = b"x" * 300
        result = self.run_tool(["--get", "IMU0000.CSV", "--io-timeout", "0.2"],
                               responses={"GET IMU0000.CSV":
                                          get_response("IMU0000.CSV", data, finish=False)})
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertFalse((self.out / "IMU0000.CSV").exists())
        self.assertEqual((self.out / "IMU0000.CSV.part").read_bytes(), data)
        self.assertIn("传输中断", result.stderr)

    def test_missing_file_is_reported(self):
        result = self.run_tool(["--get", "IMU9999.CSV"], responses={
            "GET IMU9999.CSV": "SDX-ERR NOT-FOUND IMU9999.CSV\n"})
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("没有这个文件", result.stderr)
        self.assertFalse((self.out / "IMU9999.CSV.part").exists())

    def test_unavailable_sd_is_reported(self):
        boot = CAPTURE_BOOT.replace("SD: ready", "SD: begin failed").replace(
            "SDX-READY sd=1", "SDX-READY sd=0")
        result = self.run_tool(["--list"], boot=boot,
                               responses={"LIST": "SDX-ERR NO-SD\n"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("SD 卡不可用", result.stderr)

    def test_existing_local_file_is_not_overwritten(self):
        self.out.mkdir(parents=True)
        target = self.out / "IMU0000.CSV"
        target.write_bytes(b"keep me\n")
        result = self.run_tool(["--get", "IMU0000.CSV"], responses={
            "GET IMU0000.CSV": get_response("IMU0000.CSV", b"new data\n")})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(target.read_bytes(), b"keep me\n")
        self.assertFalse((self.out / "IMU0000.CSV.part").exists())

    def test_overwrite_is_explicit(self):
        self.out.mkdir(parents=True)
        (self.out / "IMU0000.CSV").write_bytes(b"old\n")
        result = self.run_tool(["--get", "IMU0000.CSV", "--overwrite"], responses={
            "GET IMU0000.CSV": get_response("IMU0000.CSV", b"new\n")})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.out / "IMU0000.CSV").read_bytes(), b"new\n")

    def test_waits_for_capture_to_finish_before_sending(self):
        # Boot output includes the capture markers; the log must show that the
        # tool listened to the whole capture instead of cutting it short.
        result = self.run_tool(["--list"], responses={
            "LIST": list_response([("IMU0000.CSV", 54720)])})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log = self.logs()[0].read_text()
        self.assertIn("capture: complete", log)
        self.assertIn("SDX-READY", log)
        self.assertIn(">>> LIST", log)
        self.assertLess(log.index("capture: complete"), log.index(">>> LIST"))

    def test_missing_ready_marker_times_out(self):
        result = self.run_tool(["--list", "--boot-timeout", "0.3"], boot=CAPTURE_BOOT.split("SDX-READY")[0])
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn("SDX-READY", result.stderr)

    def test_protocol_markers_match_firmware_header(self):
        header = (REPO / "src/sd_export.h").read_text()
        script = TOOL.read_text()
        for marker in ("SDX-READY", "SDX-LIST-BEGIN", "SDX-FILE", "SDX-LIST-END",
                       "SDX-BEGIN", "SDX-DATA", "SDX-END", "SDX-ERR"):
            self.assertIn(marker, header)
            self.assertIn(marker, script)


class CRC32Test(unittest.TestCase):
    """用主机工具链编译固件的 CRC32 头文件，确认与 Python 的表现一致。"""

    def test_crc32_header_matches_python(self):
        compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            self.skipTest("no host C++ compiler")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "check.cpp"
            executable = root / "check"
            payload = root / "payload.bin"
            data = b"123456789" + bytes(range(256)) * 4
            payload.write_bytes(data)
            source.write_text('''
#include <cstdio>
#include <cstdlib>
#include "crc32.h"
int main(int argc, char** argv) {
    FILE* file = std::fopen(argv[1], "rb");
    if (file == nullptr) return 1;
    uint32_t crc = crc32_initial_value;
    uint8_t buffer[97];
    size_t count;
    while ((count = std::fread(buffer, 1, sizeof(buffer), file)) > 0) {
        crc = crc32_update(crc, buffer, count);
    }
    std::fclose(file);
    std::printf("%08X\\n", crc32_final(crc));
    return 0;
}
''')
            build = subprocess.run([compiler, "-std=c++11", "-I", str(REPO / "src"),
                                    str(source), "-o", str(executable)],
                                   capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(executable), str(payload)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(),
                             f"{binascii.crc32(data) & 0xFFFFFFFF:08X}")


if __name__ == "__main__":
    unittest.main()