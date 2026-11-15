"""Host checks for numeric capture and SD/serial comparison; no board required."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

LISTENER = Path(__file__).resolve().parents[1] / 'firmware/imu_listen.py'
ROW = '12000,0,-0.123456,0.000000,9.810000,-0.010000,0.020000,0.000000,9.800000\n'


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        # Split rows across reads; include diagnostics between numeric rows.
        (self.root / 'serial.py').write_text('''import os
class SerialException(Exception): pass
class Serial:
 def __init__(self,*a,**k):
  text=os.environ['TEST_STREAM'].encode()
  self.chunks=[text[i:i+7] for i in range(0,len(text),7)]
 def __enter__(self): return self
 def __exit__(self,*a): pass
 def read(self,n): return self.chunks.pop(0) if self.chunks else b''
''')
        self.env = dict(os.environ, PYTHONPATH=str(self.root), OMB_LOG_FILE=str(self.root / 'run.log'))

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, rows, expected=1, ending='capture: SD saved\ncapture: complete\n'):
        self.env['TEST_STREAM'] = f'imu_capture_only: 1\nimu_capture_samples: {expected}\nuse_dummy_imu_data: 0\n' + rows + ending
        return subprocess.run([sys.executable, str(LISTENER), 'fake', '1'], env=self.env,
                              capture_output=True, text=True, timeout=5)

    def test_fragmented_serial_preserves_numeric_rows(self):
        result = self.capture(ROW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / 'run.csv').read_bytes(), ROW.encode())
        self.assertIn('SD saved', (self.root / 'run.log').read_text())

    def test_missing_samples_fail(self):
        self.assertEqual(self.capture(ROW, expected=2).returncode, 1)

    def test_missing_sd_fails_but_keeps_csv(self):
        self.assertEqual(self.capture(ROW, ending='capture: SD not saved\ncapture: complete\n').returncode, 1)
        self.assertEqual((self.root / 'run.csv').read_text(), ROW)

    def test_compare_exact_and_changed_rows(self):
        a, b = self.root / 'computer.csv', self.root / 'sd.csv'
        a.write_text(ROW)
        b.write_bytes(ROW.replace('\n', '\r\n').encode())
        command = [sys.executable, str(LISTENER), '--compare', str(a), str(b)]
        self.assertEqual(subprocess.run(command, env=self.env, capture_output=True).returncode, 0)
        b.write_text(ROW.replace('9.800000', '9.700000'))
        self.assertEqual(subprocess.run(command, env=self.env, capture_output=True).returncode, 1)

    def test_bash_entry_saves_both_outputs_and_record(self):
        (self.root / 'record.md').write_text('# experiment\n')
        self.env.update(OMB_EXPERIMENT_DIR=str(self.root), OMB_PYTHON=sys.executable,
                        TEST_STREAM='imu_capture_only: 1\nimu_capture_samples: 1\nuse_dummy_imu_data: 0\n' + ROW +
                        'capture: SD saved\ncapture: complete\n')
        result = subprocess.run(['bash', str(LISTENER.with_name('run_experiment.sh')),
                                 '--experiment', 'test-demo', '--source', 'real',
                                 '--port', 'fake', '--seconds', '1', '--no-open'],
                                env=self.env, input='\n', capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        csvs = list(self.root.glob('*.csv'))
        self.assertEqual(len(csvs), 1)
        self.assertEqual(csvs[0].read_text(), ROW)
        self.assertIn(csvs[0].name, (self.root / 'record.md').read_text())

    def test_empty_files_fail(self):
        a = self.root / 'empty.csv'; a.write_text('')
        self.assertEqual(subprocess.run([sys.executable, str(LISTENER), '--compare', str(a), str(a)],
                                       env=self.env, capture_output=True).returncode, 1)


if __name__ == '__main__':
    unittest.main()
