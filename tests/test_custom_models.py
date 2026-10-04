"""Endpoint-scoped user model registration and actual HTTP dispatch, synthetic only."""
import json
import io
import unittest
import urllib.error
from unittest.mock import Mock, patch

from workos.custom_models import CustomModels
from workos.model_catalog import resolve_selection, selection_identity
from workos.password_auth import SESSION_COOKIE
from workos.server import Application
from tests import test_jobs_http as fixture
from tests.test_model_selection_http import Response


class CustomModelHttpTests(unittest.TestCase):
    setUp = fixture.AsyncJobsHttpTests.setUp
    tearDown = fixture.AsyncJobsHttpTests.tearDown
    request = fixture.AsyncJobsHttpTests.request
    normal_model = fixture.AsyncJobsHttpTests.normal_model
    finished = fixture.AsyncJobsHttpTests.finished

    def register(self, endpoint='https://synthetic-a.invalid/v1', *, key='SYNTHETIC_A_KEY',
                 model='shared-upstream-id', **extra):
        body = {'base_url': endpoint, 'model_id': model, 'name': 'Synthetic custom',
                'provider_label': 'Synthetic service', **extra}
        if key is not None:
            body['api_key'] = key
        code, result = self.request('POST', '/api/models/custom', body)
        self.assertEqual(code, 200, result)
        return result['model']

    def transport(self, reply):
        self.reply, self.calls = reply, []
        self.app.ai.update(base_url='https://synthetic-legacy.invalid/v1',
                           model='legacy-configured-model', api_key='SYNTHETIC_LEGACY_KEY')
        self.app.local_chat = Application.local_chat.__get__(self.app, Application)
        self.app.dsh_answer = Mock(side_effect=AssertionError('No DSH fallback permitted'))

        def compatible(request, timeout=None):
            payload = json.loads(request.data)
            self.calls.append({'url': request.full_url, 'payload': payload,
                               'headers': dict(request.header_items())})
            answer = self.reply(payload) if callable(self.reply) else self.reply
            return Response({'choices': [{'finish_reason': 'stop', 'message': {'content': answer}}]})
        self.stack.enter_context(patch('urllib.request.urlopen', side_effect=compatible))

    def choice_body(self, entry, surface, **changes):
        body = {'mode': entry['mode'], 'provider': entry['mode'], 'model_id': entry['model_id'],
                'project_id': self.projects['personal']['id']}
        if surface == 'ask':
            body.update(question='Explain selected synthetic evidence', document_ids=[self.docs['personal']['id']])
        elif surface == 'valuation':
            body.update(method='net_income', text='Synthetic RMB million FY2025A profit100, PE12')
        elif surface == 'meeting':
            body.update(transcript='Synthetic expert transcript. No real business content.')
        elif surface == 'actions':
            body.update(message='只回复收到', document_ids=[self.docs['personal']['id']])
        elif surface == 'workflow':
            body.update(workflow_key='brief', message='分析这一份合成材料', quality_mode='fast',
                        document_ids=[self.docs['personal']['id']], request_id='custom-test-' + entry['mode'])
        return {**body, **changes}

    def post_surface(self, surface, body):
        routes = {'ask': 'ask', 'valuation': 'model/parse-assumptions', 'meeting': 'meeting-draft',
                  'actions': 'agent', 'workflow': 'workflows/run'}
        return self.request('POST', '/api/' + routes[surface], body)

    def assert_call(self, entry, key):
        self.assertEqual(len(self.calls), 1, self.calls)
        call = self.calls[0]
        self.assertEqual(call['url'], entry['base_url'] + '/chat/completions')
        self.assertEqual(call['payload']['model'], entry['model_id'])
        authorization = next((value for header, value in call['headers'].items()
                              if header.lower() == 'authorization'), None)
        self.assertEqual(authorization, 'Bearer ' + key if key else None)
        self.app.dsh_answer.assert_not_called()

    def test_distinct_endpoint_identities_persist_without_credentials_and_reload(self):
        self.transport('NOT CALLED')
        first = self.register()
        second = self.register('https://synthetic-b.invalid/v1', key='SYNTHETIC_B_KEY')
        self.assertNotEqual(first['mode'], second['mode'])
        self.assertEqual(first['model_id'], second['model_id'])
        first_choice = resolve_selection(self.choice_body(first, 'ask'), self.app.ai)
        second_choice = resolve_selection(self.choice_body(second, 'ask'), self.app.ai)
        self.assertNotEqual(selection_identity(first_choice), selection_identity(second_choice))
        raw = self.app.custom_models.path.read_text(encoding='utf-8')
        self.assertNotIn('SYNTHETIC_A_KEY', raw)
        self.assertNotIn('SYNTHETIC_B_KEY', raw)
        self.assertNotIn('api_key', raw)
        reloaded = CustomModels(self.data_dir)
        self.assertEqual({entry['mode'] for entry in reloaded.public()}, {first['mode'], second['mode']})
        self.assertTrue(all(not entry['has_api_key'] for entry in reloaded.public()))
        self.assertEqual(reloaded.keys_snapshot(), {})
        with self.assertRaisesRegex(ValueError, '多个连接'):
            resolve_selection({'model_id': 'shared-upstream-id'}, self.app.ai)
        # Exercise restart key loss through the actual adapter, not just a flag.
        self.app.custom_models = reloaded
        self.app.refresh_custom_models()
        self.reply = 'Synthetic reply [S1]'
        code, result = self.post_surface('ask', self.choice_body(first, 'ask'))
        self.assertEqual(code, 200, result)
        self.assert_call(first, '')

    def test_duplicate_endpoint_model_pair_updates_stably_and_key_clear_is_explicit(self):
        self.transport('NOT CALLED')
        first = self.register()
        changed = self.register('https://synthetic-a.invalid/v1/', key=None,
                                name='Updated display', provider_label='Updated group')
        self.assertEqual(first['mode'], changed['mode'])
        self.assertTrue(changed['has_api_key'])
        self.assertEqual(len(self.app.custom_models.configs()), 1)
        code, listed = self.request('GET', '/api/models/custom', csrf=False)
        self.assertEqual(code, 200, listed)
        self.assertEqual(listed['models'][0]['name'], 'Updated display')
        cleared = self.register(key='')
        self.assertEqual(cleared['mode'], first['mode'])
        self.assertFalse(cleared['has_api_key'])
        self.assertEqual(self.calls, [])

    def test_register_list_catalog_remove_never_call_models_or_expose_keys(self):
        self.transport('NOT CALLED')
        first = self.register()
        second = self.register('https://synthetic-b.invalid/v1', key=None)
        for route in ('/api/models/custom', '/api/models', '/api/bootstrap'):
            code, result = self.request('GET', route, csrf=False)
            self.assertEqual(code, 200, result)
            self.assertNotIn('SYNTHETIC_A_KEY', json.dumps(result))
        code, catalog = self.request('GET', '/api/models', csrf=False)
        entries = {item['selection_id']: item for group in catalog['groups'] for item in group['models']}
        for entry in (first, second):
            public = entries[entry['mode'] + ':' + entry['model_id']]
            self.assertTrue(public['available'])
            self.assertEqual(public['status'], 'not_checked')
        code, removed = self.request('DELETE', '/api/models/custom/' + first['mode'][7:])
        self.assertEqual(code, 200, removed)
        self.assertTrue(removed['removed'])
        self.assertNotIn(first['mode'], self.app.custom_models.keys_snapshot())
        self.assertEqual([entry['mode'] for entry in self.app.custom_models.public()], [second['mode']])
        code, result = self.post_surface('ask', self.choice_body(first, 'ask'))
        self.assertEqual(code, 400, result)
        self.assertEqual(self.calls, [])

    def test_same_upstream_id_dispatches_own_endpoint_and_key_on_all_surfaces(self):
        self.transport('unused')
        first = self.register()
        second = self.register('https://synthetic-b.invalid/v1', key='SYNTHETIC_B_KEY')
        replies = {
            'ask': 'Synthetic explanation, needs verification. [S1]',
            'valuation': json.dumps({'assumptions': {'currency': 'RMB', 'unit': 'million',
                    'period': 'FY2025A', 'net_income': 100, 'pe_multiple': 12}}),
            'meeting': json.dumps({'title': 'Synthetic notes', 'summary': 'Synthetic summary',
                                  'experts': [], 'matrix': {}, 'contents': []}),
            'actions': json.dumps({'action': 'final', 'answer': '收到'}),
            'workflow': 'Synthetic brief, needs verification. [S1]',
        }
        for surface, reply in replies.items():
            for entry, key in ((first, 'SYNTHETIC_A_KEY'), (second, 'SYNTHETIC_B_KEY')):
                with self.subTest(surface=surface, mode=entry['mode']):
                    self.reply, self.calls = reply, []
                    code, result = self.post_surface(surface, self.choice_body(entry, surface))
                    self.assertEqual(code, 201 if surface == 'workflow' else 200, result)
                    self.assert_call(entry, key)
                    self.assertIn(entry['model_id'], result['model'])
                    self.assertNotIn('SYNTHETIC_A_KEY', json.dumps(result))
                    self.assertNotIn('SYNTHETIC_B_KEY', json.dumps(result))
        # Presets and the legacy configured model cannot inherit either custom key.
        for mode, model, expected_key in (('deepseek', 'deepseek-v4-pro', ''),
                                          ('model', 'legacy-configured-model', 'SYNTHETIC_LEGACY_KEY')):
            self.calls = []; self.reply = 'Synthetic explanation [S1]'
            code, result = self.post_surface('ask', {**self.choice_body(first, 'ask'),
                                                    'mode': mode, 'provider': mode, 'model_id': model})
            self.assertEqual(code, 200, result)
            auth = next((value for name, value in self.calls[0]['headers'].items() if name.lower() == 'authorization'), None)
            self.assertEqual(auth, 'Bearer ' + expected_key if expected_key else None)

    def test_custom_agent_rounds_and_synthetic_check_preserve_identity_without_business_probe(self):
        self.transport('unused')
        entry = self.register()
        rounds = []

        def action_reply(_):
            rounds.append(1)
            return json.dumps({'action': 'create_note', 'args': {'title': 'Synthetic note', 'body': 'Synthetic content'}}
                              if len(rounds) == 1 else {'action': 'final', 'answer': '已保存'})
        self.reply = action_reply
        code, result = self.post_surface('actions', self.choice_body(entry, 'actions', message='保存一条合成结论'))
        self.assertEqual(code, 200, result)
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(call['url'] == entry['base_url'] + '/chat/completions' for call in self.calls))
        self.assertTrue(all(call['payload']['model'] == entry['model_id'] for call in self.calls))
        self.assertEqual(len(result['steps']), 1)
        self.reply, self.calls = '{"ok":true}', []
        history = self.app.conversations.backup('personal')
        store = self.app.stores['personal']
        with patch.object(store, 'get', side_effect=AssertionError('Connection check must not read records')), \
             patch.object(store, 'list', side_effect=AssertionError('Connection check must not scan records')):
            code, result = self.request('POST', '/api/models/check', {
                'mode': entry['mode'], 'model_id': entry['model_id'],
                'text': 'PRIVATE_SYNTHETIC_SENTINEL', 'document_ids': [self.docs['personal']['id']],
                'api_key': 'UNTRUSTED_CLIENT_KEY'})
        self.assertEqual(code, 200, result)
        self.assertEqual(result['model']['status'], 'verified')
        self.assert_call(entry, 'SYNTHETIC_A_KEY')
        prompt = json.dumps(self.calls[0]['payload'])
        self.assertNotIn('PRIVATE_SYNTHETIC_SENTINEL', prompt)
        self.assertNotIn(self.docs['personal']['id'], prompt)
        self.assertNotIn('UNTRUSTED_CLIENT_KEY', json.dumps(self.calls))
        self.assertEqual(self.app.conversations.backup('personal'), history)
        cache = self.app.model_health_path.read_text(encoding='utf-8')
        self.assertNotIn('SYNTHETIC_A_KEY', cache)
        self.assertNotIn('PRIVATE_SYNTHETIC_SENTINEL', cache)

    def test_custom_service_failure_does_not_fallback_save_or_echo_upstream_error(self):
        self.transport('unused')
        entry = self.register()
        store = self.app.stores['personal']
        with patch('workos.store.now', return_value='2030-01-01T23:59:59+00:00'):
            before = store.backup('personal')

        def failed(request, timeout=None):
            self.calls.append({'url': request.full_url, 'payload': json.loads(request.data),
                               'headers': dict(request.header_items())})
            raise urllib.error.HTTPError(request.full_url, 400, 'Synthetic unavailable', {},
                                         io.BytesIO(b'UPSTREAM_PRIVATE_ERROR_SENTINEL'))
        with patch('urllib.request.urlopen', side_effect=failed):
            code, result = self.post_surface('workflow', self.choice_body(entry, 'workflow'))
        self.assertEqual(code, 400, result)
        self.assert_call(entry, 'SYNTHETIC_A_KEY')
        self.assertNotIn('UPSTREAM_PRIVATE_ERROR_SENTINEL', json.dumps(result))
        # Cross a deterministic clock boundary while preserving every stored
        # record and the backup's format/version/workspace scope checks.
        with patch('workos.store.now', return_value='2030-01-02T00:00:00+00:00'):
            after = store.backup('personal')
        self.assertNotEqual(after['exported_at'], before['exported_at'])
        self.assertEqual({key: value for key, value in after.items() if key != 'exported_at'},
                         {key: value for key, value in before.items() if key != 'exported_at'})

    def test_durable_custom_job_uses_exact_registered_endpoint_and_keeps_secrets_out_of_snapshot(self):
        self.transport('Synthetic research result, needs verification. [S1]')
        entry = self.register()
        body = self.choice_body(entry, 'workflow', request_id='durable-custom-registration-test')
        code, result = self.request('POST', '/api/workflows/jobs', body)
        self.assertEqual(code, 202, result)
        job = self.finished(result['job']['id'])
        self.assertEqual(job['status'], 'completed', job)
        self.assert_call(entry, 'SYNTHETIC_A_KEY')
        self.assertNotIn('SYNTHETIC_A_KEY', json.dumps(job))
        for path in self.data_dir.rglob('*.json'):
            self.assertNotIn('SYNTHETIC_A_KEY', path.read_text(encoding='utf-8'), str(path))
        saved = self.app.stores['personal'].get('deliverables', job['result']['deliverable']['id'])
        self.assertEqual(saved['source_ids'], [self.docs['personal']['id']])

    def test_invalid_endpoint_model_and_mode_fail_without_registration_or_dispatch(self):
        self.transport('NOT CALLED')
        for changes in ({'base_url': 'https://user:password@synthetic.invalid/v1'},
                        {'base_url': 'https://synthetic.invalid/v1?key=secret'},
                        {'base_url': 'http://synthetic.invalid/v1'},
                        {'base_url': 'file:///tmp/model'},
                        {'base_url': 'https://synthetic.invalid/v1\n'},
                        {'model_id': 'invalid model id'}, {'model_id': '../arbitrary'},
                        {'model_id': 'https://synthetic.invalid/model'},
                        {'api_key': 'bad\nheader'}, {'provider_label': 'bad\nlabel'}):
            with self.subTest(changes=changes):
                code, result = self.request('POST', '/api/models/custom', {
                    'base_url': 'https://synthetic.invalid/v1', 'model_id': 'safe-model-id', **changes})
                self.assertEqual(code, 400, result)
                self.assertEqual(self.app.custom_models.configs(), [])
        entry = self.register()
        for changes in ({'mode': 'custom-invalid'}, {'mode': 'custom-' + '0' * 16},
                        {'mode': entry['mode'], 'model_id': 'different-upstream'},
                        {'mode': entry['mode'], 'provider': 'deepseek'}):
            with self.subTest(changes=changes):
                code, result = self.post_surface('ask', {**self.choice_body(entry, 'ask'), **changes})
                self.assertEqual(code, 400, result)
        self.assertEqual(self.calls, [])

    def test_public_authentication_and_session_csrf_precede_registration_key_access(self):
        self.transport('NOT CALLED')
        entry = self.register()
        self.app.public_origin = 'https://workos.synthetic.invalid'
        self.app.public_auth_mode = 'password'
        headers = {'Host': 'workos.synthetic.invalid', 'Origin': self.app.public_origin}
        before = self.app.custom_models.path.read_bytes()
        for method, path in (('GET', '/api/models/custom'), ('GET', '/api/models'),
                             ('POST', '/api/models/custom'),
                             ('DELETE', '/api/models/custom/' + entry['mode'][7:])):
            code, result = self.request(method, path, csrf=False, headers=headers)
            self.assertEqual(code, 401, result)
            self.assertNotIn('SYNTHETIC_A_KEY', json.dumps(result))
        self.app.password_auth.configure_password('Synthetic-Only-Password2026!')
        nonce = self.app.password_auth.issue_challenge('synthetic-client')
        token, _ = self.app.password_auth.login(self.app.password_auth.username,
            'Synthetic-Only-Password2026!', 'synthetic-client', nonce, nonce, True)
        cookie = SESSION_COOKIE + '=' + token
        session = self.app.password_auth.get_session(cookie)
        authenticated = {**headers, 'Cookie': cookie}
        code, result = self.request('GET', '/api/models/custom', csrf=False, headers=authenticated)
        self.assertEqual(code, 200, result)
        self.assertTrue(result['models'][0]['has_api_key'])
        self.assertNotIn('SYNTHETIC_A_KEY', json.dumps(result))
        for method, path in (('POST', '/api/models/custom'),
                             ('DELETE', '/api/models/custom/' + entry['mode'][7:])):
            code, result = self.request(method, path, headers=authenticated)
            self.assertEqual(code, 403, result)
            self.assertEqual(result['code'], 'csrf_expired')
        self.assertEqual(self.app.custom_models.path.read_bytes(), before)
        code, result = self.request('DELETE', '/api/models/custom/' + entry['mode'][7:],
            csrf=False, headers={**authenticated, 'X-CSRF-Token': session['csrf']})
        self.assertEqual(code, 200, result)
        self.assertEqual(self.calls, [])

    def test_local_csrf_and_cross_origin_fail_before_custom_definition_changes(self):
        self.transport('NOT CALLED')
        entry = self.register()
        before = self.app.custom_models.path.read_bytes()
        for method, path in (('POST', '/api/models/custom'),
                             ('DELETE', '/api/models/custom/' + entry['mode'][7:])):
            code, result = self.request(method, path, csrf=False)
            self.assertEqual(code, 403, result)
            self.assertEqual(result['code'], 'csrf_expired')
            code, result = self.request(method, path, headers={'Origin': 'https://evil.synthetic.invalid'})
            self.assertEqual(code, 403, result)
            self.assertNotIn('code', result)
        self.assertEqual(self.app.custom_models.path.read_bytes(), before)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
