"""Editable text inputs; JSON is metadata, never an executable workflow input."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_steps
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, atomic_text
from proximic_ring.agent_workflow.execution.workflow_trials import make_case, validate_case


def load_task(directory):
    directory = Path(directory)
    files = {name: (directory/name).read_text(encoding='utf-8')
             for name in ('task.json', 'baseline.txt', 'steps.txt')}
    config = json.loads(files['task.json'])
    required = {'schema_version', 'name', 'parameters', 'initial_state'}
    if (not isinstance(config, dict) or not required <= set(config) or set(config)-required-{'source'}
            or config['schema_version'] != 2 or not isinstance(config['parameters'], dict)
            or not isinstance(config['name'], str) or not config['name'].strip()
            or not isinstance(config['initial_state'], str)):
        raise ValueError('task.json 需要 schema_version=2、name、parameters 对象和 initial_state 文字。')
    if not files['steps.txt'].strip(): raise ValueError('steps.txt 不能为空。')
    # Minimal internal descriptor; no workflow.json or guided-workflow.txt is read.
    flow = dict(name=config['name'], goal=files['baseline.txt'],
                parameters=[dict(name=name, description=name) for name in config['parameters']])
    entry = dict(flow=flow, natural_language_workflow=files['steps.txt'],
        renderer_version='editable-trial-text-2', source=dict(
            kind='editable_trial_task', task_key=directory.name,
            extracted_from=config.get('source'),
            file_sha256={name: hashlib.sha256(text.encode('utf-8')).hexdigest() for name, text in files.items()}))
    return validate_case(make_case(entry, files['baseline.txt'], config['parameters'], config['initial_state']))


def prepare_task(directory, entry):
    """Copy model-authored steps into a NEW editable task; never overwrite a task."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    flow = entry['flow']
    atomic_text(directory/'baseline.txt', flow['goal'])
    atomic_text(directory/'steps.txt', workflow_steps(flow))
    atomic_json(directory/'task.json', dict(schema_version=2, name=flow['name'],
        parameters={p['name']: '' for p in flow['parameters']}, initial_state='', source=entry['source']))
    return directory
