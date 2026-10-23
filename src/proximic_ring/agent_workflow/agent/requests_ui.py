"""Translate server requests into explicit, request-bound UI decisions."""
import copy
import json
import uuid

LABELS = {'accept':'允许','acceptForSession':'允许此会话','decline':'拒绝','cancel':'取消'}
TOOLS = {'get_app_state':'读取应用界面','get_app_screenshot':'截取应用画面',
         'get_app_state_and_screenshot':'读取界面和截图','click':'点击',
         'press_key':'按键','type_text':'输入文字','paste':'粘贴文字',
         'set_value':'填写内容','scroll':'滚动','select_text':'选择文字',
         'perform_secondary_action':'执行界面菜单操作','drag':'拖动'}

def app_label(app):
    # Display aliases only. Unknown application IDs remain fully supported.
    return {'com.apple.calculator':'计算器','com.apple.TextEdit':'TextEdit'}.get(app,app)

def safe(value):
    if isinstance(value, dict):
        return {k: ('[已隐藏]' if any(s in k.lower() for s in ('token','secret','password','challenge','api_key')) else safe(v)) for k,v in value.items()}
    if isinstance(value, list): return [safe(v) for v in value]
    return value

def option(label, result=None, action='reply', enabled=True, reason=''):
    return dict(label=label, result=result, action=action, enabled=enabled, reason=reason)

def is_cua_app_consent(p):
    """Identify the CUA application grant, whose risk describes the whole app.

    Safari's app-access card carries riskLevel=high even for get_app_state.
    Its connector, approval kind and offered persistence distinguish it from
    a separate action confirmation. Keep approving each callback locally so
    cancel/steer can still gate the next action.
    """
    meta=p.get('_meta') or {}
    persist=meta.get('persist') or []
    persist=[persist] if isinstance(persist,str) else persist
    app=(meta.get('tool_params') or {}).get('app')
    return (p.get('serverName')=='cua_repl'
            and meta.get('connector_id')=='computer-use'
            and meta.get('codex_approval_kind')=='mcp_tool_call'
            and isinstance(app,str) and bool(app.strip())
            and isinstance(persist,list) and bool({'session','always'}.intersection(
                value for value in persist if isinstance(value,str)))
            and meta.get('tool_name') in TOOLS)

def build(rid, method, p, *, epoch, supported_ui, routine_ui=False):
    meta=p.get('_meta') or {}
    app=(meta.get('tool_params') or {}).get('app')
    tool=meta.get('tool_name')
    r=dict(id=rid, method=method, params=p, epoch=epoch, token=uuid.uuid4().hex,
           app=app, tool=tool, kind='permission', needsDecision=True, routine=False, source=p.get('serverName') or 'Codex Agent',
           title='需要处理的请求', message=p.get('message') or p.get('reason') or p.get('description') or '',
           options=[], selected=-1, blocked='', answers={}, questionIndex=0,
           appConsent=False)
    opts=r['options']
    if method=='mcpServer/elicitation/request':
        r['title']=TOOLS.get(tool,'应用权限 / 外部服务请求') + (' · '+ app_label(app) if app else '')
        if meta.get('tool_params'):
            r['message']+='\n具体操作：'+json.dumps(safe(meta['tool_params']),ensure_ascii=False)
        schema=p.get('requestedSchema') or {}
        properties=schema.get('properties') or {}
        r['blocked']='' if p.get('mode') in ('form','openai/form','openaiForm') and not properties and not schema.get('required') else '此请求需要尚未接入的表单、身份验证或工具协议；可查看详情、拒绝或取消。'
        r['appConsent']=bool(not r['blocked'] and supported_ui and is_cua_app_consent(p))
        r['routine']=bool(not r['blocked'] and (routine_ui or r['appConsent']))
        persist=meta.get('persist',[])
        persist=[persist] if isinstance(persist,str) else persist
        is_app=meta.get('codex_approval_kind')=='mcp_tool_call' and app and persist
        r['appPermission']=bool(is_app and supported_ui and not r['blocked'])
        if is_app:
            # Match Codex's Computer Use app approval card. No custom actions.
            r['title']='使用应用授权 · '+app_label(app)
            if 'session' in persist:
                opts.append(option('允许此对话',{'action':'accept','content':{},'_meta':{'persist':'session'}},enabled=not r['blocked'],reason=r['blocked']))
            else:opts.append(option('允许此对话',{'action':'accept','content':{}},enabled=not r['blocked'],reason=r['blocked']))
            if 'always' in persist:
                opts.append(option('始终允许',{'action':'accept','content':{},'_meta':{'persist':'always'}},enabled=not r['blocked'],reason='由此戒指客户端保存；后续仅自动执行该应用内的普通操作，额外请求仍会询问。'))
            opts.append(option('拒绝',{'action':'decline','content':None}))
        else:
            opts.extend([option('允许',{'action':'accept','content':{}},enabled=not r['blocked'],reason=r['blocked']),
                         option('拒绝',{'action':'decline','content':None}),
                         option('取消',{'action':'cancel','content':None})])
    elif method in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):
        r['title']='执行命令授权' if 'commandExecution' in method else '修改文件授权'
        decisions=p.get('availableDecisions')
        if decisions is None:decisions=['accept','acceptForSession','decline','cancel']
        for d in decisions:
            label=LABELS.get(d, str(d)) if isinstance(d,str) else json.dumps(d,ensure_ascii=False)
            opts.append(option(label,{'decision':d}))
    elif method=='item/permissions/requestApproval':
        r['title']='新增系统 / 网络 / 文件权限'
        permissions=p.get('permissions')
        valid=isinstance(permissions,dict) and set(permissions)<= {'network','fileSystem'}
        if not valid:r['blocked']='权限请求格式尚未接入，请查看完整请求。'
        opts.extend([option('授予所列权限（本轮）',{'permissions':copy.deepcopy(permissions),'scope':'turn'},enabled=valid,reason=r['blocked']),
                     option('不授予额外权限',{'permissions':{},'scope':'turn'})])
    elif method=='item/tool/requestUserInput':
        r['kind']='question';r['title']='Agent 追问'
        r['questions']=p.get('questions') or []
        configure_question(r)
    elif method=='item/tool/call' and p.get('tool')=='ask_ring_user':
        r['kind']='question';r['title']='Agent 追问'
        args=p.get('arguments') or {}
        r['message']=args.get('question','')
        for label in args.get('options') or []:
            opts.append(option(label,{'success':True,'contentItems':[{'type':'inputText','text':label}]}))
    else:
        r['kind']='unsupported';r['title']='需要人工处理的请求'
        r['blocked']='此接口尚未接入，未自动授权。可取消任务；详细来源和请求内容如下。'
    if method!='item/tool/requestUserInput':finish_options(r)
    r['details']=json.dumps(safe(p),ensure_ascii=False,indent=2)
    return r

def finish_options(r):
    r['selected']=next((i for i,o in enumerate(r['options']) if o['enabled']),-1)

def configure_question(r):
    qs=r['questions'];i=r['questionIndex'];r['options']=[]
    if i>=len(qs):return
    r['token']=uuid.uuid4().hex
    q=qs[i];r['message']=f"问题 {i+1}/{len(qs)}：{q['question']}"
    r['secret']=q.get('isSecret',False)
    r['blocked']='敏感信息请在原应用填写，此测试不通过语音收集。' if r['secret'] else ''
    for o in q.get('options') or []:
        r['options'].append(option(o['label'],o['label'],action='answer',enabled=not r['secret'],reason=o.get('description','')))
    finish_options(r)

def card(r):
    opts=[]
    for i,o in enumerate(r['options']):
        opts.append({k:o[k] for k in ('label','enabled','reason')} | dict(index=i,
            preferred=isinstance(o.get('result'),dict) and o['result'].get('_meta',{}).get('persist')=='always',
            selected=i==r['selected'], gesture='上下滑选择 → tap 确认' if o['enabled'] else '不可选；原因见请求说明'))
    return {k:r.get(k,'') for k in ('token','title','source','message','details','blocked','kind')} | dict(
        requestId=str(r['id']),method=r['method'],options=opts,optionCount=len(opts),
        selected=r['selected'],
        selection=opts[r['selected']]['label'] if opts and r['selected']>=0 else '',
        status='等待你的选择')

def automatic_approval(r):
    """Return a protocol approval, never an answer to an Agent question.

    Approve one request at a time so disabling the mode and cancellation still work.
    Do not install persistent command/network rules or hide future CUA callbacks.
    """
    if r['kind']!='permission' or r['blocked']:return None
    risk=(r['params'].get('_meta') or {}).get('riskLevel')
    if risk is not None and risk!='low' and not r['appConsent']:return None
    enabled=[o for o in r['options'] if o['enabled']]
    if r['method']=='mcpServer/elicitation/request':
        for o in enabled:
            result=o.get('result')
            if isinstance(result,dict) and result.get('action')=='accept':
                return {k:copy.deepcopy(v) for k,v in result.items() if k!='_meta'}
    elif r['method'] in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):
        for decision in ('accept','acceptForSession'):
            for o in enabled:
                if o.get('result',{}).get('decision')==decision:
                    return copy.deepcopy(o['result'])
    elif r['method']=='item/permissions/requestApproval':
        if enabled and enabled[0]['result'].get('permissions')==r['params'].get('permissions'):
            return copy.deepcopy(enabled[0]['result'])
    return None

def decline_result(r):
    if r['method']=='mcpServer/elicitation/request':return {'action':'decline','content':None}
    if r['method'] in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):
        return {'decision':'decline'}
    return cancel_result(r)

def cancel_result(r):
    m=r['method']
    if m=='mcpServer/elicitation/request':return {'action':'cancel','content':None}
    if m in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):return {'decision':'cancel'}
    if m=='item/permissions/requestApproval':return {'permissions':{},'scope':'turn'}
    if m=='item/tool/requestUserInput':return {'answers':{}}
    if m=='item/tool/call':return {'success':False,'contentItems':[{'type':'inputText','text':'用户取消了请求。'}]}
    return None
