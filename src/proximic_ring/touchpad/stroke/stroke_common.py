"""Offline, verified level-one/two scope, independent of frequency ranks."""
from functools import lru_cache
import hashlib
from importlib.resources import files
import json

COMMON_CHARACTER_COUNT = 6500


@lru_cache(maxsize=1)
def _data():
    data=json.loads(files('proximic_ring').joinpath('assets/stroke-common-characters.json').read_text(encoding='utf-8'))
    text=data.get('characters','')
    if (data.get('schema')!='ring-common-stroke-characters' or data.get('version')!=2
            or data.get('count')!=COMMON_CHARACTER_COUNT
            or len(text)!=COMMON_CHARACTER_COUNT or len(set(text))!=COMMON_CHARACTER_COUNT
            or hashlib.sha256(text.encode('utf-8')).hexdigest()!=data.get('charactersSha256')):
        raise ValueError('常用字表校验失败，请恢复项目内置字表。')
    return data


@lru_cache(maxsize=1)
def common_characters():
    return frozenset(_data()['characters'])


def candidate_scope_snapshot(common_only=True):
    if not common_only:return dict(mode='all-characters',allowedCount=None)
    data=_data()
    return dict(mode=f'common-{data["count"]}',allowedCount=data['count'],table=data['table'],
                charactersSha256=data['charactersSha256'],source=dict(data['source']))
