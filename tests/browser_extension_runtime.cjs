/* Run with NODE_PATH pointing to a Playwright installation. Isolated browsers;
 * no user profile, website, account, media bytes or browser settings are used. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const http = require('node:http');
const os = require('node:os');
const {chromium, webkit} = require('playwright');
const root = path.resolve(__dirname, '..');
const source = path.join(root, 'src/proximic_ring/browser_bridge/extension');
const core = fs.readFileSync(path.join(source, 'core.js'), 'utf8');
const content = fs.readFileSync(path.join(source, 'content.js'), 'utf8');

async function main() {
  const box = {}; vm.createContext(box); vm.runInContext(core, box);
  const candidate = {media:'a', kind:'video', loaded:true, visible:true, area:100, playing:false};
  assert.equal(box.MythlinkMedia.select([candidate]).player.media, 'a');
  assert.equal(box.MythlinkMedia.select([candidate, {...candidate,media:'b'}]).reason, 'multiple_players');
  assert.equal(box.MythlinkMedia.select([candidate, {...candidate,media:'b',interacted:1}]).player.media, 'b');
  assert.equal(box.MythlinkMedia.select([{...candidate,loaded:false}]).reason,'no_web_player');
  assert.equal(box.MythlinkMedia.select([{...candidate,kind:'audio',visible:false}]).player.media,'a');
  assert.equal(box.MythlinkMedia.select([{...candidate,visible:false}]).reason,'no_web_player');
  const wav = Buffer.alloc(44 + 16000 * 16);
  wav.write('RIFF',0); wav.writeUInt32LE(wav.length - 8,4); wav.write('WAVEfmt ',8);
  wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);
  wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(wav.length-44,40);
  const server = http.createServer((req,res) => {
    if (req.url.startsWith('/tone.wav')) {
      const range = /bytes=(\d+)-(\d*)/.exec(req.headers.range || '');
      const start = range ? Number(range[1]) : 0, end = range?.[2] ? Math.min(Number(range[2]),wav.length-1) : wav.length-1;
      res.writeHead(range ? 206 : 200, {'Content-Type':'audio/wav','Content-Length':end-start+1,'Accept-Ranges':'bytes',
        ...(range ? {'Content-Range':`bytes ${start}-${end}/${wav.length}`} : {})});res.end(wav.subarray(start,end+1));
    }
    else {res.writeHead(200,{'Content-Type':'text/html'});res.end('<!doctype html><meta charset=utf-8><title>Local media test</title><input id=editor><div id=host></div>');}
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${server.address().port}`;
  let count = 6;
  try {
    for (const [name, engine] of [['Chromium',chromium],['WebKit',webkit]]) {
      const browser = await engine.launch({headless:true});
      try {
        const page = await browser.newPage();
        await page.addInitScript(() => {
          window.chrome = {runtime:{id:'test',onMessage:{addListener: listener => {window.listener=listener;}}}};
          window.probe = () => new Promise(resolve => window.listener({type:'probe'}, {id:'test'}, resolve));
          window.command = message => new Promise(resolve => window.listener(message, {id:'test'}, resolve));
        });
        await page.goto(origin);
        await page.addScriptTag({content});
        await page.evaluate(() => {
          const video = document.createElement('video');video.id='media';video.controls=true;video.muted=true;
          video.style.cssText='width:640px;height:360px';video.src='/tone.wav';document.body.append(video);
        });
        await page.waitForFunction(() => document.querySelector('video').readyState>=1);
        let first = await page.evaluate(() => probe());
        assert.equal(first.media.length,1);assert.equal(first.media[0].loaded,true);count++;
        const command = async action => {
          const s = await page.evaluate(() => probe());const media=s.media[0];
          return page.evaluate(m => window.command(m),{type:'action',action,expires:Date.now()+1000,framePage:s.page,
            target:{document:s.document+":"+s.page,media:media.media,source:media.source}});
        };
        assert.equal((await command('play')).ok,true);
        assert.equal(await page.evaluate(() => document.querySelector('video').paused),false);count++;
        assert.equal((await command('play')).ok,true);
        assert.equal(await page.evaluate(() => document.querySelector('video').paused),true);count++;
        await page.evaluate(() => {document.querySelector('video').play = () => Promise.reject(new DOMException('Blocked by browser policy','NotAllowedError'));});
        assert.equal((await command('play')).reason,'browser_playback_permission_required');count++;
        await page.evaluate(() => {delete document.querySelector('video').play;});
        await page.waitForFunction(() => {const m=document.querySelector('video');return m.seekable.length && m.seekable.end(0)>12;});
        assert.equal((await command('forward')).ok,true);
        await page.waitForFunction(() => !document.querySelector('video').seeking);
        assert.ok(await page.evaluate(() => document.querySelector('video').currentTime>=9));count++;
        assert.equal((await command('backward')).ok,true);
        await page.waitForFunction(() => !document.querySelector('video').seeking);
        assert.ok(await page.evaluate(() => document.querySelector('video').currentTime<1));count++;
        assert.equal((await command('volume-down')).ok,true);
        assert.ok(await page.evaluate(() => Math.abs(document.querySelector('video').volume-.9)<.01));count++;
        await page.locator('#editor').focus();
        assert.equal((await page.evaluate(() => probe())).input_context,'text');
        assert.equal((await command('play')).reason,'text_focus');count++;
        await page.locator('#editor').evaluate(el => el.blur());
        await page.evaluate(() => {
          const host=document.createElement('private-editor');document.body.append(host);
          const input=document.createElement('input');host.attachShadow({mode:'closed'}).append(input);input.focus();
        });
        assert.equal((await page.evaluate(() => probe())).input_context,'unknown');count++;
        await page.evaluate(() => document.querySelector('private-editor').remove());
        const paused = await page.evaluate(() => probe());
        await page.evaluate(() => history.pushState({},'', '/different'));
        const changed = await page.evaluate(() => probe());
        assert.notEqual(paused.page,changed.page);
        assert.equal((await page.evaluate(m => window.command(m),{type:'action',action:'play',expires:Date.now()+1000,framePage:paused.page,
          target:{document:paused.document+":"+paused.page,media:paused.media[0].media,source:paused.media[0].source}})).reason,'page_changed');count++;
        const before = await page.evaluate(() => probe());
        await page.evaluate(() => {document.querySelector('video').src='/tone.wav?second';});
        await page.waitForFunction(() => document.querySelector('video').readyState>=1);
        const after = await page.evaluate(() => probe());assert.notEqual(before.media[0].source,after.media[0].source);count++;
        await page.evaluate(() => {
          const host=document.querySelector('#host');const shadow=host.attachShadow({mode:'open'});
          const audio=document.createElement('audio');audio.src='/tone.wav';audio.preload='auto';shadow.append(audio);
        });
        let shadow;
        for(let i=0;i<15;i++){shadow=await page.evaluate(() => probe());if(shadow.media.length===2)break;await page.waitForTimeout(200);}
        assert.equal(shadow.media.length,2);assert.equal(shadow.media[1].kind,'audio');count++;
        await page.evaluate(() => document.querySelector('video').remove());
        assert.equal((await page.evaluate(() => probe())).media.length,1);count++;
        await page.evaluate(url => {const frame=document.createElement('iframe');frame.src=url;document.body.append(frame);},origin.replace('127.0.0.1','localhost'));
        // Use a distinct hostname with the same loopback server for a real cross-origin frame.
        const frame = await new Promise(resolve => {
          const ready=page.frames().find(f=>f!==page.mainFrame() && f.url().startsWith('http://localhost'));
          if(ready)resolve(ready);else page.once('framenavigated',resolve);
        });
        await frame.waitForLoadState();
        await frame.addScriptTag({content});
        const frameSnapshot=await frame.evaluate(() => probe());
        assert.notEqual(frameSnapshot.document,after.document);count++;
        console.log(`${name}: DOM media, pause, seek, volume, text focus, navigation, source replacement, shadow DOM, iframe passed`);
      } finally {await browser.close();}
    }
    const profile = fs.mkdtempSync(path.join(os.tmpdir(),'mythlink-extension-test-'));
    const extension = path.join(root,'.build/browser-extension/Chrome');
    const installed = await chromium.launchPersistentContext(profile,{channel:'chromium',headless:true,
      args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
    try {
      const worker = installed.serviceWorkers()[0] || await installed.waitForEvent('serviceworker');
      const page = await installed.newPage();await page.goto(origin);await page.bringToFront();
      await page.evaluate(() => {
        const m=document.createElement('video');m.src='/tone.wav';m.muted=true;m.controls=true;
        m.style.cssText='width:640px;height:360px';document.body.append(m);
      });
      await page.waitForFunction(() => document.querySelector('video').readyState>=1);
      await worker.evaluate(() => {
        const contains = api.permissions.contains.bind(api.permissions);
        globalThis.permissionPatterns = [];
        api.permissions.contains = async details => {
          for (const pattern of details.origins || []) {
            globalThis.permissionPatterns.push(pattern);
            if (/^https?:\/\/[^/]+:\d+\//.test(pattern)) throw new Error('Safari rejects ports in match patterns');
          }
          return contains(details);
        };
      });
      const captured=await worker.evaluate(async () => collect());
      assert.equal(captured.scene,'video');assert.equal(captured.authorized,true);assert.equal(captured.focused,true);count++;
      assert.ok((await worker.evaluate(() => globalThis.permissionPatterns)).includes('http://127.0.0.1/*'));count++;
      const applied=await worker.evaluate(async target => execute({id:'action',operation:'action',action:'play',target,expires:Date.now()+1200}),captured);
      assert.equal(applied.ok,true);assert.equal(await page.evaluate(() => document.querySelector('video').paused),false);count++;
      const second=await installed.newPage();await second.goto(origin);await second.bringToFront();
      const stale=await worker.evaluate(async target => execute({id:'stale',operation:'action',action:'play',target,expires:Date.now()+1200}),captured);
      assert.equal(stale.ok,false);assert.equal(await page.evaluate(() => document.querySelector('video').paused),false);count++;
      const expired=await worker.evaluate(async target => execute({id:'expired',operation:'action',action:'play',target,expires:Date.now()-1}),captured);
      assert.equal(expired.reason,'browser_command_expired');count++;
      await page.bringToFront();
      await page.evaluate(url => {
        document.querySelector('video').remove();const f=document.createElement('iframe');f.src=url;f.style.cssText='width:800px;height:500px';document.body.append(f);
      },origin+'/nested');
      await page.waitForFunction(() => document.querySelector('iframe').contentDocument?.readyState==='complete');
      const nested=page.frames().find(f=>f!==page.mainFrame());await nested.waitForLoadState();
      await nested.evaluate(() => {const m=document.createElement('video');m.src='/tone.wav';m.controls=true;m.muted=true;document.body.append(m);m.focus();});
      await nested.waitForFunction(() => document.querySelector('video').readyState>=1);
      const frameTarget=await worker.evaluate(async () => collect());assert.equal(frameTarget.scene,'video');
      await nested.evaluate(() => history.pushState({},'','/nested-next'));
      const frameChanged=await worker.evaluate(async target => execute({id:'frame-route',operation:'action',action:'play',target,expires:Date.now()+1200}),frameTarget);
      assert.equal(frameChanged.reason,'page_changed');count++;
      console.log('Installed Chrome extension: real tab/frame messaging, DOM command, tab switch and expiration passed');
    } finally {await installed.close();fs.rmSync(profile,{recursive:true,force:true});}
    console.log(`${count} browser assertions passed`);
  } finally {await new Promise(resolve=>server.close(resolve));}
}
main().catch(error => {console.error(error);process.exitCode=1;});
