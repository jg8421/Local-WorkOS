"""Isolated authoring and real Microsoft Excel recalculation of new workbooks."""
from pathlib import Path
import json
import math
import os
import subprocess
import tempfile
import time
import hashlib
import copy
import threading
from collections import OrderedDict
from .cancellation import check_cancelled, report_progress, CancelledError
from .investor_returns import calculate


class ExcelUnavailable(ValueError):
    pass


_cache=OrderedDict()
_lock=threading.Lock()
_excel_lock=threading.Lock()


def _run(arguments, timeout=90):
    # Cancellation asks the COM worker to exit gracefully, so its finally quits
    # only its own Excel instance. No global Excel kill or user-workbook access.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(arguments, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                check_cancelled()
                if time.monotonic() > deadline: raise ExcelUnavailable('Excel计算超时；没有保存项目记录，请重试')
                time.sleep(.1)
        except BaseException:
            # The generated workbook lives only in this temp directory. Give
            # short-running Excel its finally; results are never committed late.
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.terminate(); process.wait(timeout=5)
            raise
        check_cancelled()
        if process.returncode:
            raise ExcelUnavailable('Excel未完成计算。请确认本机Excel可用；当前条件已保留，可重试。')


def _workbook(a, *, preview=None):
    result = calculate(a)
    if os.name != 'nt': raise ExcelUnavailable('投资回报需要本机Microsoft Excel；当前运行环境尚未连接Excel')
    runtime = Path.home()/'.cache'/'codex-runtimes'/'codex-primary-runtime'/'dependencies'/'node'
    node = Path(os.environ.get('WORKOS_ARTIFACT_NODE') or runtime/'bin'/'node.exe')
    modules = Path(os.environ.get('WORKOS_ARTIFACT_MODULES') or runtime/'node_modules')
    if not node.is_file() or not (modules/'@oai'/'artifact-tool').exists():
        raise ExcelUnavailable('Excel工作簿生成组件尚未配置；条件已保留，请在本机完成运行环境配置后重试')
    report_progress('generate', '正在建立投资人现金流和可编辑Excel公式')
    with tempfile.TemporaryDirectory(prefix='workos-return-') as directory:
        folder=Path(directory);input_=folder/'inputs.json';output=folder/'returns.xlsx';receipt=folder/'calculated.json'
        input_.write_text(json.dumps({'assumptions':a,'result':result},ensure_ascii=False,allow_nan=False),encoding='utf-8')
        _run([str(node),str(Path(__file__).with_name('return_workbook.mjs')),str(input_),str(output),str(modules), *([str(preview)] if preview else [])])
        report_progress('generate', 'Microsoft Excel正在重算MOC和实际日期XIRR')
        _run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
              str(Path(__file__).with_name('return_excel.ps1')),'-WorkbookPath',str(output),'-ResultPath',str(receipt)])
        computed=json.loads(receipt.read_text(encoding='utf-8-sig'))
        for key in ('moic','total_invested','total_received','irr','entry_ownership','exit_ownership','exit_equity_value','exit_proceeds'):
            if result[key] is None: continue
            actual=computed.get(key)
            if isinstance(actual,bool) or not isinstance(actual,(float,int)) or not math.isfinite(actual) or not math.isclose(actual,result[key],rel_tol=1e-7,abs_tol=1e-8):
                raise ExcelUnavailable('Excel结果与独立现金流校验不一致；未保存，请核对条件后重试')
            result[key]=actual
        result['moc']=result['moic'];result['calculation_engine']='Microsoft Excel · XIRR';result['excel_verified']=True
        result['answer']=f"MOC {result['moic']:.2f}×；IRR {result['irr']:.1%}。" if result['irr'] is not None else f"MOC {result['moic']:.2f}×；IRR无有限XIRR解。"
        check_cancelled()
        return result, output.read_bytes()


def workbook(a, *, preview=None):
    check_cancelled()
    key=hashlib.sha256(json.dumps(a,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    with _lock:
        cached=_cache.get(key)
        if not preview and cached and time.monotonic()-cached[0]<300:
            _cache.move_to_end(key)
            return copy.deepcopy(cached[1]),cached[2]
    while not _excel_lock.acquire(timeout=.1):check_cancelled()
    try:
        check_cancelled()
        with _lock:
            cached=_cache.get(key)
            if not preview and cached and time.monotonic()-cached[0]<300:return copy.deepcopy(cached[1]),cached[2]
        result,raw=_workbook(a,preview=preview)
        check_cancelled()
        with _lock:
            _cache[key]=(time.monotonic(),copy.deepcopy(result),raw)
            while len(_cache)>8:_cache.popitem(last=False)
        return result,raw
    finally:_excel_lock.release()


def calculate_excel(a):
    return workbook(a)[0]
