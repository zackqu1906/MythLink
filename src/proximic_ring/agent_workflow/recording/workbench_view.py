"""Task-first console; offline exports remain useful and cannot execute actions."""
import html
from pathlib import Path

from .workbench_data import snapshot
from .records import _page
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_text

CSS = """
body{max-width:1480px;background:#f5f6f8;margin:28px auto;color:#202b3b}
.wb-top{display:flex;justify-content:space-between;align-items:center;gap:16px}
.wb-top a{margin-right:16px}.wb-sub{color:#637083}.wb-layout{display:grid;grid-template-columns:330px minmax(0,1fr);gap:20px;align-items:start}
.wb-sidebar{position:sticky;top:18px;background:#fff;border:1px solid #dfe4ed;border-radius:14px;overflow:hidden}
.wb-tools{padding:16px;border-bottom:1px solid #e9edf2}.wb-tools input{width:100%;box-sizing:border-box;margin-bottom:10px}
.wb-tools select,.wb-tools input,textarea{font:inherit;padding:9px;border:1px solid #cbd4e1;border-radius:7px;background:white;color:inherit}
.wb-tools select{max-width:100%}#wb-list{max-height:72vh;overflow:auto;padding:8px}
.wb-task{display:block;width:100%;text-align:left;border:0;border-radius:9px;padding:13px;margin:2px 0;color:#25354b;background:transparent}
.wb-task[aria-current=true]{background:#eaf1ff;box-shadow:inset 3px 0 #3568d4}.wb-task:hover{background:#f0f4fa}
.wb-task strong{display:block;font-size:14px;line-height:1.55}.wb-task small{display:block;color:#64748b;margin-top:6px}
.wb-pane{min-width:0}.wb-heading{padding:8px 3px 16px}.wb-heading h2{margin:0 0 10px;font-size:24px}
.wb-card{padding:22px;margin:0 0 16px;border:1px solid #dfe4ed;border-radius:14px;background:#fff}
.wb-card h3{font-size:18px;margin:0}.wb-card h4{font-size:16px;margin:0 0 10px}.wb-row{display:flex;justify-content:space-between;align-items:center;gap:15px;flex-wrap:wrap}
.wb-step{color:#3568d4;font-size:13px;margin-bottom:7px;font-weight:700;letter-spacing:.06em}
.wb-action{background:#3568d4;color:#fff;border-color:#3568d4;padding:9px 15px;font-weight:600}.wb-action:hover:enabled{background:#2456be;color:white}
.wb-badge{display:inline-block;border-radius:5px;background:#eef1f6;color:#536178;font-size:12px;padding:3px 8px}
.wb-badge.completed{background:#e7f4ed;color:#276749}.wb-badge.failed{background:#fff0ee;color:#a6382b}
.wb-badge.review{background:#fff4d9;color:#8a5d13}.wb-badge.running,.wb-badge.queued{background:#eaf1ff;color:#3568d4}
.wb-flow{background:#f8fafc;border:1px solid #e4e9f0;border-radius:10px;padding:16px;margin-top:16px}
.wb-flow ol{padding-left:24px}.wb-flow li{padding:4px 0}.wb-flow p{overflow-wrap:anywhere}
.wb-flow dl{display:grid;grid-template-columns:minmax(90px,160px) 1fr;gap:8px;font-size:13px}.wb-flow dt{overflow-wrap:anywhere}.wb-flow dd{margin:0;color:#58677a}
.wb-history{border:0;border-top:1px solid #edf0f5;padding:14px 0;margin:14px 0 0;border-radius:0}
.wb-history summary{color:#566781}.wb-history details{margin:10px 0}.wb-history pre{max-height:360px;overflow:auto}
.wb-note{padding:12px 16px;border-radius:9px;background:#edf3ff;line-height:1.6}
.wb-error{color:#9f3027;background:#fff1ef;padding:12px;border-radius:8px;white-space:pre-wrap;overflow-wrap:anywhere}
.wb-review{border:1px solid #dfc68e;background:#fffcf4;padding:16px;border-radius:10px;margin-top:16px}
.wb-review textarea{display:block;width:100%;box-sizing:border-box;min-height:68px;margin:10px 0}.wb-review label{font-weight:600}
.wb-table{font-size:13px;table-layout:fixed}.wb-table td{overflow-wrap:anywhere}.wb-table th{color:#64748b}
#wb-notice:empty{display:none}#wb-notice{position:sticky;top:0;z-index:2}
.wb-empty{padding:30px;color:#657389;text-align:center}.wb-versions{margin-bottom:16px}
@media(max-width:850px){.wb-layout{grid-template-columns:1fr}.wb-sidebar{position:static}#wb-list{max-height:260px}.wb-card{padding:16px}.wb-flow dl{grid-template-columns:1fr}.wb-flow dd{margin-bottom:8px}}
"""


def export_workbench(root):
    root = Path(root)
    data = snapshot(root)
    body = '<style>' + CSS + '</style>'
    body += ("<nav class='wb-top'><div><a href='overview.html'>全流程总览</a>"
             "<a href='index.html'>任务记录</a><a href='operations/index.html'>通用组</a></div>"
             "<span class='wb-badge'>任务操作台</span></nav>"
             "<p class='wb-sub'>按任务依次处理：提取 Workflow → 合并或生成通用 Workflow → 按通用组提取参数。每次运行保留独立记录。</p>"
             "<p id='wb-worker' class='wb-sub'>离线快照：从应用“记录”打开操作台后可以运行各步骤。</p>"
             "<p id='wb-notice' role='status' aria-live='polite'></p>"
             "<div id='wb-app'>")
    for n, task in enumerate(data['tasks'], 1):
        body += (f"<article><h3>{n:02d} · {html.escape(task['goal'])}</h3>"
                 f"<p>{html.escape(task['created_at'])} · {html.escape(task['status'])}</p>"
                 f"<p><a href='sessions/{task['session_id']}/episode-{task['id']}.html'>查看任务记录</a></p>"
                 "<p>① 提取 Workflow　→　② 合并 / 生成通用 Workflow　→　③ 按通用组提取参数</p></article>")
    if not data['tasks']:
        body += '<p class="wb-empty">尚无任务记录。完成任务后会出现在这里。</p>'
    body += '</div>'
    root.mkdir(exist_ok=True, parents=True)
    atomic_text(root / 'workbench.html', _page('任务操作台', body))
    return root / 'workbench.html'


SCRIPT = r"""
(() => {
  'use strict';
  const base = new URL('./', document.currentScript.src);
  const app = document.querySelector('#wb-app');
  const notice = document.querySelector('#wb-notice'), worker = document.querySelector('#wb-worker');
  let state, selected = decodeURIComponent(location.hash.slice(1)), order = 'asc', search = '', filter = '';
  let sending = false, stopped = false, connected = false, lastDetail = '', tracking = null;
  const flowSelection = new Map(), drafts = new Map();
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const id = t => t.session_id + '/' + t.id;
  const time = s => s ? new Date(s).toLocaleString('zh-CN', {hour12:false}) : '';
  const labels = {queued:'排队中',running:'处理中',completed:'完成',failed:'失败',review:'待采用',
    ready:'参数已提取',grouped:'已归组 · 待参数',extracted:'已提取 · 待归组',empty:'无可复用流程',
    interrupted:'已中断',unfinished:'未结束',start_failed:'启动失败',missing_source:'缺少来源',deferred:'等待空闲'};
  const badge = (status, label) => `<span class="wb-badge ${esc(status)}">${esc(label || labels[status] || status)}</span>`;
  const link = (path, label) => `<a href="${esc(path)}" target="_blank" rel="noopener">${esc(label)}</a>`;
  const file = (directory, identifier, suffix, label) => link(directory+'/'+encodeURIComponent(identifier)+suffix,label);
  const currentTask = () => state?.tasks.find(t => id(t) === selected);
  const currentFlow = t => t.flows[flowSelection.get(selected) || 0];
  const pending = (t, step, f) => t.actions.some(a => a.step === step && ['queued','running'].includes(a.status)
    && a.target.job_id === t.latest_job?.id && a.target.workflow_index === f?.index);
  const button = (action, text, enabled, extra='') =>
    `<button type="button" class="wb-action" data-wb="${action}" ${extra} ${enabled && !sending && connected && state?.worker?.pipeline_enabled !== false ? '' : 'disabled'}>${esc(text)}</button>`;
  const summary = t => {
    if (t.actions.some(a => ['queued','running'].includes(a.status)) || ['queued','running'].includes(t.latest_job?.status)) return 'running';
    if (t.warning || t.actions[0]?.status==='failed' || t.latest_job?.status==='failed') return 'failed';
    if (t.flows.some(f=>f.candidates.length)) return 'review';
    if (t.flows.length && t.flows.every(f=>f.revision_id && (
      f.parameters[0]?.status==='completed' && f.parameters[0]?.generation.method!=='reviewed_contract_mapping'
      || !f.general_flow.parameters.length))) return 'ready';
    if (t.flows.some(f=>f.revision_id)) return 'grouped';
    if (t.flows.length) return 'extracted';
    return t.latest_job?.status==='completed' ? 'empty' : 'none';
  };
  function show(message, error=false) {notice.textContent=message;notice.className=error?'wb-error':'wb-note';}
  async function request(route, payload) {
    const response = await fetch(new URL('api/'+route,base), payload === undefined ? {cache:'no-store'} :
      {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '操作失败，请稍后重试');
    return value;
  }
  function flowView(flow, title) {
    if (!flow) return '';
    const steps = flow.steps.map(s => typeof s === 'string' ? s : [s.action,s.method,s.check].filter(Boolean).join('；'));
    return `<div class="wb-flow"><h4>${esc(title || flow.name)}</h4><p>${esc(flow.goal)}</p>
      <ol>${steps.map(s=>'<li>'+esc(s)+'</li>').join('')}</ol>
      ${flow.parameters.length ? '<dl>'+flow.parameters.map(p=>`<dt>${esc(p.name)}</dt><dd>${esc(p.description)}${p.required===false?'（可选）':''}</dd>`).join('')+'</dl>':''}
      ${flow.completion?'<p>完成判断：'+esc(flow.completion)+'</p>':''}
      ${flow.conditions?.length?'<p>使用条件：'+esc(flow.conditions.join('；'))+'</p>':''}
      ${flow.fallbacks?.length?'<p>异常处理：'+esc(flow.fallbacks.map(f=>f.when+'：'+f.action).join('；'))+'</p>':''}</div>`;
  }
  function operationHistory(actions) {
    if (!actions.length) return '<p class="wb-sub">尚无手动处理记录。</p>';
    return actions.map(a => {
      const jobs = state.operation_jobs.filter(j => (a.result.operation_job_ids || []).includes(j.id));
      return `<details class="wb-history" data-keep="${esc(a.id)}"><summary>${badge(a.status)} ${esc(time(a.created_at))} · ${esc(a.progress || a.error)}</summary>
        ${a.error?'<p class="wb-error">'+esc(a.error)+'</p>':''}<p class="wb-sub">请求 ${esc(a.id)} · 更新于 ${esc(time(a.updated_at))}</p>
        ${jobs.map(j => {
          let g={};try{g=JSON.parse(j.generation);}catch(_){}
          return `<p>${badge(j.status)} ${esc({admission:'同类判断',update:'通用流程生成',parameters:'参数提取'}[j.kind] || j.kind)}
            ${file('operations',j.id,'.operation-input.json','输入')} · ${file('operations',j.id,'.operation-result.json','结果与用量')}</p>
            ${g.model?'<p class="wb-sub">模型：'+esc(g.model)+'</p>':''}${j.error?'<p class="wb-error">'+esc(j.error)+'</p>':''}`;
        }).join('')}<details><summary>版本与结果标识</summary><pre>${esc(JSON.stringify(a,null,2))}</pre></details></details>`;
    }).join('');
  }
  function extraction(t) {
    const j=t.latest_job, pending=j && ['queued','running'].includes(j.status);
    const ended=['completed','failed','interrupted','unfinished','start_failed'].includes(t.status);
    return `<section class="wb-card"><div class="wb-step">01 / 任务 Workflow</div><div class="wb-row"><h3>从记录提取可复用流程</h3>
      ${button('extract',j?'重新提取 Workflow':'提取 Workflow',ended && !pending)}</div>
      <p>${j?badge(j.status):badge('none','尚未提取')} <span class="wb-sub">${!ended?'任务结束后可提取':pending?'任务已提交，等待后台完成':j?.status==='completed'?'当前结果：'+t.flows.length+' 个 Workflow':'每次提取生成新版本，保留之前的结果'}</span></p>
      ${j?.error?'<p class="wb-error">'+esc(j.error)+'</p>':''}${t.warning?'<p class="wb-error">'+esc(t.warning)+'</p>':''}
      ${t.flows.length>1?`<label class="wb-versions">本任务的 Workflow： <select id="wb-flow-select">${t.flows.map((f,i)=>`<option value="${i}" ${i===(flowSelection.get(selected)||0)?'selected':''}>${i+1}. ${esc(f.flow.name)}</option>`).join('')}</select></label>`:''}
      ${currentFlow(t)?`<p><strong>${esc(currentFlow(t).flow.name)}</strong></p><p class="wb-sub">${esc(currentFlow(t).flow.goal)}</p>
        <details data-keep="source-flow"><summary>查看完整 Workflow（${currentFlow(t).flow.steps.length} 步）</summary>${flowView(currentFlow(t).flow)}</details>`:''}
      ${j?.status==='completed'&&!t.flows.length?'<p class="wb-note">本次未提取出可复用流程。'+esc(typeof t.extraction_notes==='string'?t.extraction_notes:JSON.stringify(t.extraction_notes))+'</p>':''}
      <details class="wb-history" data-keep="extraction"><summary>提取历史（${t.jobs.length} 次）</summary>
      ${t.jobs.map(j=>`<p>${badge(j.status)} ${esc(time(j.created_at))} · ${esc(j.prompt_version)}
        ${j.result_file?file('workflows',j.id,'.flow.txt','流程文本')+' · '+file('workflows',j.id,'.audit.html','完整审计'):file('workflows',j.id,'.model-input.json','模型输入')}
        ${j.error?'<br>'+esc(j.error):''}</p>`).join('') || '<p>尚无记录</p>'}</details></section>`;
  }
  function candidate(c) {
    const vids=Object.keys(c.bindings);
    return `<div class="wb-review"><div class="wb-row"><h4>新通用流程 · 待采用</h4>${badge('review')}</div><p>${esc(c.reason)}</p>
      ${flowView(c.flow)}
      <p class="wb-sub">查看各来源的目标、步骤和参数对应，填写核对说明后采用。采用会更新整个组；新参数值在第三步提取。</p>
      ${vids.map(v=>`<details data-keep="source-${esc(c.id)}-${esc(v)}"><summary>来源：${esc(state.source_flows[v]?.name || v.slice(0,12))}</summary>
        ${flowView(state.source_flows[v])}<pre>${esc(JSON.stringify(c.bindings[v],null,2))}</pre></details>
        <label for="review-${esc(c.id)}-${esc(v)}">此来源的分支与参数核对说明</label>
        <textarea id="review-${esc(c.id)}-${esc(v)}" data-review="${esc(c.id)}" data-version="${esc(v)}" maxlength="1500"
          placeholder="例如：学校分支仍保留类型和地址核验；类型筛选可留空。">${esc(drafts.get(c.id+'/'+v)||'')}</textarea>`).join('')}
      ${button('adopt','采用此通用流程',vids.every(v=>(drafts.get(c.id+'/'+v)||'').trim()),`data-candidate="${esc(c.id)}"`)}
      <p class="wb-sub">此操作记录你的审阅，不代表已经实际执行过该流程。</p></div>`;
  }
  function group(t,f) {
    const acts=t.actions.filter(a=>a.step==='group'&&a.target.workflow_index===f?.index);
    return `<section class="wb-card"><div class="wb-step">02 / 通用 Workflow</div><div class="wb-row"><h3>合并或生成通用流程</h3>
      ${button('group',f?.revision_id?'重新判断 / 生成通用流程':'合并 / 生成通用 Workflow',!!f&&!pending(t,'group',f))}</div>
      <p class="wb-sub">${f?'召回相似通用组并判断同类；没有同类则建立新组。':'先完成第一步，再处理通用流程。'}</p>
      ${f?.revision_id?`<p>${badge('completed','当前生效')} ${esc(f.general_flow.name)} · ${f.members} 个成员 · ${link('operations/group-'+encodeURIComponent(f.operation_id)+'.html','查看整个通用组')}</p>`:f?'<p>'+badge('none','尚未归入当前版本的通用组')+'</p>':''}
      ${acts[0]&&['queued','running'].includes(acts[0].status)?'<p>'+badge(acts[0].status)+' '+esc(acts[0].progress)+'</p>':''}
      ${f?.admissions[0]?'<p class="wb-sub">最近归组：'+esc({accepted:'已入组',new_operation:'新组已建立',generalization_pending:'等待通用流程采用',failed:'处理失败',pending:'等待处理',running:'正在判断',merged:'已合并'}[f.admissions[0].status]||f.admissions[0].status)+'</p>':''}
      ${f?.candidates.map(candidate).join('')||''}
      ${f?.general_flow?'<details data-keep="active-flow"><summary>查看当前生效的通用流程</summary>'+flowView(f.general_flow)+'</details>':''}
      <details class="wb-history" data-keep="group-history"><summary>归组操作历史（${acts.length} 次）</summary>${operationHistory(acts)}</details></section>`;
  }
  function parameters(t,f) {
    const records=f?.parameters || [], latest=records[0], acts=t.actions.filter(a=>a.step==='parameters'&&a.target.workflow_index===f?.index);
    return `<section class="wb-card"><div class="wb-step">03 / 通用组参数</div><div class="wb-row"><h3>提取本任务的参数值</h3>
      ${button('parameters',latest?'重新提取参数':'提取参数',!!f?.revision_id&&!pending(t,'parameters',f)&&['user','test'].includes(f.scope)&&t.status==='completed')}</div>
      <p class="wb-sub">${f?.revision_id?'按“'+esc(f.general_flow.name)+'”当前版本的参数定义，从本任务记录取值。规则优先，LLM 补充。':'先归组并采用通用流程，才能明确需要提取哪些参数。'}</p>
      ${t.status!=='completed'?'<p class="wb-sub">任务未正常完成，不作为参数历史样本。</p>':''}
      ${f?.candidates.length?'<p class="wb-note">新候选尚未采用，此处仍对应当前生效版本。采用后请重新提取参数。</p>':''}
      ${acts[0]&&['queued','running'].includes(acts[0].status)?'<p>'+badge(acts[0].status)+' '+esc(acts[0].progress)+'</p>':''}
      ${latest?`<p>${pending(t,'parameters',f)?'上次结果：':''}${latest.generation.method==='reviewed_contract_mapping'?badge('queued','契约已更新 · 待提取'):badge(latest.status)} ${esc(time(latest.created_at))} · ${file('operations/parameter-history',latest.id,'.json','来源与参数审计')}</p>
        ${latest.generation.error?'<p class="wb-error">'+esc(latest.generation.error)+'</p>':''}
        <table class="wb-table"><thead><tr><th>参数</th><th>本任务的值</th><th>依据</th></tr></thead><tbody>
        ${latest.parameters.map(p=>`<tr><td>${esc(p.name)}</td><td>${esc(p.value||'未指定')}<br><small>${esc({observed:'来源已记录',missing:'缺失',uncertain:'不确定',mapped:'历史迁移'}[p.status]||p.status)}</small></td><td>${esc(p.reason)}</td></tr>`).join('')}</tbody></table>`:
        '<p>'+badge('none',f?.general_flow&&!f.general_flow.parameters.length?'此流程无需参数':'尚无当前版本的参数记录')+'</p>'}
      <details class="wb-history" data-keep="parameter-history"><summary>参数操作与历史</summary>${operationHistory(acts)}
        ${records.slice(1).map(r=>`<p>${esc(time(r.created_at))} ${badge(r.status)} ${file('operations/parameter-history',r.id,'.json','查看参数记录')}</p>`).join('')}</details></section>`;
  }
  function renderDetail(force=false) {
    const pane=document.querySelector('#wb-pane'), t=currentTask();
    if (!t) {pane.innerHTML='<div class="wb-empty">选择左侧任务开始处理</div>';return;}
    const signature=JSON.stringify([t,flowSelection.get(selected),sending,connected]);
    if (!force && (signature===lastDetail || pane.contains(document.activeElement)&&document.activeElement.matches('textarea'))) return;
    const open=new Set(Array.from(pane.querySelectorAll('details[open][data-keep]')).map(e=>e.dataset.keep));
    const f=currentFlow(t);
    pane.innerHTML=`<header class="wb-heading"><h2>${esc(t.goal)}</h2><p class="wb-sub">${esc(time(t.created_at))} · ${badge(t.status)}
      · ${esc(t.source)} · ${link('sessions/'+encodeURIComponent(t.session_id)+'/episode-'+encodeURIComponent(t.id)+'.html','原始任务记录')}</p></header>`+
      extraction(t)+group(t,f)+parameters(t,f);
    pane.querySelectorAll('details[data-keep]').forEach(e=>{if(open.has(e.dataset.keep))e.open=true;});
    lastDetail=signature;
  }
  function render() {
    if (!document.querySelector('#wb-list')) {
      app.innerHTML=`<div class="wb-layout"><aside class="wb-sidebar"><div class="wb-tools">
        <label><input id="wb-search" type="search" placeholder="搜索任务…" aria-label="搜索任务"></label>
        <select id="wb-order" aria-label="排序"><option value="asc">时间：从早到晚</option><option value="desc">时间：从晚到早</option></select>
        <select id="wb-filter" aria-label="状态筛选"><option value="">全部状态</option><option value="failed">处理失败</option><option value="review">待采用</option><option value="running">处理中</option><option value="none">尚未提取</option><option value="extracted">待归组</option><option value="grouped">待参数</option><option value="ready">参数已提取</option><option value="empty">无可复用流程</option></select>
        <p id="wb-count" class="wb-sub"></p></div><div id="wb-list"></div></aside><main id="wb-pane" class="wb-pane"></main></div>`;
    }
    if (!currentTask()) selected=state.tasks[0]?id(state.tasks[0]):'';
    let tasks=state.tasks.map((t,i)=>({t,i})).filter(({t})=>t.goal.toLowerCase().includes(search.toLowerCase())&&(!filter||summary(t)===filter));
    if(order==='desc')tasks.reverse();
    document.querySelector('#wb-count').textContent=`${tasks.length} / ${state.tasks.length} 个任务`;
    document.querySelector('#wb-list').innerHTML=tasks.map(({t,i})=>`<button type="button" class="wb-task" data-task="${esc(id(t))}" aria-current="${id(t)===selected}">
      <strong>${String(i+1).padStart(2,'0')} · ${esc(t.goal)}</strong><small>${esc(time(t.created_at))} · ${esc(labels[summary(t)]||'尚未提取')}</small></button>`).join('')||'<p class="wb-empty">没有匹配的任务</p>';
    renderDetail();
    worker.textContent=state.worker?.error || (state.worker?.busy?'前台正在工作，已提交步骤会等待空闲。':'后台已连接 · 使用应用当前模型配置 · 自动处理设置保持不变');
    if(tracking){
      const t=state.tasks.find(t=>id(t)===tracking.task);
      const a=tracking.extraction?t?.jobs.find(j=>j.id===tracking.id):t?.actions.find(a=>a.id===tracking.id);
      if(a){
        show((a.progress || labels[a.status] || a.status)+(a.error?'：'+a.error:''),a.status==='failed');
        if(!['queued','running'].includes(a.status))tracking=null;
      }
    }
  }
  async function poll() {
    if(stopped)return;
    try{state=await request('workbench');connected=true;render();}
    catch(error){connected=false;worker.textContent='连接已断开。请保持应用运行，从“记录”重新打开操作台。';if(state)renderDetail(true);}
    if(!stopped)setTimeout(poll,2500);
  }
  app.addEventListener('input',event=>{
    if(event.target.id==='wb-search'){search=event.target.value;render();}
    if(event.target.matches('[data-review]')){
      const el=event.target;drafts.set(el.dataset.review+'/'+el.dataset.version,el.value);
      const c=currentFlow(currentTask()).candidates.find(c=>c.id===el.dataset.review);
      el.closest('.wb-review').querySelector('[data-wb=adopt]').disabled=sending||!connected||state?.worker?.pipeline_enabled===false||!Object.keys(c.bindings).every(v=>(drafts.get(c.id+'/'+v)||'').trim());
    }
  });
  app.addEventListener('change',event=>{
    if(event.target.id==='wb-order'){order=event.target.value;render();}
    if(event.target.id==='wb-filter'){filter=event.target.value;render();}
    if(event.target.id==='wb-flow-select'){flowSelection.set(selected,Number(event.target.value));renderDetail(true);}
  });
  app.addEventListener('click',async event=>{
    const taskButton=event.target.closest('[data-task]');
    if(taskButton){selected=taskButton.dataset.task;tracking=null;notice.textContent='';history.replaceState(null,'','#'+encodeURIComponent(selected));lastDetail='';render();return;}
    const b=event.target.closest('[data-wb]');if(!b||b.disabled||sending)return;
    const t=currentTask(),f=currentFlow(t),action=b.dataset.wb;
    const payload={session_id:t.session_id,episode_id:t.id,job_id:t.latest_job?.id,workflow_index:f?.index,revision_id:f?.revision_id};
    let route;
    if(action==='extract')route='extract';
    else if(action==='adopt'){route='workbench/adopt';payload.candidate_id=b.dataset.candidate;
      const c=f.candidates.find(c=>c.id===b.dataset.candidate);payload.reviews=Object.fromEntries(Object.keys(c.bindings).map(v=>[v,drafts.get(c.id+'/'+v)]));}
    else{route='workbench/run';payload.step=action;}
    sending=true;renderDetail(true);show('正在提交…');
    try{const result=await request(route,payload);show(result.message);
      if(result.id || result.jobs?.[0])tracking={task:id(t),id:result.id || result.jobs[0],extraction:action==='extract'};
      state=await request('workbench');}
    catch(error){show(error.message,true);}
    finally{sending=false;render();}
  });
  window.addEventListener('pagehide',()=>{stopped=true;});
  window.addEventListener('hashchange',()=>{selected=decodeURIComponent(location.hash.slice(1));tracking=null;notice.textContent='';render();});
  poll();
})();
"""
