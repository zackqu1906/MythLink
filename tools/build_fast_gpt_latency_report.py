"""Offline report for the ten-case stateless GPT latency probe."""
from collections import Counter
from html import escape
import json
from pathlib import Path
import re
import statistics
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'src')]
from tools.check_fast_gpt_latency import OUT
from tools.build_character_clarification_report import renderer
from tools.character_clarification_experiment import digest, jd, save

LABELS={'luna_none':'GPT-6 Luna · none','terra_none':'GPT-6.1 Terra · none','deepseek':'火山 DeepSeek · 关闭思考'}


def build():
    m=json.loads((OUT/'manifest.json').read_text());prompt=(OUT/'v4d.txt').read_text()
    rows=[json.loads(l) for l in (OUT/'generations.jsonl').open()]
    reviews=json.loads((OUT/'reviews.json').read_text());cases={c['id']:c for c in m['cases']}
    assert len(rows)==len({r['id'] for r in rows})==30
    assert {r['id'] for r in rows}=={j['case_id']+'__'+j['variant'] for j in m['jobs']}
    assert digest(prompt)==m['prompt_sha256']
    models={};summary=[];tokens=[]
    for v,label in LABELS.items():
        arm=[r for r in rows if r['variant']==v];ok=[r for r in arm if not r.get('error')]
        t=[r['latency_s'] for r in ok];first=[r['first_text_s'] for r in ok if r.get('first_text_s') is not None]
        u=[r['response']['usage'] if v!='deepseek' else r['calls'][0]['response']['usage'] for r in ok]
        counts=Counter(reviews[r['id']]['status'] for r in arm)
        s={'attempts':len(arm),'success':len(ok),'errors':len(arm)-len(ok),
           'p50_complete_s':statistics.median(t),'p95_complete_s':renderer.percentile(t,.95),
           'min_complete_s':min(t),'max_complete_s':max(t),
           'p50_first_text_s':statistics.median(first) if first else None,
           'within_0_8s':sum(x<=.8 for x in t),'within_1_0s':sum(x<=1 for x in t),
           'semantic_pass':counts['pass'],'semantic_fail':counts['fail'],
           'error_waits_s':[r['latency_s'] for r in arm if r.get('error')]}
        models[v]=s
        summary.append([label,f'{len(ok)}/10',f'{s["p50_complete_s"]:.3f}',f'{s["p95_complete_s"]:.3f}',
                        f'{min(t):.3f}～{max(t):.3f}',f'{s["within_0_8s"]}/10',f'{s["within_1_0s"]}/10',s['errors']])
        tokens.append([label,f'{statistics.mean(x["input_tokens"] for x in u):.1f}',
                       f'{sum(x.get("input_tokens_details",{}).get("cached_tokens",0) for x in u)/sum(x["input_tokens"] for x in u):.1%}',
                       sum(x.get('output_tokens_details',{}).get('reasoning_tokens',0) for x in u),
                       f'{s["p50_first_text_s"]:.3f}' if first else '非流式，不可测'])
    normalized=[];by_id={r['id']:r for r in rows}
    for c in m['cases']:
        for v in LABELS:
            r=by_id[c['id']+'__'+v];inp={'utterance':c['utterance'].strip(),'editable_text':c['target'],'screen_context':c['context']}
            assert r['model_input']==inp and r['input_sha256']==jd(inp)
            rv=reviews[r['id']];assert rv['input_sha256']==r['input_sha256'] and rv['output_sha256']==digest(r['final_text'])
            if v!='deepseek':
                p=r['request'];assert p['instructions']==prompt and len(p['input'])==1
                assert p['input'][0]['content'][0]['text']==json.dumps(inp,ensure_ascii=False)
                assert p['tools']==[] and p['tool_choice']=='none' and p['reasoning']['effort']=='none'
                assert not any(k in p for k in ['previous_response_id','conversation','developer_instructions'])
                if not r.get('error'):
                    assert r['response']['model']==m['models'][v] and r['response']['reasoning']['effort']=='none'
                    assert r['response']['usage']['output_tokens_details']['reasoning_tokens']==0
            else:
                p=r['calls'][0]['request'];assert p['input'][0]['content'][0]['text']==prompt
                assert p['input'][1]['content'][0]['text']==json.dumps(inp,ensure_ascii=False)
            normalized.append({'id':r['id'],'group':v,'instruction':c['utterance'],'source_text':c['target'],
                'context':c['context'],'expected_result':c.get('review_points','无明显改错即通过。')+'\n参考：'+c.get('reference',''),
                'output':r['final_text'],'review':rv,'error':r.get('error',''),'latency_s':r['latency_s'],
                'features':[c['mechanism'],'单次、无历史'],'fields':[
                    {'label':'首段正文耗时','value':f'{r["first_text_s"]:.3f} 秒' if r.get('first_text_s') is not None else '未取得／非流式'},
                    {'label':'评测进程峰值 RSS','value':f'{r["peak_rss_mib"]:.1f} MiB'}],
                'evidence':{'raw_record':'generations.jsonl: '+r['id'],'input_sha256':r['input_sha256'],
                            'output_sha256':r['output_sha256'],'prompt_sha256':m['prompt_sha256']}})
    detail=[]
    for c in m['cases']:
        detail.append([c['id'],c['utterance']]+[
            (f'{by_id[c["id"]+"__"+v]["latency_s"]:.3f} 秒'+(' · 连接失败' if by_id[c['id']+'__'+v].get('error') else '')) for v in LABELS])
    errors=[r for r in rows if r.get('error')]
    data={'schema_version':1,'evaluation':{'classification':False},'groups':LABELS,
          'title':'10 道题延迟探测：Luna none / Terra none / DeepSeek',
          'summary':[
              '结论：本次所测 GPT 路径未达到约 0.8 秒。无工具、无前题历史、无代理环境信息的请求已打通，所有成功 GPT 返回均确认 none 且推理 tokens 为 0。',
              '10 道不同题，每模型每题 1 次，共 30 次请求，单进程串行运行。覆盖连续释字、字位取字、释字中的 ASR 错字、局部精确修改、润色、格式、翻译及组合要求；使用完整同一 v4d，未缩短提示词。样例为定向选取，不代表总体准确率。',
              'GPT 使用本机现有 ChatGPT 登录，直接向 ChatGPT Codex 后端发送请求；没有启动 app-server。客户端请求只有 v4d、当前题和控制参数，不含工具定义或对话历史。仍不是 api.openai.com 的 API Key 服务，不能将结果推广到所有 OpenAI API 路由或加速档。',
              f'共 {len(errors)} 次连接中断，均保留，未重试；连接中断不能直接归因于模型。延迟表的 P50/P95 仅统计成功完整正文，失败耗时逐例另列。≤0.8 秒和≤1.0 秒的分母均为每模型 10 次尝试，失败不会算作达标。',
              f'评测进程实测峰值 {max(r["peak_rss_mib"] for r in rows):.1f} MiB，无额外代理进程。未自动打开浏览器；最终所有请求进程已退出。',
              '输出由 Codex 按无明显错误的宽松口径逐例复核，并绑定输入与输出哈希；没有额外调用评审模型。10 道单次样例只能用于延迟初筛，P95 尤其不稳定。'],
          'methodology':'主耗时从调用开始到完整正文完成，含网络；不含 ASR、OCR、应用写回或认证文件读取。GPT 流式返回，DeepSeek 非流式；首段正文单列，不能把首字时间当成完成时间。P50 使用通常的中位数（偶数样本取中间两项均值），P95 使用 nearest-rank；10 条的小样本中 P95 往往就是最大值。模型先后顺序按冻结随机种子打散；无额外预热，第一条也计入。缓存与服务负载未控制。',
          'metadata':{'manifest':m,'models':models,'generations_sha256':digest((OUT/'generations.jsonl').read_text()),
                      'source_files':{f:digest((ROOT/f).read_text()) for f in ['tools/chatgpt_stateless_text_client.py','tools/check_fast_gpt_latency.py']}},
          'links':[{'label':label,'href':f} for f,label in [('manifest.json','10 道题与冻结条件'),('v4d.txt','完整 v4d'),
                     ('generations.jsonl','全部原始请求和返回'),('reviews.json','逐例复核'),('latency-summary.json','延迟指标 JSON')]],
          'analysis_tables':[
              {'title':'完整结果延迟','description':'成功正文耗时；连接失败不混入成功延迟，但保留在达标率分母。','columns':['模型','成功／尝试','P50 秒','P95 秒','最短～最长','≤0.8 秒','≤1.0 秒','连接失败'],'rows':summary},
              {'title':'逐题完整耗时','description':'每格一次真实请求，未采用重复取最优。','columns':['题号','口述',*LABELS.values()],'rows':detail},
              {'title':'输入、缓存与首段正文','description':'只统计成功返回。token 数受分词器差异影响；请求字节级提示词及用户 JSON 已核对。','columns':['模型','平均输入 tokens','输入缓存占比','推理 tokens 合计','首段正文 P50 秒'],'rows':tokens}],
          'cases':normalized}
    html,metrics=renderer.render(data)
    html=re.sub(r'<div class="kpis">[\s\S]*?(?=<div class="panel"><h3>样例分布与表现)','',html)
    html=html.replace('样例分布与表现','输出复核概览（10 道定向样例，非总体准确率）')
    (OUT/'report.html').write_text(html);save(OUT/'report.json',data)
    save(OUT/'report.metrics.json',metrics);save(OUT/'latency-summary.json',models)
    print(json.dumps(models,ensure_ascii=False,indent=2))


if __name__=='__main__':build()
