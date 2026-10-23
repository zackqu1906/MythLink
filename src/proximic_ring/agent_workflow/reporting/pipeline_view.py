"""四个独立阅读模块，按真实流程排序；一个目标页面只提供一个主入口。"""
import html


def render_pipeline(root, link, stats):
    labels = {'completed': '提取成功', 'failed': '提取失败', 'queued': '排队中', 'running': '提取中'}
    stages = (
        ('tasks', '用户调用 Agent：任务与原始记录',
         '提交任务 → Agent 执行 → 异步记录调用、返回内容与任务结束状态。',
         f"{stats['tasks']} 个任务 · {stats['sessions']} 个会话", 'index.html', '查看任务记录',
         'sessions/<session_id>/：原始事件、任务索引、已返回附件。'),
        ('workflows', '从任务记录提取 Workflow',
         '任务结束后，在后台根据证据提取自然语言步骤、参数和条件。先看流程概括，再展开完整步骤。',
         ' · '.join(f"{labels.get(j['status'], j['status'])} {j['n']}" for j in stats['extraction_jobs']) or '尚无提取任务',
         'workflows/library.html', '查看 Workflow 库',
         'workflows/：队列、自然语言 Workflow、内部 JSON 和来源审计。'),
        ('groups', '归组与通用 Workflow',
         'BGE-M3 召回候选组 → 同类复核 → 入组或建组。已有组有实质新增时才生成候选修订，验证启用前继续使用当前版。',
         f"{stats['groups']} 个组 · {stats['memberships']} 条成员关系", 'operations/index.html', '查看分组与通用流程',
         'operations/：成员、通用流程、候选和历史版本、召回及复核依据。'),
        ('recommendations', '用户唤起推荐、选择并再次执行',
         '手势或按钮唤起 → 获取当前场景 → 本地排序前四项 → 用户选择并填参数 → 现有 Agent 执行，产生下一次任务记录。',
         f"{stats['requests']} 次推荐请求 · {stats['exposures']} 次曝光 · {stats['feedback']} 条反馈",
         'recommendations/index.html', '查看推荐与执行反馈',
         'operations/catalog.sqlite3 的 recommendation_* 表；本页展示已保存的推荐与执行快照。'),
    )
    body = '''<style>
.pipeline{list-style:none;padding:0;counter-reset:stage}.pipeline>li{counter-increment:stage;display:grid;grid-template-columns:42px minmax(0,1fr);gap:14px;margin-bottom:14px}
.pipeline>li::before{content:counter(stage,decimal-leading-zero);color:#245eb5;font-size:18px;font-weight:700;padding-top:18px;text-align:center}
.pipeline article{margin:0}.pipeline h3{margin:0 0 8px;font-size:20px}.pipeline .count{font-weight:600;color:#186657}
</style><p>从用户任务开始，按四个模块查看。推荐由用户之后主动唤起，不会因归并结束而自动弹出。</p><ol class="pipeline">'''
    for key, title, summary, count, target, label, location in stages:
        body += f'<li id="stage-{key}"><article><h3>{html.escape(title)}</h3><p>{html.escape(summary)}</p>'
        body += f'<p class="count">{html.escape(count)}</p><p>{link(root, target, label)}</p>'
        body += '<details><summary>数据保存位置</summary><p>' + html.escape(location) + '</p></details></article></li>'
    return body + '</ol>'
