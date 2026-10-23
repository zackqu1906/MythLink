"""Shared controls for exported records. Offline exports remain script-free."""
from __future__ import annotations

import html
from pathlib import PurePosixPath

from proximic_ring.agent_workflow.extraction.workflow_store import TERMINAL


def task_controls(session_id, episode_id, *, status=None, initial_message=''):
    sid = html.escape(session_id, quote=True)
    eid = html.escape(episode_id, quote=True)
    ended = 'true' if status is None or status in TERMINAL else 'false'
    return (f"<div class='workflow-task' data-session='{sid}' data-episode='{eid}' data-ended='{ended}'>"
            "<button type='button' data-action='extract' disabled>提取 / 重新提取 Workflow</button>"
            f" <span class='workflow-status muted' role='status'>{html.escape(initial_message)}</span></div>")


def workflow_toolbar(library_href, *, navigation=True):
    overview_href = str(PurePosixPath(library_href).parent / '..' / 'overview.html')
    workbench_href = str(PurePosixPath(library_href).parent / '..' / 'workbench.html')
    back=f"<p><a href='{html.escape(overview_href, quote=True)}'>← 返回全流程总览</a></p>" if navigation else ''
    return (back + f"<p><a href='{html.escape(workbench_href, quote=True)}'>打开任务操作台：提取 → 通用流程 → 参数</a></p>" +
            "<details class='workflow-controls'><summary>提取与刷新操作</summary><div class='workflow-toolbar'>"
            "<button type='button' data-action='latest' disabled>重新提取最近结束的任务</button>"
            "<button type='button' data-action='retry-failed' disabled>重试失败的提取</button>"
            "<button type='button' data-action='refresh' disabled>刷新记录</button>"
            "<label><input type='checkbox' data-action='automatic' disabled>任务结束后自动提取</label>"
            "</div><p class='workflow-notice muted' role='status'>"
            "从应用的“记录”按钮打开可提取和重试；直接打开 HTML 文件仅供回查。</p></details>")


CONTROLS_CSS = """
.workflow-toolbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.workflow-controls{padding:14px 0}.workflow-task{margin:10px 0}
button{font:inherit;font-size:13px;padding:6px 12px;border:1px solid #b8c6dc;
border-radius:7px;background:#fff;color:#245eb5;cursor:pointer}
button:hover:enabled{background:#edf3fd}button:disabled{color:#777;cursor:default;opacity:.65}
.workflow-toolbar label{display:flex;align-items:center;gap:5px;font-size:13px}
.workflow-status{display:block;font-size:13px;max-width:460px;overflow-wrap:anywhere}
.workflow-notice{font-size:13px;min-height:1.5em}
.codex-open{display:inline-block;font-size:13px;padding:6px 12px;border:1px solid #b8c6dc;
border-radius:7px;background:#fff;text-decoration:none;white-space:nowrap}
.codex-open:hover,.codex-open:focus-visible{background:#edf3fd}
.codex-unavailable{display:block;font-size:13px}
"""


# Served only by the app's loopback server; never embedded in an offline export.
CONTROLS_JS = r"""
(() => {
  'use strict';
  const base = new URL('./', document.currentScript.src);
  const all = selector => Array.from(document.querySelectorAll(selector));
  let state, busy = false, stopped = false, terminalRevision;
  const labels = {queued:'排队中',running:'提取中',completed:'草稿已生成',failed:'提取失败'};
  const key = value => value.session_id + '/' + (value.episode_id || value.id);
  const notice = message => all('.workflow-notice').forEach(el => {el.textContent = message;});
  async function request(route, payload) {
    const response = await fetch(new URL('api/' + route, base), payload === undefined ?
      {cache:'no-store'} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '操作失败');
    return value;
  }
  function render() {
    if (!state) return;
    if (state.pipeline_enabled === false) {
      all('[data-action]').forEach(el => {el.disabled = el.dataset.action !== 'refresh' || busy;});
      all('[data-action="automatic"]').forEach(el => {el.checked = false;});
      notice(state.error);
      return;
    }
    const latest = new Map(), tasks = new Map(state.tasks.map(t => [key(t),t]));
    state.jobs.forEach(job => {if (!latest.has(key(job))) latest.set(key(job),job);});
    all('.workflow-task').forEach(el => {
      const id = el.dataset.session + '/' + el.dataset.episode;
      const job = latest.get(id), task = tasks.get(id), button = el.querySelector('button');
      const ended = task ? state.terminal.includes(task.status) : el.dataset.ended === 'true';
      const pending = job && ['queued','running'].includes(job.status);
      button.disabled = busy || !ended || !!pending || !task;
      button.textContent = !ended ? '任务结束后可提取' : pending ?
        (job.retry_number ? '自动重试 ' + job.retry_number + '/3' : labels[job.status]) :
        !job ? '提取 Workflow' : job.status === 'failed' ? '重试提取 Workflow' : '重新提取 Workflow';
      el.querySelector('.workflow-status').textContent = !task ? '原始任务暂不可读取' :
        !job ? '尚未提取' : job.status_message || labels[job.status] + (job.error ? '：' + job.error : '');
    });
    all('[data-action="automatic"]').forEach(el => {el.checked = state.automatic; el.disabled = busy;});
    all('[data-action="latest"]').forEach(el => {
      el.disabled = busy || !state.tasks.some(t => state.terminal.includes(t.status));
    });
    all('[data-action="retry-failed"]').forEach(el => {
      el.disabled = busy || !Array.from(latest.values()).some(j => j.status === 'failed');
    });
    all('[data-action="refresh"]').forEach(el => {el.disabled = busy;});
  }
  async function poll() {
    if (stopped) return;
    try {
      state = await request('status'); render();
      const revision = JSON.stringify(state.jobs.filter(j => ['completed','failed'].includes(j.status))
        .map(j => [j.id,j.status,j.updated_at]));
      if (terminalRevision !== undefined && terminalRevision !== revision && !busy) {
        busy = true; render();
        try {await request('refresh', {});}
        catch (error) {busy = false; throw error;}
        location.reload(); return;
      }
      terminalRevision = revision;
      notice(state.error || (state.busy ? '前台正在工作；提取任务会等待空闲。' :
        '提取在后台独立进行。重新提取生成新版本，保留已有流程和原始记录。'));
    } catch (_) {
      all('[data-action]').forEach(el => {el.disabled = true;});
      notice('未连接到本机应用。请保持应用运行，并从“记录”按钮重新打开。');
    }
    if (!stopped) setTimeout(poll, 2000);
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-action]');
    if (!button || button.disabled || busy) return;
    const action = button.dataset.action;
    const task = button.closest('.workflow-task');
    const payload = action === 'extract' ? {session_id:task.dataset.session,episode_id:task.dataset.episode} :
      action === 'automatic' ? {automatic:button.checked} : {};
    busy = true; render(); notice('正在提交…');
    try {
      const result = await request(action, payload);
      if (action === 'refresh') {location.reload(); return;}
      state = await request('status');
      notice(result.message);
    } catch (error) {notice(error.message);}
    finally {busy = false; render();}
  });
  window.addEventListener('pagehide', () => {stopped = true;});
  poll();
})();
"""
