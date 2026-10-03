"""Read-only deployment probes. Never obtains production passwords or cookies."""
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launch import configure_saved_env
import os


def verify_work_contracts(get, base_url, ui):
    """Read installed contracts only; no model probes, mutation or secret output."""
    _, body = get(base_url + '/api/models')
    catalog = json.loads(body)
    groups = catalog.get('groups', [])
    if not isinstance(groups, list) or not {'dsh', 'deepseek', 'glm', 'kimi', 'hunyuan'}.issubset(
            {group.get('id') for group in groups if isinstance(group, dict)}):
        raise ValueError('The installed unified model catalog is incomplete')
    if catalog.get('default_selection_id') != 'deepseek:deepseek-v4.1-flash':
        raise ValueError('The installed default work model is inconsistent')
    selection_ids = {item.get('selection_id') for group in groups if isinstance(group, dict)
                     for item in group.get('models', []) if isinstance(item, dict)}
    _, body = get(base_url + '/api/models/custom')
    custom = json.loads(body).get('models')
    if not isinstance(custom, list):
        raise ValueError('The installed custom model API is unavailable')
    for entry in custom:
        if (not isinstance(entry, dict) or not isinstance(entry.get('mode'), str)
                or not re.fullmatch(r'custom-[a-f0-9]{16}', entry['mode'])
                or not isinstance(entry.get('model_id'), str) or not entry['model_id']
                or not isinstance(entry.get('has_api_key'), bool)
                or any(key in entry for key in ('api_key', 'authorization', 'password', 'registered_api_keys'))):
            raise ValueError('The installed custom model public contract is invalid')
        if entry['mode'] + ':' + entry['model_id'] not in selection_ids:
            raise ValueError('A registered custom model is absent from the unified catalog')
    _, body = get(base_url + '/api/guidance')
    guidance = json.loads(body)
    if (not isinstance(guidance.get('title'), str) or not guidance['title'].strip()
            or not isinstance(guidance.get('content'), str) or len(guidance['content'].strip()) < 100):
        raise ValueError('The installed usage guide is unavailable')
    for marker in (b'unifiedModelOptions', b'data-model-picker', b'renderClarification',
                   b'needs_input', b'/models/custom', b'/guidance'):
        if marker not in ui:
            raise ValueError('The installed conversational model controls are unavailable')


def verify_anonymous_denial(get, origin):
    for route in ('/api/state', '/api/workflows', '/api/health', '/api/models',
                  '/api/models/custom', '/api/guidance', '/api/conversations'):
        try:
            get(origin + route)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise ValueError('Anonymous API denial returned an unexpected status') from None
        else:
            raise ValueError('Anonymous public workspace access was allowed')


def main():
    env = dict(os.environ)
    configure_saved_env(env)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # Match a normal browser entry: edge browser-integrity rules reject Python's default UA.
    # These probes remain anonymous and never attach production cookies or credentials.
    opener.addheaders = [('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36')]
    def get(url):
        with opener.open(url, timeout=20) as response:
            return response.status, response.read()
    _, body = get('http://127.0.0.1:18866/api/workflows')
    if len(json.loads(body).get('workflows', [])) < 11:
        raise ValueError('Workflow catalogue is unavailable')
    _, shell = get('http://127.0.0.1:18866/')
    _, client = get('http://127.0.0.1:18866/api-client.js')
    if b'/api-client.js' not in shell or b'WorkOSApiClient' not in client or b'csrf_expired' not in client:
        raise ValueError('The installed session recovery client is unavailable')
    _, ui = get('http://127.0.0.1:18866/app.js')
    if b'ai-run-controls' not in shell or b'X-WorkOS-Request-ID' not in ui or b'bindAiComposer' not in ui:
        raise ValueError('The installed keyboard and stop controls are unavailable')
    for route, key in (('/api/operations', 'operations'), ('/api/conversations', 'conversations'), ('/api/artifacts/config', 'roots')):
        _, body = get('http://127.0.0.1:18866' + route)
        if not isinstance(json.loads(body).get(key), list):
            raise ValueError('The installed contextual AI or archive API is unavailable')
    if b'renderConversation' not in ui or b'execution-record' not in ui or b'renderArchiveReceipt' not in ui:
        raise ValueError('The installed contextual AI or archive UI is unavailable')
    verify_work_contracts(get, 'http://127.0.0.1:18866', ui)
    origin = env.get('WORKOS_PUBLIC_ORIGIN', '').rstrip('/')
    _, body = get('http://127.0.0.1:18866/api/sync/status')
    sync = json.loads(body)
    if env.get('WORKOS_SYNC_ROOT') and not sync.get('enabled'):
        raise ValueError('The configured project mirror is not enabled')
    if sync.get('error'):
        raise ValueError('Project mirror reported a synchronization error')
    if origin:
        if env.get('WORKOS_PUBLIC_AUTH_MODE') != 'password' or not origin.startswith('https://'):
            raise ValueError('This SOP verifies the configured password-protected HTTPS deployment')
        _, body = get('http://127.0.0.1:18866/api/public/status')
        public = json.loads(body)
        if public.get('origin') != origin or public.get('auth_mode') != 'password' or not public.get('password_configured'):
            raise ValueError('The running service lacks the configured public origin or login account')
        status, body = get(origin + '/auth/login')
        if status != 200 or b'WorkOS' not in body:
            raise ValueError('Public login page unavailable')
        verify_anonymous_denial(get, origin)
    print(json.dumps({'local_workflows': 'ok', 'session_recovery_client': 'ok', 'ai_keyboard_and_stop_controls': 'ok',
        'context_progress_archives': 'ok', 'conversational_model_guide_contracts': 'ok',
        'public_login_and_anonymous_denial': 'ok' if origin else 'not-configured'}))


if __name__ == '__main__':
    main()
