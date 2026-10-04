"""Back up existing workspace databases without changing authentication or originals."""
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from contextlib import closing


def backup(data_dir, destination):
    data_dir, destination = Path(data_dir), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    saved = []
    for name in ('personal.sqlite3', 'demo.sqlite3', 'workflow-jobs.sqlite3', 'conversations.sqlite3', 'project-artifacts.sqlite3', 'project-learning.sqlite3'):
        source = data_dir / name
        if not source.is_file():
            continue
        with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(destination / name)) as target:
                original.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('Workspace backup integrity check failed')
        saved.append(name)
    configurations = []
    for name in ('project-artifacts.json', 'custom-models.json'):
        configuration = data_dir / name
        if not configuration.is_file():
            continue
        raw = configuration.read_bytes()
        parsed = json.loads(raw)
        if name == 'custom-models.json':
            if not isinstance(parsed, dict) or not isinstance(parsed.get('models'), list) or any(
                    not isinstance(item, dict) for item in parsed['models']):
                raise ValueError('Custom model definition backup structure is invalid')
            # Definitions are private, but keys must never be copied even if a
            # manually modified/older runtime file contains unexpected fields.
            fields = ('mode', 'model_id', 'base_url', 'name', 'provider_label')
            definitions = [{key: item[key] for key in fields if key in item} for item in parsed['models']]
            if any(not isinstance(value, str) for item in definitions for value in item.values()):
                raise ValueError('Custom model definition backup fields are invalid')
            raw = json.dumps({'schema_version': 1,
                              'models': definitions}, ensure_ascii=False, indent=2).encode('utf-8')
        (destination / name).write_bytes(raw)
        configurations.append(name)
    (destination / 'manifest.json').write_text(json.dumps({'databases': saved, 'configurations': configurations,
        'originals': 'retained-in-place', 'authentication': 'retained-in-place',
        'model_keys': 'not-copied', 'model_status': 'not-copied'}), encoding='utf-8')
    return saved


if __name__ == '__main__':
    root = Path(os.environ['LOCALAPPDATA']) / 'LocalWorkOS'
    target = root / 'release-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    print(json.dumps({'backed_up': backup(root, target)}))
