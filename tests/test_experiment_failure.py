"""Failure artifacts must preserve diagnosis without leaking runtime secrets."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.run import main


class ExperimentFailureTests(unittest.TestCase):
    def test_failure_evidence_is_incomplete_and_redacted(self):
        secret='synthetic-secret-only-for-redaction-test'
        with tempfile.TemporaryDirectory() as directory:
            out=Path(directory)/'failed-run'
            with patch.dict(os.environ,{'API_KEY':secret}), \
                 patch('sys.argv',['experiment','--case','attention','--out',str(out)]), \
                 patch('experiments.run.require_cuda',side_effect=RuntimeError('CUDA probe failed '+secret)):
                self.assertEqual(main(),1)
            manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
            detail=json.loads((out/'failure.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['complete'])
            self.assertEqual(manifest['failure']['evidence'],'failure.json')
            self.assertIn('CUDA probe failed',detail['traceback'])
            self.assertIn('Traceback',detail['traceback'])
            self.assertNotIn(secret,(out/'failure.json').read_text(encoding='utf-8'))
            self.assertIn('[REDACTED]',detail['traceback'])
