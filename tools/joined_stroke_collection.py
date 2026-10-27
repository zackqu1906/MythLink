"""Study labels for one physical contact that may encode two logical strokes."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import random
import uuid

from selection_capture import SAMPLE_PERIOD_MS, now, validate_trace

SCHEMA = 'ring-joined-stroke-collection'
PLAN_VERSION = 2
FILE_PREFIX = 'joined-strokes'
TITLE = '连笔适配 · 反／的／了／子／衤专项采集'
INTRO = '按提示写连笔或单笔对照；动作符合提示就保存，屏幕轨迹或现有预测不准也保留。'
HIDE_CANDIDATES = True
TRAIN_ROUNDS = EVALUATION_ROUNDS = 2
SETUP_NOTES_LABEL = '佩戴／实际写法'
DEFAULT_SETUP_NOTES = '请记录佩戴手指、模块朝向；反首笔习惯横还是撇、了末笔写法；验证时是否重新佩戴'
PLAN_HELP = '默认2轮，新计划共70条，可设1轮35条。旧记录保持原计划续采。1开始／保存下一条，2重采；一条提示只做一次接触。连笔中途不抬手；单笔对照只写指定一笔。'
COMPLETION_NOTE = '已自动保存。请另建“独立验证”记录，稍后或重新佩戴后再采；保留两份JSON及旁边的events.jsonl。'


def _inventory_v1():
    # Historical labels and instructions are immutable so v1 remains resumable.
    tasks = []
    for target, title, codes, note in (
        ('fan', '反 · 前两笔连写', ['pp', 'hp'], '只写“反”的前两笔，按你的习惯连接短撇（或横）和第二笔撇，不写后面的又。'),
        ('de', '的 · 前两笔连写', ['ps'], '只写“的”左边白的前两笔：短撇接竖，不写后面的横折和横。'),
        ('le', '了 · 整字一笔写完', ['zs', 'zz'], '把“了”的横撇和末笔钩按日常习惯连起来，写完整个了。'),
    ):
        for variant, suffix, style in (
            ('natural', '自然写法', '按平时的幅度和速度写，不刻意停顿。'),
            ('small', '小幅写法', '自然缩小整条轨迹，不省掉原本要写的部分。'),
            ('fast', '自然快写', '按日常较快的速度写，不刻意夸大连接线。'),
        ):
            tasks.append(dict(label=f'joined-{target}-{variant}', kind='joined', target=target,
                title=f'{title} · {suffix}', note=note + style + ' 中途保持触桌，全部完成后抬手；不要跟屏幕修正动作。',
                expectedCodes=codes, expectedLogicalStrokes=2, variant=variant))
    for label, title, codes, note in (
        ('fan-first', '反 · 仅第一笔', ['p', 'h'], '只写反的第一笔短撇（习惯横起就写横，并在佩戴／实际写法中记录）。'),
        ('fan-second', '反 · 仅第二笔', ['p'], '只写反的第二笔长撇。'),
        ('de-first', '的 · 仅第一笔', ['p'], '只写的左边白的第一笔短撇。'),
        ('de-second', '的 · 仅第二笔', ['s'], '只写的左边白的第二笔竖。'),
        ('le-first', '了 · 仅第一笔', ['z'], '只写了的第一笔横撇。'),
        ('le-second', '了 · 仅第二笔', ['s', 'z'], '只写了的第二笔钩，按你平时的竖钩／弯钩写法。'),
    ):
        tasks.append(dict(label='single-'+label, kind='single-control', title=title,
            note=note+' 写完这一笔就抬手，不接下一笔。', expectedCodes=codes, expectedLogicalStrokes=1))
    for shape, code, example in (
        ('横撇', 'z', '又的第一笔'), ('横折', 'z', '日的第二笔'),
        ('横折钩', 'z', '月的第二笔'), ('横撇弯钩', 'z', '队左耳旁的第一笔'),
        ('撇折', 'z', '公下方厶的第一笔'), ('撇点', 'z', '女的第一笔'),
        ('竖折', 'z', '山的第二笔'), ('竖钩', 's', '小的第一笔'),
        ('弯钩', 'z', '狗左边犭的第二笔'),
    ):
        tasks.append(dict(label='fold-'+shape, kind='fold-control', title=f'单笔对照 · {shape}',
            note=f'只写{example}（{shape}），这是一个正常笔画。按平时写法完成后抬手，不写整个字。',
            shape=shape, expectedCodes=[code], expectedLogicalStrokes=1))
    return tasks


def inventory(plan_version=PLAN_VERSION):
    if plan_version not in (1, 2):
        raise ValueError('不支持的连笔采集计划。')
    tasks = _inventory_v1()
    if plan_version == 1:
        return tasks
    for target, title, codes, note in (
        ('zi', '子 · 前两笔连写', ['zs', 'zz'],
         '只写“子”的前两笔横撇接钩，按你写“好、字、学”中子部件的习惯连写；不要写最后一横。'),
        ('yi-pie-dian', '衣字旁衤 · 末两笔撇点连写', ['pn'],
         '想象“被、补、初”左边的衣字旁衤，只连写最后的短撇和点，形成日常的小撇点；前面的点、横撇和竖都不写。这两笔对应撇＋点，不是女字的一笔撇点。'),
    ):
        for variant, suffix, style in (
            ('natural', '自然写法', '按平时的幅度和速度写，不刻意停顿。'),
            ('small', '小幅写法', '自然缩小整条轨迹，不省掉原本要写的部分。'),
            ('fast', '自然快写', '按日常较快的速度写，不刻意夸大连接线。'),
        ):
            tasks.append(dict(label=f'joined-{target}-{variant}', kind='joined', target=target,
                title=f'{title} · {suffix}', note=note+style+' 中途保持触桌，全部完成后抬手；不要跟屏幕修正动作。',
                expectedCodes=codes, expectedLogicalStrokes=2, variant=variant))
    for label, title, codes, note in (
        ('zi-first', '子 · 仅第一笔', ['z'], '只写子的第一笔横撇。'),
        ('zi-second', '子 · 仅第二笔', ['s', 'z'], '只写子的第二笔钩，按平时的竖钩／弯钩写法。'),
        ('yi-pie', '衣字旁衤 · 仅末尾的撇', ['p'], '只写被、补、初左边衤的最后两笔中的短撇，不接最后一点。'),
        ('yi-dian', '衣字旁衤 · 仅最后的点', ['n'], '只写被、补、初左边衤的最后一点，不写前面的撇。'),
    ):
        tasks.append(dict(label='single-'+label, kind='single-control', title=title,
            note=note+' 写完这一笔就抬手，不接下一笔。', expectedCodes=codes, expectedLogicalStrokes=1))
    tasks.append(dict(label='fold-撇点-small', kind='fold-control', title='单笔对照 · 小幅撇点',
        note='只写“女”的第一笔撇点，缩小到平时偏旁中短笔画的幅度，仍是一笔正常撇点。完成后抬手，不写整个女。',
        shape='撇点', variant='small', expectedCodes=['z'], expectedLogicalStrokes=1))
    return tasks


@lru_cache(maxsize=1)
def baseline():
    from importlib.resources import files
    from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
    dictionary = StrokeDictionary(recognizer='dtw')
    dictionary.recognize([(0, 0), (1, 0)])
    assets = files('proximic_ring').joinpath('assets')
    hashes = {name: hashlib.sha256(assets.joinpath(name).read_bytes()).hexdigest()
              for name in ('strokes.json.gz', 'stroke-personal.json.gz') if assets.joinpath(name).is_file()}
    return dictionary, dict(recognizer='dtw_v1', sampleCount=32, radius=6,
        directionWeight=dictionary._dtw.weight, templateProfile=dictionary._dtw.profile,
        assetSha256=hashes, adaptiveLearning=False)


def new_session(participant, phase='train', rounds=TRAIN_ROUNDS, device=''):
    if (not participant.strip() or phase not in ('train', 'evaluation')
            or not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 30):
        raise ValueError('请填写参与者编号，并选择1–30轮。')
    session_id = str(uuid.uuid4())
    rng = random.Random(session_id)
    tasks = []
    for rnd in range(1, rounds+1):
        block = inventory()
        rng.shuffle(block)
        tasks.extend({**task, 'round': rnd} for task in block)
    return dict(schema=SCHEMA, version=1, planVersion=PLAN_VERSION, sessionId=session_id,
        participant=participant.strip(), phase=phase, device=device, createdAt=now(),
        source='ring-sdk-touchpad', setupNotes=DEFAULT_SETUP_NOTES,
        coordinates=dict(xPositive='right', yPositive='down', unit='sdk-displacement',
            samplePeriodMs=SAMPLE_PERIOD_MS, normalization='none'),
        baseline=baseline()[1], tasks=tasks, samples=[], rejected=[], interruptions=[], draft=None)


def validate_session(data):
    if (not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('version') != 1
            or data.get('planVersion') not in (1, 2) or data.get('phase') not in ('train', 'evaluation')
            or not isinstance(data.get('participant'), str) or not data['participant'].strip()
            or not isinstance(data.get('sessionId'), str) or not isinstance(data.get('setupNotes'), str)):
        raise ValueError('不是兼容的连笔采集记录。')
    tasks, samples = data.get('tasks'), data.get('samples')
    known = {t['label']: t for t in inventory(data['planVersion'])}
    size = len(known)
    if not isinstance(tasks, list) or not tasks or len(tasks) % size or len(tasks) > 30*size:
        raise ValueError('连笔采集计划不完整。')
    for i, task in enumerate(tasks):
        base = known.get(task.get('label')) if isinstance(task, dict) else None
        if base is None or task != {**base, 'round': i//size+1}:
            raise ValueError('任务标签或逻辑笔数与计划不一致。')
    for start in range(0, len(tasks), size):
        if {t['label'] for t in tasks[start:start+size]} != set(known):
            raise ValueError('本轮有重复或遗漏任务。')
    if not isinstance(samples, list) or len(samples) > len(tasks):
        raise ValueError('样本数量不正确。')
    for i, sample in enumerate(samples):
        if not isinstance(sample, dict) or sample.get('taskIndex') != i or sample.get('task') != tasks[i]:
            raise ValueError('样本与任务不一致。')
        validate_trace(sample)
    if data.get('draft') is not None:
        if len(samples) == len(tasks):
            raise ValueError('完成后仍有草稿。')
        validate_trace(data['draft'])
    return data


def sample_annotations(trace, task):
    # Ground truth belongs to task only. Never feed it into a prediction.
    dictionary, source = baseline()
    prediction = dictionary.recognize([(p['x'], p['y']) for p in trace['points']])
    return dict(singleStrokeBaseline=prediction, baselineSource=source)


def trace_details(trace, task):
    return '只根据你是否完成提示动作决定保存；现有单笔模型结果保存到JSON，采集中不展示，避免跟随预测改写法。'
