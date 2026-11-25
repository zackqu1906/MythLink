(() => {
  const panel = document.getElementById('human-review');
  if (!panel) return;
  const port = '18761';
  const live = location.origin === 'http://127.0.0.1:' + port;
  const marker = '/docs/reports/';
  const path = location.pathname;
  const relative = live ? path : path.includes(marker) ? path.slice(path.lastIndexOf(marker) + marker.length - 1) : '';
  const message = panel.querySelector('[role=status]');
  const active = new Map();
  const identity = item => JSON.stringify([item.run,item.id, item.input_sha256, item.output_sha256]);
  const items = [...document.querySelectorAll('.review-actions')];
  const deletions = [...document.querySelectorAll('.case-deletion')];
  function payload(box) {
    return {run:panel.dataset.run,id:box.dataset.id, input_sha256:box.dataset.input, output_sha256:box.dataset.output};
  }
  async function api(action, data={}) {
    const response = await fetch('/api/review', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({action, report:decodeURIComponent(relative), ...data})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '保存失败，请重试。');
    return result;
  }
  function show(box, item) {
    const card = box.closest('article');
    card.dataset.body = 'pass';
    card.dataset.status = card.dataset.route === 'false' ? 'fail' : 'pass';
    card.dataset.reviewed = 'true';
    const badge = card.querySelector('.case-head .badge');
    badge.textContent = wasFailureNowAccepted(card) ? '原失败、已判对' :
      card.dataset.status === 'fail' ? '正文人工通过 · 整体失败' : '人工判对';
    badge.className = 'badge ' + card.dataset.status;
    box.querySelector('output').textContent = '判对时保存的标准：' + item.requirements.join(' ');
    active.set(identity(item),item);
  }
  function refresh() {
    applyFilters();
    const scored = cards.filter(c=>c.dataset.status !== 'observation');
    const count = (field,state) => scored.filter(c=>c.dataset[field]===state).length;
    message.textContent = '人工标记 ' + active.size + ' 条 · 当前通过 ' + count('status','pass') +
      ' / ' + scored.length + ' · 仍失败 ' + count('status','fail') + ' · 待确认 ' + count('status','uncertain') +
      ' · 未复核 ' + count('status','pending') + ' · 原失败已判对 ' + scored.filter(wasFailureNowAccepted).length;
    document.getElementById('export-reviews').disabled = !active.size;
  }
  function jump(box,field='rule') {
    if (!relative) throw new Error('请在项目报告目录中打开本机审阅页。');
    const target = new URL(relative, 'http://127.0.0.1:' + port);
    target.searchParams.set('edit',box.closest('article').id);
    target.searchParams.set('field',field);
    target.hash = box.closest('article').id;
    location.assign(target.href);
  }
  function editedLines(form) {
    return [...new Set(form.querySelector('textarea').value.split('\n').map(s=>s.trim()).filter(Boolean))];
  }
  function allowSave(form) {
    const original = JSON.parse(form.dataset.original);
    const lines = editedLines(form);
    return lines.length > 0 && (original.length !== lines.length || lines.some(line=>!original.includes(line)));
  }
  function closeRule(box) {
    box.querySelector('.rule-form').hidden = true;
    const button = box.querySelector('[data-action=rule]');
    button.disabled = false;
    button.textContent = '更新标准并判对';
    box.querySelector('[data-action=cancel]').hidden = true;
  }
  async function action(box, button) {
    const item = payload(box);
    const feedback = box.querySelector('output');
    const form = box.querySelector('.rule-form');
    const primary = box.querySelector('[data-action=rule]');
    const cancel = box.querySelector('[data-action=cancel]');
    if (button === cancel) {closeRule(box);return;}
    if (!live) {
      try {jump(box);} catch (e) {feedback.textContent=e.message;}
      return;
    }
    primary.disabled = true;
    cancel.disabled = true;
    try {
      if (form.hidden) {
        const current = await api('get_rule',item);
        form.dataset.version = current.contract_sha256;
        form.dataset.inputVersion = current.version;
        form.dataset.original = JSON.stringify([...new Set(current.requirements.map(s=>s.trim()))]);
        primary.textContent = current.same_input ? '保存标准并判对' : '保存新输入的验收标准';
        form.querySelector('textarea').value = current.requirements.join('\n');
        form.hidden = false;
        cancel.hidden = false;
        form.querySelector('textarea').focus();
      } else {
        const decision = await api('rule',{...item,requirements:editedLines(form),contract_sha256:form.dataset.version,version:form.dataset.inputVersion});
        if (decision.rule_only) feedback.textContent='新输入的验收标准已保存。';
        else show(box,decision);
        closeRule(box);
        refresh();
      }
    } catch (e) { feedback.textContent = '未完成。' + e.message; }
    finally {
      cancel.disabled = false;
      primary.disabled = !form.hidden && !allowSave(form);
    }
  }
  function showInputs(card, values) {
    for (const field of card.querySelectorAll('.input-field[data-field]')) {
      const feedback=field.querySelector('output');
      if (!feedback) continue;
      const latest=values[field.dataset.field];
      const original=field.querySelector('.input-text');
      const frozen=original.dataset.empty==='true' ? '' : original.textContent;
      feedback.textContent = latest===frozen ? '' : '题库现用：'+(latest || '（空）')+'。本次模型收到的是上方原始输入。';
    }
  }
  async function inputAction(field, operation) {
    const card = field.closest('article');
    const box = card.querySelector('.review-actions');
    const form = field.querySelector('.input-form');
    const name = field.dataset.field;
    const area = form.querySelector('textarea');
    const feedback = field.querySelector('output');
    try {
      if (operation === 'cancel') {form.hidden=true;return;}
      if (!live) return jump(box,name);
      if (operation === 'edit') {
        const current = await api('get_input',payload(box));
        area.value = current[name];
        form.dataset.version = current.version;
        form.hidden = false;
        area.focus();
      }
      if (operation === 'save') {
        const result=await api('save_input',{...payload(box),field:name,value:area.value,version:form.dataset.version});
        form.hidden=true;
        showInputs(card,result);
        if (!feedback.textContent) feedback.textContent='题库已保存。';
      }
    } catch(e) {feedback.textContent='未完成。'+e.message;}
  }
  function showRemoved(box) {
    const card = box.closest('article');
    card.dataset.removed = 'true';
    for (const el of card.querySelectorAll('.input-form,.rule-form,[data-input-action],[data-action],.delete-confirm,[data-delete=open]')) {
      el.hidden = true;
    }
    box.querySelector('output').textContent = '已从题库删除。下次评测会跳过这题，本次结果保留。';
  }
  async function deletion(box, operation) {
    const form = box.querySelector('.delete-confirm');
    const opener = box.querySelector('[data-delete=open]');
    const confirm = box.querySelector('[data-delete=confirm]');
    const cancel = box.querySelector('[data-delete=cancel]');
    const output = box.querySelector('output');
    if (operation === 'cancel') {
      form.hidden = true;
      opener.hidden = false;
      opener.focus();
      return;
    }
    opener.disabled = confirm.disabled = cancel.disabled = true;
    output.textContent = '';
    try {
      if (!live) return jump(box,'delete');
      if (operation === 'open') {
        const preview = await api('prepare_delete',payload(box));
        if (preview.removed) return showRemoved(box);
        form.dataset.version = preview.version;
        form.querySelector('p').textContent = '从真实用户题库删除 '+preview.id+'？\n当前口述：'+preview.utterance;
        opener.hidden = true;
        form.hidden = false;
      } else if (operation === 'confirm') {
        await api('delete_case',{...payload(box),version:form.dataset.version});
        showRemoved(box);
      }
    } catch (e) {
      form.hidden = true;
      opener.hidden = false;
      output.textContent = e.message;
    } finally {
      opener.disabled = confirm.disabled = cancel.disabled = false;
      if (!form.hidden) cancel.focus();
    }
  }
  deletions.forEach(box=>box.addEventListener('click',event=>{
    const button = event.target.closest('[data-delete]');
    if (button) deletion(box, button.dataset.delete);
  }));
  document.querySelectorAll('.case-inputs').forEach(section=>section.addEventListener('click',event=>{
    const button=event.target.closest('[data-input-action]');
    if (button) inputAction(button.closest('[data-field]'),button.dataset.inputAction);
  }));
  items.forEach(box=>{
    const form=box.querySelector('.rule-form');
    form.querySelector('textarea').addEventListener('input',()=>{
      if (!form.hidden && !box.querySelector('[data-action=cancel]').disabled) {
        box.querySelector('[data-action=rule]').disabled = !allowSave(form);
      }
    });
  });
  items.forEach(box=>box.addEventListener('click',e=>{
    const button=e.target.closest('button[data-action]');
    if (button) action(box,button);
  }));
  document.getElementById('export-reviews').onclick = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({report:relative,reviewed:[...active.values()]},null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download='人工审阅.json';a.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  const pageLink=panel.querySelector('a');
  if (!live && relative) {pageLink.href='http://127.0.0.1:'+port+relative;pageLink.hidden=false;}
  const resultFilter = document.getElementById('result');
  function rememberResultFilter() {
    if (!live) return;
    const url=new URL(location.href);
    if (resultFilter.value==='all') url.searchParams.delete('result');
    else url.searchParams.set('result',resultFilter.value);
    history.replaceState(null,'',url);
  }
  resultFilter.addEventListener('change',rememberResultFilter);
  document.getElementById('reset').addEventListener('click',rememberResultFilter);
  (async () => {
    try {
      const stored = live ? await api('load') : [];

      const matches = new Map(stored.map(item=>[identity(item),item]));
      items.forEach(box=>{
        const item=matches.get(identity(payload(box)));
        if (item?.requirements?.length) show(box,item);
        else if (item) box.querySelector('output').textContent='旧版记录已保留，请补充验收标准以确认判对。';
      });
      const preset = new URLSearchParams(location.search).get('result');
      const result = document.getElementById('result');
      if ([...result.options].some(o=>o.value===preset)) result.value=preset;
      refresh();
      if (live) {
        const changes = await api('input_changes',{run:panel.dataset.run});
        items.forEach(box=>{
          const values = changes[box.dataset.id];
          if (values && 'utterance' in values) showInputs(box.closest('article'),values);
        });
        deletions.forEach(box=>{
          const values = changes[box.dataset.id];
          if (values?.removed) showRemoved(box);
          if (values?.unlinked) box.hidden = true;
        });
      }
      const params = new URLSearchParams(location.search);
      const id = params.get('edit');
      const card = id && document.getElementById(id);
      if (live && card && !card.dataset.removed) {
        const name = params.get('field');
        const box = card.querySelector('.review-actions');
        if (name==='delete') {
          const del = card.querySelector('.case-deletion');
          if (del && !del.hidden) await deletion(del,'open');
        } else if (name==='editable_text' || name==='utterance') {
          const field=card.querySelector('[data-field="'+name+'"]');
          if (field) await inputAction(field,'edit');
        } else if (box) await action(box,box.querySelector('[data-action=rule]'));
      }
    } catch (e) { message.textContent='无法读取已存审阅。'+e.message; }
  })();
})();
