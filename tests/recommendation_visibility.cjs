// Synthetic DOM contract test: no browser, real desktop or user report is read.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
  constructor(tag){this.tag=tag;this.children=[];this.disabled=false;this.hidden=false;this.textContent='';}
  append(...els){this.children.push(...els);}
  replaceChildren(...els){this.children=els;}
}
const elements=Object.fromEntries(['operation-panel','op-recommend','op-message','op-items','op-scope','op-app','op-limit'].map(id=>[id,new Element(id)]));
elements['op-scope'].value='user';elements['op-app'].value='';elements['op-limit'].value='4';
let focused=true, observer, timers=new Map(),nextTimer=0, requests=[],exposures=0;
const events={};
const document={currentScript:{src:'http://localhost/token/operations.js'},visibilityState:'visible',hasFocus:()=>focused,
  getElementById:id=>elements[id],createElement:tag=>new Element(tag),addEventListener:(type,fn)=>events[type]=fn};
const items=[1,2,3,4].map(n=>({operation_id:'op'+n,workflow_revision:'r'+n,name:'操作'+n,score:1/n,reasons:['来源已记录'],
  missing_parameters:[],statistics:{used_level:'同应用'},feedback:{exposures:0},features:{bias:1}}));
const sandbox={document,window:{addEventListener:(type,fn)=>events[type]=fn},URL,
  setTimeout:(fn,ms)=>{assert.equal(ms,500);const id=++nextTimer;timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),
  IntersectionObserver:class{constructor(cb){this.cb=cb;this.targets=[];observer=this;}observe(t){this.targets.push(t);}disconnect(){this.disconnected=true;}},
  fetch:async(url,options)=>{const kind=String(url).split('/').at(-1),payload=JSON.parse(options.body);requests.push({kind,payload});
    let result;
    if(kind==='recommend')result={request_id:'request'+requests.length,items,candidate_count:4,explanation:'统计',context:{app:null,local_hour:null,missing:[]}};
    else if(kind==='exposure'){exposures++;result={exposure_id:'exposure'+exposures};}
    else if(kind==='feedback')result={message:'已记录',selection:{workflow_revision:'r1',missing_parameters:[]}};
    else throw Error(kind);
    return {ok:true,json:async()=>result};}
};
vm.runInNewContext(fs.readFileSync(process.argv[2],'utf8'),sandbox);
async function tick(){const jobs=[...timers.values()];timers.clear();for(const fn of jobs)await fn();}
function show(index){observer.cb([{target:observer.targets[index],isIntersecting:true,intersectionRatio:1}]);}
(async()=>{
  await elements['op-recommend'].onclick();assert.equal(exposures,0);assert.equal(requests[0].payload.limit,4);
  document.visibilityState='hidden';show(0);assert.equal(timers.size,0);
  document.visibilityState='visible';events.visibilitychange();assert.equal(timers.size,1);
  focused=false;events.blur();await tick();assert.equal(exposures,0);
  focused=true;events.focus();await tick();assert.equal(exposures,1);
  show(0);await tick();assert.equal(exposures,1); // same visible request is idempotent
  const first=elements['op-items'].children[0];const choose=first.children.find(e=>e.textContent==='选择此操作');
  assert.equal(choose.disabled,false);await choose.onclick();
  assert.equal(requests.at(-1).payload.exposure_id,'exposure1');assert.equal(requests.at(-1).payload.kind,'accepted');
  show(1);assert.equal(timers.size,1); // starts dwell for second candidate
  await elements['op-recommend'].onclick();await tick();assert.equal(exposures,1); // refresh cancelled old timer
  assert.equal(elements['op-items'].children.length,4);
  console.log('visibility, focus, dwell, refresh cancellation and feedback association passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
