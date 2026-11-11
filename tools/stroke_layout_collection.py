"""Whole characters whose stroke codes coincide and only the layout differs.

Each sample is one written character: every complete contact plus the token
window between them. The SDK reports no lifted-hand movement, so stroke
positions are estimated offline from the raw tokens (tools/analyze_stroke_layout.py).
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import random
import uuid

from selection_capture import SAMPLE_PERIOD_MS, now, validate_trace

SCHEMA = 'ring-stroke-layout-collection'
PLAN_VERSION = 2
FILE_PREFIX = 'stroke-layout'
TITLE = '笔画相对位置 · 同码字与左右结构整字采集'
INTRO = '按提示写整个字，笔与笔之间正常抬手；最后一笔抬手后按 1 结束。屏幕只分别显示每一笔，不显示笔间位置。'
MULTI_CONTACT = True
UNIT = '字'
HIDE_CANDIDATES = True
TRAIN_ROUNDS = EVALUATION_ROUNDS = 2
SETUP_NOTES_LABEL = '佩戴／书写方式'
DEFAULT_SETUP_NOTES = '请记录佩戴手指、模块朝向、书写平面（桌面／腿上）、字的大致尺寸；验证时是否重新佩戴'
PLAN_HELP = ('默认2轮。1开始；按日常笔顺写完整个字，最后一笔抬手后按1结束；再按1保存并开始下一个字，2重写。'
             '笔间抬手移动按平时习惯，不刻意停顿或夸大；每笔仍需完整触桌。')
ARMED_NOTE = '已准备：按笔顺写完整个字，笔间正常抬手；最后一笔抬手后按 1 结束。'
IDLE_DETAIL = '一个字一条样本。1 开始／结束本字／保存并下一个字，2 重写；空格同“结束本字”。'
COMPLETION_NOTE = '已自动保存。请另建“独立验证”记录，稍后或重新佩戴后再采；保留 JSON 及旁边的 events.jsonl、tokens.jsonl。'

# Plan 1: same five-class code inside each group; compact layouts differ.
GROUPS = (
    ('pn', '人八入', '撇和点／捺：交于顶部、左右分开、右笔高起'),
    ('pz', '儿几', '两笔左右分开，或在顶部相接'),
    ('hsh', '土士工', '上下两横的长短与竖的位置'),
    ('zp', '刀力', '撇在折内侧下方，或穿出折的顶部'),
    ('hpn', '大丈', '横的位置与撇捺的交点'),
    ('hhpn', '天夫', '撇是否穿出上横'),
    ('phhs', '午牛', '竖是否穿出上横'),
    ('szhsh', '田由', '中竖是否穿出顶部'),
    ('szhhs', '甲申', '中竖向下或上下都穿出'),
    ('hhspn', '未末', '上下两横的长短'),
    ('zhz', '己已巳', '末笔竖弯钩起点的高低'),
)


# Plan 2: left-right versus other frames. Codes follow the hanzi-writer stroke
# order (checked against the dictionary). compareStrokes limits the offline
# comparison to a shared prefix: 打 and 寺 both start hshhs.
GROUPS_V2 = (
    ('这冷', ('nhpnnzn',), '冷左边两点水，令在右；这的走之写在最后', None),
    ('相查', ('hspnszhhh',), '相的木在左、目在右；查的木在上、日和横在下', None),
    ('必议', ('nznpn',), '议的讠在左；必为一个整体', None),
    ('早吁', ('szhhhs',), '吁的口在左、于在右；早的日在上', None),
    ('持封', ('hshhshhsn',), '持在扌之后分左右；封在圭之后分左右', None),
    ('外处', ('pznsn',), '外的夕在左、卜在右；处的夂包着卜', None),
    ('叶申', ('szhhs',), '叶的口在左、十在右；申的竖穿过日', None),
    ('巴孔', ('zshz',), '孔的子在左、乚在右；巴为一个整体', None),
    ('加召', ('zpszh',), '加的力在左、口在右；召的刀在上、口在下', None),
    ('吧邑', ('szhzshz',), '吧的口在左、巴在右；邑的口在上、巴在下', None),
    ('岁帆', ('szspzn',), '帆的巾在左、凡在右；岁的山在上、夕在下', None),
    ('者拍', ('hshpszhh',), '拍的扌在左、白在右；者的土和撇在上、日在下', None),
    ('打寺', ('hshhs', 'hshhsn'), '前五笔同码：打的第四笔横在扌右侧，寺的第四笔横在土下方', 5),
)


def inventory(plan_version=PLAN_VERSION):
    if plan_version not in (1, 2):
        raise ValueError('不支持的笔画位置采集计划。')
    groups = ([(characters, (code,), contrast, None) for code, characters, contrast in GROUPS]
              if plan_version == 1 else GROUPS_V2)
    tasks = []
    for characters, codes, contrast, compare in groups:
        for i, character in enumerate(characters):
            code = codes[min(i, len(codes)-1)]
            others = '／'.join(c for c in characters if c != character)
            task = dict(label=f'layout-{character}', kind='layout', character=character,
                group=characters, expectedCode=code, expectedLogicalStrokes=len(code),
                title=f'写整个字：{character}',
                note=f'按日常笔顺写完整的“{character}”（{len(code)} 笔），注意与{others}的区别：{contrast}。'
                     '按平时大小写，不要为了区分刻意夸大。')
            if plan_version == 2:
                task['compareStrokes'] = compare or len(code)
            tasks.append(task)
    return tasks


@lru_cache(maxsize=1)
def baseline():
    from importlib.resources import files
    from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
    dictionary = StrokeDictionary(recognizer='dtw')
    dictionary.recognize([(0, 0), (1, 0)])
    assets = files('proximic_ring').joinpath('assets')
    hashes = {name: hashlib.sha256(assets.joinpath(name).read_bytes()).hexdigest()
              for name in ('strokes.json.gz',) if assets.joinpath(name).is_file()}
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
            samplePeriodMs=SAMPLE_PERIOD_MS, normalization='none', perContactOrigin=True),
        tokens=dict(file='<json stem>.tokens.jsonl', fields='T1–T3 accel, T4–T6 gyro, token units',
            join='tokenEpoch + step'),
        baseline=baseline()[1], tasks=tasks, samples=[], rejected=[], interruptions=[], draft=None)


def validate_character(sample):
    validate_trace(sample)
    contacts = sample.get('contacts')
    if not isinstance(contacts, list) or not contacts:
        raise ValueError('样本没有笔画。')
    for trace in contacts:
        validate_trace(trace)
    if not all(a['endStep'] <= b['startStep'] for a, b in zip(contacts, contacts[1:])):
        raise ValueError('笔画顺序不正确。')
    if not (isinstance(sample.get('tokenEpoch'), int)
            and sample.get('windowStartStep', 1 << 62) <= contacts[0]['startStep']
            and contacts[-1]['endStep'] <= sample.get('windowEndStep', -1)+1):
        raise ValueError('样本缺少 token 对齐窗口。')


def validate_session(data):
    if (not isinstance(data, dict) or data.get('schema') != SCHEMA or data.get('version') != 1
            or data.get('planVersion') not in (1, 2) or data.get('phase') not in ('train', 'evaluation')
            or not isinstance(data.get('participant'), str) or not data['participant'].strip()
            or not isinstance(data.get('sessionId'), str) or not isinstance(data.get('setupNotes'), str)):
        raise ValueError('不是兼容的笔画位置采集记录。')
    tasks, samples = data.get('tasks'), data.get('samples')
    known = {t['label']: t for t in inventory(data['planVersion'])}
    size = len(known)
    if not isinstance(tasks, list) or not tasks or len(tasks) % size or len(tasks) > 30*size:
        raise ValueError('笔画位置采集计划不完整。')
    for i, task in enumerate(tasks):
        base = known.get(task.get('label')) if isinstance(task, dict) else None
        if base is None or task != {**base, 'round': i//size+1}:
            raise ValueError('任务与计划不一致。')
    for start in range(0, len(tasks), size):
        if {t['label'] for t in tasks[start:start+size]} != set(known):
            raise ValueError('本轮有重复或遗漏任务。')
    if not isinstance(samples, list) or len(samples) > len(tasks):
        raise ValueError('样本数量不正确。')
    for i, sample in enumerate(samples):
        if not isinstance(sample, dict) or sample.get('taskIndex') != i or sample.get('task') != tasks[i]:
            raise ValueError('样本与任务不一致。')
        validate_character(sample)
    if data.get('draft') is not None:
        if len(samples) == len(tasks):
            raise ValueError('完成后仍有草稿。')
        validate_character(data['draft'])
    return data


def sample_annotations(trace, task):
    # Ground truth belongs to task only. Never feed it into a prediction.
    dictionary, source = baseline()
    predictions = [dictionary.recognize([(p['x'], p['y']) for p in contact['points']])
                   for contact in trace['contacts']]
    code = ''.join(p['category'] if p else '?' for p in predictions)
    return dict(perStrokeBaseline=predictions, baselineCode=code, baselineSource=source,
                strokeCountMatches=len(trace['contacts']) == task['expectedLogicalStrokes'])


def trace_details(trace, task):
    count, expected = len(trace['contacts']), task['expectedLogicalStrokes']
    if count != expected:
        return f'采到 {count} 笔，提示字为 {expected} 笔；若有连笔或漏笔请按 2 重写，确属日常写法可保存。'
    return '笔数一致。只根据是否按提示写完决定保存；不要按屏幕轨迹好不好看筛选。'
