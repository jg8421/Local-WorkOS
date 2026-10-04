"""Selected-source execution, not a model-quality benchmark."""
import json
import unittest
from unittest.mock import Mock
from workos.evidence_harness import EvidenceTools, run, ROUND_BUDGET


class EvidenceHarnessTests(unittest.TestCase):
    def setUp(self):
        self.docs = [{'id': 'synthetic-doc', 'title': 'Synthetic source', 'content': 'Unconfirmed proposal. Evidence at a later position.'}]
        self.coverage = [{'document_id': 'synthetic-doc', 'source_id': 'S1'}]

    def test_literal_search_and_read_track_actual_ranges(self):
        tools = EvidenceTools(self.docs, self.coverage)
        self.assertEqual(tools.execute('workos_find_evidence', {'query': '.*'})['matches'], [])
        found = tools.execute('workos_find_evidence', {'query': 'later'})['matches'][0]
        self.assertIn('later', found['text'])
        report = tools.finish('An unconfirmed proposal [S1].')
        self.assertTrue(report['completion_verified'])
        self.assertEqual(report['coverage'][0]['excerpt_chars'], len(self.docs[0]['content']))

    def test_unread_foreign_or_memory_sources_cannot_become_verified_citations(self):
        for draft in ('Not read [S1]', 'Unknown [S2]', 'No citations'):
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                EvidenceTools(self.docs, self.coverage).finish(draft)
        with self.assertRaises(ValueError): EvidenceTools([{**self.docs[0], 'kind': 'memory'}], self.coverage)

    def test_model_arguments_never_select_a_path_command_or_other_source(self):
        tools = EvidenceTools(self.docs, self.coverage)
        for tool, args in [('bash', {'command': 'synthetic'}), ('workos_read_source', {'source_id': '../outside', 'start': 0, 'length': 1}),
                           ('workos_read_source', {'source_id': 'S1', 'start': False, 'length': 1}),
                           ('workos_sources', {'path': 'synthetic'})]:
            with self.subTest(tool=tool), self.assertRaises(ValueError): tools.execute(tool, args)
        self.assertEqual(tools.trace['read_ranges'], [])

    def test_repeat_reads_count_against_budget_and_full_read_claim_is_accurate(self):
        docs = [{**self.docs[0], 'content': 'x'*20000}]
        tools = EvidenceTools(docs, self.coverage)
        for _ in range(6): tools.execute('workos_read_source', {'source_id': 'S1', 'start': 0, 'length': 12000})
        with self.assertRaises(ValueError): tools.execute('workos_read_source', {'source_id': 'S1', 'start': 0, 'length': 12000})
        report = tools.finish('Only the supplied portion supports this draft [S1]')
        self.assertEqual(report['coverage'][0]['excerpt_chars'], 12000)
        self.assertFalse(report['full_material_read'])

    def test_provider_neutral_loop_reads_then_finishes_without_leaking_full_file(self):
        call = Mock(side_effect=[(json.dumps({'action': 'tool', 'name': 'workos_read_source', 'arguments': {'source_id': 'S1', 'start': 0, 'length': 10}}), 'Chosen model', 'model'),
                                 (json.dumps({'action': 'final', 'body': 'A proposal remains unconfirmed [S1]'}), 'Chosen model', 'model')])
        answer, model, report = run(call, 'Scope', 'Requested draft', self.docs, self.coverage)
        self.assertEqual(model, 'Chosen model')
        self.assertTrue(report['completion_verified'])
        self.assertNotIn(self.docs[0]['content'], call.call_args_list[0].args[1])
        self.assertEqual(report['coverage'][0]['excerpt_chars'], 10)

    def test_missing_conditions_and_invalid_protocol_do_not_return_a_completed_draft(self):
        question = json.dumps({'status': 'needs_input', 'message': 'Which audience?', 'questions': [{'id': 'audience', 'label': 'Audience?'}]})
        answer, _, report = run(Mock(return_value=(question, 'Chosen', 'model')), '', '', self.docs, self.coverage)
        self.assertFalse(report['completion_verified'])
        with self.assertRaisesRegex(ValueError, '工具协议'):
            run(Mock(return_value=('Arbitrary plain text', 'Chosen', 'model')), '', '', self.docs, self.coverage)

    def test_loop_budget_stops_a_model_that_repeats_disallowed_actions(self):
        call = Mock(return_value=(json.dumps({'action': 'tool', 'name': 'shell', 'arguments': {}}), 'Chosen', 'model'))
        with self.assertRaisesRegex(ValueError, '执行预算'): run(call, '', '', self.docs, self.coverage)
        self.assertEqual(call.call_count, ROUND_BUDGET)

    def test_final_without_read_can_be_repaired_in_scope(self):
        actions = [{'action': 'final', 'body': 'Unconfirmed [S1]'},
                   {'action': 'tool', 'name': 'workos_read_source', 'arguments': {'source_id': 'S1', 'start': 0, 'length': 12}},
                   {'action': 'final', 'body': 'Unconfirmed [S1]'}]
        _, _, report = run(Mock(side_effect=[(json.dumps(a), 'Chosen', 'model') for a in actions]), '', '', self.docs, self.coverage)
        self.assertEqual(report['tool_counts']['workos_check_draft'], 2)


if __name__ == '__main__': unittest.main()
