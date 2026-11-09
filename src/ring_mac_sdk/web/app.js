'use strict';
const SENSITIVITY_KEY='ring-studio.sensitivity.v1';
function validSensitivity(value){
  const number=Number(value);
  return Number.isFinite(number)&&number>=.25&&number<=8?number:1;
}
function readSensitivity(storage){
  try{return validSensitivity(storage.getItem(SENSITIVITY_KEY));}catch{return 1;}
}
function saveSensitivity(storage,value){
  try{storage.setItem(SENSITIVITY_KEY,String(validSensitivity(value)));}catch{/* Private mode may deny storage. */}
}
class MotionView {
  constructor(sensitivity=1){this.sensitivity=validSensitivity(sensitivity);this.key=null;this.reset();}
  reset(){this.position=[0,0];this.previous=[0,0];this.points=[];this.camera=[0,0];}
  setSensitivity(value){this.sensitivity=validSensitivity(value);}
  consume(snapshot){
    const key=JSON.stringify([snapshot.revision,snapshot.session,snapshot.generation]);
    if(key!==this.key){this.reset();this.key=key;}
    const [x,y]=snapshot.position;
    const dx=x-this.previous[0],dy=y-this.previous[1];
    this.previous=[x,y];
    if(dx===0&&dy===0)return; // Duplicate 10 Hz snapshots must not replay motion.
    this.position=[this.position[0]+dx*this.sensitivity,this.position[1]+dy*this.sensitivity];
    this.points.push([...this.position,snapshot.points.at(-1)?.[2]||'SINGLE']);
    if(this.points.length>800)this.points.shift();
  }
}
function followCamera(center,position,width,height){
  const scale=2; // Fixed CSS pixels per display unit: never fit the historical path.
  const margin=[Math.max(1,width/4)/scale,Math.max(1,height/4)/scale];
  return {scale,center:center.map((value,i)=>Math.max(position[i]-margin[i],Math.min(position[i]+margin[i],value)))};
}
if(typeof module!=='undefined')module.exports={MotionView,readSensitivity,saveSensitivity,followCamera};
if(typeof document!=='undefined'){
const $ = id => document.getElementById(id);
let preferences;
try{preferences=window.localStorage;}catch{/* Keep controls usable without persistence. */}
const motion=new MotionView(readSensitivity(preferences));
const stages = {STOPPED:'未启动',STARTING:'准备模型',STARTING_STREAM:'等待启动响应',RUNNING:'运行中',STOPPING:'正在停止',FAILED:'启动或运行失败'};
const links = {DISCONNECTED:'未连接',CONNECTING:'连接中',CONNECTED:'已连接'};
const kinds = {connection:'连接',status:'状态',scan:'扫描',contact:'接触',click:'点击',preview:'预告',clear:'清空',error:'错误',cursor:'系统控制'};
let state=null, socket=null, selected='', devicesKey='', logKey='', waiting=false;
function send(action, extra={}) {
  if (!socket || socket.readyState!==WebSocket.OPEN || waiting) return;
  waiting=true; controls(); socket.send(JSON.stringify({action,...extra}));
}
function controls() {
  const ready=socket?.readyState===WebSocket.OPEN && state, busy=!ready||state?.busy||waiting;
  const cursor=state?.cursor;
  $('cursor-stop').disabled=!ready||!cursor;
  $('cursor-permission').disabled=Boolean(busy||!cursor||state?.demo);
  $('cursor-enable').disabled=Boolean(busy||!cursor||state?.demo||cursor?.enabled||cursor?.inflight||state?.link!=='CONNECTED'||state?.stage!=='RUNNING');
  for(const id of ['cursor-gain','scroll-gain','finger-mapping','system-clicks'])$(id).disabled=Boolean(busy||cursor?.enabled);
  for (const id of ['scan','connect','disconnect','start','stop','refresh','clear','devices']) $(id).disabled=Boolean(busy);
  if(!ready)return;
  $('scan').disabled=busy||state.has_ring;
  $('devices').disabled=busy||state.has_ring;
  $('connect').disabled=busy||state.has_ring||!selected;
  $('disconnect').disabled=busy||!state.has_ring;
  $('start').disabled=busy||state.link!=='CONNECTED'||!['STOPPED','FAILED'].includes(state.stage);
  $('stop').disabled=busy||state.link!=='CONNECTED'||state.stage==='STOPPED';
  $('refresh').disabled=busy||state.link!=='CONNECTED';
  $('scan').textContent=state.busy&&!state.has_ring?'正在操作…':'扫描附近戒指';
}
function render(s){
  state=s; waiting=false;
  motion.consume(s);
  const cursor=s.cursor;
  $('cursor-state').textContent=cursor?.enabled?'控制中':cursor?.preparing?'准备中 · 尚未开启':'已关闭';
  $('cursor-state').className='pill'+(cursor?.enabled?' active':'');
  const permission={unchecked:'权限未检查',allowed:'辅助功能已允许',denied:'缺少辅助功能权限',unavailable:'依赖或权限检查不可用',demo:'模拟模式'};
  $('cursor-message').textContent=!cursor?'系统控制需要重启 Python 服务加载新后端。':s.demo?'模拟模式禁止系统输出，不会移动鼠标、滚动或点击。':`${permission[cursor.permission]||cursor.permission} · ${cursor.message} · 待派发 ${cursor.pending} / 迟到继续 ${cursor.late||0}`;
  $('mode').className='banner'+(s.demo?' demo':'');
  $('mode').textContent=s.demo?'模拟模式 · 所有设备、电量、轨迹和点击均为演示数据，不访问蓝牙，不代表真机测试结果':'真机模式 · 选择设备后连接；系统控制默认关闭，开启后会影响本机光标和滚轮';
  const key=JSON.stringify(s.devices);
  if(key!==devicesKey){devicesKey=key;$('devices').replaceChildren();const blank=new Option(s.devices.length?'请选择一只戒指':'尚未发现设备，请扫描','');$('devices').append(blank);for(const d of s.devices)$('devices').append(new Option(`${d.name} · ${d.rssi} dBm`,d.identifier));if(s.devices.some(d=>d.identifier===selected))$('devices').value=selected;else selected='';}
  $('device-name').textContent=s.name||'未连接戒指';
  $('link-label').textContent=s.demo?'模拟设备 · 无硬件连接':links[s.link]||s.link;
  $('link').textContent=links[s.link]||s.link;
  $('firmware').textContent=s.firmware||'—';
  $('battery').textContent=s.battery===null?'未读取':`${s.battery}%${s.demo?'（模拟）':''}`;
  $('stage').textContent=stages[s.stage]||s.stage;
  $('stage').className='pill'+(s.stage==='RUNNING'?' active':'');
  $('canvas-label').textContent=s.stage==='RUNNING'?(s.demo?'模拟轨迹 · 实时显示':'实时轨迹'):(s.stage==='FAILED'?'数据流已停止 · 请查看下方错误':'等待开始');
  $('position').textContent=`X ${motion.position[0].toFixed(1)} / Y ${motion.position[1].toFixed(1)}`;
  $('contact').textContent=s.contact?({SINGLE:'单指接触',TWO_FINGER:'双指接触',UNKNOWN:'接触 · 类型未确定'}[s.contact.mode]||s.contact.mode):'未接触';
  $('contact').style.color=s.contact?.mode==='TWO_FINGER'?'#7a6bc6':s.contact?'#13796b':'';
  $('click').textContent=s.click?`已确认 · ${s.click.button==='RIGHT'?'右键':s.click.count===2?'双击':'单击'}`:'等待点击';
  $('preview').hidden=!s.preview;
  $('session').textContent=s.session?`会话 ${s.session.slice(0,8)} · generation ${s.generation}`:'尚无触摸板会话';
  $('empty').hidden=Boolean(motion.points.length);
  $('diagnostics').textContent=s.diagnostics ? `${s.diagnostics.severity} · ${s.diagnostics.active_issues.join(', ') || '无持续告警'}\n${s.diagnostics.message}\n${JSON.stringify(s.diagnostics.counters)}` : '暂无触摸板诊断';
  $('error').hidden=!s.error;
  if(s.error){$('error-hint').textContent=s.error.hint;$('error-summary').textContent=`${s.error.reason} · 设备码 ${s.error.device_code??'—'} · 阶段 ${s.error.phase??'—'}`;$('error-detail').textContent=JSON.stringify(s.error,null,2);}
  const lk=s.logs.map(l=>l.id).join(',');
  if(lk!==logKey){logKey=lk;$('logs').replaceChildren();for(const log of [...s.logs].reverse()){const row=document.createElement('div');row.className=`log-line ${log.kind}`;const t=document.createElement('time');t.textContent=log.time;const kind=document.createElement('span');kind.className='log-kind';kind.textContent=kinds[log.kind]||log.kind;const msg=document.createElement('span');msg.className='log-message';msg.textContent=log.message;row.append(t,kind,msg);$('logs').append(row);}if(!s.logs.length){const empty=document.createElement('div');empty.className='log-empty';empty.textContent='准备就绪。扫描设备，开启第一次试用。';$('logs').append(empty);}$('log-count').textContent=`最近 ${s.logs.length} 条 / 最多 100 条`;}
  controls(); draw();
}
function draw(){
 const c=$('trail'), r=c.getBoundingClientRect(), dpr=window.devicePixelRatio||1;
 c.width=Math.round(r.width*dpr);c.height=Math.round(r.height*dpr);const ctx=c.getContext('2d');ctx.scale(dpr,dpr);
 ctx.fillStyle='#dce7e1';for(let x=16;x<r.width;x+=22)for(let y=16;y<r.height;y+=22){ctx.beginPath();ctx.arc(x,y,.7,0,Math.PI*2);ctx.fill();}
 if(!motion.points.length)return;
 const camera=followCamera(motion.camera,motion.position,r.width,r.height);
 motion.camera=camera.center;
 const point=p=>[r.width/2+(p[0]-camera.center[0])*camera.scale,r.height/2+(p[1]-camera.center[1])*camera.scale];
 ctx.lineWidth=2.2;ctx.lineCap='round';
 for(let i=1;i<motion.points.length;i++){const a=point(motion.points[i-1]),b=point(motion.points[i]);ctx.strokeStyle=motion.points[i][2]==='TWO_FINGER'?'#9180d0':'#4b9f8d';ctx.globalAlpha=.2+.8*i/motion.points.length;ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();}
 ctx.globalAlpha=1;const last=motion.points.at(-1),p=point(last);ctx.fillStyle=last[2]==='TWO_FINGER'?'#7a6bc6':'#13796b';ctx.beginPath();ctx.arc(...p,5,0,Math.PI*2);ctx.fill();ctx.globalAlpha=.12;ctx.beginPath();ctx.arc(...p,13,0,Math.PI*2);ctx.fill();
}
function sensitivity(value){
  motion.setSensitivity(value);
  $('sensitivity').value=motion.sensitivity;
  $('sensitivity-value').textContent=`${Number(motion.sensitivity.toFixed(2))}×`;
  $('sensitivity').setAttribute('aria-valuetext',`${motion.sensitivity} 倍`);
  saveSensitivity(preferences,motion.sensitivity);
}
$('sensitivity').addEventListener('input',e=>sensitivity(e.target.value));
$('sensitivity-reset').addEventListener('click',()=>sensitivity(1));
sensitivity(motion.sensitivity);
function stopSystem(){
  // Never gate emergency output-disable on waiting/busy UI state.
  if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({action:'cursor_stop'}));
}
$('cursor-stop').addEventListener('click',stopSystem);
window.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();stopSystem();}});
$('cursor-permission').addEventListener('click',()=>send('cursor_permission'));
$('cursor-enable').addEventListener('click',()=>send('cursor_enable',{config:{gain:Number($('cursor-gain').value),scroll_gain:Number($('scroll-gain').value),finger_mapping:$('finger-mapping').checked,clicks:$('system-clicks').checked}}));
for(const action of ['scan','connect','disconnect','start','stop','clear'])$(action).addEventListener('click',()=>send(action,action==='connect'?{identifier:selected}:{}));
$('refresh').addEventListener('click',()=>send('battery'));
$('devices').addEventListener('change',()=>{selected=$('devices').value;$('device-hint').textContent=selected||'不会自动连接任何设备';controls();});
new ResizeObserver(draw).observe($('trail'));
socket=new WebSocket(`ws://${location.host}/ws`);
socket.onopen=()=>{$('service').textContent='页面服务已连接';$('service').className='pill active';controls();};
socket.onmessage=e=>render(JSON.parse(e.data));
socket.onclose=()=>{waiting=false;$('service').textContent='页面服务已断开';$('service').className='pill';$('offline').hidden=false;$('offline').textContent='控制连接已关闭。请确认本地服务仍在运行，并关闭其他控制标签页后刷新。设备连接会由后端释放；刷新不会自动重连戒指。';if(state)render({...state,revision:state.revision+1,link:'DISCONNECTED',stage:'STOPPED',points:[],position:[0,0],contact:null,preview:null,click:null,session:null});controls();};
window.addEventListener('pagehide',()=>socket?.close());
controls();
}
