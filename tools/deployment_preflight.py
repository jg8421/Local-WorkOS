"""Portable startup/readiness. No installs, account reads or global process stops."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser


def readiness(app_root, *, module_available=None, which=None, platform=None, excel_registered=None):
    modules = module_available or (lambda name: importlib.util.find_spec(name) is not None)
    if which is None:
        env = runtime_environment(Path(app_root))
        which = lambda name: shutil.which(name, path=env.get('PATH', ''))
    platform = platform or os.name
    if excel_registered is None:
        excel_registered = False
        if platform == 'nt':
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, 'Excel.Application\\CLSID'):
                    excel_registered = True
            except OSError:
                pass
    docx, pptx, xlsx = (modules(name) for name in ('docx', 'pptx', 'openpyxl'))
    node, dsh = which('node'), which('dsh')
    # Probe installation paths only; never inspect account tokens or sessions.
    dsh_entry = Path(dsh).resolve().parent/'node_modules'/'@deepseek-ai'/'dsh'/'lib'/'bin.js' if dsh else None
    kit = os.environ.get('WORKOS_LIBREOFFICE_CLI')
    kit_node = os.environ.get('WORKOS_NODE')
    return {'schema_version': 1, 'core_ready': sys.version_info >= (3, 11),
        'python': '.'.join(map(str, sys.version_info[:3])), 'checks': [
        {'id': 'core', 'ready': sys.version_info >= (3, 11), 'label': '平台、资料及HTML', 'detail': 'Python3.11+；个人资料不包含在软件包中'},
        {'id': 'docx', 'ready': docx, 'label': 'Word导出', 'detail': 'full包内置python-docx' if not docx else '导出库可用'},
        {'id': 'pptx', 'ready': pptx, 'label': 'PowerPoint导出', 'detail': 'full包内置python-pptx' if not pptx else '导出库可用'},
        {'id': 'xlsx', 'ready': xlsx, 'label': 'Excel工作簿作者', 'detail': 'openpyxl便携作者可用' if xlsx else 'full包内置openpyxl；不会复制宿主SDK'},
        {'id': 'excel', 'ready': bool(platform == 'nt' and excel_registered and xlsx), 'label': '投资回报原生Excel计算',
         'detail': '已检测Excel注册和工作簿作者；真实重算仍需实际成功' if excel_registered and xlsx else '需要本机Microsoft Excel与full作者；缺少时保留假设，不宣称已重算'},
        {'id': 'dsh', 'ready': bool(node and dsh_entry and dsh_entry.is_file()), 'label': 'GPT / DSH',
         'detail': '可选；须本人安装DSH并登录，软件包不迁移凭证；检测安装不保证账号权限'},
        {'id': 'pdf', 'ready': bool(kit and kit_node and Path(kit).is_file() and Path(kit_node).is_file()),
         'label': '纪要PDF导出', 'detail': '需要已授权LibreOffice Kit配置；未配置可先导出Word'},
        {'id': 'models', 'ready': False, 'label': '模型服务', 'detail': '在设置检测WorkBuddy桥接或登记兼容服务；核心运行不要求联网模型'}]}


def clean_environment():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('WORKOS_PUBLIC_', 'WORKOS_ACCESS_', 'WORKOS_TUNNEL_', 'WORKOS_SYNC_', 'WORKOS_MEMORY_')):
            env.pop(key, None)
    env['WORKOS_PUBLIC_ORIGIN'] = ''; env['WORKOS_SYNC_ROOT'] = ''; env['WORKOS_MEMORY_ROOT'] = ''
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def runtime_environment(app_root):
    env = clean_environment()
    node = app_root.parent/'runtime/node/node.exe'
    if node.is_file():
        env['PATH'] = str(node.parent) + os.pathsep + env.get('PATH', '')
        env.setdefault('WORKOS_NODE', str(node))
    return env


def health(port):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
            info = json.load(response)
            return info if isinstance(info, dict) else {'app': 'other-app'}
    except (OSError, ValueError):
        return None


def start(app_root, data_dir, port, *, browser=True):
    info = health(port)
    if info is not None and info.get('app') != 'local-workos':
        raise ValueError('端口由其他程序占用；没有停止或替换它，请使用其他端口')
    if info is None:
        data_dir.mkdir(parents=True, exist_ok=True)
        env = runtime_environment(app_root)
        env['PYTHONPATH'] = str(app_root/'vendor')
        with (data_dir/'portable-launcher.log').open('a', encoding='utf-8') as log:
            bootstrap = ('import sys,runpy;from pathlib import Path;'
                         'p=Path(sys.argv.pop(1));sys.path[:0]=[str(p),str(p/"vendor")];'
                         'runpy.run_module("workos.server",run_name="__main__",alter_sys=True)')
            process = subprocess.Popen([sys.executable, '-I', '-c', bootstrap, str(app_root.resolve()), '--port', str(port), '--data-dir', str(data_dir)],
                cwd=app_root, env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        for _ in range(100):
            info = health(port)
            if info and info.get('app') == 'local-workos': break
            if process.poll() is not None: raise ValueError('启动未完成，请查看本机portable-launcher.log；可能端口已被占用')
            time.sleep(.15)
        else:
            process.terminate(); process.wait(timeout=5)
            raise ValueError('本次新进程启动超时，已停止；其他应用未受影响')
    if browser: webbrowser.open(f'http://127.0.0.1:{port}/')
    return info


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--launch', action='store_true'); parser.add_argument('--json', action='store_true')
    parser.add_argument('--no-browser', action='store_true'); parser.add_argument('--port', type=int, default=18866)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args(); app_root = Path(__file__).resolve().parents[1]
    report = readiness(app_root)
    if args.json: print(json.dumps(report, ensure_ascii=False))
    else:
        print('Local WorkOS · 环境检查（没有安装或更改系统）')
        for item in report['checks']: print(('[可用] ' if item['ready'] else '[需配置] ') + item['label'] + '：' + item['detail'])
    if args.launch:
        if not 1024 <= args.port <= 65535: raise ValueError('端口应在1024至65535之间')
        root = args.data_dir or Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'.local/share')))/'LocalWorkOS'
        print('正在打开本机WorkOS；首次启动可能需要等待安全软件检查。', flush=True)
        info = start(app_root, root, args.port, browser=not args.no_browser)
        print('本机WorkOS版本：' + str(info.get('version', '未知'))[:40])
    return 0


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as exc:
        print('无法完成启动：' + str(exc)); raise SystemExit(1)
