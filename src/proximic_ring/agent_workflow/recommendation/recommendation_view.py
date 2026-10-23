"""统计解释与主动请求 UI。离线导出只展示快照；真实曝光由前台可见确认。"""
import html
import json

esc = html.escape


def score_card(item):
    feedback = item['feedback']
    return (f"<p class='group-score'>推荐分数 <strong>{item['score']:.4f}</strong> · {esc(item['statistics']['used_level'])}</p>"
            f"<p class='group-meta'>{esc('；'.join(item['reasons']))}</p>"
            f"<p class='group-meta'>已确认曝光 {feedback['exposures']} · 接受 {feedback['accepted']} · 拒绝 {feedback['rejected']}"
            '（反馈不参与评分）</p>')


def score_detail(item, result):
    return ("<section class='group-section'><h2>推荐分数与依据</h2>"
            "<p class='group-meta'>导出时的真实使用统计快照：未提供当前唤起场景，按总体频率回退。此页面不计推荐曝光。</p>"
            + score_card(item) + "<details><summary>计数、平滑过程与特征快照</summary><pre>"
            + esc(json.dumps(dict(policy=result['policy'], context=result['context'], generated_at=result['generated_at'],
                                 statistics=item['statistics'], features=item['features']), ensure_ascii=False, indent=2))
            + '</pre></details></section>')


def controls():
    return """<h2>主动获取推荐</h2>
<p>点击后按唤起场景排序全部通用 Workflow，默认展示前 4 个。选择只记录接受反馈，暂不执行任务；不自动弹窗。</p>
<div id='operation-panel'><label>范围 <select id='op-scope'><option value='user'>真实使用</option><option value='test'>测试演示</option></select></label>
<label>场景 <select id='op-app'><option value=''>最近唤起场景 / 未知</option><option value='com.apple.Notes'>模拟：备忘录</option><option value='com.apple.Music'>模拟：音乐</option><option value='com.apple.Safari'>模拟：Safari</option></select></label>
<label>展示 <select id='op-limit'><option value='4'>前 4 个</option><option value='5'>前 5 个</option><option value='10'>前 10 个</option></select></label>
<button id='op-recommend' disabled>获取推荐</button>
<p id='op-message' role='status'>离线页面可查看统计快照；通过应用记录入口或 serve 服务打开后可请求推荐和反馈。</p>
<div id='op-items'></div></div>"""


OPERATIONS_JS = r"""
(() => {
  const panel=document.getElementById('operation-panel'); if(!panel) return;
  const base=new URL('./',document.currentScript.src), button=document.getElementById('op-recommend');
  const notice=document.getElementById('op-message'), items=document.getElementById('op-items');
  const scope=document.getElementById('op-scope'), app=document.getElementById('op-app'), limit=document.getElementById('op-limit');
  let generation=0, observer=null, trackers=[];
  button.disabled=false;
  async function post(route,payload){
    const response=await fetch(new URL('api/operations/'+route,base),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const data=await response.json();if(!response.ok)throw Error(data.error||'操作失败');return data;
  }
  const foreground=()=>document.visibilityState==='visible' && document.hasFocus();
  function cancel(t){if(t.timer){clearTimeout(t.timer);t.timer=null;}}
  function arm(t){
    if(t.done||t.pending||t.exposure||!t.visible||!foreground()||t.generation!==generation){cancel(t);return;}
    if(t.timer)return;
    t.timer=setTimeout(async()=>{
      t.timer=null;if(!t.visible||!foreground()||t.generation!==generation)return;
      t.pending=true;
      try{
        const data=await post('exposure',{request_id:t.request,operation_id:t.operation});
        t.exposure=data.exposure_id;t.status.textContent='已确认可见，可选择或反馈';
        t.buttons.forEach(b=>b.disabled=false);t.retry.hidden=true;
      }catch(e){t.status.textContent='曝光确认失败：'+e.message;t.retry.hidden=false;}
      finally{t.pending=false;}
    },500);
  }
  function visibility(){for(const t of trackers){cancel(t);arm(t);}}
  document.addEventListener('visibilitychange',visibility);
  window.addEventListener('focus',visibility);window.addEventListener('blur',visibility);
  app.onchange=()=>{if(app.value){scope.value='test';scope.disabled=true;}else scope.disabled=false;};
  button.onclick=async()=>{
    const current=++generation;button.disabled=true;trackers.forEach(cancel);trackers=[];
    if(observer)observer.disconnect();items.replaceChildren();
    try{
      const payload={scope:scope.value,limit:Number(limit.value),trigger:'manual'};
      if(app.value)payload.context={source:'manual_demo',app:app.value};
      const data=await post('recommend',payload);if(current!==generation)return;
      notice.textContent=data.explanation+' 排序 '+data.candidate_count+' 个，展示前 '+data.items.length+' 个。场景：'+
        (data.context.app||'应用未知')+'；'+(data.context.local_hour===null?'时间未知':data.context.local_hour+' 点')+
        '；缺失：'+(data.context.missing.join(', ')||'无')+(data.context_recording_error?'；'+data.context_recording_error:'');
      observer=new IntersectionObserver(entries=>{
        for(const entry of entries){const t=trackers.find(x=>x.target===entry.target);if(!t)continue;
          t.visible=entry.isIntersecting&&entry.intersectionRatio>=0.5;if(!t.visible)cancel(t);else arm(t);}
      },{threshold:[0,0.5,1]});
      for(const [position,item] of data.items.entries()){
        const block=document.createElement('article'),target=document.createElement('div'),heading=document.createElement('h3');
        heading.textContent=(position+1)+'. '+item.name+' · '+item.score.toFixed(4);target.append(heading);
        const info=document.createElement('p');info.textContent=item.reasons.join('；');target.append(info);block.append(target);
        const link=document.createElement('a');link.href='group-'+encodeURIComponent(item.operation_id)+'.html';link.textContent='查看通用流程与组内成员';block.append(link);
        const missing=document.createElement('p');missing.textContent='本次仍需提供：'+(item.missing_parameters.join('、')||'无必填参数缺失；使用条件仍需确认');block.append(missing);
        const detail=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');
        summary.textContent='评分依据、计数与已确认反馈';pre.textContent=JSON.stringify({statistics:item.statistics,feedback:item.feedback,features:item.features},null,2);
        detail.append(summary,pre);block.append(detail);
        const status=document.createElement('p');status.textContent='等待推荐摘要在前台可见 500ms 后确认曝光';block.append(status);
        const retry=document.createElement('button');retry.textContent='重试曝光确认';retry.hidden=true;block.append(retry);
        const t={target,request:data.request_id,operation:item.operation_id,generation:current,visible:false,pending:false,done:false,exposure:null,timer:null,buttons:[],status,retry};
        retry.onclick=()=>arm(t);
        for(const [kind,label] of [['accepted','选择此操作'],['rejected','现在不需要'],['ignored','暂不判断']]){
          const b=document.createElement('button');b.textContent=label;b.disabled=true;t.buttons.push(b);
          b.onclick=async()=>{t.buttons.forEach(x=>x.disabled=true);
            try{const result=await post('feedback',{exposure_id:t.exposure,operation_id:item.operation_id,kind});
              t.done=true;b.textContent='已记录：'+label;status.textContent=result.message;
              if(result.selection){status.textContent='已选择 '+item.name+'（版本 '+(result.selection.workflow_revision||'未建立')+'）；尚未执行。'+
                (result.selection.missing_parameters.length?'仍需参数：'+result.selection.missing_parameters.join('、'):'');}
            }catch(e){status.textContent=e.message;t.buttons.forEach(x=>x.disabled=false);}};
          block.append(b);
        }
        items.append(block);trackers.push(t);observer.observe(target);
      }
    }catch(e){notice.textContent=e.message;}finally{button.disabled=false;}
  };
})();
"""
