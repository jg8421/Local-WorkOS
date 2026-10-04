'use strict';
// Native Windows process state, using the already configured test Python runtime.
// A JSON reply completes the probe even when Windows delays its child exit event.
const {spawn}=require('node:child_process');
const PROBE=String.raw`
import ctypes, json, sys, time
from ctypes import wintypes
pid = int(sys.argv[1])
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel.WaitForSingleObject.restype = wintypes.DWORD
kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel.GetExitCodeProcess.restype = wintypes.BOOL
kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
kernel.GetProcessTimes.restype = wintypes.BOOL
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL

def ticks(value):
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

def live_pids():
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.EnumProcesses.restype = wintypes.BOOL
    size = 4096
    while size <= 65536:
        array = (wintypes.DWORD * size)()
        count = wintypes.DWORD()
        if not psapi.EnumProcesses(array, ctypes.sizeof(array), ctypes.byref(count)):
            raise OSError(ctypes.get_last_error(), 'EnumProcesses failed')
        if count.value < ctypes.sizeof(array):
            return set(array[:count.value // ctypes.sizeof(wintypes.DWORD)])
        size *= 2
    raise RuntimeError('Process enumeration was truncated')

class PendingTeardown(RuntimeError):
    def __init__(self, birth, code):
        self.birth = str(birth)
        self.code = code
        self.created_ms = (birth - 116444736000000000) // 10000
        super().__init__('Terminal code received but PID is still enumerated; no cleanup permitted')

def probe():
    handle = kernel.OpenProcess(0x101000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:
            return {'status': 'exited', 'basis': 'PID no longer exists'}
        raise OSError(error, 'OpenProcess failed')
    try:
        wait = kernel.WaitForSingleObject(handle, 0)
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise OSError(ctypes.get_last_error(), 'GetExitCodeProcess failed')
        created, ended, ktime, utime = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(x) for x in (created, ended, ktime, utime)]):
            raise OSError(ctypes.get_last_error(), 'GetProcessTimes failed')
        birth, end = ticks(created), ticks(ended)
        if wait == 0:
            return {'status': 'exited', 'basis': 'native process handle signalled', 'exit_code': code.value}
        if wait != 258:
            raise RuntimeError('Unexpected native wait state: ' + str(wait))
        if code.value == 259:
            if not birth:
                raise RuntimeError('Native creation identity missing')
            return {'status': 'active', 'basis': 'native process handle not signalled', 'creation_id': str(birth),
                    'created_at_ms': (birth - 116444736000000000) // 10000}
        if end > 0 and end >= birth:
            return {'status': 'exited', 'basis': 'native exit code and exit time verified; wait handle not yet signalled', 'exit_code': code.value}
        if pid not in live_pids():
            return {'status': 'exited', 'basis': 'native terminal code and absence from live process enumeration; object cleanup pending', 'exit_code': code.value}
        if not birth:
            raise RuntimeError('Native creation identity missing during teardown')
        raise PendingTeardown(birth, code.value)
    finally:
        kernel.CloseHandle(handle)

deadline = time.monotonic() + 8
pending_birth = None
while True:
    try:
        result = probe()
        if result['status'] == 'active' and pending_birth is not None and result['creation_id'] != pending_birth:
            raise RuntimeError('PID birth identity changed during teardown probe')
        break
    except PendingTeardown as error:
        if pending_birth is not None and pending_birth != error.birth:
            result = {'status': 'unknown', 'reason': 'PID birth identity changed during teardown probe'}
            break
        pending_birth = error.birth
        if time.monotonic() >= deadline:
            result = {'status': 'terminating', 'basis': 'native terminal code; Windows process object cleanup still pending',
                      'creation_id': error.birth, 'created_at_ms': error.created_ms, 'exit_code': error.code}
            break
        time.sleep(0.2)
    except Exception as error:
        result = {'status': 'unknown', 'reason': str(error)}
        break
print(json.dumps({'pid': pid, **result}), flush=True)
`;
function releaseChildReferences(child){
  child.unref();child.stdout?.destroy();child.stderr?.destroy();
}
const releaseExitedChild=releaseChildReferences;
function windowsProcessState(pid){
  if(!Number.isSafeInteger(pid)||pid<=0||pid>0xffffffff)return Promise.reject(Error('Invalid created process PID'));
  return new Promise((resolve,reject)=>{
    const child=spawn(process.env.WORKOS_TEST_PYTHON||'python',['-c',PROBE,String(pid)],{windowsHide:true,stdio:['ignore','pipe','pipe']});
    let settled=false,output='',detail='';
    const finish=(error,state)=>{if(settled)return;settled=true;clearTimeout(timer);releaseExitedChild(child);error?reject(error):resolve(state);};
    const timer=setTimeout(()=>{try{child.kill();}catch{}finish(Error('Native created-process probe returned no JSON within 10 seconds'));},10000);
    child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
    child.stdout.on('data',chunk=>{
      output+=chunk;if(output.length>16000){finish(Error('Native created-process probe exceeded its output limit'));return;}
      const end=output.indexOf('\n');if(end<0)return;
      try{const state=JSON.parse(output.slice(0,end));if(state.pid!==pid||!['active','exited','terminating'].includes(state.status))throw Error(state.reason||'Unverified native process state');
        if(state.status!=='exited'&&(!/^\d+$/.test(state.creation_id)||!Number.isFinite(state.created_at_ms)))throw Error('Unverified native process birth identity');
        finish(null,state);
      }catch(error){finish(error);}
    });
    child.stderr.on('data',chunk=>{detail=(detail+chunk).slice(-2000);});
    child.once('error',error=>finish(error));
    child.once('exit',code=>{if(!settled)finish(Error('Native created-process probe exited without a verified JSON reply ('+code+'): '+detail));});
  });
}
module.exports={windowsProcessState,releaseExitedChild,releaseChildReferences};
