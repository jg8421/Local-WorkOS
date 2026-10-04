import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from tools.deployment_preflight import readiness, clean_environment, runtime_environment, start
from tools.package_windows import allowed_source, extract, fetch, digest, build


class DeploymentTests(unittest.TestCase):
    def test_runtime_source_allowlist_excludes_private_payload_and_test_trees(self):
        for name in ('workos/server.py', 'web/app.js', 'vendor/pypdf/_reader.py', 'docs/DEPLOYMENT.md', 'tools/deployment_preflight.py'):
            self.assertTrue(allowed_source(name), name)
        for name in ('work/private.py', '.env', 'data/personal.sqlite3', 'tests/test_server.py',
                     'workos/notes.docx', 'credentials/token.json', 'outputs/private.pdf',
                     'web/../../private.py', '/workos/server.py', 'workos/__pycache__/x.py'):
            self.assertFalse(allowed_source(name), name)

    def test_hash_cache_is_verified_and_downloads_are_official_only(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder); raw = b'Synthetic official input'
            entry = {'filename': 'python.zip', 'url': 'https://www.python.org/synthetic.zip', 'sha256': digest(raw)}
            (cache/'python.zip').write_bytes(raw)
            with patch('urllib.request.urlopen', side_effect=AssertionError('Verified cache needs no request')):
                self.assertEqual(fetch(entry, cache).read_bytes(), raw)
            (cache/'python.zip').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA256'): fetch(entry, cache)
            with self.assertRaisesRegex(ValueError, 'official'): fetch({**entry, 'url': 'https://unknown.invalid/model.zip'}, cache)
            with self.assertRaisesRegex(ValueError, 'filename'): fetch({**entry, 'filename': '../private.zip'}, cache)

    def test_runtime_archive_rejects_traversal_absolute_paths_and_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('../outside.py', '/outside.py', 'C:/outside.py', 'CON.txt', 'safe/LPT1.py', 'trailing .py ', 'alternate*name.py'):
                with self.subTest(name=name):
                    archive = root/'input.zip'
                    with zipfile.ZipFile(archive, 'w') as zipped: zipped.writestr(name, b'synthetic')
                    with self.assertRaises(ValueError): extract(archive, root/'target')
            link = zipfile.ZipInfo('link'); link.external_attr = 0o120777 << 16
            with zipfile.ZipFile(root/'input.zip', 'w') as zipped: zipped.writestr(link, '../outside')
            with self.assertRaises(ValueError): extract(root/'input.zip', root/'target')
            self.assertFalse((root/'outside.py').exists())

    def test_wheel_installs_libraries_and_licenses_without_scripts_or_system_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); archive = root/'synthetic.whl'
            with zipfile.ZipFile(archive, 'w') as zipped:
                zipped.writestr('synthetic/__init__.py', '')
                zipped.writestr('synthetic-1.dist-info/licenses/LICENSE', 'Synthetic license')
                zipped.writestr('synthetic-1.data/scripts/helper.exe', 'Not installed')
                zipped.writestr('synthetic-1.data/purelib/extra.py', '# synthetic')
                zipped.writestr('synthetic/templates/_rels/.rels', 'Synthetic Office relationships')
            extract(archive, root/'site', wheel=True)
            self.assertTrue((root/'site/synthetic/__init__.py').exists())
            self.assertTrue((root/'site/synthetic-1.dist-info/licenses/LICENSE').exists())
            self.assertTrue((root/'site/extra.py').exists())
            self.assertTrue((root/'site/synthetic/templates/_rels/.rels').exists())
            self.assertFalse((root/'site/synthetic-1.data/scripts/helper.exe').exists())

    def test_missing_excel_and_optional_tools_do_not_block_core_or_claim_verification(self):
        report = readiness(Path('.'), module_available=lambda name: name == 'openpyxl',
                           which=lambda name: None, platform='nt', excel_registered=False)
        checks = {item['id']: item for item in report['checks']}
        self.assertTrue(report['core_ready']); self.assertTrue(checks['xlsx']['ready'])
        self.assertFalse(checks['excel']['ready']); self.assertFalse(checks['dsh']['ready'])
        self.assertFalse(checks['docx']['ready']); self.assertNotIn('excel_verified', json.dumps(report))
        self.assertFalse(checks['models']['ready'])

    def test_fresh_portable_start_does_not_restore_public_sync_or_memory_settings(self):
        with patch.dict(os.environ, {'WORKOS_PUBLIC_ORIGIN': 'https://synthetic.invalid',
                'WORKOS_SYNC_ROOT': 'SYNTHETIC_PRIVATE_PATH', 'WORKOS_MEMORY_ROOT': 'SYNTHETIC_MEMORY',
                'WORKOS_ACCESS_TEAM': 'synthetic-team', 'WORKOS_LIBREOFFICE_CLI': 'synthetic-approved-kit'}):
            result = clean_environment()
            self.assertEqual(result['WORKOS_PUBLIC_ORIGIN'], '')
            self.assertEqual(result['WORKOS_SYNC_ROOT'], '')
            self.assertEqual(result['WORKOS_MEMORY_ROOT'], '')
            self.assertNotIn('WORKOS_ACCESS_TEAM', result)
            self.assertEqual(result['WORKOS_LIBREOFFICE_CLI'], 'synthetic-approved-kit')
            self.assertEqual(os.environ['WORKOS_SYNC_ROOT'], 'SYNTHETIC_PRIVATE_PATH')

    def test_optional_portable_node_only_changes_child_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); app = root/'app'; app.mkdir()
            node = root/'runtime/node/node.exe'; node.parent.mkdir(parents=True); node.write_bytes(b'synthetic')
            with patch.dict(os.environ, {'PATH': 'synthetic-host-path'}, clear=True):
                env = runtime_environment(app)
                self.assertTrue(env['PATH'].startswith(str(node.parent) + os.pathsep))
                self.assertEqual(env['WORKOS_NODE'], str(node))
                self.assertEqual(os.environ['PATH'], 'synthetic-host-path')
                self.assertNotIn('WORKOS_NODE', os.environ)

    def test_existing_services_are_not_stopped_or_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); data = root/'not-created'
            with patch('tools.deployment_preflight.health', return_value={'app': 'local-workos', 'version': 'synthetic-existing'}), \
                    patch('tools.deployment_preflight.subprocess.Popen') as popen:
                result = start(root, data, 18991, browser=False)
                self.assertEqual(result['version'], 'synthetic-existing')
                self.assertFalse(data.exists()); popen.assert_not_called()
            with patch('tools.deployment_preflight.health', return_value={'app': 'other-app'}), \
                    patch('tools.deployment_preflight.subprocess.Popen') as popen:
                with self.assertRaisesRegex(ValueError, '其他程序'): start(root, data, 18991, browser=False)
                self.assertFalse(data.exists()); popen.assert_not_called()

    def test_new_worker_has_explicit_app_path_in_isolated_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            app=Path(folder)/'app with spaces';app.mkdir();data=Path(folder)/'temporary-data'
            with patch('tools.deployment_preflight.health', side_effect=[None, {'app':'local-workos','version':'synthetic'}]), \
                    patch('tools.deployment_preflight.subprocess.Popen') as popen:
                result=start(app,data,18991,browser=False)
                command=popen.call_args.args[0]
                self.assertEqual(command[1:3], ['-I','-c'])
                self.assertIn(str(app.resolve()), command)
                self.assertNotIn(str(app), command[3])
                self.assertIn('sys.path[:0]', command[3])
                popen.return_value.terminate.assert_not_called()
                self.assertEqual(result['version'], 'synthetic')

    def test_real_package_manifest_matches_public_files_and_offline_licenses(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'repo'; root.mkdir(); cache = Path(folder)/'cache'; cache.mkdir()
            public = {'workos/__init__.py': "__version__ = '0.0.0'\n", 'workos/server.py': '# Synthetic public source',
                      'web/index.html': '<html>synthetic</html>', 'tools/deployment_preflight.py': '# Synthetic launcher',
                      'docs/DEPLOYMENT.md': '# Synthetic public deployment guide'}
            private = {'.env': 'SYNTHETIC_PRIVATE_SECRET', 'work/business.py': 'SYNTHETIC_PRIVATE_BUSINESS',
                       'workos/private.sqlite3': 'SYNTHETIC_PRIVATE_DB', 'web/secrets/key.js': 'SYNTHETIC_KEY'}
            for name, value in {**public, **private}.items():
                path = root/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value, encoding='utf-8')
            entries = []
            for name, filename, host, contents in (
                ('python', 'python.zip', 'www.python.org', {'python.exe': b'synthetic-runtime', 'LICENSE.txt': b'Python license'}),
                ('library', 'library.whl', 'files.pythonhosted.org', {'synthetic/__init__.py': b'', 'synthetic.dist-info/licenses/LICENSE': b'Wheel license'}),
                ('node', 'node-synthetic-win-x64.zip', 'nodejs.org', {'node-synthetic-win-x64/node.exe': b'synthetic-node', 'node-synthetic-win-x64/LICENSE': b'Node license'})):
                archive = cache/filename
                with zipfile.ZipFile(archive, 'w') as zipped:
                    for member, raw in contents.items(): zipped.writestr(member, raw)
                entries.append({'name': name, 'filename': filename, 'url': f'https://{host}/{filename}',
                                'version': 'synthetic', 'sha256': digest(archive.read_bytes())})
            (root/'deployment').mkdir()
            (root/'deployment/runtime-lock.json').write_text(json.dumps({'entries': entries}), encoding='utf-8')
            for name in ('Start-WorkOS.cmd', 'Check-Environment.cmd', 'Optional-Setup.ps1'):
                (root/'deployment'/name).write_text('# Synthetic public startup', encoding='utf-8')
            def git(command):
                return {'ls-files': '\0'.join({**public, **private}).encode(), 'rev-parse': b'synthetic-revision\n', 'status': b''}[command[3]]
            with patch('tools.package_windows.subprocess.check_output', side_effect=git), \
                    patch('urllib.request.urlopen', side_effect=AssertionError('All runtime inputs are cached')):
                result = build(root, Path(folder)/'out', cache, profile='full', with_node=True)
            archive = Path(result['path'])
            self.assertTrue(archive.is_file())
            self.assertTrue(archive.with_suffix('.sha256').read_text().startswith(digest(archive.read_bytes())))
            with zipfile.ZipFile(archive) as zipped:
                prefix = archive.stem + '/'
                manifest = json.loads(zipped.read(prefix+'package-manifest.json'))
                self.assertFalse(manifest['private_data_included']); self.assertFalse(manifest['working_tree_changes'])
                paths = {item['path'] for item in manifest['files']}
                self.assertIn('runtime/python/LICENSE.txt', paths)
                self.assertIn('runtime/python/Lib/site-packages/synthetic.dist-info/licenses/LICENSE', paths)
                self.assertIn('runtime/node/node.exe', paths)
                self.assertIn('runtime/node/LICENSE', paths)
                self.assertIn('启动.cmd', paths)
                self.assertTrue(zipped.read(prefix+'Optional-Setup.ps1').startswith(b'\xef\xbb\xbf'))
                self.assertNotIn('node-input', ''.join(paths))
                self.assertFalse(any('SYNTHETIC_PRIVATE' in zipped.read(name).decode('utf-8', errors='ignore')
                                     for name in zipped.namelist()))
                for item in manifest['files']:
                    raw = zipped.read(prefix+item['path'])
                    self.assertEqual(len(raw), item['bytes']); self.assertEqual(digest(raw), item['sha256'])
                self.assertIn(b'../../app/vendor', zipped.read(prefix+'runtime/python/python313._pth'))
                self.assertNotIn(b'import site', zipped.read(prefix+'runtime/python/python313._pth'))
            def dirty_git(command):
                return b' M workos/server.py\n' if command[3]=='status' else git(command)
            with patch('tools.package_windows.subprocess.check_output', side_effect=dirty_git):
                with self.assertRaisesRegex(ValueError, 'clean Git HEAD'):
                    build(root, Path(folder)/'strict-out', cache, profile='core')
                preview=build(root, Path(folder)/'preview-out', cache, profile='core', allow_dirty=True)
            self.assertEqual(preview['build_status'], 'preview')
            self.assertTrue(preview['working_tree_changes']); self.assertTrue(preview['path'].endswith('-preview.zip'))
            with zipfile.ZipFile(preview['path']) as zipped:
                preview_manifest=json.loads(zipped.read(Path(preview['path']).stem+'/package-manifest.json'))
                self.assertEqual(preview_manifest['build_status'], 'preview')
            head_reads=[]
            def changing_head(command):
                if command[3]=='rev-parse':
                    head_reads.append(True)
                    return b'initial-revision' if len(head_reads)==1 else b'changed-revision'
                return git(command)
            with patch('tools.package_windows.subprocess.check_output', side_effect=changing_head):
                with self.assertRaisesRegex(ValueError, 'HEAD changed'):
                    build(root, Path(folder)/'changed-out', cache, profile='core')
            self.assertFalse(list((Path(folder)/'changed-out').glob('*.zip')))
