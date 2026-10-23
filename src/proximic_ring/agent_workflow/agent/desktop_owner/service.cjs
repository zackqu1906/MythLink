// Mythlink's per-application task service. No window, login item or network listener.
'use strict';
const fs = require('node:fs');
const net = require('node:net');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {createDesktopIpcLiveOwner} = require('./desktop-ipc-live-owner');
const [root, codex, parentText, version] = process.argv.slice(2);
const parentPid = Number(parentText);
if (!root || !codex || !Number.isInteger(parentPid) || parentPid < 2) process.exit(2);
process.umask(0o077);
fs.mkdirSync(root, {recursive:true, mode:0o700});
const socketPath = path.join(root, 'control.sock');
const lockPath = path.join(root, 'service.lock');
try {fs.writeFileSync(lockPath, String(process.pid), {flag:'wx', mode:0o600});}
catch {process.exit(3);} // The client removes a stale lock only after checking its PID.
const pending = new Map(), requests = new Map(), threads = new Set(), resumes = new Map();
const cuaTurns = new Set(), draining = new Map(), cleanupErrors = new Map();
let serial = 0, closing = false, owner;
const log = fs.openSync(path.join(root, 'app-server.log'), 'a', 0o600);
const child = spawn(codex, ['app-server','--listen','stdio://'], {stdio:['pipe','pipe',log]});
function write(message, internal=false) {
  if (!child.stdin.writable) throw new Error('执行进程已退出；本次操作未自动重试');
  if (!internal) owner?.observeInbound(null, message);
  child.stdin.write(JSON.stringify(message)+'\n');
  // Dynamic tool requests do not always receive serverRequest/resolved. Retire
  // the exact request only after its response was submitted to this executor.
  if (message.id != null && !message.method && requests.has(String(message.id))) {
    const request = requests.get(String(message.id)); requests.delete(String(message.id));
    owner.observeOutbound(null, {method:'serverRequest/resolved',params:{
      threadId:request.params.threadId, requestId:request.id}});
  }
}
async function rpc(method, params={}, internal=false) {
  if(method==='turn/start') {
    if(draining.has(params.threadId)) await draining.get(params.threadId);
    if(cleanupErrors.has(params.threadId)) throw new Error('上一轮电脑控制未正常释放，请退出 Mythlink 后重新连接');
  }
  const id = ++serial;
  return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{pending.delete(String(id));reject(new Error('操作回执超时；不会自动重发: '+method));},30000);
    pending.set(String(id),{resolve,reject,timer,internal});
    try {write({id,method,params},internal);} catch(e) {clearTimeout(timer);pending.delete(String(id));reject(e);}
  });
}
owner = createDesktopIpcLiveOwner({
  sendCodexRequest:(method,params)=>rpc(method,params,true),
  sendRawCodexMessage:raw=>write(typeof raw==='string'?JSON.parse(raw):raw,true),
  startRouterWhenMissing:true, logPrefix:'[Mythlink]',
});
readline.createInterface({input:child.stdout}).on('line',line=>{
  let message; try {message=JSON.parse(line);} catch{return;}
  if(['item/started','item/completed'].includes(message.method) && message.params?.item?.type==='mcpToolCall'
      && message.params.item.server==='cua_repl') cuaTurns.add(message.params.turnId);
  if(message.method==='turn/completed' && cuaTurns.delete(message.params.turn.id)) {
    const {threadId,turn}=message.params;
    const cleanup=(async()=>{
      for(const [tool,args] of [['turn_ended',{session_id:threadId,turn_id:turn.id,hook_event_name:'Stop'}],['js_reset',{}]]) {
        try {
          const result=await rpc('mcpServer/tool/call',{threadId,server:'cua_repl',tool,arguments:args},true);
          if(result?.isError || !Array.isArray(result?.content)) throw new Error('Tool cleanup failed');
        } catch(error) {cleanupErrors.set(threadId,String(error));}
      }
      retireTurnRequests(threadId,turn.id);
      owner.observeOutbound(null,message);
    })();
    draining.set(threadId,cleanup);
    cleanup.finally(()=>draining.delete(threadId));
    return;
  }
  const waiter = message.id != null && !message.method ? pending.get(String(message.id)) : null;
  if (!waiter?.internal) owner.observeOutbound(null,message);
  if (waiter) {
    clearTimeout(waiter.timer);pending.delete(String(message.id));
    message.error ? waiter.reject(new Error(message.error.message || JSON.stringify(message.error))) : waiter.resolve(message.result);
  } else if (message.id != null && message.method) {
    requests.set(String(message.id),message);
  } else if (message.method==='serverRequest/resolved') {
    requests.delete(String(message.params.requestId));
  }
  if (message.method==='turn/completed') {
    const {threadId,turn}=message.params;
    retireTurnRequests(threadId,turn.id);
  }
});
function retireTurnRequests(threadId,turnId) {
  // Async questions can outlive a turn; synchronous requests cannot authorize
  // operations in a subsequent turn.
  for(const [key,request] of requests) {
    if(request.params?.threadId===threadId && request.params?.turnId===turnId && request.params?.isBlocking!==false) {
      requests.delete(key);
      owner.observeOutbound(null,{method:'serverRequest/resolved',params:{threadId,requestId:request.id}});
    }
  }
}
function remember(thread) {threads.add(thread.id); return thread;}
async function resume(threadId) {
  if (threads.has(threadId)) return {thread:owner._debugSnapshot(threadId)};
  if (resumes.has(threadId)) return resumes.get(threadId);
  const work=(async()=>{
    const data=await rpc('thread/read',{threadId,includeTurns:false});
    if(data.thread.id!==threadId || data.thread.ephemeral!==false || data.thread.archived) throw new Error('此对话不可连接');
    // App Server's writer lock is the authority; do not override, unlink or retry it.
    const result=await rpc('thread/resume',{threadId});remember(result.thread);return result;
  })();
  resumes.set(threadId,work);
  try{return await work;}finally{resumes.delete(threadId);}
}
async function command(method, params={}) {
  if(closing) throw new Error('Mythlink 正在退出，已停止接受新任务');
  if(method==='ping') return {pid:process.pid,serverPid:child.pid,version,threads:[...threads]};
  if(method==='shutdown') {setTimeout(shutdown,0);return {closing:true};}
  if(method==='create') {
    const result=await rpc('thread/start',{cwd:params.cwd,ephemeral:false});remember(result.thread);
    await rpc('thread/name/set',{threadId:result.thread.id,name:params.title});return result;
  }
  if(method==='resume') return resume(params.threadId);
  if(!threads.has(params.threadId)) throw new Error('此任务不由当前后台进程持有');
  if(method==='snapshot') return owner._debugSnapshot(params.threadId);
  if(method==='turn/start') return rpc(method,params);
  throw new Error('Unsupported service operation');
}
const sockets=new Set();
const server=net.createServer(socket=>{
  socket.setEncoding('utf8');
  sockets.add(socket);socket.on('error',()=>{});socket.on('close',()=>sockets.delete(socket));
  let buffer='';
  socket.on('data',chunk=>{
    buffer+=chunk.toString('utf8');
    if(Buffer.byteLength(buffer)>32*1024*1024) return socket.destroy();
    let newline;
    while((newline=buffer.indexOf('\n'))>=0) {
      const line=buffer.slice(0,newline);buffer=buffer.slice(newline+1);
      Promise.resolve().then(async()=>{
        const request=JSON.parse(line);let response;
        try{response={id:request.id,result:await command(request.method,request.params)};}
        catch(error){response={id:request.id,error:String(error.message||error)};}
        if(!socket.destroyed) socket.write(JSON.stringify(response)+'\n');
      }).catch(()=>socket.destroy());
    }
  });
});
function cleanup() {
  owner.stopAll(); for(const socket of sockets)socket.destroy();server.close();
  try{fs.unlinkSync(socketPath);}catch{}
  try{fs.unlinkSync(lockPath);}catch{}
}
async function shutdown() {
  if(closing)return;closing=true;
  const hardStop=setTimeout(()=>{child.kill('SIGKILL');cleanup();process.exit(1);},8000);
  try {
    await Promise.allSettled([...threads].map(async threadId=>{
      const active=owner._debugSnapshot(threadId)?.turns?.findLast(t=>t.status==='inProgress');
      if(active) await rpc('turn/interrupt',{threadId,turnId:active.turnId});
    }));
    child.kill('SIGTERM');
    await new Promise(resolve=>{if(child.exitCode!==null)resolve();else child.once('exit',resolve);});
  } finally {clearTimeout(hardStop);cleanup();process.exit(0);}
}
child.on('error',()=>shutdown());
child.on('exit',()=>{if(!closing){closing=true;cleanup();process.exit(1);}});
process.on('SIGTERM',shutdown);process.on('SIGINT',shutdown);
const parentCheck=setInterval(()=>{try{process.kill(parentPid,0);}catch{shutdown();}},1000);parentCheck.unref();
(async()=>{
  await rpc('initialize',{clientInfo:{name:'mythlink',title:'Mythlink',version:'1.0'},capabilities:{experimentalApi:true}});
  write({method:'initialized'});
  server.listen(socketPath,()=>fs.chmodSync(socketPath,0o600));
})().catch(error=>{console.error(error.message);shutdown();});
