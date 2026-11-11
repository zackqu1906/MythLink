"""Estimate how much each kind of evidence narrows the target after k strokes.

For the 6,500 common characters (frequency from the context model's corpus
unigrams), after the user has written k strokes of a character that has at
least k strokes, report the posterior uncertainty about which character it is:

- H: conditional entropy in bits, E[-log2 p(target | observation)];
- top1 / top5: share of targets the Bayes-optimal ranker puts first / in top 5.

These are upper bounds for a ranker that uses the stated evidence with the
right probabilities; the live ranker uses heuristic costs and will do worse.

Evidence layers, all simulated from hanzi-writer-data medians:
- codes: the five-class code of each stroke, through a symmetric confusion
  channel (accuracy a, errors spread evenly over the other four classes);
- vectors: each stroke's start→end vector, both sides RMS-normalized over the
  written prefix, plus isotropic Gaussian noise sigma_v;
- gaps: previous stroke end → next stroke start in the same units, plus noise
  sigma_g (the lifted-hand displacement the SDK does not observe today);
- context: one preceding character, a bigram rebuilt from the context asset
  (see Universe.context_prior).

Noise defaults come from the P01 stroke-layout recordings (docs/STROKE_LAYOUT.md);
sigma_v is re-measured here from those recordings. Context targets are drawn
from the same bigram that scores them (no held-out text in the repository), so
that row is optimistic about a one-character context and ignores longer ones.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path
import sys
import tarfile

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
ASSETS = ROOT / 'src' / 'proximic_ring' / 'assets'
DEFAULT_GLYPHS = ROOT / 'tmp' / 'hanzi-writer-data-2.0.1.tgz'
DEFAULT_RECORDINGS = [ROOT / 'data' / 'recordings' / 'stroke-layout' / name
                      for name in ('stroke-layout-P01-train-50.json', 'stroke-layout-P01-train-50-2.json')]
CLASSES = 'hspnz'
LOG2 = math.log(2)


def load_universe(glyph_archive):
    common = json.loads((ASSETS / 'stroke-common-characters.json').read_text(encoding='utf-8'))['characters']
    with gzip.open(ASSETS / 'strokes.json.gz', 'rt', encoding='utf-8') as stream:
        entries = json.load(stream)['entries']
    with gzip.open(ASSETS / 'stroke-context.json.gz', 'rt', encoding='utf-8') as stream:
        context = json.load(stream)
    best = {}
    for char, code, level in entries:
        # The highest-level reading is the dictionary's standard stroke order.
        if char in common and level > best.get(char, ('', 0))[1]:
            best[char] = (code, level)
    wanted = set(best)
    medians = {}
    with tarfile.open(glyph_archive) as archive:
        for member in archive:
            name = Path(member.name).stem
            if member.isfile() and member.name.endswith('.json') and name in wanted:
                medians[name] = json.load(archive.extractfile(member))['medians']
    chars, codes, strokes = [], [], []
    for char in common:
        if char not in best:
            continue
        code = best[char][0]
        median = medians.get(char)
        # Vector i must belong to code letter i, so only aligned glyphs are kept.
        if median is None or len(median) != len(code) or any(c not in CLASSES for c in code):
            continue
        chars.append(char)
        codes.append(code)
        strokes.append([(np.array(s[0], float) * (1, -1), np.array(s[-1], float) * (1, -1)) for s in median])
    counts = np.array([context['unigrams'].get(c, 0) for c in chars], float)
    coverage = dict(commonTable=len(common), withStandardCode=len(best), aligned=len(chars),
                    corpusShare=float(counts.sum() / context['total']))
    return chars, codes, strokes, counts, context, coverage


class Universe:
    def __init__(self, chars, codes, strokes, counts, context):
        self.chars, self.codes, self.context = chars, codes, context
        self.index = {c: i for i, c in enumerate(chars)}
        self.lengths = np.array([len(c) for c in codes])
        n, width = len(chars), max(self.lengths)
        self.code = np.zeros((n, width), int)
        self.vec = np.zeros((n, width, 2))
        self.gap = np.zeros((n, width, 2))
        for i, (code, glyph) in enumerate(zip(codes, strokes)):
            self.code[i, :len(code)] = [CLASSES.index(c) for c in code]
            for j, (start, end) in enumerate(glyph):
                self.vec[i, j] = end - start
                if j:
                    self.gap[i, j] = start - glyph[j-1][1]
        total, vocab = context['total'], len(context['unigrams'])
        self.prior = (counts + .1) / (total + .1 * vocab)
        self.prior /= self.prior.sum()

    def prefix(self, k):
        """Candidates with at least k strokes, with prefix-normalized features."""
        rows = np.flatnonzero(self.lengths >= k)
        vec = self.vec[rows, :k]
        rms = np.sqrt((vec**2).sum(axis=2).mean(axis=1))[:, None, None]
        return rows, self.code[rows, :k], vec / rms, self.gap[rows, 1:k] / rms

    def context_prior(self, previous):
        """Bigram estimate for one preceding character.

        The asset keeps only the top 8 followers and their own total, so
        StrokeContext's estimate puts almost all mass on those 8. Here the
        followers get count / unigram(previous), and the unseen remainder is
        spread like the unigram prior, which keeps the bigram honest.
        """
        row = self.context['contexts'].get(previous)
        seen = self.context['unigrams'].get(previous, 0)
        if row is None or seen <= 0:
            return self.prior
        explicit = np.zeros_like(self.prior)
        for char, count in zip(row[1], row[2]):
            if char in self.index:
                explicit[self.index[char]] = count / seen
        explicit *= min(1., .98 / max(explicit.sum(), 1e-12))
        rest = np.where(explicit > 0, 0., self.prior)
        value = explicit + (1 - explicit.sum()) * rest / rest.sum()
        return value / value.sum()


def confusion_log(accuracy):
    off = (1 - accuracy) / (len(CLASSES) - 1)
    matrix = np.full((len(CLASSES),) * 2, off)
    np.fill_diagonal(matrix, accuracy)
    return np.log(matrix)


def measure_vector_sigma(universe, paths):
    """Per-axis residual of RMS-normalized stroke vectors in real recordings."""
    residuals = []
    for path in paths:
        if not Path(path).exists():
            continue
        for sample in json.loads(Path(path).read_text(encoding='utf-8'))['samples']:
            char = sample['task']['character']
            i = universe.index.get(char)
            contacts = sample['contacts']
            if i is None or len(contacts) != universe.lengths[i]:
                continue
            observed = np.array([[c['points'][-1]['x'] - c['points'][0]['x'],
                                  c['points'][-1]['y'] - c['points'][0]['y']] for c in contacts], float)
            template = universe.vec[i, :len(contacts)]
            unit = lambda v: v / np.sqrt((v**2).sum(axis=1).mean())
            residuals.append(unit(observed) - unit(template))
    if not residuals:
        return None, 0
    stacked = np.vstack(residuals)
    return float(np.sqrt((stacked**2).mean() )), len(stacked)


def simulate(universe, k, *, samples, rng, accuracy=None, sigma_v=None, sigma_g=None, context=False):
    rows, codes, vec, gap = universe.prefix(k)
    base = universe.prior[rows]
    log_conf = confusion_log(accuracy) if accuracy is not None else None
    previous = None
    if context:
        names = list(universe.context['unigrams'])
        weights = np.array([universe.context['unigrams'][c] for c in names], float)
        previous = rng.choice(names, size=samples, p=weights / weights.sum())
    bits, top1, top5 = [], 0, 0
    for s in range(samples):
        prior = base
        if context:
            prior = universe.context_prior(previous[s])[rows]
        prior = prior / prior.sum()
        target = rng.choice(len(rows), p=prior)
        score = np.log(prior)
        if log_conf is None:
            # Perfect recognition: only exact prefixes survive.
            score = np.where((codes == codes[target]).all(axis=1), score, -np.inf)
        else:
            truth = codes[target]
            observed = np.where(rng.random(k) < accuracy, truth,
                                (truth + rng.integers(1, len(CLASSES), k)) % len(CLASSES))
            score = score + log_conf[codes, observed].sum(axis=1)
        if sigma_v is not None:
            seen = vec[target] + rng.normal(0, sigma_v, vec[target].shape)
            score = score - ((vec - seen)**2).sum(axis=(1, 2)) / (2 * sigma_v**2)
        if sigma_g is not None and k > 1:
            seen = gap[target] + rng.normal(0, sigma_g, gap[target].shape)
            score = score - ((gap - seen)**2).sum(axis=(1, 2)) / (2 * sigma_g**2)
        score = score - score.max()
        log_total = math.log(np.exp(score).sum())
        bits.append(-(score[target] - log_total) / LOG2)
        rank = int((score > score[target]).sum())
        top1 += rank == 0
        top5 += rank < 5
    return dict(H=float(np.mean(bits)), top1=top1 / samples, top5=top5 / samples)


def exact_code_entropy(universe, k):
    """Closed form for perfect recognition and frequency prior."""
    rows = np.flatnonzero(universe.lengths >= k)
    prior = universe.prior[rows] / universe.prior[rows].sum()
    groups = {}
    for r, p in zip(rows, prior):
        groups.setdefault(universe.codes[r][:k], []).append(p)
    bits = top1 = top5 = 0.
    # Type-weighted: every character equally likely as target, still ranked by frequency.
    type1 = type5 = 0
    for members in groups.values():
        members = np.sort(np.array(members))[::-1]
        mass = members.sum()
        bits -= float((members * np.log2(members / mass)).sum())
        top1 += float(members[0])
        top5 += float(members[:5].sum())
        type1 += 1
        type5 += min(5, len(members))
    reach = float(universe.prior[rows].sum() / universe.prior.sum())
    return dict(H=bits, top1=top1, top5=top5, typeTop1=type1 / len(rows), typeTop5=type5 / len(rows),
                groups=len(groups), candidates=len(rows), massWithKStrokes=reach)


def score_observation(universe, k, observed_codes, observed_vec, *, accuracy, sigma_v):
    rows, codes, vec, _ = universe.prefix(k)
    score = np.log(universe.prior[rows])
    score = score + confusion_log(accuracy)[codes, observed_codes].sum(axis=1)
    if sigma_v is not None:
        score = score - ((vec - observed_vec)**2).sum(axis=(1, 2)) / (2 * sigma_v**2)
    return rows, score


def check_recordings(universe, paths, *, accuracy, sigma_v, draws, rng):
    """Rank recorded characters with their real codes and vectors, beside
    synthetic draws for the same targets, to see whether the noise model holds."""
    result = {}
    for path in paths:
        if not Path(path).exists():
            continue
        for sample in json.loads(Path(path).read_text(encoding='utf-8'))['samples']:
            char, code = sample['task']['character'], sample.get('baselineCode') or ''
            i = universe.index.get(char)
            contacts = sample['contacts']
            if i is None or len(contacts) != universe.lengths[i] or len(code) != len(contacts):
                continue
            real = np.array([[c['points'][-1]['x'] - c['points'][0]['x'],
                              c['points'][-1]['y'] - c['points'][0]['y']] for c in contacts], float)
            for k in range(1, len(contacts) + 1):
                bucket = result.setdefault(k, dict(n=0, real=dict(codes=[0, 0], vectors=[0, 0]),
                                                   synthetic=dict(codes=[0, 0], vectors=[0, 0])))
                bucket['n'] += 1
                seen_codes = np.array([CLASSES.index(c) for c in code[:k]])
                seen_vec = real[:k] / np.sqrt((real[:k]**2).sum(axis=1).mean())
                rows, _, vec, _ = universe.prefix(k)
                target = int(np.flatnonzero(rows == i)[0])
                for name, sv in (('codes', None), ('vectors', sigma_v)):
                    _, score = score_observation(universe, k, seen_codes, seen_vec, accuracy=accuracy, sigma_v=sv)
                    rank = int((score > score[target]).sum())
                    bucket['real'][name][0] += rank == 0
                    bucket['real'][name][1] += rank < 5
                    hits = [0, 0]
                    for _ in range(draws):
                        truth = universe.code[i, :k]
                        noisy = np.where(rng.random(k) < accuracy, truth,
                                         (truth + rng.integers(1, len(CLASSES), k)) % len(CLASSES))
                        fake = vec[target] + rng.normal(0, sigma_v, vec[target].shape)
                        _, score = score_observation(universe, k, noisy, fake, accuracy=accuracy, sigma_v=sv)
                        rank = int((score > score[target]).sum())
                        hits[0] += rank == 0
                        hits[1] += rank < 5
                    bucket['synthetic'][name][0] += hits[0] / draws
                    bucket['synthetic'][name][1] += hits[1] / draws
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--glyphs', type=Path, default=DEFAULT_GLYPHS)
    parser.add_argument('--recordings', type=Path, nargs='*', default=DEFAULT_RECORDINGS)
    parser.add_argument('--strokes', type=int, default=6, help='largest k to evaluate')
    parser.add_argument('--samples', type=int, default=2000)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--sigma-g', type=float, default=.28,
                        help='inter-stroke gap noise; P01 corrected gyro was 0.25 watched / 0.31 blind')
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'diagnostics' / 'stroke-entropy' / 'report.json')
    args = parser.parse_args()

    chars, codes, strokes, counts, context, coverage = load_universe(args.glyphs)
    universe = Universe(chars, codes, strokes, counts, context)
    sigma_v, n_vectors = measure_vector_sigma(universe, args.recordings)
    sigma_v = sigma_v or .3
    prior = universe.prior
    report = dict(coverage=coverage, unigramBits=float(-(prior * np.log2(prior)).sum()),
                  sigmaVector=sigma_v, sigmaVectorStrokes=n_vectors, sigmaGap=args.sigma_g,
                  samples=args.samples, seed=args.seed, rows=[])
    rng = np.random.default_rng(args.seed)
    names = list(context['unigrams'])
    weights = np.array([context['unigrams'][c] for c in names], float)
    bigram = []
    for previous in rng.choice(names, size=args.samples, p=weights / weights.sum()):
        p = universe.context_prior(previous)
        bigram.append(float(-(p * np.log2(p)).sum()))
    report['bigramBits'] = float(np.mean(bigram))
    print(f"universe {coverage}  H(char)={report['unigramBits']:.2f} bit  "
          f"H(char | previous char)={report['bigramBits']:.2f} bit  "
          f"sigma_v={sigma_v:.3f} from {n_vectors} strokes  sigma_g={args.sigma_g}")

    scenarios = [
        ('codes, perfect', dict()),
        ('codes 98%', dict(accuracy=.98)),
        ('codes 85%', dict(accuracy=.85)),
        ('codes 70%', dict(accuracy=.70)),
        ('codes 85% + vectors', dict(accuracy=.85, sigma_v=sigma_v)),
        ('codes 85% + vectors + gaps', dict(accuracy=.85, sigma_v=sigma_v, sigma_g=args.sigma_g)),
        ('codes 85% + vectors + ideal gaps', dict(accuracy=.85, sigma_v=sigma_v, sigma_g=.05)),
        ('codes 85% + context', dict(accuracy=.85, context=True)),
        ('codes 85% + vectors + context', dict(accuracy=.85, sigma_v=sigma_v, context=True)),
        ('codes 85% + vectors + gaps + context', dict(accuracy=.85, sigma_v=sigma_v, sigma_g=args.sigma_g, context=True)),
        ('codes 70% + vectors + context', dict(accuracy=.70, sigma_v=sigma_v, context=True)),
    ]
    for k in range(1, args.strokes + 1):
        exact = exact_code_entropy(universe, k)
        report['rows'].append(dict(k=k, scenario='codes, perfect (exact)', **exact))
        print(f"\nk={k}  candidates={exact['candidates']}  code groups={exact['groups']}  "
              f"freq mass with >=k strokes={exact['massWithKStrokes']:.2f}")
        print(f"  {'frequency only':38s} H={-(prior[universe.lengths >= k] / prior[universe.lengths >= k].sum() * np.log2(prior[universe.lengths >= k] / prior[universe.lengths >= k].sum())).sum():5.2f}")
        print(f"  {'codes, perfect (exact)':38s} H={exact['H']:5.2f}  top1={exact['top1']:.2f}  top5={exact['top5']:.2f}"
              f"  (each char equally: top1={exact['typeTop1']:.2f} top5={exact['typeTop5']:.2f})")
        for name, options in scenarios:
            if name == 'codes, perfect':
                continue
            rng = np.random.default_rng(args.seed * 100 + k)
            result = simulate(universe, k, samples=args.samples, rng=rng, **options)
            report['rows'].append(dict(k=k, scenario=name, **result))
            print(f"  {name:38s} H={result['H']:5.2f}  top1={result['top1']:.2f}  top5={result['top5']:.2f}")
    # Recorded codes were 98% correct (careful single characters), so check at that accuracy.
    check = check_recordings(universe, args.recordings, accuracy=.98, sigma_v=sigma_v, draws=20,
                             rng=np.random.default_rng(args.seed))
    report['recordingCheck'] = check
    print('\nrecorded characters: real observation vs synthetic draws for the same targets (top1 / top5)')
    for k, b in sorted(check.items()):
        n = b['n']
        cells = '  '.join(f"{name}: real {b['real'][name][0]/n:.2f}/{b['real'][name][1]/n:.2f}"
                          f" synth {b['synthetic'][name][0]/n:.2f}/{b['synthetic'][name][1]/n:.2f}"
                          for name in ('codes', 'vectors'))
        print(f"  k={k} n={n:3d}  {cells}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"\nwrote {args.output}")


if __name__ == '__main__':
    main()
