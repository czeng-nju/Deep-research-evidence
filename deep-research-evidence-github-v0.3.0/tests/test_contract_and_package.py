"""Adversarial public-CLI checks, executed only after implementation freeze."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VALIDATE = ROOT / 'skill' / 'scripts' / 'validate.py'


class ContractAndPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, name, data):
        path = self.base / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def run_cli(self, *args):
        result = subprocess.run([sys.executable, str(VALIDATE), *map(str, args)],
                                capture_output=True, text=True, timeout=20)
        self.assertTrue(result.stdout.strip(), result.stderr)
        output = json.loads(result.stdout)
        return result.returncode, output

    def result_case(self):
        return self.write_json('case.json', {
            'case_id': 'TEST-CONTRACT',
            'output_contract': {'type': 'object', 'required': ['case_id', 'status', 'answer'],
                                'properties': {'case_id': {'type': 'string'},
                                               'status': {'enum': ['complete', 'partial', 'blocked']},
                                               'answer': {'type': 'string'}}}})

    def test_valid_result_shape(self):
        result = self.write_json('result.json', {'case_id': 'TEST-CONTRACT', 'status': 'partial', 'answer': ''})
        code, _ = self.run_cli('result', '--case', self.result_case(), '--result', result)
        self.assertEqual(code, 0)

    def test_wrong_case_identity_rejected(self):
        result = self.write_json('result.json', {'case_id': 'OTHER', 'status': 'complete', 'answer': 'x'})
        code, _ = self.run_cli('result', '--case', self.result_case(), '--result', result)
        self.assertNotEqual(code, 0)

    def test_missing_output_requirement_rejected(self):
        result = self.write_json('result.json', {'case_id': 'TEST-CONTRACT', 'status': 'complete'})
        code, _ = self.run_cli('result', '--case', self.result_case(), '--result', result)
        self.assertNotEqual(code, 0)

    def test_wrong_output_type_rejected(self):
        result = self.write_json('result.json', {'case_id': 'TEST-CONTRACT', 'status': 'partial', 'answer': 42})
        code, _ = self.run_cli('result', '--case', self.result_case(), '--result', result)
        self.assertNotEqual(code, 0)

    def test_invalid_status_rejected(self):
        result = self.write_json('result.json', {'case_id': 'TEST-CONTRACT', 'status': 'perfect', 'answer': 'x'})
        code, _ = self.run_cli('result', '--case', self.result_case(), '--result', result)
        self.assertNotEqual(code, 0)

    def package(self):
        package = self.base / 'package'
        package.mkdir()
        (package / 'SKILL.md').write_text('---\nname: example-research\ndescription: A test-only research skill.\n---\n\nRead actual inputs.\n', encoding='utf-8')
        return package

    def test_minimal_text_package(self):
        code, _ = self.run_cli('package', '--path', self.package())
        self.assertEqual(code, 0)

    def test_binary_file_rejected(self):
        package = self.package()
        (package / 'binary.dat').write_bytes(b'\xff\xfe\x00')
        code, _ = self.run_cli('package', '--path', package)
        self.assertNotEqual(code, 0)

    def test_symlink_rejected(self):
        package = self.package()
        (package / 'link.txt').symlink_to(package / 'SKILL.md')
        code, _ = self.run_cli('package', '--path', package)
        self.assertNotEqual(code, 0)

    def test_file_count_limit(self):
        package = self.package()
        for i in range(50):
            (package / ('f%d.txt' % i)).write_text('test', encoding='utf-8')
        code, _ = self.run_cli('package', '--path', package)
        self.assertNotEqual(code, 0)

    def test_single_file_size_limit(self):
        package = self.package()
        (package / 'large.txt').write_text('x' * 500001, encoding='utf-8')
        code, _ = self.run_cli('package', '--path', package)
        self.assertNotEqual(code, 0)

    def test_missing_entrypoint_rejected(self):
        package = self.package()
        (package / 'SKILL.md').unlink()
        code, _ = self.run_cli('package', '--path', package)
        self.assertNotEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
