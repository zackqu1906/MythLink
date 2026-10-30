'use strict';

function signalNames(stream) {
  return stream==='token'?{axes:['T1','T2','T3','T4','T5','T6'],acc:'T1–T3',gyro:'T4–T6'}:
    {axes:['ax','ay','az','gx','gy','gz'],acc:'加速度',gyro:'陀螺仪'};
}

// Pure signal/view helpers are also exercised with Node, without a browser.
function median(values) {
  const v = values.filter(Number.isFinite).sort((a,b)=>a-b);
  return v.length ? (v[(v.length-1)>>1] + v[v.length>>1])/2 : null;
}
function nearest(rows, t) {
  if (!rows.length) return null;
  let lo=0, hi=rows.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(rows[mid][0]<t)lo=mid+1;else hi=mid;}
  if(!lo)return rows[0];if(lo===rows.length)return rows.at(-1);
  return t-rows[lo-1][0] <= rows[lo][0]-t ? rows[lo-1] : rows[lo];
}
function clampRange(start,end,min,max) {
  const span=Math.min(Math.max(.05,end-start),Math.max(.05,max-min));
  start=Math.max(min,Math.min(start,max-span));
  return [start,Math.min(max,start+span)];
}
function thinPoints(points, bins=800) {
  if(points.length<=bins*2)return points;
  const result=[];
  for(let i=0;i<bins;i++){
    const lo=Math.floor(i*points.length/bins),hi=Math.floor((i+1)*points.length/bins);
    let a=lo,b=lo;for(let j=lo+1;j<hi;j++){if(points[j][1]<points[a][1])a=j;if(points[j][1]>points[b][1])b=j;}
    result.push(points[Math.min(a,b)]);if(a!==b)result.push(points[Math.max(a,b)]);
  }
  return result;
}
function fftPower(input) {
  const n=input.length,re=Float64Array.from(input),im=new Float64Array(n);
  for(let i=1,j=0;i<n;i++){let bit=n>>1;for(;j&bit;bit>>=1)j^=bit;j^=bit;if(i<j)[re[i],re[j]]=[re[j],re[i]];}
  for(let size=2;size<=n;size*=2){const angle=-2*Math.PI/size;
    for(let base=0;base<n;base+=size)for(let j=0;j<size/2;j++){
      const c=Math.cos(angle*j),s=Math.sin(angle*j),p=base+j,q=p+size/2;
      const tr=c*re[q]-s*im[q],ti=s*re[q]+c*im[q];
      re[q]=re[p]-tr;im[q]=im[p]-ti;re[p]+=tr;im[p]+=ti;
    }
  }
  return Array.from({length:n/2+1},(_,i)=>re[i]*re[i]+im[i]*im[i]);
}
function rangeSpectrum(rows,hz) {
  const n=256, a=new Float64Array(n/2+1),g=new Float64Array(n/2+1);
  const w=Array.from({length:n},(_,i)=>.5-.5*Math.cos(2*Math.PI*i/(n-1)));
  const norm=hz*w.reduce((s,v)=>s+v*v,0);let count=0,segment=0;
  function add(end){
    for(let start=segment;start+n<=end;start+=n/2){count++;
      for(let c=1;c<=6;c++){
        let mean=0;for(let j=0;j<n;j++)mean+=rows[start+j][c]/n;
        const p=fftPower(w.map((v,j)=>(rows[start+j][c]-mean)*v)),out=c<=3?a:g;
        for(let k=0;k<p.length;k++)out[k]+=p[k]*(k&&k<n/2?2:1)/norm;
      }
    }
  }
  for(let i=1;i<=rows.length;i++)if(i===rows.length||rows[i][0]-rows[i-1][0]>1.8/hz||rows[i][0]<=rows[i-1][0]){add(i);segment=i;}
  return {count,hz:Array.from({length:a.length},(_,i)=>i*hz/n),
    acc:count?Array.from(a,v=>10*Math.log10(v/count+1e-20)):[],
    gyro:count?Array.from(g,v=>10*Math.log10(v/count+1e-20)):[]};
}
function shiftedFeatures(features,audio,rate,offset) {
  let j=0;
  return features.map(f=>{
    const lo=f[0]-.1,hi=f[0]+.1;let energy=0,coverage=0,bad=false;
    while(j<audio.length&&audio[j][0]+offset+audio[j][4]/rate/2<=lo)j++;
    for(let k=j;k<audio.length;k++){
      const r=audio[k],duration=r[4]/rate,start=r[0]+offset-duration/2;
      if(start>=hi)break;
      const overlap=Math.max(0,Math.min(hi,start+duration)-Math.max(lo,start));
      coverage+=overlap;energy+=overlap*r[1]*r[1];if(r[5])bad=true;
    }
    return [f[0],f[1],f[2],!bad&&coverage>=.16&&coverage<=.202?Math.sqrt(energy/coverage):null];
  });
}
function corrWindow(f,channel) {
  const lookup=new Map(f.map(r=>[Math.round(r[0]/.2-.5),r]));let best=null;
  for(let lag=-5;lag<=5;lag++){
    const p=[];for(const [k,r]of lookup){const b=lookup.get(k+lag);if(b&&r[3]!==null&&Number.isFinite(r[3]))p.push([r[3],b[channel]]);}
    if(p.length<20)continue;
    const mx=p.reduce((s,r)=>s+r[0],0)/p.length,my=p.reduce((s,r)=>s+r[1],0)/p.length;
    let vx=0,vy=0,cov=0;for(const [x,y]of p){vx+=(x-mx)**2;vy+=(y-my)**2;cov+=(x-mx)*(y-my);}
    if(vx<1e-18||vy<1e-18)continue;
    const r=cov/Math.sqrt(vx*vy);if(!best||Math.abs(r)>Math.abs(best.r))best={r,lag:lag*.2,n:p.length};
  }
  return best;
}
function timeToAudio(rows,t,rate,offset=0) {
  const row=nearest(rows,t-offset);if(!row)return null;
  const start=row[0]+offset-row[4]/rate/2;
  if(t<start-.001||t>start+row[4]/rate+.001)return null;
  return row[3]/rate+Math.max(0,Math.min(row[4]/rate,t-start));
}
function audioToTime(rows,seconds,rate,offset=0) {
  const frame=seconds*rate;let lo=0,hi=rows.length;
  while(lo<hi){const mid=(lo+hi)>>1;if(rows[mid][3]+rows[mid][4]<=frame)lo=mid+1;else hi=mid;}
  const r=rows[Math.min(lo,rows.length-1)];
  return r?r[0]+offset-r[4]/rate/2+(frame-r[3])/rate:null;
}
function speechIntervals(speech,offset=0){
  if(speech?.status!=='ready')return [];
  return (speech.segments||[]).filter(s=>Number.isFinite(s.start)&&Number.isFinite(s.end)&&s.end>s.start)
    .map((s,i)=>({...s,start:s.start+offset,end:s.end+offset,index:i}));
}
function speechRange(segment,bounds,padding=1){
  return [Math.max(bounds[0],segment.start-padding),Math.min(bounds[1],segment.end+padding)];
}
function rowsInSpeech(rows,segments){
  let j=0;return rows.filter(r=>{while(j<segments.length&&segments[j].end<r[0])j++;return j<segments.length&&r[0]>=segments[j].start&&r[0]<segments[j].end;});
}
function sessionTitle(id){
  const m=/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/.exec(id||'');
  return m?`${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]}`:(id||'尚未开始');
}
if(typeof module!=='undefined')module.exports={signalNames,median,nearest,clampRange,thinPoints,fftPower,rangeSpectrum,shiftedFeatures,corrWindow,timeToAudio,audioToTime,speechIntervals,speechRange,rowsInSpeech,sessionTitle};

if(typeof document!=='undefined'){
const $=id=>document.getElementById(id), COLORS=['#177657','#407bd1','#dd755b'];
const LABELS={unlabeled:'未标记',silence:'静默',self_speech:'自己说话',vowel:'自己长音啊',other_speech:'旁人说话',playback:'音箱播放',movement:'只动手',both:'同时说话'};
let latest=null,liveState=null,view=null,follow=!OFFLINE,reviewMode=!!OFFLINE,loadedPath=null;
let cursor=null,pinned=false,guide=null,guideTimer=null,localError='',computed=null,fixedY={};
let player=null,playerURL=null,playerSession=null,playEnd=null,playFrame=null,requestId=0;
let selectedSpeech=-1,loadingSession=false;
const enabled=new Set([1,2,3,4,5,6]), layouts={}, timeCharts=['acc','gyro','audio','energy'];
const fmt=(n,d=2)=>n===null||n===undefined||!Number.isFinite(n)?'—':n.toFixed(d);
const db=v=>v>0?20*Math.log10(v):null;
const audioOffset=()=>Number($('audioOffset').value||0)/1000;
async function api(path,data){const r=await fetch('/api/'+path,{method:data?'POST':'GET',headers:{'X-Lab-Token':TOKEN,...(data?{'Content-Type':'application/json'}:{})},body:data?JSON.stringify(data):undefined});const v=await r.json();if(!r.ok)throw Error(v.error||r.statusText);return v;}
function error(e){localError=e.message||String(e);$('error').textContent=localError;$('error').classList.remove('hidden');}
function limits(s=latest){const offset=audioOffset();return [Math.min(0,s?.imu?.[0]?.[0]||0,s?.audio?.length?s.audio[0][0]+offset-.01:0),Math.max(.1,s?.imu?.at(-1)?.[0]||0,s?.audio?.length?s.audio.at(-1)[0]+offset+.01:0)];}
function setView(a,b,speechIndex=-1){if(!latest||loadingSession)return;view=clampRange(a,b,...limits());selectedSpeech=speechIndex;follow=false;computed=null;pinned=false;stopAudio();paint();}
function allRange(){setView(...limits());}
function offsetView(factor,move=0){if(!view)return;const span=(view[1]-view[0])*factor,center=(view[0]+view[1])/2+move*(view[1]-view[0]);setView(center-span/2,center+span/2,move===0?selectedSpeech:-1);}

function plot(id,series,opts={}){
  const canvas=$(id),dpr=window.devicePixelRatio||1,w=canvas.clientWidth,h=canvas.clientHeight;
  canvas.width=w*dpr;canvas.height=h*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  const L=58,R=15,T=14,B=27,pw=w-L-R,ph=h-T-B,xmin=opts.xmin,xmax=opts.xmax;
  const selected=series.map(s=>({...s,points:s.points.filter(p=>p[0]>=xmin&&p[0]<=xmax&&p[1]!==null&&Number.isFinite(p[1]))}));
  let ymin=opts.ymin,ymax=opts.ymax;
  if(ymin===undefined){ymin=Infinity;ymax=-Infinity;for(const s of selected)for(const p of s.points){ymin=Math.min(ymin,p[1]);ymax=Math.max(ymax,p[1]);}
    if(!Number.isFinite(ymin)){ymin=0;ymax=1;}const pad=(ymax-ymin)*.1||.001;ymin-=pad;ymax+=pad;}
  if($('lockY').checked&&['acc','gyro'].includes(id)){if(!fixedY[id])fixedY[id]=[ymin,ymax];[ymin,ymax]=fixedY[id];}
  const X=x=>L+(x-xmin)/(xmax-xmin||1)*pw,Y=y=>T+ph-(y-ymin)/(ymax-ymin||1)*ph;
  layouts[id]={L,pw,xmin,xmax,X};ctx.font='10px sans-serif';
  if(opts.markers)for(let i=0;i<opts.markers.length;i++){
    const m=opts.markers[i],end=opts.markers[i+1]?.t??xmax;if(m.label==='unlabeled'||m.t>xmax||end<xmin)continue;
    ctx.fillStyle=m.label==='silence'?'#c8d5cb':m.label==='vowel'?'#ddba7c':'#b5aebf';
    const left=X(Math.max(xmin,m.t)),width=X(Math.min(end,xmax))-left;
    ctx.fillRect(left,T+ph-5,width,5);
    if(id==='overview'&&width>65){ctx.fillStyle='#627366';ctx.textAlign='left';ctx.fillText(LABELS[m.label]||m.label,left+3,T+ph-8);}
  }
  if(opts.speech)for(const s of opts.speech){
    if(s.start>xmax||s.end<xmin)continue;
    const left=X(Math.max(xmin,s.start)),right=X(Math.min(xmax,s.end));
    ctx.fillStyle='#dce9ff99';ctx.fillRect(left,T,right-left,ph-6);
    ctx.fillStyle='#6c8fd2';ctx.fillRect(left,T,right-left,3);
    if(right-left>28){ctx.textAlign='left';ctx.fillStyle='#486dae';ctx.fillText('S'+(s.index+1),left+3,T+14);}
  }
  for(let i=0;i<4;i++){const y=ymin+(ymax-ymin)*i/3;ctx.strokeStyle='#e4e9e3';ctx.beginPath();ctx.moveTo(L,Y(y));ctx.lineTo(w-R,Y(y));ctx.stroke();ctx.fillStyle='#77897d';ctx.textAlign='right';ctx.fillText(Math.abs(y)<.001&&y!==0?y.toExponential(1):y.toFixed(Math.abs(y)<.1?4:1),L-6,Y(y)+3);}
  for(let i=0;i<6;i++){const x=xmin+(xmax-xmin)*i/5;ctx.textAlign='center';ctx.fillStyle='#77897d';ctx.fillText(fmt(x,(xmax-xmin)<2?2:1)+(opts.unit||'s'),X(x),h-7);}
  ctx.save();ctx.beginPath();ctx.rect(L,T,pw,ph);ctx.clip();
  let any=false;
  for(const s of selected){ctx.strokeStyle=s.color;ctx.lineWidth=1.2;ctx.setLineDash(s.dash||[]);ctx.beginPath();let prev=null;
    // Split BEFORE visual reduction so a missing interval is never bridged.
    const segments=[];let segment=[];for(const p of s.points){if(prev!==null&&s.maxGap&&p[0]-prev>s.maxGap){segments.push(segment);segment=[];}segment.push(p);prev=p[0];}segments.push(segment);
    for(const seg of segments){let pen=false;for(const [x,y]of thinPoints(seg,Math.max(100,Math.floor(pw)))){if(pen)ctx.lineTo(X(x),Y(y));else ctx.moveTo(X(x),Y(y));pen=true;any=true;}}ctx.stroke();ctx.setLineDash([]);
  }
  if(opts.selected){ctx.fillStyle='#17765718';ctx.fillRect(X(opts.selected[0]),T,X(opts.selected[1])-X(opts.selected[0]),ph);ctx.strokeStyle='#177657';ctx.strokeRect(X(opts.selected[0]),T,X(opts.selected[1])-X(opts.selected[0]),ph);}
  if(opts.time&&cursor!==null&&cursor>=xmin&&cursor<=xmax){ctx.strokeStyle='#374c48';ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(X(cursor),T);ctx.lineTo(X(cursor),T+ph);ctx.stroke();ctx.setLineDash([]);}
  ctx.restore();if(!any){ctx.fillStyle='#8b9b91';ctx.textAlign='left';ctx.fillText(opts.empty||'此范围没有有效数据',L,T+ph/2);}
}

function compute(){
  if(computed)return computed;
  const [lo,hi]=view,rows=latest.imu.filter(r=>r[0]>=lo&&r[0]<=hi),rate=latest.config.audio_rate||16000,offset=audioOffset();
  const f=shiftedFeatures(latest.features||[],latest.audio||[],rate,offset).filter(r=>r[0]-.1>=lo&&r[0]+.1<=hi);
  const audio=(latest.audio||[]).map(r=>[r[0]+offset,...r.slice(1)]).filter(r=>r[0]>=lo&&r[0]<=hi);
  const segments=speechIntervals(latest.speech,offset),hz=latest.quality?.packet_hz||latest.config.hz,empty={count:0,hz:[],acc:[],gyro:[]};
  const psd=latest.quality?.timing_ok?rangeSpectrum(rows,hz):empty;
  const speechPsd=latest.quality?.timing_ok?rangeSpectrum(rowsInSpeech(rows,segments),hz):empty;
  computed={rows,f,audio,psd,speechPsd,segments};return computed;
}
function paintCursor(){
  const box=$('cursorValues');box.replaceChildren();
  if(cursor===null){box.textContent='移动到图上查看同一时刻的原始数值；点击可固定游标。';return;}
  const r=nearest(latest.imu,cursor),a=nearest(latest.audio,cursor-audioOffset()),valid=r&&Math.abs(r[0]-cursor)<1.8/latest.config.hz;
  const av=a&&Math.abs(a[0]+audioOffset()-cursor)<=a[4]/(latest.config.audio_rate||16000)/2+.001;
  const names=signalNames(latest.config.stream).axes;
  const candidate=speechIntervals(latest.speech,audioOffset()).find(s=>cursor>=s.start&&cursor<s.end);
  const labels=[`游标 ${fmt(cursor,3)} s`,candidate?`疑似语音 S${candidate.index+1}`:'未落在自动语音区间',...names.map((n,i)=>`${n} ${valid?fmt(r[i+1],6):'缺样'}`),`音量 ${av?(a[1]===0?'数字全零':fmt(db(a[1]),1)+' dBFS'):'缺样'}`,`峰值 ${av?fmt(a[2],5):'—'}`,`IMU 样本时刻 ${valid?fmt(r[0],4):'—'}`];
  if(latest.config.stream==='raw'&&valid&&r.length>=13)labels.push('原始 int16 ['+r.slice(7,13).map(v=>Number.isFinite(v)?Math.round(v):'—').join(', ')+']');
  for(const value of labels){const span=document.createElement('span');span.textContent=value;box.append(span);}
}
function paint(){
  if(!latest||!view)return;const {rows,f,audio,psd,speechPsd,segments}=compute(),visible=$('showSpeech').checked?segments:[],opts={xmin:view[0],xmax:view[1],markers:latest.markers||[],speech:visible,time:true};
  for(const [id,off]of [['acc',1],['gyro',4]]){
    const series=[];for(let c=off;c<off+3;c++){if(!enabled.has(c))continue;const mean=$('center').checked&&rows.length?rows.reduce((s,r)=>s+r[c],0)/rows.length:0;
      series.push({color:COLORS[(c-1)%3],maxGap:1.8/latest.config.hz,points:rows.map(r=>[r[0],r[c]-mean])});}plot(id,series,opts);
  }
  plot('audio',[{color:COLORS[2],maxGap:.031,points:audio.map(r=>[r[0],db(r[1])])},{color:'#d3b19d',maxGap:.031,points:audio.map(r=>[r[0],db(r[2])])}],{...opts,ymin:-100,ymax:0,empty:'无有效音频（缺失或数字全零）'});
  plot('energy',[1,2,3].map(c=>{const v=f.filter(r=>r[c]!==null),mean=v.reduce((s,r)=>s+r[c],0)/(v.length||1),std=Math.sqrt(v.reduce((s,r)=>s+(r[c]-mean)**2,0)/(v.length||1));return{color:COLORS[c-1],maxGap:.3,points:f.map(r=>[r[0],r[c]===null?null:std>1e-12?(r[c]-mean)/std:0])};}),opts);
  for(const [id,key,color]of [['accPsd','acc',COLORS[0]],['gyroPsd','gyro',COLORS[1]]]){
    const series=[{color,points:psd.hz.map((v,i)=>[v,psd[key][i]])}];
    if(visible.length&&speechPsd.count)series.push({color:'#8664bb',dash:[5,3],points:speechPsd.hz.map((v,i)=>[v,speechPsd[key][i]])});
    plot(id,series,{xmin:0,xmax:(latest.quality?.packet_hz||latest.config.hz)/2,unit:'Hz',empty:'区间需包含至少 256 个连续样本'});
  }
  const speechSeconds=segments.reduce((sum,s)=>sum+Math.max(0,Math.min(view[1],s.end)-Math.max(view[0],s.start)),0);
  $('accPeak').textContent=$('gyroPeak').textContent=`实线：选区整体 · ${psd.count} 窗；${visible.length?(speechPsd.count?`紫色虚线：语音 · ${speechPsd.count} 窗`:'语音段不足 256 个连续样本'):'语音标记已隐藏或无区间'}`;
  const range=limits();plot('overview',[{color:COLORS[0],maxGap:.3,points:(latest.features||[]).map(r=>[r[0],Math.log10(Math.max(r[1],1e-8))])}],{xmin:range[0],xmax:range[1],markers:latest.markers||[],speech:visible,selected:view,time:true});
  $('rangeStart').value=fmt(view[0],3);$('rangeEnd').value=fmt(view[1],3);$('rangeInfo').textContent=`所选 ${fmt(view[0])}–${fmt(view[1])} 秒 · 共 ${fmt(view[1]-view[0])} 秒 · 疑似语音 ${fmt(speechSeconds,1)} 秒`;
  $('speechSelect').value=selectedSpeech<0?'':String(selectedSpeech);
  document.querySelectorAll('#speechList button').forEach(b=>b.setAttribute('aria-pressed',String(Number(b.dataset.index)===selectedSpeech)));
  $('prevSpeech').disabled=!segments.length||selectedSpeech<=0;$('nextSpeech').disabled=!segments.length||selectedSpeech>=segments.length-1;
  const empty=!latest.imu.length&&!latest.audio.length;
  for(const id of ['allRange','zoomIn','zoomOut','prevRange','nextRange','applyRange','rangeStart','rangeEnd'])$(id).disabled=empty||loadingSession;
  $('followLive').disabled=!!OFFLINE||!liveState?.active;$('returnLive').disabled=!!OFFLINE||!liveState?.active;
  if(reviewMode){const level=median(audio.map(r=>r[1]));$('audioLevel').textContent=level===0?'数字全零':level===null?'—':fmt(db(level),1)+' dB';}
  const room=range[1]-range[0]-(view[1]-view[0]);$('panRange').value=room>0?1000*(view[0]-range[0])/room:0;$('panRange').disabled=room<=.001;
  const ca=corrWindow(f,1),cg=corrWindow(f,2),corr=c=>c?`r=${fmt(c.r)} / ${fmt(c.lag,1)} s`:'不可计算';
  $('correlation').textContent=`所选区间包络相关 · ${signalNames(latest.config.stream).acc} ${corr(ca)}；${signalNames(latest.config.stream).gyro} ${corr(cg)}。搜索 ±1 s 最大 |r|，不代表因果或佩戴者识别。`;
  rangeStats(rows,f,audio);paintCursor();
}
function table(target,headers,rows){const t=document.createElement('table'),h=t.createTHead().insertRow();for(const v of headers){const th=document.createElement('th');th.textContent=v;h.append(th);}const b=t.createTBody();for(const row of rows){const tr=b.insertRow();for(const v of row)tr.insertCell().textContent=v;}$(target).replaceChildren(t);}
function rangeStats(rows,f,audio){
  const names=signalNames(latest.config.stream).axes;
  const data=names.map((name,i)=>{const v=rows.map(r=>r[i+1]);if(!v.length)return[name,'—','—','—','—'];let min=Infinity,max=-Infinity,sum=0;for(const n of v){min=Math.min(min,n);max=Math.max(max,n);sum+=n;}const mean=sum/v.length,rms=Math.sqrt(v.reduce((s,n)=>s+(n-mean)**2,0)/v.length);return[name,fmt(mean,6),fmt(min,6),fmt(max,6),fmt(rms,6)];});
  table('rangeStats',['通道','均值','最小值','最大值','去所选区间均值 RMS'],data);
  const note=document.createElement('p');note.className='small';const ar=median(audio.map(r=>r[1]));
  note.textContent=`200 ms 窗口 RMS 中位数：${signalNames(latest.config.stream).acc} ${fmt(median(f.map(r=>r[1])),6)}；${signalNames(latest.config.stream).gyro} ${fmt(median(f.map(r=>r[2])),6)}。音频 ${ar===0?'数字全零':fmt(db(ar),1)+' dBFS'}。区间统计使用全部选中样本；它与全程条件统计的边界规则不同。`;$('rangeStats').append(note);
}
function renderSpeech(){
  const segments=speechIntervals(latest.speech,audioOffset()),detector=latest.speech;
  const signature=JSON.stringify([latest.path,detector,audioOffset()]);
  if($('speechList').dataset.signature===signature)return;
  $('speechList').dataset.signature=signature;$('speechList').replaceChildren();
  $('speechSelect').replaceChildren(new Option(segments.length?'选择一段语音查看细节':'暂无语音区间',''));
  for(const s of segments){
    const label=`S${s.index+1} · ${fmt(s.start)}–${fmt(s.end)} 秒`;
    $('speechSelect').append(new Option(label,String(s.index)));
    const b=document.createElement('button');b.className='speech-chip';b.dataset.index=s.index;b.textContent=label;b.setAttribute('aria-pressed','false');b.onclick=()=>focusSpeech(s.index);$('speechList').append(b);
  }
  $('speechSelect').disabled=!segments.length;$('speechPadding').disabled=!segments.length;
  const total=segments.reduce((sum,s)=>sum+s.end-s.start,0);
  $('speechSummary').textContent=detector?.status==='ready'?(segments.length?`全程检测到 ${segments.length} 段，共 ${fmt(total,1)} 秒。点下面任一段，所有图同步跳转。`:'未检测到满足条件的语音区间，仍可拖选、播放或手动输入时间。'):
    detector?.reason||(latest.active?'采集进行中。停止保存后，会自动检测完整戒指录音并标出区间。':'打开已保存的戒指录音后，自动检测说话区间。');
  $('speechMethod').textContent=detector?.status==='ready'?`本地 WebRTC VAD · 20 ms 音频帧 · 音量门限 ${fmt(detector.gate_dbfs,1)} dBFS。只用录音判断；不区分本人、旁人，动作声可能误检。蓝色区间需播放核对，人工标签不被覆盖。`:'只根据戒指录音检测，不使用 IMU。没有自动标记不等于没有说话。';
}
function focusSpeech(index){
  const s=speechIntervals(latest?.speech,audioOffset())[index];if(!s)return;
  setView(...speechRange(s,limits(),Number($('speechPadding').value)),index);
}
function render(s){
  s={...s,imu:s.imu||[],audio:s.audio||[],features:s.features||[]};
  latest=s;computed=null;
  if(!view){view=limits(s);cursor=null;}
  if(s.review&&loadedPath!==s.path){view=limits(s);loadedPath=s.path;follow=false;cursor=null;selectedSpeech=-1;fixedY={};}
  if(follow){const [min,max]=limits(s);view=[Math.max(min,max-15),max];}else view=clampRange(...view,...limits(s));
  $('status').textContent=reviewMode?'回看已保存实验':s.status;$('demoBanner').classList.toggle('hidden',!s.demo);
  $('deliveryLabel').textContent=reviewMode?'全程有效送达 Hz':'当前缓存有效送达 Hz';
  $('audioLevelLabel').textContent=reviewMode?'所选音量中位数 · dBFS':'实时音量 RMS · dBFS';$('pause').disabled=reviewMode;
  const sid=s.session_id||s.path?.split('/').at(-1);
  $('viewTitle').textContent=sid?sessionTitle(sid):'等待选择记录';
  $('viewMode').textContent=reviewMode?'历史回看':s.active?'正在采集':'暂无数据';
  $('viewMeta').textContent=sid?`${s.config.stream==='raw'?'原始六轴':'历史 token'} · ${s.config.mic==='ring'?'戒指录音':s.demo?'合成演示':'无戒指录音'} · ${fmt(s.elapsed||0,1)} 秒 · ${(s.count||0).toLocaleString()} 个样本${s.config.note?' · '+s.config.note:''}`:'从上方选择历史实验，或展开“采集新实验”。';
  $('error').textContent=s.error||localError;$('error').classList.toggle('hidden',!s.error&&!localError);
  const token=s.config.stream==='token',aName=token?'T1–T3':'加速度',gName=token?'T4–T6':'陀螺仪';
  $('tokenBanner').classList.toggle('hidden',!token);$('accTitle').textContent=aName+' · '+(token?'token 单位':'m/s²');$('gyroTitle').textContent=gName+' · '+(token?'token 单位':'°/s');$('accPsdTitle').textContent=aName+' 所选区间功率谱';$('gyroPsdTitle').textContent=gName+' 所选区间功率谱';$('energyA').textContent=aName;$('energyG').textContent=gName;
  $('axisLegend').textContent=token?'T1 / T2 / T3':'X / Y / Z';$('streamNote').textContent=token?'历史 token 流':'raw int16 · 新 SDK';
  signalNames(s.config.stream).axes.forEach((name,i)=>$('channelName'+(i+1)).textContent=name);
  $('accPsdTitle').textContent=aName+' 功率谱 · dB ['+(token?'token²':'(m/s²)²')+'/Hz]';
  $('gyroPsdTitle').textContent=gName+' 功率谱 · dB ['+(token?'token²':'(°/s)²')+'/Hz]';
  const active=!!liveState?.active,ready=!reviewMode&&['采集中','演示数据采集中'].includes(s.status);
  $('start').disabled=active;$('stop').disabled=!active;$('guide').disabled=!ready||(!s.demo&&s.config.mic==='ring'&&!s.audio_health?.ready);
  document.querySelectorAll('.config input,.config select,#demo').forEach(e=>e.disabled=active);$('note').disabled=false;
  for(const id of ['hz','accel_fs','gyro_fs','chip'])$(id).disabled=true;
  if(!active&&guide)cancelGuide();document.querySelectorAll('#labels button').forEach(b=>{b.disabled=!ready||!!guide;b.classList.toggle('selected',b.dataset.label===s.markers?.at(-1)?.label);});
  const q=s.quality||{},stats=s.sdk_stats?.sample_count!==undefined?s.sdk_stats:s.summary?.metadata?.sdk_stats;
  $('rate').textContent=`${fmt(q.packet_hz,1)} / ${s.config.hz}`;$('delivery').textContent=fmt(q.delivered_hz,1);$('duration').textContent=fmt(s.elapsed||0,1)+' s';$('count').textContent=(s.count||0).toLocaleString()+' samples';
  $('loss').textContent=stats?`${stats.estimated_lost_samples/10} / ${stats.invalid_packet_count+stats.dropped_packet_count}`:'—';$('gaps').textContent=`时间间断 ${q.gaps||0} · ${token?'token 无原始 LSB':''}`;
  const a=s.audio||[],level=a.at(-1)?.[1];$('audioLevel').textContent=level===0?'数字全零':level===undefined?'—':fmt(db(level),1)+' dB';
  const source=s.demo?'演示':s.config.mic==='ring'?'戒指麦克风':s.config.mic==='none'?'未采集音频':'历史电脑麦克风';
  $('audioHealth').textContent=`${source} · ${s.audio_frames||0} frames · 缺音频块 ${s.audio_health?.missing_blocks??'—'}`;
  const warnings=[];if(a.length>=100&&a.slice(-100).every(r=>r[1]===0))warnings.push('音频为数字全零，不能作为说话参照。');if(!s.demo&&!['ring','none'].includes(s.config.mic))warnings.push('这是旧版电脑麦克风记录，不是戒指录音。');if(s.count&&!q.timing_ok)warnings.push('采样速率检查未通过，暂不计算频谱。');if(q.gaps)warnings.push('存在 IMU 时间缺口，频谱不跨缺口计算。');
  $('qualityWarning').textContent=warnings.join(' ');$('qualityWarning').classList.toggle('hidden',!warnings.length);
  $('timing').textContent=s.timing||'';$('hardware').textContent=`${s.hardware?.info||''} · ${source} · ${s.hardware?.sdk_path||''}`;
  $('savedPath').textContent=s.path?`保存：${s.path} · 完整 CSV、WAV 与可离线缩放的 report.html`:'';
  const conditions=s.summary?.conditions||[];if(conditions.length){table('conditions',['条件','有效秒数',aName+' RMS','/ 静默',gName+' RMS','/ 静默','原始音频 dBFS'],conditions.map(c=>[LABELS[c.label]||c.label,fmt(c.seconds,1),fmt(c.acc_rms,5),fmt(c.acc_ratio)+'×',fmt(c.gyro_rms,5),fmt(c.gyro_ratio)+'×',c.audio_dbfs<=-180?'数字全零':fmt(c.audio_dbfs,1)]));$('conditions').classList.remove('empty');}
  else{$('conditions').textContent=reviewMode?'这条记录没有人工条件标签。可使用上方自动语音区间跳转，或手动选择时间对照。':'停止并保存后显示全程条件对比。';}
  const signature=JSON.stringify(s.markers||[]);if($('segmentSelect').dataset.signature!==signature){$('segmentSelect').dataset.signature=signature;$('segmentSelect').replaceChildren(new Option('选择条件片段',''));(s.markers||[]).forEach((m,i)=>{if(m.label!=='unlabeled')$('segmentSelect').append(new Option(`${fmt(m.t,1)}s · ${LABELS[m.label]}`,String(i)));});}
  $('playRange').disabled=!reviewMode||!a.length;renderSpeech();paint();
}

async function refreshSessions(){if(OFFLINE)return;const v=await api('sessions'),selected=$('sessions').value;$('sessions').replaceChildren();for(const s of v.sessions){$('sessions').append(new Option(`${sessionTitle(s.id)} · ${s.synthetic?'演示':s.stream==='raw'?'原始六轴':'历史 token'} · ${s.mic==='ring'?'戒指录音':s.synthetic?'合成音频':'无戒指录音'} · ${s.samples.toLocaleString()} 样本`,s.id));}if(!v.sessions.length)$('sessions').append(new Option('暂无已保存记录',''));if([...$('sessions').options].some(o=>o.value===selected))$('sessions').value=selected;$('loadSession').disabled=!v.sessions.length;}
async function loadSession(id){
  if(!id)return;const req=++requestId;loadingSession=true;stopAudio();$('status').textContent='正在加载完整记录并检测语音…';
  try{const s=await api('review?session='+encodeURIComponent(id));if(req!==requestId)return;
    loadingSession=false;reviewMode=true;view=null;loadedPath=null;localError='';$('pause').checked=false;$('sessions').value=id;$('captureDetails').open=false;
    $('audioOffset').value=0;selectedSpeech=-1;pinned=false;
    history.replaceState(null,'','#session='+encodeURIComponent(id));render(s);
  }finally{if(req===requestId){loadingSession=false;if(latest){$('status').textContent=reviewMode?'回看已保存实验':latest.status;paint();}}}
}
function cancelGuide(){clearTimeout(guideTimer);guide=null;$('guide').textContent='开始 65 秒引导实验';$('guideText').textContent='先静默 10 秒 → 说话 → 静默 → 长音“啊” → 静默';}
async function mark(label){await api('mark',{label,note:$('note').value});}
async function guideStep(){
  if(!guide||!liveState?.active)return cancelGuide();if(guide.index>=guide.steps.length){cancelGuide();await api('stop',{});return;}
  const label=guide.steps[guide.index];let remaining=3;await mark('unlabeled');$('guideText').textContent=`准备：${LABELS[label]} · ${remaining} 秒后开始`;
  const prep=async()=>{if(!guide)return;remaining--;if(remaining>0){$('guideText').textContent=`准备：${LABELS[label]} · ${remaining} 秒后开始`;guideTimer=setTimeout(()=>prep().catch(error),1000);return;}
    await mark(label);remaining=10;const tick=()=>{if(!guide)return;$('guideText').textContent=`${LABELS[label]} · 还剩 ${remaining} 秒`;if(remaining--<=0){guide.index++;guideStep().catch(error);}else guideTimer=setTimeout(tick,1000);};tick();};guideTimer=setTimeout(()=>prep().catch(error),1000);
}
function stopAudio(){if(player)player.pause();if(playFrame)cancelAnimationFrame(playFrame);playFrame=null;playEnd=null;$('playStatus').textContent='';}
async function playRange(){
  stopAudio();const rate=latest.config.audio_rate||16000,offset=audioOffset(),rows=latest.audio;
  // Choose the first available block within the selection; gaps stay explicit.
  let start=timeToAudio(rows,view[0],rate,offset);if(start===null){const row=rows.find(r=>r[0]+offset>=view[0]&&r[0]+offset<=view[1]);if(!row)throw Error('选中范围没有音频');start=row[3]/rate;}
  if(playerSession!==latest.path){if(playerURL)URL.revokeObjectURL(playerURL);playerURL=null;
    let url='audio.wav';if(!OFFLINE){const response=await fetch('/api/wav?session='+encodeURIComponent(latest.session_id),{headers:{'X-Lab-Token':TOKEN}});if(!response.ok)throw Error('读取音频失败');url=playerURL=URL.createObjectURL(await response.blob());}
    player=new Audio(url);await new Promise((resolve,reject)=>{player.addEventListener('loadedmetadata',resolve,{once:true});player.addEventListener('error',()=>reject(Error('读取 WAV 失败，请将 report.html 与 audio.wav 放在同一目录。')),{once:true});});playerSession=latest.path;
  }
  player.currentTime=start;playEnd=view[1];pinned=true;await player.play();
  function tick(){if(player.paused||playEnd===null)return;cursor=audioToTime(rows,player.currentTime,rate,offset);if(cursor>=playEnd){stopAudio();return;}paint();$('playStatus').textContent=`播放 ${fmt(cursor,2)} s`;playFrame=requestAnimationFrame(tick);}
  playFrame=requestAnimationFrame(tick);
}

Object.entries(LABELS).forEach(([key,text],i)=>{const b=document.createElement('button');b.textContent=`${i} ${text}`;b.dataset.label=key;b.disabled=true;b.onclick=()=>mark(key).catch(error);$('labels').append(b);});
for(let c=1;c<=6;c++){const label=document.createElement('label');label.className='check';label.style.color=COLORS[(c-1)%3];const box=document.createElement('input');box.type='checkbox';box.checked=true;box.onchange=()=>{box.checked?enabled.add(c):enabled.delete(c);fixedY={};paint();};const name=document.createElement('span');name.id='channelName'+c;name.textContent=signalNames('raw').axes[c-1];label.append(box,name);$(c<4?'channelsA':'channelsG').append(label);}
for(const id of [...timeCharts,'overview']){
  const canvas=$(id);let drag=null;
  function t(e){const l=layouts[id];return l?Math.max(l.xmin,Math.min(l.xmax,l.xmin+(e.clientX-canvas.getBoundingClientRect().left-l.L)/l.pw*(l.xmax-l.xmin))):0;}
  canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,t:t(e)};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(!latest||!layouts[id]||pinned&&!drag)return;cursor=t(e);paint();});
  canvas.addEventListener('pointerup',e=>{if(!drag)return;const a=drag;drag=null;if(Math.abs(e.clientX-a.x)>6)setView(Math.min(a.t,t(e)),Math.max(a.t,t(e)));else{cursor=t(e);pinned=!pinned;paint();}});
  canvas.addEventListener('pointercancel',()=>{drag=null;});canvas.addEventListener('dblclick',allRange);
}
$('allRange').onclick=allRange;$('zoomIn').onclick=()=>offsetView(.5);$('zoomOut').onclick=()=>offsetView(2);$('prevRange').onclick=()=>offsetView(1,-.8);$('nextRange').onclick=()=>offsetView(1,.8);
$('applyRange').onclick=()=>{const a=Number($('rangeStart').value),b=Number($('rangeEnd').value);if(!Number.isFinite(a)||!Number.isFinite(b)||b<=a)return error('终点必须大于起点');setView(a,b);};
$('panRange').oninput=()=>{const [min,max]=limits(),span=view[1]-view[0],a=min+(max-min-span)*Number($('panRange').value)/1000;setView(a,a+span);};
$('followLive').onclick=()=>{if(reviewMode&&!OFFLINE){reviewMode=false;requestId++;loadingSession=false;latest=liveState;}selectedSpeech=-1;follow=true;view=null;stopAudio();history.replaceState(null,'',location.pathname);if(latest)render(latest);};
$('segmentSelect').onchange=()=>{const i=Number($('segmentSelect').value);if($('segmentSelect').value==='')return;setView(latest.markers[i].t,latest.markers[i+1]?.t??limits()[1]);};
function updateAudioOffset(){let n=Number($('audioOffset').value);if(!Number.isFinite(n))n=0;$('audioOffset').value=Math.max(-1000,Math.min(1000,n));computed=null;stopAudio();if(latest){renderSpeech();paint();}}
$('audioOffset').onchange=updateAudioOffset;
$('audioOffset').oninput=()=>{if(Number.isFinite($('audioOffset').valueAsNumber))updateAudioOffset();};
$('showSpeech').onchange=paint;
$('speechSelect').onchange=()=>{if($('speechSelect').value!=='')focusSpeech(Number($('speechSelect').value));};
$('prevSpeech').onclick=()=>focusSpeech(selectedSpeech-1);$('nextSpeech').onclick=()=>focusSpeech(selectedSpeech+1);
$('speechPadding').onchange=()=>{if(selectedSpeech>=0)focusSpeech(selectedSpeech);};
$('playRange').onclick=()=>playRange().catch(error);$('stopAudio').onclick=stopAudio;
$('loadSession').onclick=()=>loadSession($('sessions').value).catch(error);
$('sessions').onchange=()=>loadSession($('sessions').value).catch(error);
$('refreshSessions').onclick=()=>refreshSessions().catch(error);
$('returnLive').onclick=()=>{reviewMode=false;requestId++;loadingSession=false;follow=true;view=null;selectedSpeech=-1;stopAudio();history.replaceState(null,'',location.pathname);if(liveState)render(liveState);};
$('guide').onclick=()=>{if(guide)return cancelGuide();guide={steps:['silence','self_speech','silence','vowel','silence'],index:0};$('guide').textContent='取消引导（继续采集）';guideStep().catch(error);};
$('hz').value='200';$('accel_fs').value='16';$('gyro_fs').value='2000';for(const id of ['hz','accel_fs','gyro_fs','chip'])$(id).disabled=true;
$('start').onclick=async()=>{try{localError='';fixedY={};const c={demo:$('demo').checked,name:$('name').value,chip:$('chip').value,stream:$('stream').value,hz:+$('hz').value,accel_fs:+$('accel_fs').value,gyro_fs:+$('gyro_fs').value,mic:$('mic').value,audio_rate:16000,note:$('note').value};await api('start',c);reviewMode=false;requestId++;view=null;follow=true;loadedPath=null;}catch(e){error(e)}};
$('stop').onclick=async()=>{cancelGuide();try{await api('stop',{});}catch(e){error(e)}};
for(const id of ['lockY','center'])$(id).onchange=()=>{fixedY={};paint();};
$('pause').onchange=()=>{if(!$('pause').checked&&latest)paint();};
document.addEventListener('keydown',e=>{if(/INPUT|SELECT|TEXTAREA/.test(e.target.tagName)||e.metaKey||e.ctrlKey||!liveState?.active||guide||reviewMode)return;if(/^[0-7]$/.test(e.key)){e.preventDefault();mark(Object.keys(LABELS)[+e.key]).catch(error);}});
window.addEventListener('resize',()=>{if(latest)paint();});
if(OFFLINE){document.querySelectorAll('.offline-hide').forEach(e=>e.classList.add('hidden'));render(OFFLINE);$('followLive').disabled=true;}
else{
  async function poll(){try{const s=await api('state');const wasActive=liveState?.active;liveState=s;if(!reviewMode&&!loadingSession&&!$('pause').checked)render(s);
    $('start').disabled=!!s.active;$('stop').disabled=!s.active;$('returnLive').disabled=!s.active;$('followLive').disabled=!s.active;
    if(!s.active&&s.path&&s.summary&&wasActive){await refreshSessions();if(!reviewMode&&!loadingSession)await loadSession(s.path.split('/').at(-1));}
  }catch(e){error(e)}finally{setTimeout(poll,liveState?.active?400:1200);}}
  async function init(){try{
    liveState=await api('state');render(liveState);await refreshSessions();
    const saved=new URLSearchParams(location.hash.slice(1)).get('session');
    if(saved&&[...$('sessions').options].some(o=>o.value===saved))await loadSession(saved);
    else if(!liveState.active&&$('sessions').value)await loadSession($('sessions').value);
    else $('captureDetails').open=true;
  }catch(e){error(e)}finally{poll();}}
  init();
}
}
