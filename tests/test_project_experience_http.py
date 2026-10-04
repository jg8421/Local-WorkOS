"""Real isolated HTTP project learning; mocked models and synthetic sources only."""
import copy
import unittest
import uuid
from unittest.mock import Mock

from tests import test_jobs_http as jobs_fixture


class ProjectExperienceHttpTests(unittest.TestCase):
    # Compose the fixture helpers, without inheriting its unrelated job tests.
    tearDown = jobs_fixture.AsyncJobsHttpTests.tearDown
    request = jobs_fixture.AsyncJobsHttpTests.request
    normal_model = jobs_fixture.AsyncJobsHttpTests.normal_model

    def setUp(self):
        jobs_fixture.AsyncJobsHttpTests.setUp(self)
        # Export formats have independent real-file tests. Keep these HTTP
        # scope checks independent of cold optional-library import times.
        self.app.archive_record = Mock(return_value={'status': 'synthetic', 'files': []})

    def route(self, workspace='personal', entry=None):
        path = '/api/projects/' + self.projects[workspace]['id'] + '/experience'
        return path + ('/' + entry if entry else '')

    def state(self, workspace='personal'):
        code, value = self.request('GET', self.route(workspace), workspace=workspace, csrf=False)
        self.assertEqual(code, 200, value)
        return value

    def create_rule(self, workspace='personal', **changes):
        body = {'kind': 'preference', 'purpose': 'ask', 'title': 'Synthetic persistent rule',
                'content': 'SYNTHETIC_EXPLICIT_PROJECT_RULE', 'confirm': True, **changes}
        code, result = self.request('POST', self.route(workspace), body, workspace)
        self.assertEqual(code, 201, result)
        return result['entry']

    def ask(self, **changes):
        workspace = changes.pop('workspace', 'personal')
        body = {'question': 'Explain this selected synthetic evidence.', 'project_id': self.projects[workspace]['id'],
                'document_ids': [self.docs[workspace]['id']], 'mode': 'deepseek', 'model_id': 'deepseek-v4.1-flash', **changes}
        return self.request('POST', '/api/ask', body, workspace,
                            headers={'X-WorkOS-Request-ID': uuid.uuid4().hex})

    def test_read_crud_and_settings_are_scoped_and_writes_require_csrf(self):
        self.assertEqual(self.state()['settings'], {'enabled': True})
        for method, route in (('POST', self.route()), ('PATCH', self.route(entry='a'*32)),
                              ('DELETE', self.route(entry='a'*32)), ('POST', self.route()+'/settings'),
                              ('POST', '/api/experience/restore')):
            with self.subTest(method=method, route=route):
                self.assertEqual(self.request(method, route, csrf=False)[0], 403)
        self.assertEqual(self.state()['entries'], [])
        entry = self.create_rule()
        code, edited = self.request('PATCH', self.route(entry=entry['id']), {'content': 'EDITED_SYNTHETIC_RULE'})
        self.assertEqual(code, 200, edited)
        self.assertEqual(edited['entry']['content'], 'EDITED_SYNTHETIC_RULE')
        self.assertEqual(edited['entry']['revisions'][0]['content'], entry['content'])
        self.assertEqual(self.request('GET', self.route(), workspace='demo', csrf=False)[0], 404)
        self.assertEqual(self.request('PATCH', self.route('demo', entry['id']), {'status': 'disabled'}, 'demo')[0], 404)
        other = self.app.stores['personal'].create('projects', {'name': 'Other synthetic project'})
        other_route = '/api/projects/' + other['id'] + '/experience/' + entry['id']
        self.assertEqual(self.request('DELETE', other_route)[0], 404)
        self.assertEqual(self.request('POST', self.route()+'/settings', {'enabled': False})[0], 200)
        self.assertFalse(self.state()['settings']['enabled'])
        self.assertEqual(self.request('DELETE', self.route(entry=entry['id']))[0], 200)
        self.assertEqual(self.state()['entries'], [])
        self.model.assert_not_called()

    def test_anonymous_public_requests_cannot_read_or_modify_experience(self):
        self.app.public_origin = 'https://workos.example.invalid'
        self.app.public_auth_mode = 'password'
        public = {'Host': 'workos.example.invalid', 'Origin': self.app.public_origin}
        for method, route in (('GET', self.route()), ('GET', '/api/experience/backup'),
                              ('POST', self.route()), ('PATCH', self.route(entry='a'*32)),
                              ('DELETE', self.route(entry='a'*32)), ('POST', '/api/experience/restore')):
            with self.subTest(method=method, route=route):
                self.assertEqual(self.request(method, route, csrf=False, headers=public)[0], 401)
        self.assertEqual(self.state()['entries'], [])

    def test_user_durable_preference_enters_new_same_scope_prompt_only(self):
        rule = '以后每次都简洁一些，采用短结论'
        code, first = self.ask(question=rule + '。')
        self.assertEqual(code, 200, first)
        state = self.state()
        self.assertEqual(state['entries'][0]['status'], 'active')
        self.assertEqual(state['entries'][0]['provenance']['conversation_id'], first['conversation_id'])
        code, second = self.ask(question='Explain the next selected evidence briefly.')
        self.assertEqual(code, 200, second)
        self.assertNotEqual(first['conversation_id'], second['conversation_id'])
        self.assertIn(rule, self.model.call_args.args[3])
        code, demo = self.ask(workspace='demo')
        self.assertEqual(code, 200, demo)
        self.assertNotIn(rule, self.model.call_args.args[3])
        store = self.app.stores['personal']
        other = store.create('projects', {'name': 'Other synthetic project'})
        document = store.create('documents', {'title': 'Other synthetic source', 'project_id': other['id'], 'content': 'Separate evidence'})
        code, foreign = self.ask(project_id=other['id'], document_ids=[document['id']])
        self.assertEqual(code, 200, foreign)
        self.assertNotIn(rule, self.model.call_args.args[3])
        code, workflow = self.request('POST', '/api/workflows/run', {'workflow_key': 'brief',
            'message': 'Summarize the selected source.', 'project_id': self.projects['personal']['id'],
            'document_ids': [self.docs['personal']['id']], 'mode': 'deepseek', 'quality_mode': 'fast',
            'request_id': 'synthetic-experience-other-purpose'})
        self.assertEqual(code, 201, workflow)
        self.assertNotIn(rule, self.model.call_args.args[3])
        state = self.state()
        self.assertEqual(len([event for event in state['events'] if event['origin'] == 'conversation']), 3)
        self.assertEqual(len([event for event in state['events'] if event['origin'] == 'record_change']), 3)

    def test_disable_still_records_steps_without_reusing_or_learning(self):
        entry = self.create_rule()
        self.assertEqual(self.request('POST', self.route()+'/settings', {'enabled': False})[0], 200)
        code, result = self.ask(question='以后每次都用英文。')
        self.assertEqual(code, 200, result)
        self.assertNotIn(entry['content'], self.model.call_args.args[3])
        state = self.state()
        self.assertEqual(len([event for event in state['events'] if event['origin'] == 'conversation']), 1)
        self.assertEqual(len(state['entries']), 1)
        self.assertEqual(state['events'][0]['status'], 'completed')
        self.assertTrue(state['events'][0]['execution_steps'])

    def test_failed_generation_logs_failure_without_learning_preference(self):
        self.model.side_effect = ValueError('Synthetic model unavailable')
        code, response = self.ask(question='以后每次都简洁一些。')
        self.assertEqual(code, 400, response)
        state = self.state()
        self.assertEqual(len([event for event in state['events'] if event['origin'] == 'conversation']), 1)
        self.assertEqual(state['events'][0]['status'], 'failed')
        self.assertTrue(state['events'][0]['execution_steps'])
        self.assertEqual(state['entries'], [])
        self.assertEqual(self.app.stores['personal'].list('deliverables'), [])

    def test_authenticated_backup_restore_is_atomic_and_has_no_model_or_store_writes(self):
        entry = self.create_rule()
        self.create_rule('demo', content='DEMO_RULE_NOT_TO_BE_RESTORED')
        code, backup = self.request('GET', '/api/experience/backup', csrf=False)
        self.assertEqual(code, 200, backup)
        stores_before = {workspace: store.backup(workspace)['data'] for workspace, store in self.app.stores.items()}
        demo_before = self.app.experience.backup('demo')
        self.assertEqual(self.request('DELETE', self.route(entry=entry['id']))[0], 200)
        code, restored = self.request('POST', '/api/experience/restore', backup)
        self.assertEqual(code, 200, restored)
        self.assertEqual(self.state()['entries'][0]['id'], entry['id'])
        self.assertEqual(self.app.experience.backup('demo'), demo_before)
        invalid = copy.deepcopy(backup)
        invalid['entries'][0]['purpose'] = 'not-a-purpose'
        self.assertEqual(self.request('POST', '/api/experience/restore', invalid)[0], 400)
        self.assertEqual(self.app.experience.backup('personal'), backup)
        self.assertEqual(self.request('POST', '/api/experience/restore', backup, workspace='demo')[0], 400)
        self.assertEqual({workspace: store.backup(workspace)['data'] for workspace, store in self.app.stores.items()}, stores_before)
        self.model.assert_not_called()

    def test_manual_save_upload_task_changes_and_delete_appear_without_teaching(self):
        import base64
        project_id = self.projects['personal']['id']
        code, note = self.request('POST', '/api/notes', {'title': 'Synthetic manual note',
            'project_id': project_id, 'body': '以后每次都用英文。UNCONFIRMED_BODY_NOT_A_RULE'})
        self.assertEqual(code, 201, note)
        code, document = self.request('POST', '/api/upload', {'name': 'synthetic.txt', 'project_id': project_id,
            'base64': base64.b64encode(b'SYNTHETIC_IMPORTED_SOURCE').decode()})
        self.assertEqual(code, 201, document)
        code, task = self.request('POST', '/api/tasks', {'title': 'Synthetic followup', 'project_id': project_id})
        self.assertEqual(code, 201, task)
        self.assertEqual(self.request('PATCH', '/api/tasks/'+task['id'], {'status': '完成'})[0], 200)
        self.assertEqual(self.request('DELETE', '/api/tasks/'+task['id'])[0], 200)
        events = [event for event in self.state()['events'] if event['origin'] == 'record_change']
        expected = {('notes', note['id'], 'create'), ('documents', document['id'], 'create'),
                    ('tasks', task['id'], 'create'), ('tasks', task['id'], 'update'), ('tasks', task['id'], 'delete')}
        self.assertTrue(expected.issubset({(event['collection'], event['record_id'], event['action']) for event in events}))
        self.assertTrue(all(event['project_id'] == project_id and event['status'] == 'completed' for event in events))
        self.assertEqual(self.state()['entries'], [])
        self.assertEqual(self.app.experience.context('personal', project_id, 'ask', [document['id']])['text'], '')
        self.assertNotIn('UNCONFIRMED_BODY_NOT_A_RULE', str(events))
        self.model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
