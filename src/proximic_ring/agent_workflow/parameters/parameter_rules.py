"""Conservative local extraction; ambiguity is left for the semantic mapper.

Rules never execute recorded code. Definitions/examples are not value evidence.
Every resolved value passes the same provenance validator as a model value.
"""
import ast
import json
import re

VERSION = 'parameter-rules-1'
LITERAL = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')
COUNT = re.compile(r'(?:^|_)(?:count|top_n|limit|quantity)$')
DIGITS = dict(zip('零一二三四五六七八九', range(10)), 两=2, 〇=0)
NUMBER = r'[0-9０-９零〇一二两三四五六七八九十百千]+'


def integer(text):
    """Small unambiguous unsigned integers only; no unit conversion or guessing."""
    import unicodedata
    text = unicodedata.normalize('NFKC', text)
    if re.fullmatch(r'[0-9]+', text):
        return str(int(text))
    if re.fullmatch(r'[零〇一二两三四五六七八九]+', text):
        return str(int(''.join(str(DIGITS[c]) for c in text)))
    if not re.fullmatch(r'[一二两三四五六七八九十百千]+', text):
        return None
    total = digit = 0
    previous = 10000
    for char in text:
        if char in DIGITS:
            if digit: return None
            digit = DIGITS[char]
        else:
            unit = {'十': 10, '百': 100, '千': 1000}[char]
            if unit >= previous: return None
            total += (digit or 1) * unit
            digit = 0; previous = unit
    return str(total + digit)


def count_parameter(parameter):
    return bool(COUNT.search(parameter['name']))


def normalized_value(parameter, value, quotes):
    """Return original span + deterministic conversion for audited numeric values."""
    if not count_parameter(parameter):
        return None
    normalized = integer(value)
    if normalized is None:
        return None
    for quote in quotes:
        for match in re.finditer(NUMBER, quote):
            if integer(match[0]) == normalized:
                return dict(raw_value=match[0], value=normalized, rule='unsigned_integer')
    return None


def entry(name, value, block, rule):
    return dict(name=name, status='observed', value=value,
                evidence=[dict(block_id=block['id'], quote=block['text'])], reason=rule)


def resolve(packet, blocks, previous=None):
    params = packet['general_workflow']['parameters']
    resolved = {}; methods = {}
    # Reuse only current-contract values whose original evidence is still intact.
    if previous and previous['status'] == 'completed':
        for p in previous['parameters']:
            if (p['status'] != 'observed' or p['name'] not in {v['name'] for v in params}
                    or re.search(r'(?:^|_)(?:password|passwd|secret|token|api_key|otp)(?:$|_)',p['name'])): continue
            refs = []
            for ref in p['evidence']:
                src = ref['source']
                b = next((b for b in blocks if all(b['source'].get(k) == src.get(k)
                         for k in ('file', 'sha256', 'json_pointer', 'transform'))
                         and ref['quote'] in b['text']), None)
                if not b: break
                refs.append(dict(block_id=b['id'], quote=ref['quote']))
            else:
                if refs:
                    resolved[p['name']] = dict(name=p['name'], status='observed', value=p['value'],
                        evidence=refs, reason='复用当前参数契约中来源未变化的已核对值')
                    methods[p['name']] = 'verified_history'
    # Incomplete projections cannot establish uniqueness or absence of corrections.
    if packet.get('warnings'):
        return resolved, methods
    user_blocks=[b for b in blocks if b['role'] in ('task','user_input')]
    if any(re.search(r'不要|不是|改成|改为|换成|或者|或是|not\b|instead\b', b['text'], re.I) for b in user_blocks):
        return resolved,methods
    for p in params:
        name = p['name']
        if name in resolved: continue
        if re.search(r'(?:^|_)(?:password|passwd|secret|token|api_key|otp)(?:$|_)', name):
            resolved[name]=dict(name=name,status='missing',value='',evidence=[],reason='认证信息不保存为历史候选')
            methods[name]='omit_credential'
            continue
        candidates = []
        for b in blocks:
            # Exact parameter keys in user-supplied JSON or in explicit tool
            # parameter dictionaries; tool "title" metadata is not a note title.
            if b['role'] in ('task', 'user_input'):
                try: obj = json.loads(b['text'])
                except (ValueError, TypeError): obj = None
                if isinstance(obj, dict) and isinstance(obj.get(name), str) and obj[name] in b['text']:
                    candidates.append((obj[name], b))
            if b['role'] == 'tool_input' and re.search(r'/parameters/' + re.escape(name) + r'$', b['field']):
                candidates.append((b['text'], b))
        distinct = {v for v, _ in candidates if v.strip()}
        if len(distinct) == 1:
            value, b = next((v, b) for v, b in candidates if v.strip())
            resolved[name] = entry(name, value, b, '明确参数字段'); methods[name] = 'exact_field'
    # A sole input-file parameter + one unique path repeated in recorded calls.
    path_params = [p for p in params if re.fullmatch(r'(?:[a-z]+_)*(?:file_path|input_path|source_file)', p['name'])
                   and not re.search(r'output|destination|target', p['name'])]
    if len(path_params) == 1 and path_params[0]['name'] not in resolved:
        paths = {}
        for b in blocks:
            if b['role'] not in ('tool_input', 'decoded_literal'): continue
            texts = [b['text']] if b['role'] == 'decoded_literal' else []
            for m in LITERAL.finditer(b['text']):
                try: v = ast.literal_eval(m[0])
                except (ValueError, SyntaxError): continue
                if isinstance(v, str): texts.append(v)
            for v in texts:
                if (re.match(r'^/(?!/)|^[A-Za-z]:\\', v) and '\n' not in v and v in b['text']):
                    paths.setdefault(v, []).append(b)
        if len(paths) == 1:
            value, evidence = next(iter(paths.items()))
            if len({b['source'].get('call_id') for b in evidence} - {None}) >= 2:
                name = path_params[0]['name']
                resolved[name] = entry(name, value, evidence[-1], '实际调用中重复使用且无冲突的唯一输入文件路径')
                methods[name] = 'unique_input_path'
    counts = [p for p in params if count_parameter(p)]
    if len(counts) == 1 and counts[0]['name'] not in resolved:
        matches = []
        for b in user_blocks:
            for m in re.finditer(r'(?:前(' + NUMBER + r')(?=[位名家所项个条种]|$)|top\s+(' + NUMBER + r')\b)', b['text'], re.I):
                # Negated/corrected/alternative requests need contextual mapping.
                if re.search(r'不要|不是|改成|改为|或者|或是|not\b|instead\b', b['text'], re.I): continue
                matches.append((m[1] or m[2], b))
        # A later request can change "前五家" to "要十家" without saying 改为.
        quantities={integer(m[1]) for b in user_blocks for m in
                    re.finditer('('+NUMBER+r')\s*[家所项个条种]',b['text'])}
        distinct={integer(v) for v,_ in matches}
        if len(distinct) == 1 and matches and not quantities-distinct:
            value, b = matches[-1]; name = counts[0]['name']
            resolved[name] = entry(name, value, b, '用户明确要求的前 N 项')
            methods[name] = 'explicit_count'
    return resolved, methods


def model_packet(packet, resolved):
    """Only unresolved parameter definitions; keep source text and evidence IDs."""
    flow = packet['general_workflow']; source = packet['source_workflow']
    return dict(general_workflow=dict(name=flow['name'], goal=flow['goal'],
                    parameters=[p for p in flow['parameters'] if p['name'] not in resolved]),
                source_workflow=dict(name=source['name'], goal=source['goal'], parameters=source['parameters']),
                resolved_parameters={name: p['value'] for name, p in resolved.items()},
                evidence=packet['evidence'], warnings=packet['warnings'])
