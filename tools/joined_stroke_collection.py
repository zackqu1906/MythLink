"""Study labels for one physical contact that may encode several logical strokes."""
from __future__ import annotations

from functools import lru_cache, partial
import hashlib
import random
from types import SimpleNamespace
import uuid

from selection_capture import SAMPLE_PERIOD_MS, now, validate_trace

SCHEMA = 'ring-joined-stroke-collection'
PLAN_VERSION = 3
FILE_PREFIX = 'joined-strokes'
TITLE = '连笔补采 · 走之底第二、三笔连写（旧记录可续采）'
INTRO = '按提示写连笔或单笔对照；动作符合提示就保存，屏幕轨迹或现有预测不准也保留。'
HIDE_CANDIDATES = True
REQUIRE_MOTION = True
TRAIN_ROUNDS = EVALUATION_ROUNDS = 2
SETUP_NOTES_LABEL = '佩戴／实际写法'
DEFAULT_SETUP_NOTES = '请记录佩戴手指、模块朝向及走之末两笔实际连写方式；走之第一点仍单独写，验证时注明是否重新佩戴'
PLAN_HELP = '新建v3只补走之：默认2轮26条，可先设1轮13条，不必重采之前70条。v1/v2记录保持原计划续采。1开始／保存下一条，2重采；整段中途不抬手，结束再抬手。'
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
    if plan_version in (4, 6):
        return silk_inventory(legacy=plan_version == 4)
    if plan_version in (5, 7):
        return additional_inventory(legacy=plan_version == 5)
    if plan_version == 3:
        return walk_inventory()
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


def walk_inventory():
    tasks = []
    for context in ('过', '近', '这'):
        for variant, title, style in (
            ('natural', '自然写法', '按日常幅度与速度写。'),
            ('small', '小幅写法', '自然缩小整段，不刻意省掉点。'),
            ('fast', '自然快写', '按日常较快速度写，不刻意夸张。'),
        ):
            tasks.append(dict(label=f'joined-walk-{context}-{variant}', kind='joined', target='walk',
                contextCharacter=context, variant=variant, title=f'{context}的走之底 · 第二、三笔连写 · {title}',
                note=f'想象“{context}”的内部和走之第一点已经写好，本条只将第二笔折与第三笔末捺连成一笔；不写第一点，也不写内部部件。'+style+
                     '中途保持触桌，全部完成后抬手；按实际动作是否符合提示保存，不跟屏幕纠正。',
                expectedCodes=['zn'], expectedLogicalStrokes=2))
    for shape, code, note in (
        ('捺', 'n', '只写人的第二笔捺，舒展向右下，不加前面的点和折。'),
        ('横折折撇', 'z', '只写及的第二笔横折折撇，这是一个正常笔画，不是整个走之底。'),
        ('横撇弯钩', 'z', '只写队左耳旁第一笔横撇弯钩，不写整个字。'),
        ('撇点', 'z', '只写女的第一笔撇点，按日常幅度。'),
    ):
        tasks.append(dict(label='walk-control-'+shape, kind='fold-control', shape=shape,
            title='单笔对照 · '+shape, note=note+' 写完这一笔就抬手。',
            expectedCodes=[code], expectedLogicalStrokes=1))
    return tasks


def joined_task(target, context, variant, title, note, code):
    return dict(label=f'joined-{target}-{context}-{variant}', kind='joined', target=target,
                contextCharacter=context, variant=variant, title=title,
                note=note+' 中间保持触桌，写完这段再抬手。',
                expectedCodes=[code], expectedLogicalStrokes=len(code))


def silk_inventory(*, legacy=False):
    tasks = []
    for context in '红线给绞':
        for variant, title, note in (
            ('natural', '自然写', '用平时的大小和速度。'),
            ('small', '偏小写', '按你写得比较小的时候自然书写。'),
            ('fast', '自然快写', '按平时较快的速度写。'),
        ):
            instruction = ('把两个撇折和最后一提连成一条轨迹。' if legacy else
                           '一气写完，按日常写法自然收笔。')
            tasks.append(joined_task('silk-whole', context, variant,
                f'{context}的纟 · 三笔连成一笔 · {title}',
                f'只写“{context}”左边的纟，{instruction}{note} 右边部件留空。', 'zzh'))
    for context in '红给':
        if legacy:
            tasks.append(joined_task('silk-head', context, 'natural',
                f'{context}的纟 · 前两笔对照', '只连写第一个和第二个撇折，第二个撇折完成后抬手，最后一提留空。', 'zz'))
        else:
            tasks.append(joined_task('silk-whole', context, 'compact',
                f'{context}的纟 · 一笔写完 · 自然收尾', '只写左边的纟，按你平时最省力的连写方式收笔。', 'zzh'))
    for shape, note in (
        ('撇折', '只写纟的第一个撇折，折完就抬手。'),
        ('撇点', '只写女的第一笔撇点，写完就抬手。'),
    ):
        tasks.append(dict(label=f'silk-control-{shape}', kind='fold-control', shape=shape,
                          title=f'一笔对照 · {shape}', note=note, expectedCodes=['z'], expectedLogicalStrokes=1))
    return tasks


def additional_inventory(*, legacy=False):
    alternate_hou = (joined_task('hou-middle', '后', 'natural', '后 · 第二、三笔',
                                 '只连写长撇接横，按你平时的写法。', 'ph') if legacy else
                     joined_task('fan', '后', 'fast', '后 · 前两笔 · 自然快写',
                                 '只连写短撇接长撇，用平时较快的速度。', 'pp'))
    tasks = [alternate_hou]
    for target, context, title, note, code in (
        ('fan', '后', '前两笔', '短撇接长撇', 'pp'),
        ('turning-head', '印', '前两笔', '左边的撇接竖提', 'pz'),
        ('turning-head', '仰', '右边前两笔', '卬的撇接竖提', 'pz'),
        ('turning-head', '氏', '前两笔', '撇接竖提', 'pz'),
        ('water-tail', '水', '最后两笔', '撇接捺', 'pn'),
        ('de', '页', '第二、三笔', '短撇接竖', 'ps'),
        ('wrap-head', '包', '前两笔', '撇接横折钩', 'pz'),
        ('de', '段', '前两笔', '短撇接长竖', 'ps'),
    ):
        tasks.append(joined_task(target, context, 'natural', f'{context} · {title}',
                                  f'只连写{note}，按你平时的写法。', code))
    if legacy:
        # Preserve the ordering used when shuffling the original plan.
        tasks[0], tasks[1] = tasks[1], tasks[0]
    for shape, example in (('竖提', '比的第二笔'), ('横折钩', '包的第二笔'), ('撇点', '女的第一笔')):
        tasks.append(dict(label=f'additional-control-{shape}', kind='fold-control', shape=shape,
                          title=f'一笔对照 · {shape}', note=f'只写{example}，写完就抬手。',
                          expectedCodes=['z'], expectedLogicalStrokes=1))
    return tasks


def collection_profile(plan):
    if plan == 'walk':
        return None  # The original collector uses this module as its profile.
    silk = plan == 'silk'
    version = 6 if silk else 7
    title = '绞丝旁一笔连写采集' if silk else '日常连笔补采'
    rounds = 3 if silk else 2
    notes = '记录佩戴手指和戒指朝向，可补充平时的连写方式。'
    return SimpleNamespace(
        PLAN_VERSION=version, FILE_PREFIX=FILE_PREFIX, TITLE=title,
        INTRO=('只用平时的动作写提示的那一段。每次触桌记录一条轨迹，抬手后确认保存。'),
        TRAIN_ROUNDS=rounds, EVALUATION_ROUNDS=1, HIDE_CANDIDATES=True, REQUIRE_MOTION=REQUIRE_MOTION,
        SETUP_NOTES_LABEL=SETUP_NOTES_LABEL, DEFAULT_SETUP_NOTES=notes,
        PLAN_HELP=('新记录默认 3 轮，含 42 条整个纟和 6 条单笔对照。' if silk else '新记录默认 2 轮，共 24 条。')
                  + ' 按 1 开始／保存，按 2 重写。可续采原记录。',
        COMPLETION_NOTE='已自动保存。请保留 JSON 与同名的事件文件；训练和独立验证分别建记录。',
        inventory=inventory,
        new_session=partial(new_session, plan_version=version),
        validate_session=validate_session, sample_annotations=sample_annotations, trace_details=trace_details)


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


def new_session(participant, phase='train', rounds=TRAIN_ROUNDS, device='', *, plan_version=PLAN_VERSION):
    if (not participant.strip() or phase not in ('train', 'evaluation')
            or not isinstance(rounds, int) or isinstance(rounds, bool) or not 1 <= rounds <= 30):
        raise ValueError('请填写参与者编号，并选择1–30轮。')
    session_id = str(uuid.uuid4())
    rng = random.Random(session_id)
    tasks = []
    for rnd in range(1, rounds+1):
        block = inventory(plan_version)
        rng.shuffle(block)
        tasks.extend({**task, 'round': rnd} for task in block)
    return dict(schema=SCHEMA, version=1, planVersion=plan_version, sessionId=session_id,
        participant=participant.strip(), phase=phase, device=device, createdAt=now(),
        source='ring-sdk-touchpad', setupNotes=(DEFAULT_SETUP_NOTES if plan_version <= 3 else ''),
        coordinates=dict(xPositive='right', yPositive='down', unit='sdk-displacement',
            samplePeriodMs=SAMPLE_PERIOD_MS, normalization='none'),
        baseline=baseline()[1], tasks=tasks, samples=[], rejected=[], interruptions=[], draft=None)


def validate_session(data):
    if (not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('version') != 1
            or data.get('planVersion') not in (1, 2, 3, 4, 5, 6, 7) or data.get('phase') not in ('train', 'evaluation')
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
