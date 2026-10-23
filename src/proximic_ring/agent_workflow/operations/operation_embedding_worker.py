"""Standalone, low-priority BGE-M3 CPU inference. Only this process imports torch."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

REVISION = '5617a9f61b028005a4858fdac845db406aefb181'
MAX_TOKENS = 2048


def write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def download(destination):
    """Explicit setup only. Normal encoding is strictly local_files_only."""
    from proximic_ring.agent_workflow.operations.embedding_install import install
    install(directory=destination,progress=lambda label,current,total:print(f'{label} {current}/{total}',flush=True))
    print('本地 BGE-M3 就绪：' + str(destination), flush=True)


def runtime_check():
    """Offline packaging probe; no checkpoint, Python install or download required."""
    from importlib.metadata import version
    from proximic_ring.agent_workflow.operations.embedding_install import manifest
    if manifest().get('revision')!=REVISION:raise ValueError('召回模型下载清单缺失或不匹配，请更新 Mythlink')
    packages={name:version(name) for name in ('transformers','torch','numpy')}
    if packages!={'transformers':'5.15.0','torch':'2.11.0','numpy':'1.26.4'}:
        raise ValueError('召回编码运行时版本不匹配，请更新 Mythlink；源码运行请按依赖锁安装')
    import torch
    from transformers.models.xlm_roberta.modeling_xlm_roberta import XLMRobertaModel
    from transformers.models.xlm_roberta.configuration_xlm_roberta import XLMRobertaConfig
    from transformers.models.xlm_roberta.tokenization_xlm_roberta import XLMRobertaTokenizer
    # Exercise native kernels and lazy architecture imports in the frozen app.
    model=XLMRobertaModel(XLMRobertaConfig(vocab_size=8,hidden_size=8,num_hidden_layers=1,
        num_attention_heads=2,intermediate_size=16)).eval()
    with torch.inference_mode():
        output=model(torch.tensor([[0,4,2]]))
        assert output.last_hidden_state.shape==(1,3,8)
    assert XLMRobertaTokenizer()("test")['input_ids']
    return packages


def main(argv=None):
    args=sys.argv[1:] if argv is None else argv
    if args==['--check-runtime']:
        runtime_check();return
    if args[:1]==['--download']:
        download(Path(args[1]));return
    source, target = map(Path, args[:2])
    result = {'vectors': {}, 'metrics': {}}
    stop = threading.Event(); rss = [0]
    started = time.monotonic()
    parent_pid = os.getppid()
    def watch_parent():
        # UI shutdown can terminate the workflow worker without Python finally
        # blocks. Never leave a heavy orphan model process behind.
        while not stop.wait(.2):
            if os.getppid() != parent_pid:
                os._exit(2)
    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        if hasattr(os, 'nice'):
            try: os.nice(10)
            except OSError: pass
        def memory():
            try:
                import resource
                value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                return int(value if sys.platform=='darwin' else value*1024)
            except ImportError:return 0
        def sample_memory():
            while not stop.is_set():
                rss[0] = max(rss[0], memory())
                stop.wait(.02)
        monitor = threading.Thread(target=sample_memory, daemon=True); monitor.start()
        import torch
        torch.set_num_threads(2); torch.set_num_interop_threads(1)
        packages=runtime_check()
        from transformers.models.xlm_roberta.tokenization_xlm_roberta import XLMRobertaTokenizer
        from transformers.models.xlm_roberta.modeling_xlm_roberta import XLMRobertaModel
        result['metrics'].update(import_seconds=time.monotonic()-started, packages=packages)
        packet = json.loads(source.read_text())
        model_path = Path(packet['model_path'])
        marker = json.loads((model_path/'proximic-model.json').read_text())
        if marker.get('revision') != REVISION:
            raise ValueError('本地模型版本不匹配；请到“设置 → Agent”校验修复召回模型')
        loading = time.monotonic()
        # The pinned checkpoint is XLM-R. Resolve it explicitly: AutoTokenizer's
        # global aliases otherwise import unrelated model families in frozen apps.
        tokenizer=XLMRobertaTokenizer.from_pretrained(str(model_path),local_files_only=True,trust_remote_code=False)
        model=XLMRobertaModel.from_pretrained(str(model_path),local_files_only=True,
            dtype=torch.float32,attn_implementation='sdpa').eval()
        result['metrics']['load_seconds'] = time.monotonic()-loading
        result['metrics']['encoding_seconds'] = 0
        result['metrics']['batches'] = []
        for key, text in packet['requests'].items():
            inputs=tokenizer(text,return_tensors='pt',truncation=False)
            tokens = inputs['input_ids'].shape[1]
            if tokens > MAX_TOKENS:
                raise ValueError(f'Workflow 文本为 {tokens} tokens，超过后台单条上限 {MAX_TOKENS}；未静默截断')
            tick = time.monotonic()
            # BGE-M3's fixed modules.json specifies CLS pooling + L2 normalize.
            with torch.inference_mode():
                vector=torch.nn.functional.normalize(model(**inputs).last_hidden_state[:,0],p=2,dim=1)[0]
            elapsed = time.monotonic()-tick
            result['vectors'][key] = dict(vector=vector.tolist(), tokens=tokens)
            result['metrics']['encoding_seconds'] += elapsed
            result['metrics']['batches'].append(dict(key=key, tokens=tokens, seconds=elapsed))
            result['metrics']['peak_rss_bytes'] = max(rss[0], memory())
            write(target, result)
        result['metrics']['cpu_seconds'] = time.process_time()
    except Exception as exc:
        # Never persist arbitrary provider output or environment contents.
        result['error'] = str(exc) if type(exc) is ValueError else '本地向量失败：' + type(exc).__name__
    finally:
        stop.set()
        result['metrics']['worker_seconds'] = time.monotonic()-started
        result['metrics']['peak_rss_bytes'] = max(rss[0], result['metrics'].get('peak_rss_bytes', 0))
        write(target, result)
    if result.get('error'):
        sys.exit(1)


if __name__ == '__main__':
    main()
