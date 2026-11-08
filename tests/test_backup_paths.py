"""Backup path checks must reject traversal before filesystem/DB side effects."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import backup_state as backup


class BackupPathTests(unittest.TestCase):
    def test_parent_traversal_rejected_before_filesystem_inspection(self):
        candidates = (backup.ROOT / '.local/../escape',
                      backup.ROOT / '.local/existing/../../escape',
                      Path('.local/../escape'), backup.ROOT / '.local')
        with patch.object(Path, 'is_symlink', side_effect=AssertionError('unexpected filesystem inspection')):
            for candidate in candidates:
                with self.subTest(path=str(candidate)), self.assertRaisesRegex(ValueError, 'backup_paths_must_be_project_local'):
                    backup.local(candidate)

    def test_backup_and_restore_reject_bad_arguments_before_database_access(self):
        valid = backup.ROOT / '.local/path-check-placeholder'
        invalid = backup.ROOT / '.local/../path-check-escape'
        for action in (backup.backup, backup.restore):
            for index in range(3):
                db = Mock(); arguments = [valid / 'one', valid / 'two', valid / 'three']; arguments[index] = invalid
                with self.subTest(action=action.__name__, argument=index), self.assertRaisesRegex(ValueError, 'backup_paths_must_be_project_local'):
                    action(db, *arguments)
                self.assertEqual(db.mock_calls, [])
        self.assertFalse(valid.exists())

    def test_symlink_manifest_is_rejected_before_any_content_read(self):
        with tempfile.TemporaryDirectory(dir=backup.ROOT / '.local', prefix='backup-path-check-') as directory:
            root = Path(directory); source = root / 'bundle'; source.mkdir()
            target = root / 'target.json'; target.write_text('{"format":"invalid"}')
            (source / 'manifest.json').symlink_to(target)
            db = Mock()
            with patch.object(Path, 'read_text', side_effect=AssertionError('unexpected content read')):
                with self.assertRaisesRegex(ValueError, 'backup_symlink_rejected'):
                    backup.restore(db, source, root / 'runtime', root / 'agent')
            self.assertEqual(db.mock_calls, [])
            self.assertFalse((root / 'runtime').exists())
            self.assertFalse((root / 'agent').exists())


if __name__ == '__main__':
    unittest.main()
