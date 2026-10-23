"""共享文本/版本标识与应用提示；不包含聚类、模型或推荐逻辑。"""
import hashlib
import json
import re
import unicodedata

APP_ALIASES = {'com.apple.Notes': ('备忘录', 'notes'),
               'com.apple.Music': ('苹果音乐', 'apple music', 'music'),
               'com.apple.Safari': ('safari',)}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def normalize(text):
    text=unicodedata.normalize('NFKC',text).lower()
    text=re.sub(r'\{\{[a-z][a-z0-9_]*\}\}', '参数', text)
    return re.sub(r'\s+', ' ', text).strip()


def app_tags(flow):
    primary=normalize(flow['name']+' '+flow['goal'])
    body=normalize(json.dumps(flow,ensure_ascii=False))
    tags={bundle for bundle,names in APP_ALIASES.items() if any(n in primary for n in names)}
    if not tags:
        tags={bundle for bundle,names in APP_ALIASES.items() if bundle.lower() in body or any(n in body for n in names)}
    tags.update(re.findall(r'\bcom\.[a-z0-9_-]+(?:\.[a-z0-9_-]+)+',body))
    # Bundle IDs are case insensitive for this feature, but use known canonical labels.
    known={b.lower():b for b in APP_ALIASES}
    return sorted({known.get(b.lower(),b) for b in tags})



def application_conflict(a,b):
    # Generic tasks can reach the same goal through UI, commands or APIs.
    # Application specificity is evaluated by semantic review, never guessed
    # from an application's incidental appearance in a method or output.
    from proximic_ring.agent_workflow.workflow_content import is_plain
    if is_plain(a) or is_plain(b) or a.get('task_profile') or b.get('task_profile'):return False
    left,right=app_tags(a),app_tags(b)
    return bool(left and right and left!=right)

