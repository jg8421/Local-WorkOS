"""HTTP durable thorough jobs: actual selected tool reads reach critic and anchors."""
import json
import threading
import unittest
from unittest.mock import Mock

from tests import test_jobs_http as jobs_fixture

CLEAN = json.dumps({'verdict': 'no_obvious_issues', 'findings': []})


class EvidenceHarnessHttpTests(unittest.TestCase):
    tearDown = jobs_fixture.AsyncJobsHttpTests.tearDown
    request = jobs_fixture.AsyncJobsHttpTests.request
    body = jobs_fixture.AsyncJobsHttpTests.body
    submitted = jobs_fixture.AsyncJobsHttpTests.submitted
    finished = jobs_fixture.AsyncJobsHttpTests.finished
    normal_model = jobs_fixture.AsyncJobsHttpTests.normal_model

    def setUp(self):
        jobs_fixture.AsyncJobsHttpTests.setUp(self)
        # Real archive format/confinement tests run separately. This test owns
        # the HTTP/provider/tool/critic/save boundary, not file rendering.
        self.app.archive_record = Mock(return_value={'status': 'synthetic', 'files': []})

    def material(self):
        self.head = 'HEAD_UNREAD_SENTINEL ' + 'h' * 7000 + '\n\n'
        self.middle = 'MIDDLE_TOOL_EVIDENCE: a proposal remains unconfirmed. ' + 'm' * 500
        self.tail = '\n\nTAIL_UNREAD_SENTINEL ' + 't' * 6000
        document = self.app.stores['personal'].update('documents', self.docs['personal']['id'], {
            'content': self.head + self.middle + self.tail,
            'chunks': [{'id': 'synthetic-head', 'ordinal': 1, 'page': 1, 'text': self.head.strip()},
                       {'id': 'synthetic-middle', 'ordinal': 2, 'page': 7, 'text': self.middle},
                       {'id': 'synthetic-tail', 'ordinal': 3, 'page': 9, 'text': self.tail.strip()}]})
        self.foreign = self.app.stores['personal'].create('documents', {'title': 'Unselected synthetic source',
            'project_id': self.projects['personal']['id'], 'content': 'UNSELECTED_PRIVATE_SENTINEL'})
        return document

    def responses(self, *items):
        values = iter(items)

        def respond(*args, **kwargs):
            self.app.completion_meta.finish_reason = 'stop'
            value = next(values)
            if isinstance(value, Exception):
                raise value
            return value if isinstance(value, str) else json.dumps(value), 'Synthetic exact selected model'

        self.model.side_effect = respond

    def read_action(self):
        return {'action': 'tool', 'name': 'workos_read_source',
                'arguments': {'source_id': 'S1', 'start': len(self.head), 'length': len(self.middle)}}

    def job(self, **changes):
        return self.submitted(self.body(quality_mode='thorough', message='用中文简短总结所选材料。', **changes))

    def test_middle_tool_read_is_the_critic_evidence_and_correct_source_anchor(self):
        document = self.material()
        self.responses(self.read_action(), {'action': 'final', 'body': '这是一项待核实提案。[S1]'}, CLEAN)
        job = self.job()
        done = self.finished(job['id'])
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(self.model.call_count, 3)
        first, second, critic = [call.args[3] for call in self.model.call_args_list]
        self.assertNotIn('MIDDLE_TOOL_EVIDENCE', first)
        self.assertIn(self.middle, second)
        self.assertIn(self.middle, critic)
        for prompt in (first, second, critic):
            self.assertNotIn('UNSELECTED_PRIVATE_SENTINEL', prompt)
            self.assertNotIn('HEAD_UNREAD_SENTINEL', prompt)
            self.assertNotIn('TAIL_UNREAD_SENTINEL', prompt)
        result = done['result']
        citation = result['citations'][0]
        self.assertEqual(citation['document_id'], document['id'])
        self.assertEqual(citation['source_id'], 'S1')
        self.assertEqual(citation['chunk_id'], 'synthetic-middle')
        self.assertEqual(citation['ordinal'], 2)
        self.assertEqual(citation['page'], 7)
        self.assertTrue(self.middle.startswith(citation['quote']))
        coverage = result['coverage'][0]
        self.assertEqual(coverage['excerpt_chars'], len(self.middle))
        self.assertEqual(coverage['total_chars'], len(document['content']))
        self.assertTrue(coverage['truncated'])
        harness = result['quality_report']['harness']
        self.assertEqual(harness['execution'], 'bounded_json_tools')
        self.assertEqual(harness['read_ranges'], [{'source_id': 'S1', 'start': len(self.head), 'end': len(self.head)+len(self.middle)}])
        self.assertFalse(result['quality_report']['facts_verified'])
        saved = self.app.stores['personal'].get('deliverables', result['deliverable_id'])
        self.assertEqual(saved['source_ids'], [document['id']])

    def test_disallowed_and_unselected_source_requests_return_safe_errors_not_data(self):
        self.material()
        self.responses({'action': 'tool', 'name': 'shell', 'arguments': {'command': 'synthetic'}},
                       {'action': 'tool', 'name': 'workos_read_source', 'arguments': {'source_id': 'S2', 'start': 0, 'length': 20}},
                       self.read_action(), {'action': 'final', 'body': '该提案仍需核实。[S1]'}, CLEAN)
        done = self.finished(self.job()['id'])
        self.assertEqual(done['status'], 'completed', done.get('error'))
        harness = done['result']['quality_report']['harness']
        self.assertEqual(harness['blocked_tools'], ['shell'])
        self.assertTrue(all(row['source_id'] == 'S1' for row in harness['read_ranges']))
        prompts = '\n'.join(call.args[3] for call in self.model.call_args_list)
        self.assertIn('仅允许本轮选定资料工具', prompts)
        self.assertIn('只能读取本轮选定source_id', prompts)
        self.assertNotIn('UNSELECTED_PRIVATE_SENTINEL', prompts)
        self.app.dsh_answer.assert_not_called()

    def test_blocking_critic_repair_uses_actual_tool_evidence_and_same_scope(self):
        self.material()
        bad = '该提案已经确认。[S1]'
        issue = json.dumps({'verdict': 'issues_found', 'findings': [{
            'criterion': 'proposal status', 'severity': 'blocking', 'quote': bad,
            'explanation': 'The read excerpt says unconfirmed.', 'proposed_fix': 'Keep the proposal unconfirmed.',
            'source_ids': ['S1']}]})
        self.responses(self.read_action(), {'action': 'final', 'body': bad}, issue,
                       '该提案仍需核实。[S1]', CLEAN)
        done = self.finished(self.job()['id'])
        self.assertEqual(done['status'], 'completed', done.get('error'))
        self.assertEqual(self.model.call_count, 5)
        self.assertEqual(done['result']['quality_report']['repair_count'], 1)
        for call in self.model.call_args_list[2:]:
            self.assertTrue(self.middle in call.args[3], 'Review/repair must include the actual middle read')
            self.assertFalse('HEAD_UNREAD_SENTINEL' in call.args[3], 'Repair supplied an unread head excerpt')
            self.assertFalse('TAIL_UNREAD_SENTINEL' in call.args[3], 'Repair supplied an unread tail excerpt')
            self.assertFalse('UNSELECTED_PRIVATE_SENTINEL' in call.args[3], 'Unselected material entered review/repair')
        self.assertEqual(done['result']['coverage'][0]['excerpt_chars'], len(self.middle))
        self.assertEqual(done['result']['citations'][0]['page'], 7)

    def test_provider_failure_after_read_saves_no_draft_and_no_private_error(self):
        self.material()
        self.responses(self.read_action(), ValueError('PRIVATE_PROVIDER_ERROR_SENTINEL'))
        done = self.finished(self.job()['id'])
        self.assertEqual(done['status'], 'failed')
        self.assertNotIn('PRIVATE_PROVIDER_ERROR_SENTINEL', done['error'])
        self.assertEqual(self.app.stores['personal'].list('deliverables'), [])
        self.assertEqual(self.model.call_count, 2)

    def test_stop_while_tool_model_is_blocked_prevents_late_final_and_save(self):
        self.material()
        entered, release = threading.Event(), threading.Event()
        self.releases.append(release)
        calls = 0

        def respond(*args, **kwargs):
            nonlocal calls
            calls += 1
            self.app.completion_meta.finish_reason = 'stop'
            if calls == 1:
                return json.dumps(self.read_action()), 'Synthetic model'
            entered.set()
            if not release.wait(3):
                raise RuntimeError('Synthetic blocked model timed out')
            return json.dumps({'action': 'final', 'body': '迟到正文。[S1]'}), 'Synthetic model'

        self.model.side_effect = respond
        job = self.job()
        self.assertTrue(entered.wait(1))
        code, response = self.request('POST', '/api/workflows/jobs/'+job['id']+'/cancel', {})
        self.assertEqual(code, 200, response)
        self.assertEqual(response['job']['status'], 'cancelled')
        release.set()
        # Wait for the late model response to be handled, not merely the eager
        # public cancellation state, before asserting it could not commit.
        self.app.jobs().executor.shutdown(wait=True)
        self.assertEqual(self.finished(job['id'])['status'], 'cancelled')
        self.assertEqual(self.app.stores['personal'].list('deliverables'), [])
        self.assertEqual(self.model.call_count, 2)


if __name__ == '__main__':
    unittest.main()
