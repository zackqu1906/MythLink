/* Transport scheduling only; no browser, profile or network required. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

async function check(commandWait) {
  const sent = [], delays = [], executed = [];
  let commands = 0;
  const event = {addListener() {}};
  const box = {
    MYTHLINK_BROWSER: 'safari', crypto: {randomUUID: () => 'session-test'},
    setTimeout: (_, delay) => {delays.push(delay); return delays.length;}, clearTimeout() {},
    browser: {
      runtime: {onInstalled: event, onMessage: event,
        async sendNativeMessage(_, data) {
          sent.push(data);
          if (data.path === '/connect') return {token:'token', command_wait:commandWait};
          return {commands: commands < 6 ? [{id:++commands, operation:'snapshot'}] : []};
        }},
      permissions: {onAdded:event}, tabs:{onActivated:event}, windows:{onFocusChanged:event}
    },
    fakeCollect: async () => ({focused:true, authorized:true, scene:'video', reason:'recognized'}),
    fakeExecute: async command => {executed.push(command.id);return {id:command.id,ok:true};}
  };
  vm.createContext(box);
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../src/proximic_ring/browser_bridge/extension/background.js'),'utf8'),box);
  vm.runInContext('collect = fakeCollect; execute = fakeExecute;',box);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(executed, [1,2,3,4,5,6], 'Every command removed from broker must be executed/ACKed');
  assert.equal(sent.filter(x=>x.replies).flatMap(x=>x.replies).length,6);
  assert.ok(sent.filter(x=>x.path==='/exchange').every(x=>x.wait_ms===(commandWait?300:0)));
  assert.deepEqual(delays,[commandWait?0:300], 'Older desktop versions must retain their polling delay');
}
async function unavailablePage(mode) {
  const sent = [], delays = [];
  const event = {addListener() {}};
  const box = {
    MYTHLINK_BROWSER:'safari',crypto:{randomUUID:()=> 'session-test'},
    setTimeout:(_,delay)=>{delays.push(delay);return delays.length;},clearTimeout(){},
    browser:{runtime:{onInstalled:event,onMessage:event,
      async sendNativeMessage(_,data){sent.push(data);return data.path==='/connect'?{token:'token',command_wait:true}:{commands:[]};}},
      permissions:{onAdded:event,contains:async()=>{throw Error('page permission query failed');}},
      tabs:{onActivated:event,query:async()=>[{id:1,url:'https://example.com'}]},
      windows:{onFocusChanged:event,getLastFocused:async()=>{if(mode==='no-window')throw Error('no windows');return {id:1,focused:true};}}
    }
  };
  vm.createContext(box);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../src/proximic_ring/browser_bridge/extension/background.js'),'utf8'),box);
  await new Promise(resolve=>setImmediate(resolve));
  const snapshot=sent.find(x=>x.path==='/exchange')?.snapshot;
  assert.ok(snapshot,'Even without a readable page the native heartbeat must continue');
  assert.equal(snapshot.authorized,false);assert.equal(snapshot.scene,'');
  assert.equal(snapshot.reason,'browser_page_unavailable');
  assert.equal(vm.runInContext('status.connected',box),true);
  assert.deepEqual(delays,[0]);
}
(async () => {
  await check(true);
  await check(false);
  await unavailablePage("no-window");
  await unavailablePage("permission-failure");
  console.log('Browser transport: wakeable command delivery, >4 command rounds, legacy pacing passed');
})().catch(error => {console.error(error);process.exitCode=1;});
