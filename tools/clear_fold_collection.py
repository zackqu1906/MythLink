"""Targeted handwriting versus clear-all study; preserve every confirmed trace."""
import random
import uuid

from selection_capture import SHAPES, SAMPLE_PERIOD_MS, now, validate_trace
from proximic_ring.touchpad.stroke.stroke_test_gestures import recognize, geometry, CLEAR, SELECTION, DELETE, DELETION_SOURCE
from proximic_ring.touchpad.stroke.stroke_clear_review import snapshot as clear_review_snapshot, vertical_structure

SCHEMA = 'ring-clear-fold-collection'
PLAN_VERSION = 2
FILE_PREFIX = 'clear-fold'
TITLE = '折笔／往返清空 · 横折弯钩与横撇弯钩重点采集'
INTRO = '只采集并显示预测，不执行清空；动作符合提示就保存，即使预测错也保留。'
HIDE_CANDIDATES = True
TRAIN_ROUNDS = 2
EVALUATION_ROUNDS = 2
SETUP_NOTES_LABEL = '佩戴／基准'
DEFAULT_SETUP_NOTES = '请记录手指、模块朝向、松紧、是否重新佩戴及实际写法'
PLAN_HELP = '横折弯钩与横撇弯钩重点采；覆盖全部25种折笔及3/4段清空、2段往返和普通笔画对照。默认新计划2轮，可先设1轮或分次续采。旧记录保持旧计划。1开始／保存下一笔，2重采。'


def _inventory_v1():
    tasks = []
    def add(label, kind, title, note, expected, **fields):
        tasks.append(dict(label=label,kind=kind,title=title,note=note+' 全程触桌，结束抬手；不要跟屏幕纠正动作。',expectedAction=expected,**fields))
    for variant, title, note in [
        ('natural','自然写法','按平时写横折弯钩：向右、向下、弯向右、末端钩起。'),
        ('short','小幅','整笔写小，保留真实弯和钩。'),
        ('long','大幅','舒适范围内整笔加长。'),
        ('slow','较慢','放慢写完整一笔。'),
        ('fast','较快','按你自然较快的速度完成，不刻意夸张。'),
        ('shallow-down','下降较浅','折后向下的一段短一些，仍有下降。'),
        ('deep-down','下降较深','折后向下的一段长一些，再弯向右。'),
        ('small-hook','末钩较小','末端钩写短，仍确实钩起。')]:
        add('fold-'+variant,'stroke','横折弯钩 · '+title,note,'stroke',shape='横折弯钩',category='z',variant=variant)
    for shape, route in [('横折钩','右、下、末端钩'),('横折弯','右、下、弯向右'),('竖弯钩','下、弯右、末端钩'),('卧钩','平卧弯右、末端钩')]:
        for variant, suffix in [('natural','自然'),('short','小幅')]:
            add('stroke-'+shape+'-'+variant,'stroke',shape+' · '+suffix,'只写这笔：'+route+'。','stroke',shape=shape,category='z',variant=variant)
    for direction, first, second in [('left','左','右'),('right','右','左')]:
        for count in (3,4):
            route=' → '.join(first if i%2==0 else second for i in range(count))
            for variant, note in [('natural','自然水平往返。'),('short','小幅水平往返，每段仍明确移动。')]:
                add(f'clear-{direction}-{count}-{variant}','clear',route+' · '+variant,
                    f'{note}共{count}段、{count-1}次换向；不先上滑。','clear-all',firstDirection=direction,targetSegments=count,variant=variant)
        for variant, note in [('natural','自然幅度'),('short','小幅')]:
            add(f'two-{direction}-{variant}','control',f'{first} → {second} · 仅两段',
                note+'往返一次，仅两段，不能标为清空。','not-clear',targetSegments=2,firstDirection=direction,variant=variant)
    for shape, category, note in [('横','h','水平向右'),('竖','s','竖直向下'),('撇','p','向左下写撇'),('浅撇','p','较平但仍下降的自然撇')]:
        add('ordinary-'+shape,'stroke',shape,'只写一笔：'+note+'。','stroke',shape=shape,category=category)
    for variant, note in [('pause-down','下降后短暂停住再继续弯和钩'),('pause-hook','末端钩起之前短暂停住')]:
        add('fold-'+variant,'stroke','横折弯钩 · '+note,note+'，只是一笔横折弯钩。','stroke',shape='横折弯钩',category='z',variant=variant)
    for direction, route in [('left','左 → 右 → 左'),('right','右 → 左 → 右')]:
        add('clear-pause-'+direction,'clear',route+' · 换向停顿','每次换向时短暂停住，再做下一段，共三段。','clear-all',firstDirection=direction,targetSegments=3,variant='pause')
    return tasks


def inventory(plan_version=PLAN_VERSION):
    if plan_version not in (1,2): raise ValueError('不是兼容的折笔／清空计划。')
    tasks = _inventory_v1()
    if plan_version == 1: return tasks
    # Leave the complete v1 labels and notes untouched for resumable recordings.
    for task in _inventory_v1():
        if task.get('shape') != '横折弯钩': continue
        variant = task['variant']
        notes = {
            'natural':'按平时写横撇弯钩：向右写横，向左下撇，转向弯下，最后钩起。',
            'short':'整笔写小，保留真实横、撇、弯和钩。',
            'long':'舒适范围内整笔加长，按日常写法完成。',
            'slow':'慢一些写完整横撇弯钩。',
            'fast':'按自然较快速度写完整横撇弯钩，不刻意夸张。',
            'shallow-down':'撇段写浅一些，但仍向左下，再弯下和钩起。',
            'deep-down':'撇段写陡一些，再完成弯和钩。',
            'small-hook':'末端钩短一些，但确实钩起。',
            'pause-down':'撇段之后短暂停住，再继续弯下和末端钩。',
            'pause-hook':'末端钩起之前短暂停住，然后完成钩。',
        }
        suffix = task['title'].split(' · ',1)[1]
        tasks.append(dict(task, label='pie-hook-'+variant, shape='横撇弯钩',
                          title='横撇弯钩 · '+suffix,
                          note=notes[variant]+' 全程触桌，结束抬手；不要跟屏幕纠正动作。'))
    existing = {task.get('shape') for task in tasks if task.get('category')=='z'}
    for category, shape, route in SHAPES:
        if category != 'z' or shape in existing: continue
        for variant, suffix, size_note in [('natural','自然写法','按平时自然幅度写。'),
                                          ('short','小幅','缩小整笔，保留真实各段，不省掉转折或末钩。')]:
            tasks.append(dict(label='stroke-'+shape+'-'+variant,kind='stroke',shape=shape,
                category='z',variant=variant,expectedAction='stroke',title=shape+' · '+suffix,
                note=f'只写这一笔：{route}。{size_note} 全程触桌，结束抬手；不要跟屏幕纠正动作。'))
    return tasks


def new_session(participant, phase='train', rounds=TRAIN_ROUNDS, device=''):
    if not participant.strip() or phase not in ('train','evaluation') or not 1<=rounds<=30:
        raise ValueError('请填参与者编号，并选择1–30轮。')
    session_id = str(uuid.uuid4())
    rng = random.Random(session_id)
    tasks = []
    for rnd in range(1,rounds+1):
        block=inventory();rng.shuffle(block)
        tasks.extend({**t,'round':rnd} for t in block)
    return dict(schema=SCHEMA,version=PLAN_VERSION,planVersion=PLAN_VERSION,sessionId=session_id,participant=participant.strip(),phase=phase,
        createdAt=now(),device=device,source='ring-sdk-touchpad',setupNotes=DEFAULT_SETUP_NOTES,
        coordinates=dict(xPositive='right',yPositive='down',unit='sdk-displacement',samplePeriodMs=SAMPLE_PERIOD_MS,normalization='none'),
        classifier=dict(clear=dict(CLEAR),clearReview=clear_review_snapshot(),selection=dict(SELECTION),
                        deletion=dict(DELETE),deletionSource=DELETION_SOURCE,contextAssist=False),
        tasks=tasks,samples=[],rejected=[],interruptions=[],draft=None)


def validate_session(data):
    if not isinstance(data,dict) or data.get('schema')!=SCHEMA:
        raise ValueError('不是横折弯钩／清空采集记录。')
    # Use the same structural checks with this study's own inventory.
    version=data.get('version')
    if version not in (1,2) or data.get('planVersion')!=version:
        raise ValueError('不是兼容的折笔／清空计划版本。')
    known={t['label']:t for t in inventory(version)}
    tasks=data.get('tasks');samples=data.get('samples')
    if (data.get('phase') not in ('train','evaluation')
        or not isinstance(data.get('participant'),str) or not data['participant'].strip()
        or not isinstance(data.get('sessionId'),str) or not isinstance(data.get('setupNotes'),str)):
        raise ValueError('采集记录字段错误。')
    size=len(known)
    if not isinstance(tasks,list) or not tasks or len(tasks)%size or len(tasks)>30*size:
        raise ValueError('采集计划不完整。')
    for i,t in enumerate(tasks):
        if not isinstance(t,dict) or t.get('label') not in known or t!={**known[t['label']],'round':i//size+1}:
            raise ValueError('任务标签不一致。')
    for start in range(0,len(tasks),size):
        if {t['label'] for t in tasks[start:start+size]}!=set(known):raise ValueError('轮次任务重复。')
    if not isinstance(samples,list) or len(samples)>len(tasks):raise ValueError('样本数量错误。')
    for i,s in enumerate(samples):
        if s.get('taskIndex')!=i or s.get('task')!=tasks[i]:raise ValueError('样本与任务不一致。')
        validate_trace(s)
    if data.get('draft') is not None:
        if len(samples)==len(tasks):raise ValueError('已完成但仍有草稿。')
        validate_trace(data['draft'])
    return data


def predict_trace(trace):
    return recognize([(p['x'],p['y']) for p in trace['points']])


def sample_annotations(trace, task):
    metrics=geometry([(p['x'],p['y']) for p in trace['points']])
    metrics.pop('cumulative')
    # Per-sample policy also identifies newly captured trials in resumed v1/v2
    # sessions whose original classifier header must remain historical.
    return dict(clearFoldGeometry=metrics, clearReviewPolicy=clear_review_snapshot(),
                deletionPolicy=dict(source=DELETION_SOURCE,parameters=dict(DELETE)),
                clearReviewGeometry=vertical_structure([(p['x'],p['y']) for p in trace['points']]))


def trace_details(trace, task):
    decision=predict_trace(trace)
    metrics=geometry([(p['x'],p['y']) for p in trace['points']])
    labels={'clear-all':'清空','stroke':'笔画','select':'选字','punctuation':'标点','delete-one':'删除','ignore':'忽略'}
    review=decision.get('clearReview')
    note=' · 清空复核后保留为笔画' if review and review['protect'] else ''
    return f"预测：{labels[decision['action']]}{note} · 横向段数{metrics['segments']} · 横向占比{metrics['horizontalShare']:.2f}。动作符合提示就保存，即使预测错误。"
