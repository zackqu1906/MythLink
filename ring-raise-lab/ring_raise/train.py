from __future__ import annotations

import copy
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn

from .common import LABELS, ROOT, read_json, sha256, write_json
from .data import load_dataset
from .metrics import choose_threshold, event_metrics, Trigger
from .model import make_model


def load_npz(path):
    with np.load(path, allow_pickle=False) as f:
        return {k: f[k] for k in f.files}


def predict(model, x, mean, std):
    model.eval()
    chunks = []
    with torch.inference_mode():
        for start in range(0, len(x), 256):
            batch = torch.from_numpy(((x[start:start+256]-mean)/std).astype(np.float32))
            chunks.append(torch.softmax(model(batch), dim=1).numpy())
    return np.concatenate(chunks)


def train(dataset, output, kind='cnn', epochs=40, seed=42, budget=1.):
    dataset, output = Path(dataset).resolve(), Path(output)
    manifest = load_dataset(dataset)
    if output.exists():
        raise ValueError('训练输出已存在；请使用新名称保存独立实验')
    if not 1 <= epochs <= 1000 or budget < 0:
        raise ValueError('epochs 必须 1–1000；误触预算不能为负')
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    train_data, val = load_npz(dataset/'train.npz'), load_npz(dataset/'val.npz')
    mask = train_data['train_mask']
    x, y = train_data['x'][mask], train_data['y'][mask]
    counts = np.bincount(y, minlength=4)
    if np.any(counts == 0):
        raise ValueError('排除边界后训练集存在空类别')
    mean = x.mean(axis=(0, 2), keepdims=True).astype(np.float32)
    std = np.maximum(x.std(axis=(0, 2), keepdims=True), 1e-4).astype(np.float32)
    xt = torch.from_numpy((x-mean)/std)
    yt = torch.from_numpy(y).long()
    xv = torch.from_numpy((val['x']-mean)/std)
    yv = torch.from_numpy(val['y']).long()
    model = make_model(kind)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.0001)
    weights = np.sqrt(len(y) / (4 * counts))
    loss_fn = nn.CrossEntropyLoss(weight=torch.tensor(weights, dtype=torch.float32))
    loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(xt, yt), batch_size=128, shuffle=True)
    best_loss, best_state, best_epoch, stale = float('inf'), None, 0, 0
    history = []
    started = time.monotonic()
    for epoch in range(1, epochs+1):
        model.train()
        total_loss = 0.
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 2.)
            optimizer.step()
            total_loss += float(loss.detach()) * len(yb)
        model.eval()
        with torch.inference_mode():
            validation_sum = 0.
            for i in range(0, len(xv), 256):
                # Unweighted CE measures the validation distribution, no test access.
                validation_sum += float(nn.functional.cross_entropy(model(xv[i:i+256]), yv[i:i+256], reduction='sum'))
            validation_loss = validation_sum / len(xv)
        history.append(dict(epoch=epoch, train_loss=total_loss/len(y), val_loss=validation_loss))
        if validation_loss < best_loss - 1e-5:
            best_loss, best_state, best_epoch, stale = validation_loss, copy.deepcopy(model.state_dict()), epoch, 0
        else:
            stale += 1
        print(f'epoch={epoch} train={total_loss/len(y):.4f} val={validation_loss:.4f}', flush=True)
        if stale >= 8:
            break
    model.load_state_dict(best_state)
    p = predict(model, val['x'], mean, std)
    operating, sweep = choose_threshold(val, p, manifest['sessions'], manifest['window'], budget)
    output.mkdir(parents=True)
    torch.save(dict(kind=kind, state_dict=best_state, mean=torch.from_numpy(mean), std=torch.from_numpy(std)), output/'model.pt')
    code_hashes = {str(p.relative_to(ROOT)): sha256(p) for p in sorted((ROOT/'ring_raise').glob('*.py'))}
    config = dict(schema=1, created_utc=datetime.now(timezone.utc).isoformat(), dataset=str(dataset),
                  dataset_manifest_sha256=sha256(dataset/'manifest.json'), synthetic=manifest['synthetic'],
                  kind=kind, seed=seed, best_epoch=best_epoch, epochs_requested=epochs,
                  val_ce=best_loss, threshold=operating['threshold'], false_trigger_budget_per_hour=budget,
                  window=manifest['window'], labels=LABELS, parameters=sum(v.numel() for v in model.parameters()),
                  firmware_version=next(iter(manifest['sessions'].values()))['metadata']['firmware_version'],
                  training_seconds=time.monotonic()-started, model_sha256=sha256(output/'model.pt'),
                  torch_version=torch.__version__, numpy_version=np.__version__, code_sha256=code_hashes,
                  validation_note='阈值和 checkpoint 仅用验证集选择；测试集未用于训练或选择。')
    write_json(output/'config.json', config)
    write_json(output/'history.json', history)
    write_json(output/'validation.json', dict(selected=operating, sweep=sweep))
    write_report(output/'validation.html', operating, config, '验证集：用于选择，不能当最终泛化证据')
    return dict(run=str(output), parameters=config['parameters'], validation=operating)


def load_run(run):
    run = Path(run)
    config = read_json(run/'config.json')
    if sha256(run/'model.pt') != config['model_sha256']:
        raise ValueError('模型文件哈希不匹配')
    state = torch.load(run/'model.pt', map_location='cpu', weights_only=True)
    model = make_model(state['kind'])
    model.load_state_dict(state['state_dict'])
    model.eval()
    torch.set_num_threads(2)
    return model, state['mean'].numpy(), state['std'].numpy(), config


def evaluate(run):
    run = Path(run)
    if (run/'test.json').exists():
        raise ValueError('本实验已出过测试结果；直接查看 test.html，不重复调测试集')
    model, mean, std, config = load_run(run)
    dataset = Path(config['dataset'])
    if sha256(dataset/'manifest.json') != config['dataset_manifest_sha256']:
        raise ValueError('数据清单发生变化')
    manifest = load_dataset(dataset)
    package = load_npz(dataset/'test.npz')
    probabilities = predict(model, package['x'], mean, std)
    result = event_metrics(package, probabilities, manifest['sessions'], config['threshold'], config['window'])
    # CPU single-window forward latency; explicitly excludes BLE/window history.
    tensor = torch.from_numpy((package['x'][:1]-mean)/std)
    costs = []
    with torch.inference_mode():
        for i in range(110):
            t = time.perf_counter()
            model(tensor)
            if i >= 10:
                costs.append((time.perf_counter()-t)*1000)
    result['cpu_forward_p50_ms'] = float(np.median(costs))
    result['cpu_forward_p95_ms'] = float(np.quantile(costs, .95))
    result['session_count'] = len(np.unique(package['session']))
    result['generalization_scope'] = manifest['plan']['mode']
    result['synthetic'] = manifest['synthetic']
    write_json(run/'test.json', result)
    np.savez_compressed(run/'test_predictions.npz', p=probabilities, t=package['t'], y=package['y'], session=package['session'])
    write_report(run/'test.html', result, config, '冻结测试集：一次评估')
    return result


def write_report(path, result, config, title):
    def fmt(value):
        return '不足以估计' if value is None else f'{value:.3f}'
    rows = ''.join('<tr><th>'+html.escape(label)+'</th>'+''.join(f'<td>{v}</td>' for v in row)+'</tr>'
                   for label, row in zip(LABELS, result['confusion_matrix']))
    failures = ''.join('<tr>'+''.join('<td>'+html.escape(str(e.get(k, '')))+'</td>' for k in ('session','t','activity','reason'))+'</tr>' for e in result['false_events'])
    misses = ''.join(f'<li>{html.escape(e["session"])} · {html.escape(e["trial_id"])} · 抬起 {e["raise_start_s"]:.2f}s / 到嘴边 {e["near_start_s"]:.2f}s</li>' for e in result['misses'])
    page = f'''<!doctype html><meta charset="utf-8"><title>抬手检测评估</title>
<style>body{{font:16px system-ui;max-width:1050px;margin:48px auto;padding:0 24px;color:#18312b;background:#f5f8f6}}h1{{font-size:30px}}section{{background:white;padding:24px;margin:20px 0;border:1px solid #d5e2db;border-radius:14px}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #dfe7e2;text-align:left}}.warning{{color:#984f12}}pre{{white-space:pre-wrap;word-break:break-word}}</style>
<h1>抬到嘴边 · {html.escape(title)}</h1><p class="warning">{'合成数据流程自检：所有数值均不能证明真实识别能力。' if config['synthetic'] else '本报告只评估动作候选；不证明语音意图，也不证明嘴部距离。'}</p>
<section><h2>事件指标</h2><p>召回：{fmt(result['event_recall'])}（{result['matched']} / {result['target_events']}） · 漏检 {result['missed']} 次</p>
<p>负例曝光：{result['negative_seconds']/60:.2f} 分钟 · 误触 {result['false_triggers']} 次 · 每小时误触 {fmt(result['false_triggers_per_hour'])}</p>
<p>误触率单侧 95% Poisson 上限：{fmt(result['poisson_upper95_per_hour'])} / 小时。零误触且观察 T 小时，上限约为 3/T；短录制不能证明低误触。</p>
<p>到嘴边后的触发延迟 P50 / P90：{fmt(result['latency_from_near_p50_s'])} / {fmt(result['latency_from_near_p90_s'])} 秒（负值=提前）。重复触发 {result['duplicate_triggers']} 次。</p>
<p>因缺口/预热排除目标事件 {len(result['excluded_events'])} 次。阈值 {config['threshold']}；{'当前阈值禁用触发，验证未通过。' if config['threshold'] > 1 else '阈值只由验证集确定。'}</p></section>
<section><h2>逐窗混淆矩阵</h2><p>行=真值，列=预测。相邻窗口有相关性，窗口数量不是独立样本量。</p><table><tr><th></th>{''.join('<th>'+s+'</th>' for s in LABELS)}</tr>{rows}</table></section>
<section><h2>误触定位</h2><table><tr><th>录制</th><th>秒</th><th>活动</th><th>原因</th></tr>{failures}</table><h2>漏检定位</h2><ul>{misses}</ul></section>
<section><h2>可复现参数与原始指标</h2><details><summary>参数</summary><pre>{html.escape(json.dumps(config,ensure_ascii=False,indent=2))}</pre></details><details><summary>完整指标</summary><pre>{html.escape(json.dumps(result,ensure_ascii=False,indent=2))}</pre></details></section>'''
    Path(path).write_text(page, encoding='utf-8')
