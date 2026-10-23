"""Local next-character suggestions, ported from the existing demo predictor."""
import gzip
from importlib.resources import files
import json
import re


class StrokeContext:
    def __init__(self, data=None):
        if data is None:
            with gzip.open(files('proximic_ring').joinpath('assets/stroke-context.json.gz'), 'rt', encoding='utf-8') as stream:
                data = json.load(stream)
        self.data = data

    def suggestions(self, text, limit=30):
        match = re.search(r'[\u3400-\u9fff]+$', text)
        if not match:
            return []
        tail = match[0][-self.data['maxContext']:]
        rows = [self.data['contexts'][tail[-n:]] for n in range(1, len(tail)+1)
                if tail[-n:] in self.data['contexts']]
        candidates = set(''.join(row[1] for row in rows))
        def probability(char):
            value = (self.data['unigrams'].get(char, 0)+.1)/(self.data['total']+.1*len(self.data['unigrams']))
            for total, followers, counts in rows:
                count = counts[followers.index(char)] if char in followers else 0
                value = (count+6*value)/(total+6)
            return value
        return sorted(candidates, key=lambda char: (-probability(char), char))[:limit]
