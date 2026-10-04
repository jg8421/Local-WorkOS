"""Build audited-source Windows x64 portable packages with pinned official inputs."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile
import urllib.request
from urllib.parse import urlsplit
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'launch.py', 'README.md', 'requirements.txt', 'requirements-development.txt'}
DOCS = {'docs/USAGE_GUIDE.md', 'docs/DEPLOYMENT.md', 'docs/PRD.md',
        'docs/AI_HANDOFF.md', 'docs/HARNESS.md', 'docs/PROJECT_EXPERIENCE.md',
        'docs/TESTING.md', 'docs/ARCHITECTURE.md', 'docs/RELEASE_SOP.md'}
EXTRAS = {'tools/deployment_preflight.py', 'docs/DEPLOYMENT.md'}
FORBIDDEN = re.compile(r'(^|/)(?:\.[^/]+|work|outputs?|data|runtime|credentials|secrets|authentication|__pycache__|node_modules|tests)(/|$)', re.I)
WINDOWS_DEVICE = re.compile(r'^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', re.I)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def allowed_source(name):
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if path.is_absolute() or ':' in name or '..' in path.parts or FORBIDDEN.search(name): return False
    if name in TOP | DOCS | EXTRAS: return True
    if name.startswith('workos/'): return path.suffix in ('.py', '.ps1', '.mjs')
    if name.startswith('web/'): return path.suffix in ('.js', '.html', '.css', '.svg')
    if name.startswith('vendor/pypdf/'): return path.suffix == '.py'
    return name == 'vendor/pypdf-LICENSE.txt'


def source_files(root):
    raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'])
    names = set(raw.decode('utf-8').split('\0')) | EXTRAS
    output = []
    for name in sorted(names):
        if not name or not allowed_source(name): continue
        path = root / name
        if not path.is_file(): raise ValueError('Required package source is absent: ' + name)
        if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Package source cannot be a link/reparse point')
        output.append((name, path))
    return output


def fetch(entry, cache):
    url = urlsplit(entry['url'])
    if url.scheme != 'https' or url.hostname not in ('www.python.org', 'files.pythonhosted.org', 'nodejs.org') or url.username or url.password:
        raise ValueError('Runtime download is not from an approved official host')
    if not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']): raise ValueError('Runtime SHA256 is invalid')
    filename = entry['filename']
    if not isinstance(filename,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,160}',filename): raise ValueError('Unsafe runtime filename')
    cache.mkdir(parents=True, exist_ok=True); path = cache / filename
    if path.exists():
        raw = path.read_bytes()
    else:
        with urllib.request.urlopen(entry['url'], timeout=60) as response:
            raw = response.read(100_000_001)
        if len(raw) > 100_000_000: raise ValueError('Runtime input exceeds package budget')
    if digest(raw) != entry['sha256']: raise ValueError('Runtime download SHA256 mismatch: ' + filename)
    if not path.exists(): path.write_bytes(raw)
    return path


def extract(archive, target, *, wheel=False):
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        if sum(item.file_size for item in zipped.infolist()) > 400_000_000:
            raise ValueError('Archive expansion exceeds budget')
        for item in zipped.infolist():
            name = item.filename.replace('\\', '/')
            parts = PurePosixPath(name).parts
            if (not parts or name.startswith('/') or '..' in parts or ':' in name or
                any(part.rstrip(' .')!=part or WINDOWS_DEVICE.match(part) or re.search(r'[<>"|?*]',part) for part in parts) or
                ((item.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError('Unsafe runtime archive member')
            if wheel and parts[0].endswith('.data'):
                if len(parts) < 3 or parts[1] not in ('purelib', 'platlib'):
                    continue
                parts = parts[2:]
            destination = target.joinpath(*parts)
            if item.is_dir(): destination.mkdir(parents=True, exist_ok=True); continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zipped.read(item))


def build(root, output, cache, *, profile='core', with_node=False, allow_dirty=False):
    if profile not in ('core', 'full'): raise ValueError('Unknown package profile')
    lock = json.loads((root/'deployment/runtime-lock.json').read_text(encoding='utf-8'))
    entries = [item for item in lock['entries'] if item['name'] == 'python' or
               (item['name'] == 'node' and with_node) or (profile == 'full' and item['name'] not in ('python', 'node'))]
    version = ast.literal_eval(next(line.split('=', 1)[1].strip() for line in (root/'workos/__init__.py').read_text().splitlines() if line.startswith('__version__')))
    revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    dirty = bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']))
    if dirty and not allow_dirty: raise ValueError('Release package requires clean Git HEAD; use --allow-dirty only for a labelled preview')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workos-package-') as temporary:
        folder = Path(temporary); app = folder/'app'; app.mkdir()
        for name, path in source_files(root):
            destination = app/name; destination.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(path, destination)
        python = folder/'runtime/python'
        for entry in entries:
            archive = fetch(entry, cache)
            if entry['name'] == 'python': extract(archive, python)
            elif entry['name'] == 'node':
                node_unpacked = folder/'node-input'
                extract(archive, node_unpacked)
                shutil.move(str(node_unpacked/Path(entry['filename']).stem), str(folder/'runtime/node'))
                node_unpacked.rmdir()
            else: extract(archive, python/'Lib/site-packages', wheel=True)
        # Do not import site: embeddable Windows Python can otherwise load a
        # user's site-packages/.pth even though its isolated flag is set.
        (python/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../../app\n../../app/vendor\n', encoding='utf-8')
        for name in ('Start-WorkOS.cmd', 'Check-Environment.cmd'):
            shutil.copyfile(root/'deployment'/name, folder/name)
        # Windows PowerShell 5.1 needs a BOM for Chinese user-facing strings.
        (folder/'Optional-Setup.ps1').write_text((root/'deployment/Optional-Setup.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
        shutil.copyfile(root/'deployment/Start-WorkOS.cmd', folder/'启动.cmd')
        shutil.copyfile(root/'deployment/Check-Environment.cmd', folder/'环境检查.cmd')
        (folder/'Optional-Setup.cmd').write_text('@echo off\ncd /d "%~dp0"\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Optional-Setup.ps1"\npause\n', encoding='ascii')
        shutil.copyfile(root/'docs/DEPLOYMENT.md', folder/'README-DEPLOYMENT.md')
        shutil.copyfile(root/'deployment/runtime-lock.json', folder/'runtime-lock.json')
        current_revision = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
        if current_revision != revision: raise ValueError('Git HEAD changed during build; build again from frozen source')
        dirty = dirty or bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain']))
        if dirty and not allow_dirty: raise ValueError('Source changed during build; release package requires clean Git HEAD')
        manifest = {'schema_version': 1, 'version': version, 'profile': profile, 'platform': 'windows-x64',
                    'source_revision': revision, 'working_tree_changes': dirty,
                    'build_status': 'preview' if dirty else 'release', 'dependencies': entries,
                    'private_data_included': False, 'files': []}
        for path in sorted(folder.rglob('*')):
            if path.is_file(): manifest['files'].append({'path': path.relative_to(folder).as_posix(),
                                                         'bytes': path.stat().st_size, 'sha256': digest(path.read_bytes())})
        (folder/'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        name = f'Local-WorkOS-{version}-windows-x64-{profile}' + ('-node' if with_node else '') + ('-preview' if dirty else '')
        destination = output/(name + '.zip')
        if destination.exists(): raise FileExistsError('Package already exists; use a fresh output folder')
        with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for path in sorted(folder.rglob('*')):
                if path.is_file(): zipped.write(path, name + '/' + path.relative_to(folder).as_posix())
        (output/(name+'.sha256')).write_text(digest(destination.read_bytes())+'  '+destination.name+'\n', encoding='ascii')
        return {'path': str(destination), 'bytes': destination.stat().st_size, 'files': len(manifest['files']),
                'uncompressed_bytes': sum(item['bytes'] for item in manifest['files']), 'version': version,
                'source_revision': revision, 'working_tree_changes': dirty, 'build_status': manifest['build_status']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cache', type=Path, default=ROOT/'work/package-cache')
    parser.add_argument('--profile', choices=('core', 'full'), default='core'); parser.add_argument('--with-node', action='store_true')
    parser.add_argument('--allow-dirty', action='store_true', help='Build a clearly labelled preview from uncommitted public source')
    args = parser.parse_args(); print(json.dumps(build(ROOT, args.output, args.cache, profile=args.profile, with_node=args.with_node, allow_dirty=args.allow_dirty)))
