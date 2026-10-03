"""Missing action inputs remain continuable after already completed work.

Temporary workspaces, synthetic model decisions, and no external provider calls.
"""
import json
import unittest
from unittest.mock import Mock

from tests import test_ai_experience_http as experience_fixture


class AgentGuidanceTests(unittest.TestCase):
    setUp = experience_fixture.AiExperienceHttpTests.setUp
    stop_server = experience_fixture.AiExperienceHttpTests.stop_server
    request = experience_fixture.AiExperienceHttpTests.request
    post = experience_fixture.AiExperienceHttpTests.post
    conversation = experience_fixture.AiExperienceHttpTests.conversation

    def action_body(self, **changes):
        return {'message': 'Complete the explicitly requested synthetic action.',
                'project_id': self.project['id'], 'document_ids': [], 'mode': 'dsh',
                'model_id': 'gpt-6-luna', **changes}

    def decide(self, action, args=None):
        return json.dumps({'action': action, 'args': args or {}, 'say': 'Synthetic action.'})

    def need_input(self, decision, *, body=None):
        self.app.dsh_answer.side_effect = [decision]
        status, result = self.post('/api/agent', body or self.action_body())
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get('status'), 'needs_input', result)
        self.assertTrue(result.get('questions'), result)
        self.assertNotIn('error', result)
        self.assertEqual(result.get('steps'), [], result)
        self.assertEqual(self.app.dsh_answer.call_count, 1)
        self.app.local_chat.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])
        if result.get('conversation_id'):
            turn = self.conversation(result['conversation_id'])['turns'][-1]
            self.assertEqual(turn['status'], 'needs_input')
            self.assertEqual(turn['current_artifact'], {})
        return result

    def test_nested_research_workflow_without_selected_sources_asks_before_another_model_call(self):
        self.need_input(self.decide('run_workflow', {'workflow_key': 'brief', 'message': 'Review the evidence.'}))
        self.assertEqual(self.app.artifacts.status('personal', self.project)['archives'], [])

    def test_model_final_clarification_persists_a_waiting_turn_without_empty_records(self):
        before_projects = self.store.list('projects')
        self.need_input(json.dumps({'action': 'final', 'status': 'needs_input',
            'message': 'Which result should I prepare?', 'questions': [
                {'id': 'goal', 'label': 'Which result should I prepare?', 'hint': 'Brief or email?'}]}))
        self.assertEqual(self.store.list('projects'), before_projects)
        for collection in ('notes', 'tasks', 'meetings', 'deliverables'):
            self.assertEqual(self.store.list(collection), [])

    def test_create_project_without_name_asks_without_creating_an_empty_project(self):
        before_projects = self.store.list('projects')
        self.need_input(self.decide('create_project'), body=self.action_body(project_id=''))
        self.assertEqual(self.store.list('projects'), before_projects)

    def test_organize_project_without_project_is_a_question_and_does_not_create_a_project(self):
        before = self.store.list('projects')
        self.need_input(self.decide('organize_project'), body=self.action_body(project_id=''))
        self.assertEqual(self.store.list('projects'), before)

    def test_subtask_missing_project_or_title_never_creates_an_empty_task(self):
        for project_id, args in [('', {'title': 'Synthetic subtask'}), (self.project['id'], {})]:
            with self.subTest(project_id=bool(project_id)):
                self.app.dsh_answer.reset_mock()
                self.need_input(self.decide('create_subtask', args), body=self.action_body(project_id=project_id))
        self.assertEqual(self.store.list('tasks'), [])

    def test_minutes_missing_selected_meeting_does_not_invent_one(self):
        self.app.meeting_draft = Mock(side_effect=AssertionError('Missing meeting must not call drafting.'))
        self.need_input(self.decide('generate_minutes'))
        self.app.meeting_draft.assert_not_called()
        self.assertEqual(self.store.list('meetings'), [])

    def test_selected_meeting_without_transcript_keeps_current_manual_summary(self):
        meeting = self.store.create('meetings', {'title': 'Synthetic meeting without transcript',
            'project_id': self.project['id'], 'summary': 'Current manual summary remains unchanged.'})
        self.app.meeting_draft = Mock(side_effect=AssertionError('Missing transcript must not call drafting.'))
        self.need_input(self.decide('generate_minutes'), body=self.action_body(meeting_id=meeting['id']))
        self.app.meeting_draft.assert_not_called()
        self.assertEqual(self.store.get('meetings', meeting['id']), meeting)

    def test_clarification_preserves_preceding_saved_note_and_its_action_receipt(self):
        self.app.dsh_answer.side_effect = [
            self.decide('create_note', {'title': 'Synthetic saved note', 'body': 'Explicit synthetic note content.'}),
            self.decide('run_workflow', {'workflow_key': 'brief', 'message': 'Review selected evidence.'})]
        status, result = self.post('/api/agent', self.action_body())
        self.assertEqual(status, 200, result)
        self.assertEqual(result.get('status'), 'needs_input', result)
        self.assertTrue(result.get('questions'), result)
        self.assertEqual(self.app.dsh_answer.call_count, 2)
        self.app.local_chat.assert_not_called()
        self.assertEqual(len(result['steps']), 1)
        receipt = result['steps'][0]
        self.assertEqual(receipt['action'], 'create_note')
        note = self.store.get('notes', receipt['result']['id'])
        self.assertEqual(note['body'], 'Explicit synthetic note content.')
        self.assertEqual(note['project_id'], self.project['id'])
        self.assertEqual(receipt['archive']['status'], 'saved')
        self.assertEqual(self.store.list('deliverables'), [])
        turn = self.conversation(result['conversation_id'])['turns'][-1]
        self.assertEqual(turn['status'], 'needs_input')
        self.assertEqual(turn['output_snapshot']['steps'], result['steps'])
        self.assertEqual(turn['current_artifact'], {})

    def test_source_expansion_remains_an_error_instead_of_a_clarification(self):
        self.app.dsh_answer.side_effect = [self.decide('run_workflow', {'workflow_key': 'brief',
            'message': 'Review evidence.', 'document_ids': [self.doc['id']]})]
        status, result = self.post('/api/agent', self.action_body())
        self.assertEqual(status, 400, result)
        self.assertIn('error', result)
        self.assertNotEqual(result.get('status'), 'needs_input', result)
        self.assertEqual(self.app.dsh_answer.call_count, 1)
        self.app.local_chat.assert_not_called()
        self.assertEqual(self.store.list('deliverables'), [])

    def test_followup_model_context_contains_completed_receipt_without_raw_tool_arguments(self):
        self.app.dsh_answer.side_effect = [
            self.decide('create_note', {'title':'Synthetic completed note','body':'Synthetic raw note body.'}),
            self.decide('run_workflow', {'workflow_key':'brief','message':'Review the evidence.'})]
        status, first = self.post('/api/agent',self.action_body())
        self.assertEqual(status,200,first)
        self.assertEqual(first.get('status'),'needs_input',first)
        note_id=first['steps'][0]['result']['id']
        def finish(prompt,model):
            self.assertIn(note_id,prompt)
            self.assertIn('Synthetic completed note',prompt)
            self.assertIn('create_note',prompt)
            self.assertIn('已完成的操作回执',prompt)
            self.assertNotIn('Synthetic raw note body.',prompt)
            self.assertNotIn(str(self.project_folder),prompt)
            return json.dumps({'action':'final','answer':'The prior note is already saved.'})
        self.app.dsh_answer.side_effect=finish
        status,second=self.post('/api/agent',self.action_body(message='Only confirm the prior saved note.',
            conversation_id=first['conversation_id']))
        self.assertEqual(status,200,second)
        self.assertEqual(second['conversation_id'],first['conversation_id'])
        self.assertEqual(second['steps'],[])
        self.assertEqual(len(self.store.list('notes')),1)
        self.assertEqual(self.store.get('notes',note_id)['body'],'Synthetic raw note body.')


if __name__ == '__main__':
    unittest.main()
