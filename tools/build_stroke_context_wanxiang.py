"""Build the next-character context asset from Wanxiang (rime-wanxiang-data).

Inputs (CC BY 4.0, downloaded by hand into tmp/wanxiang/):
- wanxiang-lts-zh-hans.gram: Rime octagram grammar, a darts-clone double array
  of encoded word n-grams ("context word" + "next word" concatenated) whose
  values are int(log(score) * 10000) (librime-octagram, BSD-3, read here from
  its documented layout; no Wanxiang code is used);
- dicts/dicts/jichu.dict.yaml and zi.dict.yaml: words and characters with
  corpus weights.

For a written suffix s (1–3 characters) the next character c is scored by two
sources, each normalized per context, then mixed:
- across words: the grammar's collocations s + (word starting with c). An exact
  key s+c gives exp(value); a key that only starts with s+c (a longer word
  follows) gets half the context's weakest exact score;
- inside words: dictionary weight of every word in which s precedes c.

Output keeps the StrokeContext schema (unigrams, contexts → [total, followers,
counts]) so ranking code is unchanged; pseudo-counts are scaled to
CONTEXT_TOTAL per context and unigrams to the legacy asset's total, which keeps
StrokeContext.stroke_bonuses on its established scale.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path
import struct

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'src' / 'proximic_ring' / 'assets'
SOURCE = ROOT / 'tmp' / 'wanxiang'
LEGACY_TOTAL = 2312539
CONTEXT_TOTAL = {1: 2000, 2: 600, 3: 200}
KEEP = {1: 200, 2: 80, 3: 30}
# Contexts kept per length, by corpus weight; the 1.3 M unpruned contexts are ~26 MB.
# This setting (~5 MB) ranked trial targets as well as one twice its size.
LIMIT = {1: None, 2: 25000, 3: 12000}
CJK = range(0x4E00, 0xA000)


class Gram:
    """Read-only view of a Rime::Grammar/1.0 file."""
    def __init__(self, path):
        with open(path, 'rb') as stream:
            head = stream.read(64)
        if not head.startswith(b'Rime::Grammar/'):
            raise ValueError('not a Rime grammar file')
        _, self.size, offset = struct.unpack_from('<IIi', head, 32)
        # OffsetPtr is relative to its own field at byte 40.
        self.units = np.memmap(path, dtype='<u4', mode='r', offset=40+offset, shape=(self.size,))

    @staticmethod
    def _offset(units):
        units = units.astype(np.int64)
        return (units >> 10) << ((units & (1 << 9)) >> 6)

    def step(self, nodes, labels):
        ids = nodes ^ self._offset(self.units[nodes]) ^ labels
        inside = (ids >= 0) & (ids < self.size)
        ids = np.where(inside, ids, 0)
        found = (self.units[ids].astype(np.int64) & (0x80000000 | 0xFF)) == labels
        return np.where(inside & found, ids, -1)

    def leaf(self, nodes):
        units = self.units[nodes].astype(np.int64)
        values = self.units[nodes ^ self._offset(self.units[nodes])].astype(np.int64) & 0x7FFFFFFF
        return np.where((units >> 8) & 1 == 1, values, -1)

    def extend(self, nodes, chars, block=2000):
        """Child node of every node for every character (two encoded bytes), or -1."""
        first = np.array([(ord(c) >> 8) + 0x40 for c in chars], np.int64)
        second = np.array([ord(c) & 0xFF for c in chars], np.int64)
        result = np.full((len(nodes), len(chars)), -1, np.int64)
        for start in range(0, len(nodes), block):
            part = nodes[start:start+block]
            a = self.step(np.repeat(part, len(chars)), np.tile(first, len(part)))
            b = np.full_like(a, -1)
            ok = a >= 0
            b[ok] = self.step(a[ok], np.tile(second, len(part))[ok])
            result[start:start+block] = b.reshape(len(part), len(chars))
        return result

    def walk(self, text):
        node = np.array([0], np.int64)
        for ch in text:
            for label in ((ord(ch) >> 8) + 0x40, ord(ch) & 0xFF):
                node = self.step(node, np.array([label], np.int64))
                if node[0] < 0:
                    return -1
        return int(node[0])


def encodable(char):
    # Characters whose low byte is 0 use a three-byte form; none are common.
    return ord(char) in CJK and ord(char) & 0xFF


def read_dict(path):
    with open(path, encoding='utf-8') as stream:
        started = False
        for line in stream:
            if not started:
                started = line.strip() == '...'
                continue
            parts = line.rstrip('\n').split('\t')
            if len(parts) >= 3 and parts[2].isdigit():
                yield parts[0], int(parts[2])


def across_words(gram, contexts, chars):
    """Normalized collocation scores s → {c: p} from the grammar.

    Only exact keys s+c are compared: values grow with key length (今天天气
    18.4 vs 我们 11.5), so longer keys are not comparable scores. A follower
    seen only as the start of a longer key gets half the context's weakest
    exact score. (Scaling that by how common its word starts are, or scoring
    s + common words, both ranked trial targets worse.)
    """
    result = {}
    nodes = np.array([gram.walk(s) for s in contexts], np.int64)
    present = np.flatnonzero(nodes >= 0)
    children = gram.extend(nodes[present], chars)
    for row, index in enumerate(present):
        found = np.flatnonzero(children[row] >= 0)
        if not len(found):
            continue
        values = gram.leaf(children[row, found])
        exact = values >= 0
        scores = np.zeros(len(found))
        if exact.any():
            logs = values[exact] / 10000.
            scores[exact] = np.exp(logs - logs.max())
            floor = scores[exact].min() / 2
        else:
            floor = 1.
        scores[~exact] = floor
        scores /= scores.sum()
        result[contexts[index]] = {chars[j]: float(p) for j, p in zip(found, scores)}
    return result


def inside_words(words, common):
    counts = {k: defaultdict(lambda: defaultdict(float)) for k in (1, 2, 3)}
    for word, weight in words:
        if len(word) < 2 or not all(encodable(c) for c in word):
            continue
        for i in range(1, len(word)):
            if word[i] not in common:
                continue
            for k in (1, 2, 3):
                if i >= k:
                    counts[k][word[i-k:i]][word[i]] += weight
    return counts


def build(source, mix, two_char_contexts):
    common = json.loads((ASSETS / 'stroke-common-characters.json').read_text(encoding='utf-8'))['characters']
    chars = [c for c in common if encodable(c)]
    common_set = set(chars)
    words = list(read_dict(source / 'dicts' / 'dicts' / 'jichu.dict.yaml'))
    unigram = defaultdict(int)
    for char, weight in read_dict(source / 'dicts' / 'dicts' / 'zi.dict.yaml'):
        unigram[char] += weight
    inner = inside_words(words, common_set)
    gram = Gram(source / 'wanxiang-lts-zh-hans.gram')
    two = [w for w, _ in heapq.nlargest(two_char_contexts, ((w, f) for w, f in words
                                                            if len(w) == 2 and all(c in common_set for c in w)),
                                        key=lambda item: item[1])]
    across = across_words(gram, chars, chars)
    across.update(across_words(gram, two, chars))
    contexts = {}
    for k in (1, 2, 3):
        mass = {s: sum(v for v in inner[k].get(s, {}).values() if v > 0) for s in inner[k]}
        # Grammar contexts are the frequent two-character words; keep them all.
        ranked = [s for s in across if len(s) == k] + sorted(
            (s for s in mass if s not in across and mass[s] > 0), key=lambda s: -mass[s])
        keys = ranked if LIMIT[k] is None else ranked[:LIMIT[k]]
        for s in keys:
            # Zero-weight dictionary entries carry no evidence.
            inside = {c: v for c, v in inner[k].get(s, {}).items() if v > 0}
            total_inside = sum(inside.values())
            outside = across.get(s, {})
            weights = defaultdict(float)
            share = mix if outside and inside else 1.
            for c, p in outside.items():
                weights[c] += share * p
            for c, value in inside.items():
                weights[c] += (1-share if outside else 1.) * value / total_inside
            top = [(c, round(p * CONTEXT_TOTAL[k])) for c, p in
                   heapq.nlargest(KEEP[k], weights.items(), key=lambda item: (item[1], item[0]))]
            top = [(c, n) for c, n in top if n >= 1]
            if top:
                contexts[s] = [CONTEXT_TOTAL[k], ''.join(c for c, _ in top), [n for _, n in top]]
    scale = LEGACY_TOTAL / sum(unigram[c] for c in unigram if encodable(c))
    unigrams = {c: max(1, round(v * scale)) for c, v in unigram.items() if encodable(c) and v * scale >= .5}
    return dict(version=2, maxContext=3, total=sum(unigrams.values()), unigrams=unigrams, contexts=contexts)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--mix', type=float, default=.5, help='share of across-word evidence when both exist')
    parser.add_argument('--two-char-contexts', type=int, default=20000)
    parser.add_argument('--limit-2', type=int, default=LIMIT[2])
    parser.add_argument('--limit-3', type=int, default=LIMIT[3])
    parser.add_argument('--keep-1', type=int, default=KEEP[1])
    parser.add_argument('--output', type=Path, default=ASSETS / 'stroke-context-wanxiang.json.gz')
    args = parser.parse_args()
    LIMIT.update({2: args.limit_2, 3: args.limit_3})
    KEEP[1] = args.keep_1
    data = build(args.source, args.mix, args.two_char_contexts)
    sources = dict(name='rime-wanxiang-data', url='https://github.com/amzxyz/rime-wanxiang-data',
                   license='CC BY 4.0', files={})
    for name in ('wanxiang-lts-zh-hans.gram', 'dicts.zip'):
        path = args.source / name
        sources['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    data['sources'] = sources
    data['build'] = dict(mix=args.mix, twoCharContexts=args.two_char_contexts, keep=KEEP, limit=LIMIT,
                         contextTotal=CONTEXT_TOTAL)
    with gzip.open(args.output, 'wt', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(',', ':'))
    print(f"{args.output}: {len(data['contexts'])} contexts, {len(data['unigrams'])} unigrams, "
          f"{args.output.stat().st_size/1e6:.1f} MB")


if __name__ == '__main__':
    main()
