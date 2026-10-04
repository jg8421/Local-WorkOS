"""Local, attributable project learning; never a new research source or tool grant.

Only user-authored working preferences are learned. Model answers, source text and
unfinished conditions are recorded as execution history, never promoted to facts.
This database belongs to the private runtime directory, not a source/cloud mirror.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid

from .conversations import PURPOSES, STATUSES
from .ai_progress import LABELS, safe_detail
from .store import now

KINDS = {'preference', 'lesson', 'fact', 'calculation_basis'}
ENTRY_STATUSES = {'pending', 'active', 'disabled'}
EXECUTION_STATUSES = {'pending', 'queued', 'running', 'completed', 'skipped', 'cancelled', 'failed', 'interrupted', 'needs_input'}
SCOPES = PURPOSES | {'general'}
_PERSISTENT = re.compile(r'以后|今后|后续都|每次|始终|一律|默认|记住|(?:本|该|这个)项目都|所有任务.*都|整个项目.*都|always|from now on|for future|every time|all tasks', re.I)
_GENERAL = re.compile(r'所有任务|所有用途|任何任务|整个项目|全项目|all tasks|across tasks|entire project', re.I)
_CORRECTION = re.compile(r'不要|别再|改成|改为|调整|重写|简洁一点|简洁些|上次|这次|以后|今后|每次|记住|instead|shorter|revise|always|from now on', re.I)
_RULES = (
    ('brevity', '表达长度', r'简洁|精简|短一点|shorter|concise|brief'),
    ('conclusion_first', '先呈现结论', r'结论.*(先|开头)|先.*(结论|判断)|结论\s*[-—→]\s*证据\s*[-—→]\s*影响|conclusions? first|lead with'),
    ('attribution', '来源与观点归属', r'引用|来源|source|管理层|专家|management|expert'),
    ('actual_forecast', '实际与预测口径', r'实际.*预测|预测.*实际|actual.*forecast|forecast.*actual'),
    ('units_periods', '币种、单位与期间', r'币种|单位|期间|currency|units?|period'),
    ('current_draft', '以当前编辑稿为准', r'当前.*(编辑|稿)|手动.*(编辑|修改)|current.*(edit|draft)'),
    ('preserve_versions', '保留资料与产物版本', r'保留.*(版本|原稿)|不要.*覆盖|preserve.*version|do not overwrite'),
    ('table_format', '表格呈现', r'表格|table'),
    ('page_count', '篇幅与页数', r'页数|[一二三四五六七八九十0-9]+页|page count|\d+ pages?'),
    ('language', '工作语言', r'英文|中文|双语|english|chinese|bilingual'),
)
_SENSITIVE = re.compile(r'(?:api[_ -]?key|password|access[_ -]?token|authorization)\s*[:=]|\bbearer\s+\S+|\bsk-[A-Za-z0-9_-]{16,}', re.I)
_FACTUAL_NUMBER = re.compile(r'(?:营收|收入|净利润|利润|市占率|估值|增长率|revenue|net income|market share|valuation)\s*(?:为|是|约|达到|[:=])?\s*[0-9]', re.I)
_SOURCE_CONTEXT = re.compile(r'请(?:总结|翻译|解释|分析|摘录)|(?:材料|资料|原文|原话|引文|摘录|引用内容|文中|协议|邮件|这段话|这段文字).{0,30}(?:写|说|记载|内容|如下|[:：])|(?:summarize|translate|quoted text|source text|document says|following excerpt|following text)', re.I)
_DIRECT_CONTEXT = re.compile(r'我(?:要求|希望)|以后|今后|每次|默认|记住|from now on|every time', re.I)
_QUOTES = re.compile(r'“[^”]*”|‘[^’]*’|"[^"\n]*"|(?<!\w)\x27[^\x27\n]*\x27|`[^`\n]*`')


def _user_directives(text):
    """Ignore quoted source instructions, retain quotes used as rule arguments.

    A quoted format such as 以后都用‘结论-证据-影响’结构 is an actual
    user directive. A source quotation requested for translation is data.
    """
    text = re.sub(r'(?ms)^[ \t]*(?:```|~~~).*?(?:^[ \t]*(?:```|~~~)[^\n]*$|\Z)', '', text)
    text = '\n'.join(line for line in text.splitlines() if not re.match(r'^\s*>', line))
    parts, end = [], 0
    for match in _QUOTES.finditer(text):
        parts.append(text[end:match.start()])
        prefix = ''.join(parts)[-160:]
        prefix = re.split(r'[\n。；;!?！？]', prefix)[-1]
        sources = list(_SOURCE_CONTEXT.finditer(prefix))
        direct = list(_DIRECT_CONTEXT.finditer(prefix))
        source_quote = sources and (not direct or sources[-1].start() > direct[-1].start())
        whole_quotation = not prefix.strip() and not text[match.end():].strip()
        if not source_quote and not whole_quotation:
            parts.append(match.group())
        end = match.end()
    parts.append(text[end:])
    return ''.join(parts)


def _text(value, label, maximum=4000, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise ValueError(label + '格式无效或超过限制')
    if _SENSITIVE.search(value):
        raise ValueError('凭证不能写入项目经验')
    return value


def _ids(values):
    if not isinstance(values, (list, tuple)) or len(values) > 80:
        raise ValueError('项目经验资料范围无效')
    return sorted(set(_text(item, '资料编号', 100) for item in values))


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _document_hash(document):
    # Recompute from authoritative content. Client-provided hash and timestamps
    # can be stale or unchanged after an edit.
    return _hash({key: document.get(key) for key in ('id', 'project_id', 'kind', 'title', 'content', 'chunks')})


class ProjectExperience:
    def __init__(self, path, stores, conversations):
        self.stores, self.conversations = stores, conversations
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=20)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS experience_settings (workspace TEXT NOT NULL, project_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(workspace,project_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS experience_events (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, project_id TEXT NOT NULL, turn_id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(workspace,turn_id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS experience_entries (id TEXT PRIMARY KEY, workspace TEXT NOT NULL, project_id TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS experience_suppressed (workspace TEXT NOT NULL, project_id TEXT NOT NULL, origin_key TEXT NOT NULL, PRIMARY KEY(workspace,project_id,origin_key))')
        self.db.commit()

    def _scope(self, workspace, project_id, purpose=None, allow_missing=False):
        if not isinstance(workspace, str) or workspace not in self.stores:
            raise ValueError('工作区选择不正确')
        _text(project_id, '项目编号', 100)
        try:
            self.stores[workspace].get('projects', project_id)
        except KeyError:
            if not allow_missing:
                raise
        if purpose is not None and (not isinstance(purpose, str) or purpose not in SCOPES):
            raise ValueError('项目经验用途无效')

    def _settings(self, workspace, project_id):
        row = self.db.execute('SELECT payload FROM experience_settings WHERE workspace=? AND project_id=?', (workspace, project_id)).fetchone()
        return json.loads(row[0]) if row else {'enabled': True}

    def update_settings(self, workspace, project_id, enabled):
        self._scope(workspace, project_id)
        if type(enabled) is not bool:
            raise ValueError('项目经验开关必须为布尔值')
        settings = {'enabled': enabled}
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO experience_settings VALUES (?,?,?)', (workspace, project_id, json.dumps(settings)))
        return settings

    def _get(self, workspace, project_id, entry_id):
        _text(entry_id, '经验编号', 100)
        row = self.db.execute('SELECT payload FROM experience_entries WHERE workspace=? AND project_id=? AND id=?', (workspace, project_id, entry_id)).fetchone()
        if not row:
            raise KeyError('项目经验不存在')
        return json.loads(row[0])

    def _write(self, entry):
        self.db.execute('INSERT OR REPLACE INTO experience_entries VALUES (?,?,?,?)',
                        (entry['id'], entry['workspace'], entry['project_id'], json.dumps(entry, ensure_ascii=False)))

    def _evidence(self, workspace, project_id, value):
        if not isinstance(value, list) or len(value) > 20:
            raise ValueError('事实经验最多引用20份资料')
        rows = []
        for item in value:
            if not isinstance(item, dict) or set(item) != {'document_id', 'quote'}:
                raise ValueError('事实经验须提供资料编号与原文摘录')
            item_id = _text(item['document_id'], '资料编号', 100)
            quote = _text(item['quote'], '证据原文', 2000)
            document = self.stores[workspace].get('documents', item_id)
            if document.get('kind') == 'memory' or document.get('project_id') != project_id:
                raise ValueError('事实证据必须为同项目研究资料')
            text = document.get('content', '')
            chunks = document.get('chunks', [])
            if quote not in text and not any(isinstance(chunk, dict) and isinstance(chunk.get('text'), str) and quote in chunk['text'] for chunk in chunks):
                raise ValueError('证据摘录不在当前资料原文中')
            rows.append({**item, 'fingerprint': _document_hash(document)})
        if len({row['document_id'] for row in rows}) != len(rows):
            raise ValueError('事实证据资料不能重复')
        return rows

    def _source_state(self, entry, selected=None):
        if entry['kind'] not in {'fact', 'calculation_basis'}:
            return 'not_required'
        if not entry['evidence']:
            return 'user_basis' if entry['kind'] == 'calculation_basis' and entry['purpose'] == 'valuation' else 'no_evidence'
        for row in entry['evidence']:
            try:
                document = self.stores[entry['workspace']].get('documents', row['document_id'])
            except KeyError:
                return 'missing'
            if document.get('kind') == 'memory' or document.get('project_id') != entry['project_id']:
                return 'scope_changed'
            if _document_hash(document) != row['fingerprint']:
                return 'changed'
        if selected is not None and not {row['document_id'] for row in entry['evidence']}.issubset(selected):
            return 'not_selected'
        return 'current'

    def _public(self, entry):
        value = copy.deepcopy(entry)
        value['source_state'] = self._source_state(entry)
        # Fingerprints are an integrity detail; user-facing evidence is an exact
        # quoted source, not an opaque hash or a model assertion.
        value['evidence'] = [{key: row[key] for key in ('document_id', 'quote')} for row in entry['evidence']]
        return value

    def _activate(self, entry):
        if entry['kind'] in {'fact', 'calculation_basis'} and self._source_state(entry) not in {'current', 'user_basis'}:
            raise ValueError('事实或测算口径需有效的同项目原文证据；资料变化后请重新核对')
        # A newer explicit user preference replaces only the same-purpose/key
        # preference. Pending suggestions never displace an active rule.
        if entry['kind'] == 'preference' and entry['rule_key']:
            for row in self.db.execute('SELECT payload FROM experience_entries WHERE workspace=? AND project_id=?',
                                       (entry['workspace'], entry['project_id'])).fetchall():
                previous = json.loads(row[0])
                if previous['id'] != entry['id'] and previous['kind'] == 'preference' and previous['status'] == 'active' and previous['purpose'] == entry['purpose'] and previous['rule_key'] == entry['rule_key']:
                    previous['status'] = 'disabled'
                    previous['superseded_by'] = entry['id']
                    previous['updated_at'] = now()
                    self._write(previous)
        entry['status'] = 'active'

    def create(self, workspace, project_id, body):
        self._scope(workspace, project_id)
        allowed = {'kind', 'purpose', 'title', 'content', 'rule_key', 'evidence', 'confirm'}
        if not isinstance(body, dict) or set(body) - allowed:
            raise ValueError('项目经验字段无效')
        kind, purpose = body.get('kind', 'preference'), body.get('purpose', 'ask')
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('项目经验类型无效')
        self._scope(workspace, project_id, purpose)
        if kind in {'fact', 'calculation_basis'} and purpose == 'general':
            raise ValueError('事实与测算口径必须限定具体用途')
        confirm = body.get('confirm', False)
        if type(confirm) is not bool:
            raise ValueError('确认标志必须为布尔值')
        evidence = self._evidence(workspace, project_id, body.get('evidence', []))
        if kind == 'fact' and not evidence:
            raise ValueError('事实必须关联原文证据；不能把模型草稿当事实')
        if kind == 'calculation_basis' and not evidence and purpose != 'valuation':
            raise ValueError('无资料的用户测算口径仅适用于估值用途')
        timestamp = now()
        entry = {'id': uuid.uuid4().hex, 'workspace': workspace, 'project_id': project_id,
                 'kind': kind, 'purpose': purpose, 'title': _text(body.get('title', '项目工作规则'), '经验标题', 120),
                 'content': _text(body.get('content'), '经验内容'), 'rule_key': _text(body.get('rule_key', ''), '规则编号', 100, True),
                 'status': 'pending', 'evidence': evidence, 'provenance': {'origin': 'user', 'conversation_id': '', 'turn_id': '', 'source_ids': [row['document_id'] for row in evidence]},
                 'origin_key': '', 'superseded_by': '', 'revisions': [], 'created_at': timestamp, 'updated_at': timestamp}
        with self.stores[workspace].lock, self.lock, self.db:
            if confirm:
                self._activate(entry)
            self._write(entry)
        return self._public(entry)

    def edit(self, workspace, project_id, entry_id, body):
        self._scope(workspace, project_id)
        if not isinstance(body, dict) or not body or set(body) - {'title', 'content', 'evidence', 'status'}:
            raise ValueError('项目经验修改字段无效')
        with self.stores[workspace].lock, self.lock, self.db:
            entry = self._get(workspace, project_id, entry_id)
            original = copy.deepcopy(entry)
            for key, maximum in (('title', 120), ('content', 4000)):
                if key in body:
                    entry[key] = _text(body[key], '经验' + key, maximum)
            if 'evidence' in body:
                entry['evidence'] = self._evidence(workspace, project_id, body['evidence'])
                entry['provenance']['source_ids'] = [row['document_id'] for row in entry['evidence']]
            status = body.get('status', entry['status'])
            if not isinstance(status, str) or status not in ENTRY_STATUSES:
                raise ValueError('项目经验状态无效')
            # Changed fact wording is a new claim, not automatically confirmed
            # by an old quote. The user must explicitly activate the edited claim.
            if entry['kind'] in {'fact', 'calculation_basis'} and 'status' not in body and ('content' in body or 'evidence' in body):
                status = 'pending'
            if status == 'active':
                self._activate(entry)
            else:
                entry['status'] = status
            entry['revisions'] = (entry['revisions'] + [{'title': original['title'], 'content': original['content'], 'status': original['status'], 'updated_at': original['updated_at']}])[-20:]
            entry['updated_at'] = now()
            self._write(entry)
            return self._public(entry)

    def set_status(self, workspace, project_id, entry_id, status):
        return self.edit(workspace, project_id, entry_id, {'status': status})

    def delete(self, workspace, project_id, entry_id):
        self._scope(workspace, project_id)
        with self.lock, self.db:
            entry = self._get(workspace, project_id, entry_id)
            if entry['origin_key']:
                self.db.execute('INSERT OR IGNORE INTO experience_suppressed VALUES (?,?,?)', (workspace, project_id, entry['origin_key']))
            self.db.execute('DELETE FROM experience_entries WHERE workspace=? AND project_id=? AND id=?', (workspace, project_id, entry_id))
        return {'deleted': entry_id}

    def observe_activity(self, workspace, activity):
        """Archive a committed Store receipt, without deriving rules or facts.

        Titles of legacy records do not reveal their collection/action. Keep the
        known project scope, label unknown metadata honestly, never guess it.
        Deleted-project history remains backup-able but cannot enter context.
        """
        if not isinstance(workspace, str) or workspace not in self.stores:
            raise ValueError('工作区选择不正确')
        if not isinstance(activity, dict):
            raise ValueError('执行记录格式无效')
        activity_id = _text(activity.get('id'), '活动编号', 100)
        actual = self.stores[workspace].get('activity', activity_id)
        if actual != activity:
            raise ValueError('只能归档实际已提交的执行记录')
        project_id = activity.get('project_id', '')
        if not project_id:
            return {'recorded': False, 'reason': 'no_project'}
        self._scope(workspace, project_id, 'actions', allow_missing=True)
        collection = activity.get('collection', '')
        action = activity.get('action', '')
        record_id = activity.get('record_id', '')
        if not isinstance(collection, str) or collection not in {'', 'projects', 'tasks', 'documents', 'meetings', 'notes', 'deliverables'} or not isinstance(action, str) or action not in {'', 'create', 'update', 'delete'}:
            raise ValueError('执行记录类型无效')
        _text(record_id, '记录编号', 100, True)
        request = safe_detail(activity.get('title', '记录已提交'))
        turn_id = 'activity:' + activity_id
        steps = ([{'action': action, 'status': 'completed', 'record_id': record_id, 'collection': collection}]
                 if collection and action and record_id else [])
        event = {'id': uuid.uuid4().hex, 'workspace': workspace, 'project_id': project_id, 'purpose': 'actions',
                 'origin': 'record_change', 'activity_id': activity_id, 'collection': collection, 'record_id': record_id, 'action': action,
                 'conversation_id': '', 'turn_id': turn_id, 'status': 'completed', 'request': request, 'source_ids': [],
                 'steps': steps, 'execution_steps': [{'stage': 'save', 'detail': request, 'status': 'completed'}],
                 'completed_actions': len(steps), 'current_artifact': {}, 'created_at': activity['created_at']}
        with self.lock, self.db:
            existing = self.db.execute('SELECT payload FROM experience_events WHERE workspace=? AND turn_id=?', (workspace, turn_id)).fetchone()
            if existing:
                return {'recorded': True, 'event': json.loads(existing[0]), 'entries': [], 'replayed': True}
            self.db.execute('INSERT INTO experience_events VALUES (?,?,?,?,?)',
                            (event['id'], workspace, project_id, turn_id, json.dumps(event, ensure_ascii=False)))
        return {'recorded': True, 'event': event, 'entries': [], 'replayed': False}

    def observe_turn(self, workspace, conversation_id, turn_id):
        # Read the actual persisted row rather than accepting caller-supplied
        # claims that a tool or generation completed successfully.
        conversation = self.conversations.get(workspace, conversation_id, max_turns=100)
        project_id, purpose = conversation['project_id'], conversation['purpose']
        if not project_id:
            return {'recorded': False, 'reason': 'no_project', 'entries': []}
        self._scope(workspace, project_id, purpose)
        turn = next((item for item in conversation['turns'] if item['id'] == turn_id), None)
        if turn is None:
            raise KeyError('待记录执行轮次不存在或不在近期范围')
        with self.lock, self.db:
            existing = self.db.execute('SELECT payload FROM experience_events WHERE workspace=? AND turn_id=?', (workspace, turn_id)).fetchone()
            if existing:
                return {'recorded': True, 'event': json.loads(existing[0]), 'entries': [], 'replayed': True}
            steps = []
            snapshot = turn.get('output_snapshot', {})
            for step in snapshot.get('steps', []) if isinstance(snapshot.get('steps', []), list) else []:
                if not isinstance(step, dict):
                    continue
                result = step.get('result', {})
                result = result if isinstance(result, dict) else {}
                steps.append({'action': str(step.get('action', ''))[:100], 'status': 'completed',
                              'record_id': str(result.get('id', ''))[:100], 'collection': str(result.get('collection', ''))[:80]})
                if len(steps) == 100:
                    break
            execution_steps = []
            rows = snapshot.get('execution_steps', [])
            if isinstance(rows, list):
                for row in rows[-50:]:
                    if not isinstance(row, dict) or not isinstance(row.get('stage'), str) or row['stage'] not in LABELS or not isinstance(row.get('status'), str) or row['status'] not in EXECUTION_STATUSES or not isinstance(row.get('detail', ''), str):
                        continue
                    # Only whitelisted public milestones. Extra fields such as
                    # thoughts, prompts, outputs and source bodies are ignored.
                    execution_steps.append({'stage': row['stage'], 'status': row['status'], 'detail': safe_detail(row.get('detail', ''))})
            # The local ledger holds the user's request, not the generated answer.
            request = turn['user_message'][:2000]
            if _SENSITIVE.search(request):
                request = '含凭证的要求已省略；请回到原对话查看。'
            event = {'id': uuid.uuid4().hex, 'workspace': workspace, 'project_id': project_id, 'purpose': purpose,
                     'origin': 'conversation', 'activity_id': '', 'collection': '', 'record_id': '', 'action': '',
                     'conversation_id': conversation_id, 'turn_id': turn_id, 'status': turn['status'],
                     'request': request, 'source_ids': turn['source_ids'], 'steps': steps, 'execution_steps': execution_steps, 'completed_actions': len(steps),
                     'current_artifact': turn['current_artifact'] if turn['status'] == 'completed' else {}, 'created_at': turn['created_at']}
            self.db.execute('INSERT INTO experience_events VALUES (?,?,?,?,?)', (event['id'], workspace, project_id, turn_id, json.dumps(event, ensure_ascii=False)))
            learned = []
            if turn['status'] == 'completed' and self._settings(workspace, project_id)['enabled']:
                for sentence in re.split(r'[\n。；;!?！？]', _user_directives(turn['user_message'][:8000])):
                    sentence = sentence.strip()
                    if not sentence or len(sentence) > 1000 or _SENSITIVE.search(sentence) or _FACTUAL_NUMBER.search(sentence) or not (_CORRECTION.search(sentence) or _PERSISTENT.search(sentence)):
                        continue
                    persistent = bool(_PERSISTENT.search(sentence))
                    for key, label, pattern in _RULES:
                        if not re.search(pattern, sentence, re.I):
                            continue
                        origin = _hash([workspace, turn_id, key, sentence])
                        if self.db.execute('SELECT 1 FROM experience_suppressed WHERE workspace=? AND project_id=? AND origin_key=?', (workspace, project_id, origin)).fetchone():
                            continue
                        entry = {'id': uuid.uuid4().hex, 'workspace': workspace, 'project_id': project_id,
                                 'purpose': 'general' if persistent and _GENERAL.search(sentence) else purpose,
                                 'kind': 'preference', 'title': label, 'content': sentence, 'rule_key': key, 'status': 'pending',
                                 'evidence': [], 'provenance': {'origin': 'explicit_user_preference' if persistent else 'user_correction_candidate', 'conversation_id': conversation_id, 'turn_id': turn_id, 'source_ids': turn['source_ids']},
                                 'origin_key': origin, 'superseded_by': '', 'revisions': [], 'created_at': now(), 'updated_at': now()}
                        if persistent:
                            self._activate(entry)
                        self._write(entry)
                        learned.append(self._public(entry))
            return {'recorded': True, 'event': event, 'entries': learned, 'replayed': False}

    def status(self, workspace, project_id, purpose=None, limit=50):
        self._scope(workspace, project_id, purpose)
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('项目经验显示上限无效')
        with self.stores[workspace].lock, self.lock:
            entries = [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM experience_entries WHERE workspace=? AND project_id=? ORDER BY rowid DESC', (workspace, project_id))]
            events = [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM experience_events WHERE workspace=? AND project_id=? ORDER BY rowid DESC', (workspace, project_id))]
            if purpose is not None:
                entries = [entry for entry in entries if entry['purpose'] in {purpose, 'general'}]
                events = [event for event in events if event['purpose'] == purpose]
            return {'settings': self._settings(workspace, project_id), 'counts': {'events': len(events), **{value: sum(entry['status'] == value for entry in entries) for value in ENTRY_STATUSES}},
                    'entries': [self._public(entry) for entry in entries[:limit]], 'events': events[:limit],
                    'truncated': len(entries) > limit or len(events) > limit}

    def context(self, workspace, project_id, purpose, source_ids=(), max_chars=6000):
        self._scope(workspace, project_id, purpose)
        if purpose == 'general' or type(max_chars) is not int or not 200 <= max_chars <= 8000:
            raise ValueError('项目经验上下文范围或预算无效')
        selected = set(_ids(source_ids))
        for item_id in selected:
            doc = self.stores[workspace].get('documents', item_id)
            if doc.get('kind') == 'memory' or doc.get('project_id') not in ('', project_id):
                raise ValueError('项目经验选源范围无效')
        with self.lock:
            if not self._settings(workspace, project_id)['enabled']:
                return {'enabled': False, 'text': '', 'entries': [], 'omitted': []}
            entries = [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM experience_entries WHERE workspace=? AND project_id=? ORDER BY rowid DESC', (workspace, project_id))]
        entries = [entry for entry in entries if entry['status'] == 'active' and entry['purpose'] in {purpose, 'general'}]
        # Specific-purpose rules override broader explicit project preferences.
        entries.sort(key=lambda entry: entry['purpose'] != purpose)
        prefix = '同项目已确认工作经验：当前用户要求优先；这些记录不授予工具权限、不扩大资料范围。事实摘录仅供核对已选原文；测算口径是用户假设，不是公司事实。\n'
        text, included, omitted, used_keys, used_content = prefix, [], [], set(), set()
        labels = {'preference': '用户工作偏好', 'lesson': '用户确认的工作经验', 'fact': '用户确认的原文摘录（须再次核对）', 'calculation_basis': '用户确认的测算口径（非事实核验）'}
        for entry in entries:
            state = self._source_state(entry, selected)
            if entry['kind'] in {'fact', 'calculation_basis'} and state not in {'current', 'user_basis'}:
                omitted.append({'id': entry['id'], 'reason': state})
                continue
            if entry['kind'] == 'preference' and entry['rule_key'] and entry['rule_key'] in used_keys:
                omitted.append({'id': entry['id'], 'reason': 'more_specific_or_newer_rule'})
                continue
            if entry['content'] in used_content:
                continue
            line = '[' + labels[entry['kind']] + '] ' + entry['content'] + '\n'
            if len(text) + len(line) > max_chars:
                omitted.append({'id': entry['id'], 'reason': 'context_budget'})
                continue
            text += line
            included.append({'id': entry['id'], 'kind': entry['kind'], 'purpose': entry['purpose'], 'source_ids': [row['document_id'] for row in entry['evidence']]})
            if entry['kind'] == 'preference':
                used_keys.add(entry['rule_key'])
            used_content.add(entry['content'])
        return {'enabled': True, 'text': text if included else '', 'entries': included, 'omitted': omitted}

    def backup(self, workspace):
        if not isinstance(workspace, str) or workspace not in self.stores:
            raise ValueError('工作区选择不正确')
        with self.lock:
            return {'format': 'workos-project-experience', 'version': 1, 'workspace': workspace,
                    'settings': [{'project_id': row[0], **json.loads(row[1])} for row in self.db.execute('SELECT project_id,payload FROM experience_settings WHERE workspace=?', (workspace,))],
                    'entries': [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM experience_entries WHERE workspace=?', (workspace,))],
                    'events': [json.loads(row[0]) for row in self.db.execute('SELECT payload FROM experience_events WHERE workspace=?', (workspace,))],
                    'suppressed': [{'project_id': row[0], 'origin_key': row[1]} for row in self.db.execute('SELECT project_id,origin_key FROM experience_suppressed WHERE workspace=?', (workspace,))]}

    def restore(self, workspace, backup):
        # Validate the complete payload before starting a write transaction.
        if not isinstance(backup, dict) or set(backup) != {'format', 'version', 'workspace', 'settings', 'entries', 'events', 'suppressed'} or backup.get('format') != 'workos-project-experience' or type(backup.get('version')) is not int or backup['version'] != 1 or backup.get('workspace') != workspace:
            raise ValueError('项目经验备份格式或工作区不匹配')
        if not isinstance(workspace, str) or workspace not in self.stores:
            raise ValueError('工作区选择不正确')
        for key in ('settings', 'entries', 'events', 'suppressed'):
            if not isinstance(backup[key], list) or len(backup[key]) > 50000:
                raise ValueError('项目经验备份过大或字段无效')
        entries, events, settings, suppressed = copy.deepcopy(backup['entries']), copy.deepcopy(backup['events']), copy.deepcopy(backup['settings']), copy.deepcopy(backup['suppressed'])
        seen = set()
        for item in settings:
            if not isinstance(item, dict) or set(item) != {'project_id', 'enabled'} or type(item['enabled']) is not bool:
                raise ValueError('项目经验开关备份无效')
            self._scope(workspace, item['project_id'], allow_missing=True)
            if item['project_id'] in seen:
                raise ValueError('项目经验项目重复')
            seen.add(item['project_id'])
        seen = set()
        entry_keys = {'id', 'workspace', 'project_id', 'kind', 'purpose', 'title', 'content', 'rule_key', 'status', 'evidence', 'provenance', 'origin_key', 'superseded_by', 'revisions', 'created_at', 'updated_at'}
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != entry_keys or entry['workspace'] != workspace or not isinstance(entry['kind'], str) or entry['kind'] not in KINDS or not isinstance(entry['status'], str) or entry['status'] not in ENTRY_STATUSES:
                raise ValueError('项目经验条目备份无效')
            self._scope(workspace, entry['project_id'], entry['purpose'], allow_missing=True)
            for key, maximum, empty in (('id', 100, False), ('title', 120, False), ('content', 4000, False), ('rule_key', 100, True), ('origin_key', 64, True), ('superseded_by', 100, True), ('created_at', 80, False), ('updated_at', 80, False)):
                _text(entry[key], '经验备份' + key, maximum, empty)
            if entry['id'] in seen:
                raise ValueError('项目经验编号重复')
            seen.add(entry['id'])
            if not isinstance(entry['provenance'], dict) or set(entry['provenance']) != {'origin', 'conversation_id', 'turn_id', 'source_ids'}:
                raise ValueError('项目经验出处备份无效')
            for key in ('origin', 'conversation_id', 'turn_id'):
                _text(entry['provenance'][key], '经验出处', 100, True)
            _ids(entry['provenance']['source_ids'])
            if not isinstance(entry['evidence'], list) or len(entry['evidence']) > 20:
                raise ValueError('项目经验证据备份无效')
            for row in entry['evidence']:
                if not isinstance(row, dict) or set(row) != {'document_id', 'quote', 'fingerprint'}:
                    raise ValueError('项目经验证据备份字段无效')
                _text(row['document_id'], '证据编号', 100)
                _text(row['quote'], '证据摘录', 2000)
                if not isinstance(row['fingerprint'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['fingerprint']):
                    raise ValueError('项目经验证据指纹无效')
            if len({row['document_id'] for row in entry['evidence']}) != len(entry['evidence']):
                raise ValueError('项目经验证据资料重复')
            if entry['kind'] == 'fact' and (entry['purpose'] == 'general' or not entry['evidence']):
                raise ValueError('事实与测算口径不能脱离证据或跨用途')
            if entry['kind'] == 'calculation_basis' and (entry['purpose'] == 'general' or (not entry['evidence'] and entry['purpose'] != 'valuation')):
                raise ValueError('无资料测算口径只能属于估值用途')
            if not isinstance(entry['revisions'], list) or len(entry['revisions']) > 20:
                raise ValueError('项目经验修改记录过大')
            for revision in entry['revisions']:
                if not isinstance(revision, dict) or set(revision) != {'title', 'content', 'status', 'updated_at'} or not isinstance(revision['status'], str) or revision['status'] not in ENTRY_STATUSES:
                    raise ValueError('项目经验修改记录无效')
                for key, maximum in (('title', 120), ('content', 4000), ('updated_at', 80)):
                    _text(revision[key], '经验修改记录', maximum)
        event_ids, turn_ids = set(), set()
        event_keys = {'id', 'workspace', 'project_id', 'purpose', 'origin', 'activity_id', 'collection', 'record_id', 'action', 'conversation_id', 'turn_id', 'status', 'request', 'source_ids', 'steps', 'execution_steps', 'completed_actions', 'current_artifact', 'created_at'}
        defaults = {'origin': 'conversation', 'activity_id': '', 'collection': '', 'record_id': '', 'action': '', 'execution_steps': []}
        for event in events:
            if isinstance(event, dict) and not set(event) - event_keys:
                for key, value in defaults.items():
                    event.setdefault(key, copy.deepcopy(value))
            if not isinstance(event, dict) or set(event) != event_keys or event['workspace'] != workspace or not isinstance(event['status'], str) or event['status'] not in STATUSES:
                raise ValueError('执行记录备份无效')
            self._scope(workspace, event['project_id'], event['purpose'], allow_missing=True)
            if not isinstance(event['origin'], str) or event['origin'] not in {'conversation', 'record_change'}:
                raise ValueError('执行记录来源无效')
            for key, maximum, empty in (('id', 100, False), ('conversation_id', 100, event['origin'] == 'record_change'), ('turn_id', 100, False), ('request', 2000, True), ('created_at', 80, False), ('activity_id', 100, True), ('collection', 80, True), ('record_id', 100, True), ('action', 40, True)):
                _text(event[key], '执行记录', maximum, empty)
            if event['origin'] == 'record_change' and (not event['activity_id'] or event['turn_id'] != 'activity:' + event['activity_id'] or event['conversation_id'] or event['purpose'] != 'actions' or event['status'] != 'completed' or event['source_ids'] or event['collection'] not in {'', 'projects', 'tasks', 'documents', 'meetings', 'notes', 'deliverables'} or event['action'] not in {'', 'create', 'update', 'delete'}):
                raise ValueError('记录提交审计范围无效')
            if event['origin'] == 'conversation' and any(event[key] for key in ('activity_id', 'collection', 'record_id', 'action')):
                raise ValueError('对话审计不能冒充记录提交')
            if event['id'] in event_ids or event['turn_id'] in turn_ids or event['purpose'] == 'general':
                raise ValueError('执行记录编号或用途无效')
            event_ids.add(event['id']); turn_ids.add(event['turn_id'])
            _ids(event['source_ids'])
            if not isinstance(event['steps'], list) or len(event['steps']) > 100 or type(event['completed_actions']) is not int or event['completed_actions'] != len(event['steps']):
                raise ValueError('执行步骤备份无效')
            for step in event['steps']:
                if not isinstance(step, dict) or set(step) != {'action', 'status', 'record_id', 'collection'} or step['status'] != 'completed':
                    raise ValueError('执行步骤字段无效')
                for key, maximum in (('action', 100), ('record_id', 100), ('collection', 80)):
                    _text(step[key], '执行步骤', maximum, True)
            if not isinstance(event['execution_steps'], list) or len(event['execution_steps']) > 50:
                raise ValueError('公开执行阶段备份无效')
            for step in event['execution_steps']:
                if not isinstance(step, dict) or set(step) != {'stage', 'detail', 'status'} or not isinstance(step['stage'], str) or step['stage'] not in LABELS or not isinstance(step['status'], str) or step['status'] not in EXECUTION_STATUSES:
                    raise ValueError('公开执行阶段备份字段无效')
                _text(step['detail'], '公开执行阶段说明', 200, True)
                if safe_detail(step['detail']) != step['detail']:
                    raise ValueError('公开执行阶段不能包含本机路径或隐藏凭证')
            if not isinstance(event['current_artifact'], dict) or set(event['current_artifact']) - {'collection', 'id', 'version', 'revision', 'updated_at'}:
                raise ValueError('执行产物引用无效')
            for key, value in event['current_artifact'].items():
                if not isinstance(value, str) and not (key in {'version', 'revision'} and type(value) is int and value >= 0):
                    raise ValueError('执行产物引用类型无效')
                if isinstance(value, str):
                    _text(value, '执行产物引用', 100, True)
        seen = set()
        for item in suppressed:
            if not isinstance(item, dict) or set(item) != {'project_id', 'origin_key'}:
                raise ValueError('项目经验删除标记无效')
            self._scope(workspace, item['project_id'], allow_missing=True)
            if not isinstance(item['origin_key'], str) or not re.fullmatch(r'[0-9a-f]{64}', item['origin_key']) or (item['project_id'], item['origin_key']) in seen:
                raise ValueError('项目经验删除标记重复或无效')
            seen.add((item['project_id'], item['origin_key']))
        with self.lock, self.db:
            for table, values in (('experience_entries', entries), ('experience_events', events)):
                for value in values:
                    row = self.db.execute('SELECT workspace FROM ' + table + ' WHERE id=?', (value['id'],)).fetchone()
                    if row and row[0] != workspace:
                        raise ValueError('项目经验编号属于另一工作区')
            for table in ('experience_settings', 'experience_entries', 'experience_events', 'experience_suppressed'):
                self.db.execute('DELETE FROM ' + table + ' WHERE workspace=?', (workspace,))
            for item in settings:
                self.db.execute('INSERT INTO experience_settings VALUES (?,?,?)', (workspace, item['project_id'], json.dumps({'enabled': item['enabled']})))
            for entry in entries:
                self._write(entry)
            for event in events:
                self.db.execute('INSERT INTO experience_events VALUES (?,?,?,?,?)', (event['id'], workspace, event['project_id'], event['turn_id'], json.dumps(event, ensure_ascii=False)))
            for item in suppressed:
                self.db.execute('INSERT INTO experience_suppressed VALUES (?,?,?)', (workspace, item['project_id'], item['origin_key']))
        return {'entries': len(entries), 'events': len(events)}

    def close(self):
        with self.lock:
            self.db.close()
