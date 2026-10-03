"""Deployment backups must capture active WAL without altering live private files."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import urllib.error
from tempfile import TemporaryDirectory
import unittest

from tools.release_backup import backup
from tools.release_verify import verify_anonymous_denial, verify_work_contracts


class ReleaseBackupTests(unittest.TestCase):
    def test_private_custom_definitions_are_backed_up_without_keys_or_probe_cache(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); data = root / 'data'; data.mkdir()
            definition = {'mode': 'custom-' + 'a' * 16, 'model_id': 'shared-model',
                          'base_url': 'https://synthetic.invalid/v1', 'name': 'Synthetic model',
                          'provider_label': 'Synthetic provider'}
            config = {'schema_version': 1, 'models': [{**definition, 'api_key': 'SYNTHETIC_PRIVATE_KEY'}],
                      'registered_api_keys': {'custom': 'SYNTHETIC_PRIVATE_KEY'}}
            source = data / 'custom-models.json'
            source.write_text(json.dumps(config), encoding='utf-8')
            original = source.read_bytes()
            (data / 'model-status.json').write_text('{"synthetic-status":true}')
            target = root / 'backup'
            self.assertEqual(backup(data, target), [])
            self.assertEqual(json.loads((target / 'custom-models.json').read_text()),
                             {'schema_version': 1, 'models': [definition]})
            self.assertNotIn('SYNTHETIC_PRIVATE_KEY', (target / 'custom-models.json').read_text())
            self.assertFalse((target / 'model-status.json').exists())
            self.assertEqual(source.read_bytes(), original)
            manifest = json.loads((target / 'manifest.json').read_text())
            self.assertEqual(manifest['configurations'], ['custom-models.json'])
            self.assertEqual(manifest['model_keys'], 'not-copied')

    def test_context_and_archive_ledgers_and_config_are_backed_up(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary); data = root / 'data'; data.mkdir()
            names = ['conversations.sqlite3', 'project-artifacts.sqlite3']
            for name in names:
                with closing(sqlite3.connect(data / name)) as live:
                    live.execute('CREATE TABLE records(value TEXT)')
                    live.execute('INSERT INTO records VALUES (?)', (name,)); live.commit()
            config = {'schema_version': 1, 'roots': [], 'bindings': {}, 'aliases': {}}
            (data / 'project-artifacts.json').write_text(json.dumps(config), encoding='utf-8')
            target = root / 'backup'
            self.assertEqual(backup(data, target), names)
            self.assertEqual(json.loads((target / 'project-artifacts.json').read_text()), config)
            for name in names:
                with closing(sqlite3.connect(target / name)) as restored:
                    self.assertEqual(restored.execute('SELECT value FROM records').fetchone()[0], name)

    def test_active_wal_is_included_and_authentication_is_not_copied(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / 'data'
            data.mkdir()
            private = data / 'authentication'
            private.mkdir()
            (private / 'account.json').write_text('synthetic-private-account')
            originals = data / 'originals'
            originals.mkdir()
            (originals / 'synthetic').write_bytes(b'synthetic original')
            with closing(sqlite3.connect(data / 'personal.sqlite3')) as live:
                live.execute('PRAGMA journal_mode=WAL')
                live.execute('CREATE TABLE records(value TEXT)')
                live.execute('INSERT INTO records VALUES (?)', ('committed-in-wal',))
                live.commit()
                target = root / 'backup'
                self.assertEqual(backup(data, target), ['personal.sqlite3'])
                with closing(sqlite3.connect(target / 'personal.sqlite3')) as restored:
                    self.assertEqual(restored.execute('SELECT value FROM records').fetchone()[0], 'committed-in-wal')
                self.assertTrue((data / 'personal.sqlite3-wal').exists())
                self.assertFalse((target / 'authentication').exists())
                self.assertFalse((target / 'originals').exists())
                self.assertEqual((private / 'account.json').read_text(), 'synthetic-private-account')
                self.assertEqual((originals / 'synthetic').read_bytes(), b'synthetic original')
                with self.assertRaises(FileExistsError):
                    backup(data, target)


class ReleaseVerifyTests(unittest.TestCase):
    def test_work_contracts_match_real_isolated_installed_routes_without_model_calls(self):
        from tests.test_jobs_http import AsyncJobsHttpTests
        isolated = AsyncJobsHttpTests(methodName='runTest')
        isolated.setUp()
        try:
            code, result = isolated.request('POST', '/api/models/custom', {
                'base_url': 'https://synthetic-release.invalid/v1', 'model_id': 'synthetic-release-model',
                'api_key': 'SYNTHETIC_RUNTIME_ONLY_KEY'})
            self.assertEqual(code, 200, result)
            base = 'http://127.0.0.1:' + str(isolated.app.port)

            def get(url):
                code, result = isolated.request('GET', url.removeprefix(base), csrf=False)
                self.assertEqual(code, 200, result)
                return code, json.dumps(result).encode('utf-8')
            ui = (Path(__file__).resolve().parents[1] / 'web' / 'app.js').read_bytes()
            verify_work_contracts(get, base, ui)
            isolated.model.assert_not_called()
            isolated.app.dsh_answer.assert_not_called()
        finally:
            isolated.tearDown()

    def contracts(self):
        entry = {'mode': 'custom-' + 'a' * 16, 'model_id': 'synthetic-model',
                 'name': 'Synthetic model', 'provider_label': 'Synthetic provider',
                 'base_url': 'https://synthetic.invalid/v1', 'has_api_key': True}
        catalog = {'default_selection_id': 'deepseek:deepseek-v4.1-flash',
                   'groups': [{'id': group, 'models': []} for group in ('dsh', 'deepseek', 'glm', 'kimi', 'hunyuan')]}
        catalog['groups'].append({'id': entry['mode'], 'models': [
            {'selection_id': entry['mode'] + ':' + entry['model_id']} ]})
        responses = {'/api/models': catalog, '/api/models/custom': {'models': [entry]},
                     '/api/guidance': {'title': '使用指南', 'content': 'Synthetic public guide. ' * 20}}
        calls = []

        def get(url):
            self.assertTrue(url.startswith('http://127.0.0.1:18899/'))
            route = url.removeprefix('http://127.0.0.1:18899')
            calls.append(route)
            return 200, json.dumps(responses[route]).encode('utf-8')
        ui = b'unifiedModelOptions data-model-picker renderClarification needs_input /models/custom /guidance'
        return get, responses, calls, ui

    def test_local_contract_probe_only_reads_guidance_catalog_and_public_definitions(self):
        get, responses, calls, ui = self.contracts()
        verify_work_contracts(get, 'http://127.0.0.1:18899', ui)
        self.assertEqual(calls, ['/api/models', '/api/models/custom', '/api/guidance'])
        # Definitions may be empty on a new install; registration is never needed.
        responses['/api/models/custom']['models'] = []
        verify_work_contracts(get, 'http://127.0.0.1:18899', ui)

    def test_probe_rejects_missing_controls_catalog_default_or_key_bearing_public_definitions(self):
        for failure in ('guide', 'catalog', 'default', 'key', 'custom-list', 'ui'):
            with self.subTest(failure=failure):
                get, responses, calls, ui = self.contracts()
                if failure == 'guide': responses['/api/guidance']['content'] = ''
                elif failure == 'catalog': responses['/api/models']['groups'].pop()
                elif failure == 'default': responses['/api/models']['default_selection_id'] = 'dsh:gpt-6-luna'
                elif failure == 'key': responses['/api/models/custom']['models'][0]['api_key'] = 'SYNTHETIC_NOT_PUBLIC'
                elif failure == 'custom-list': responses['/api/models/custom']['models'] = {}
                elif failure == 'ui': ui = ui.replace(b'needs_input', b'')
                with self.assertRaises(ValueError) as raised:
                    verify_work_contracts(get, 'http://127.0.0.1:18899', ui)
                self.assertNotIn('SYNTHETIC_NOT_PUBLIC', str(raised.exception))

    def test_anonymous_probe_checks_guidance_custom_models_and_context_without_cookies(self):
        calls = []

        def denied(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 401, 'Authentication required', {}, None)
        verify_anonymous_denial(denied, 'https://synthetic.invalid')
        self.assertIn('https://synthetic.invalid/api/guidance', calls)
        self.assertIn('https://synthetic.invalid/api/models/custom', calls)
        self.assertIn('https://synthetic.invalid/api/conversations', calls)

        def allowed(url):
            if url.endswith('/api/guidance'): return 200, b'{}'
            return denied(url)
        with self.assertRaisesRegex(ValueError, 'allowed'):
            verify_anonymous_denial(allowed, 'https://synthetic.invalid')
        with self.assertRaisesRegex(ValueError, 'unexpected status'):
            verify_anonymous_denial(lambda url: (_ for _ in ()).throw(
                urllib.error.HTTPError(url, 403, 'Wrong rejection', {}, None)), 'https://synthetic.invalid')


if __name__ == '__main__':
    unittest.main()
