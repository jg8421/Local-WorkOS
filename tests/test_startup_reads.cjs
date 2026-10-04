/* Startup GET deadlines and retry isolation; synthetic transport, no providers. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
function block(a,b){const start=source.indexOf(a),end=source.indexOf(b,start);assert.ok(start>=0&&end>start);return source.slice(start,end);}
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};}
function harness(transport=async path=>path==='/bootstrap'?{csrf:'synthetic-token'}:path==='/state'?{documents:[],projects:[]}:path==='/workflows'?{workflows:[]}:{} ){
  let sequence=0,renders=0;const timers=new Map(),calls=[],main={innerHTML:'',setAttribute(key,value){this[key]=value;}},label={},app={workspace:'personal',epoch:1,data:null};
  const state={page:'overview',question:'Synthetic unsent question',selectedSources:new Set(),projectArchives:new Map()};
  const sandbox={AbortController,Promise,app,location:{hash:'#overview'},ROUTES:[['overview']],
    view:()=>state,list:key=>app.data?.[key]||[],api:(path,options)=>{calls.push({path,options});return transport(path,options);},
    $:selector=>selector==='#main'?main:selector==='#connection-label'?label:null,esc:String,actionButton:label=>`<button>${label}</button>`,renderShell:()=>{},render:()=>{renders++;},
    restoreWorkflowJobs:async()=>{},restoreAiOperations:async()=>{},loadProjectArchives:async()=>{},showError:()=>{},
    setTimeout:(callback,delay)=>{const id=++sequence;timers.set(id,{callback,delay});return id;},clearTimeout:id=>timers.delete(id),
    StaleRequestError:class extends Error{constructor(){super('Workspace changed');this.name='StaleRequestError';}}
  };
  const functions=vm.runInNewContext(block('  const STARTUP_READ_PATHS=','  function renderIOStatus()')+block('  async function refreshData(','  function renderShell()')+'\n({startupRead,taskStatusRead,boot});',sandbox);
  return {...functions,app,state,main,calls,timers,get renders(){return renders;},expire:()=>[...timers.values()].forEach(timer=>timer.callback())};
}
const tests=[];function test(name,run){tests.push({name,run});}
test('startup deadline aborts only its GET and rejects even if a transport ignores abort',async()=>{
  const gate=deferred(),h=harness(()=>gate.promise),pending=h.startupRead('/bootstrap');assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.body,undefined);
  assert.equal([...h.timers.values()][0].delay,12000);h.expire();await assert.rejects(pending,error=>error.name==='StartupTimeoutError'&&error.path==='/bootstrap');assert.equal(h.calls[0].options.signal.aborted,true);assert.equal(h.timers.size,0);gate.resolve({csrf:'late'});
});
test('successful status reads clear their timers without writes or automatic retries',async()=>{
  const h=harness(async()=>({ok:true}));assert.equal((await h.startupRead('/state')).ok,true);assert.equal(h.timers.size,0);assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.body,undefined);
  await assert.rejects(h.startupRead('/ask'),/启动读取仅用于本地状态/);assert.equal(h.calls.length,1);
});
test('a stalled boot shows a reconnect action and retains drafts without dispatching a model',async()=>{
  const gate=deferred(),h=harness(()=>gate.promise),boot=h.boot();h.expire();await boot;
  assert.match(h.main.innerHTML,/本地服务尚未响应/);assert.match(h.main.innerHTML,/重新连接/);assert.match(h.main.innerHTML,/资料与草稿保留/);assert.equal(h.main['aria-busy'],'false');assert.equal(h.state.question,'Synthetic unsent question');assert.deepEqual(h.calls.map(item=>item.path),['/bootstrap']);gate.resolve({csrf:'late'});
});
test('an explicit reconnect wins over a late earlier boot and does not replay a mutation',async()=>{
  const gate=deferred();let boots=0;const h=harness(async path=>path==='/bootstrap'?(++boots===1?gate.promise:{csrf:'fresh-token'}):path==='/state'?{documents:[],projects:[]}:path==='/workflows'?{workflows:[]}:{});
  const old=h.boot();await h.boot();assert.equal(h.app.csrf,'fresh-token');const renders=h.renders;gate.resolve({csrf:'obsolete-token'});await old;
  assert.equal(h.app.csrf,'fresh-token');assert.equal(h.renders,renders);assert.equal(h.state.question,'Synthetic unsent question');assert.ok(h.calls.every(item=>!item.options?.body));
});
test('ordinary read failures stop once and never alter saved records or the draft',async()=>{
  const h=harness(async()=>{throw Error('Synthetic connection closed');});await h.boot();assert.equal(h.calls.length,1);assert.equal(h.timers.size,0);assert.equal(h.app.data,null);assert.equal(h.state.question,'Synthetic unsent question');assert.match(h.main.innerHTML,/查看连接说明/);
});
test('held execution-status GET releases its poll on deadline without replaying model generation',async()=>{
  for(const path of ['/operations/synthetic-operation','/workflows/jobs/synthetic-job']){
    const gate=deferred(),h=harness(()=>gate.promise),pending=h.taskStatusRead(path);assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.body,undefined);assert.equal([...h.timers.values()][0].delay,12000);
    h.expire();await assert.rejects(pending,error=>error.name==='StatusTimeoutError'&&error.path===path);assert.equal(h.calls[0].options.signal.aborted,true);assert.equal(h.timers.size,0);assert.equal(h.state.question,'Synthetic unsent question');gate.resolve({status:'late'});
  }
});
test('status deadlines cannot be attached to provider calls, retry or cancellation mutations',async()=>{
  const h=harness(async()=>({operation:{status:'completed'}}));assert.equal((await h.taskStatusRead('/operations/synthetic-operation')).operation.status,'completed');assert.equal(h.timers.size,0);
  for(const path of ['/ask','/workflows/jobs','/workflows/jobs/synthetic-job/retry','/operations/synthetic-operation/cancel'])await assert.rejects(h.taskStatusRead(path),/执行状态读取仅用于单个任务/);
  assert.equal(h.calls.length,1);assert.equal(h.calls[0].options.body,undefined);
});
(async()=>{for(const item of tests){await item.run();console.log('PASS '+item.name);}console.log('RESULT '+tests.length+'/'+tests.length);})().catch(error=>{console.error(error);process.exitCode=1;});
