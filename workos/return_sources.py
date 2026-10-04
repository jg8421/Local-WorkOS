"""Read bounded financial evidence only inside the selected project scope."""
import hashlib
from pathlib import Path
import os
import re
import stat
from .attachments import read_original, OriginalUnavailable
from .engine import parse_upload
from .cancellation import check_cancelled, report_progress, CancelledError


def collect(app, store, workspace, body):
    if body.get('use_project_sources') is not True: return {'text':'', 'sources':[], 'notes':[]}
    project_id = body.get('project_id')
    if not project_id: return {'text':'', 'sources':[], 'notes':['未关联项目，本轮仅使用你提供的条件。']}
    project = store.get('projects', project_id)
    docs = []
    for item_id in body.get('document_ids', []):
        doc = store.get('documents', item_id)
        if doc.get('project_id') != project_id or doc.get('kind') == 'memory':
            raise ValueError('回报测算只读取当前项目资料，不能使用其他项目或个人记忆')
        docs.append(doc)
    candidates = []
    notes = []
    for doc in docs:
        name = doc.get('attachment_name') or doc.get('title') or '项目资料'
        candidates.append((name, doc, None))
    binding = app.artifacts.resolve(workspace, project)
    if workspace == 'personal' and not binding['managed']:
        root = app.artifacts._business_path(binding['folder'])
        seen = 0
        for current, directories, files in os.walk(root, followlinks=False):
            check_cancelled()
            here = Path(current)
            if len(here.relative_to(root).parts) >= 4: directories[:] = []
            else:
                directories[:] = [name for name in directories if not name.startswith('.') and name not in ('WorkOS产物', 'node_modules', '__pycache__')
                                  and not _reparse(here/name)]
            for name in files:
                seen += 1
                if seen > 500: break
                path = here/name
                if path.suffix.lower() in ('.xlsx', '.xlsm', '.pdf') and not name.startswith('~$') and not _reparse(path):
                    candidates.append((str(path.relative_to(root)), None, path))
            if seen > 500:
                notes.append('项目目录超过500项，仅检查了有限范围；可导入指定模型继续测算。'); break
    # Model/forecast/return materials precede agreements. Version names are
    # evidence, never an automatic declaration of which version is approved.
    def relevance(item):
        name, doc, path = item
        score = len(re.findall(r'model|forecast|return|financial|预算|预测|模型|回报|财务',name,re.I))
        return (-score, name.casefold())
    candidates.sort(key=relevance)
    chunks=[]; sources=[]; total=0; hashes=set()
    report_progress('context','正在读取当前项目的Excel/PDF及对应页码/单元格')
    for name, doc, path in candidates:
        check_cancelled()
        if len(sources) >= 8 or total >= 44000: break
        try:
            if path:
                # Revalidate confinement and reparse status at read time.
                path=app.artifacts._business_path(path.parent)/path.name
                if _reparse(path) or path.stat().st_size > 20_000_000: continue
                with path.open('rb') as stream: raw=stream.read(20_000_001)
                if len(raw)>20_000_000: continue
            else:
                try: raw=read_original(app.data_dir,workspace,doc)
                except OriginalUnavailable:
                    raw=None
                    if doc.get('attachment_ref'):notes.append(name+'：原文件不可用，本轮使用导入时保留正文，未读取当前原件。')
            if raw is not None:
                digest=hashlib.sha256(raw).hexdigest()
                if digest in hashes: continue
                parsed=parse_upload(name,raw)
                pages=parsed.get('pages') or [{'page':1,'text':parsed.get('content','')}]
                content='\n\n'.join('页/工作表 '+str(p['page'])+'\n'+p['text'] for p in pages)
            else:
                content=doc.get('content','');digest=hashlib.sha256(content.encode()).hexdigest()
            if not content.strip(): notes.append(name+'：未提取到文字，请提供可读模型或非扫描PDF。');continue
            # Keep headers and financial rows from long spreadsheets, rather
            # than a blind first-characters cut losing a late Return sheet.
            original_length=len(content)
            if len(content)>11000:
                lines=content.splitlines()
                focus=re.compile(r'Sheet |工作表|currency|million|人民币|美元|单位|USD|CNY|203\d|202\d|净利|profit|income|PE|P/E|dilut|IPO|return|ownership|持股|估值|equity|MOIC|XIRR',re.I)
                selected=set(range(min(12,len(lines))))
                for i,line in enumerate(lines):
                    if focus.search(line): selected.update(range(max(0,i-1),min(len(lines),i+2)))
                content='\n'.join(lines[i] for i in sorted(selected))
            excerpt=content[:min(11000,44000-total)]
            source_id='R'+str(len(sources)+1)
            source={'id':source_id,'name':name,'hash':digest,'truncated':len(excerpt)<original_length,
                    'document_id':doc.get('id') if doc else None}
            sources.append(source);hashes.add(digest);total+=len(excerpt)
            chunks.append('['+source_id+'] '+name+'\n'+excerpt)
        except CancelledError: raise
        except (ValueError,OSError) as exc:
            notes.append(name+'：本轮未读取（'+str(exc)[:120]+'）。')
    if len(candidates)>len(sources): notes.append('只选取了最多8份相关资料；来源详情标明读取范围，未覆盖全部项目文件。')
    return {'text':'\n\n'.join(chunks),'sources':sources,'notes':notes[:8]}


def _reparse(path):
    from .project_artifacts import _linked
    try:
        return _linked(Path(path))
    except OSError: return True


def verify(app, store, workspace, body, sources):
    """A source change during the model request blocks stale saving."""
    from .project_artifacts import _confined
    binding=None
    for source in sources:
        check_cancelled()
        try:
            if source['document_id']:
                doc=store.get('documents',source['document_id'])
                if doc.get('kind')=='memory' or doc.get('project_id')!=body['project_id']:raise ValueError('资料范围已改变')
                try:raw=read_original(app.data_dir,workspace,doc)
                except OriginalUnavailable:raw=doc.get('content','').encode()
            else:
                if binding is None:binding=app.artifacts.resolve(workspace,store.get('projects',body['project_id']))
                if binding['managed']:raise ValueError('项目文件夹关联已改变')
                path=_confined(binding['folder'],Path(binding['folder'])/source['name'])
                with path.open('rb') as stream:raw=stream.read(20_000_001)
            if hashlib.sha256(raw).hexdigest()!=source['hash']:raise ValueError('资料内容已改变')
        except (OSError,KeyError,ValueError) as exc:
            if isinstance(exc,CancelledError):raise
            raise ValueError('本轮读取的项目资料已改变或不可用；条件已保留，请重新读取后测算，未保存过期结果') from exc
