from pathlib import Path
import tempfile
import unittest

from compare_graphify import GRAF_ARMS, cited_path_order, graf_query_command, validate_focused_units


class FocusedUnitReceipts(unittest.TestCase):
    def test_frozen_evidence_limit_and_unchanged_historical_commands(self):
        for arm, (_, (command, *flags)) in GRAF_ARMS.items():
            actual = graf_query_command(Path('/graf'), Path('/source'), arm, 'needle', 2000)
            limit = [] if command == 'units' else ['--limit', '20' if arm in ('graf_evidence', 'graf_evidence_units', 'graf_evidence_linked', 'graf_evidence_bundles', 'graf_evidence_coverage', 'graf_evidence_families', 'graf_evidence_passages', 'graf_evidence_complement', 'graf_evidence_stratified', 'graf_evidence_fused', 'graf_evidence_referenced', 'graf_evidence_rescue', 'graf_evidence_callees', 'graf_evidence_selective') else '1000']
            self.assertEqual(actual, ['/graf', '--root', '/source', command, 'needle', *flags,
                                      '--budget', '2000', *limit, '--text'])

    def test_exact_source_ranges_and_wrong_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'x.py').write_text('first\nneedle\nlast\n')
            context = '"x.py":2-2\nneedle\n\n'
            self.assertEqual(validate_focused_units(context, root), 1)
            self.assertEqual(cited_path_order('graf_units_focus', context, root, {'x.py'}), ['x.py'])
            for bad in [context.replace('needle', 'wrong'), context.replace('2-2', '1-2'), 'preamble\n' + context]:
                with self.assertRaises(AssertionError):
                    validate_focused_units(bad, root)

    def test_legacy_unit_paths_are_not_silently_lost(self):
        self.assertEqual(cited_path_order('graf_units', 'x.py:1-2\nbody\n', Path('.'), {'x.py'}), ['x.py'])

    def test_physical_evidence_ranges_preserve_unicode_and_nonterminating_cr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'x.py').write_bytes('first\u2028still first\r\nsecond\r'.encode())
            context = '\"x.py\":1-2\nfirst\u2028still first\nsecond\r\n\n'
            self.assertEqual(validate_focused_units(context, root, physical=True), 1)
            with self.assertRaises(AssertionError):
                validate_focused_units(context.replace('second', 'wrong'), root, physical=True)
