"""Read-only audit. Emits metadata/findings, never changes recorded utterances."""
import collections
import json
from pathlib import Path
import wave

ROOTS = {'source': Path(__file__).resolve().parents[2] / 'dataset',
         'installed': Path.home() / 'Library/Application Support/ProxiMic Voice/dataset'}

def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []

summaries = {}
rows = []
for location, root in ROOTS.items():
    counts = collections.Counter()
    recent = collections.Counter()
    outcomes = collections.Counter()
    targets = collections.Counter()
    for path in sorted(root.glob('*/interactions/*/record.json')):
        issues = []
        try:
            r = json.loads(path.read_text())
        except Exception:
            rows.append({'path': str(path), 'issues':['record_unreadable']})
            counts['record_unreadable'] += 1
            continue
        folder = path.parent
        is_recent = folder.name >= 'interaction_2026-09-17'
        def flag(name):
            issues.append(name)
        audio, asr, mode, outcome = (r.get(k,{}) for k in ['audio','asr','mode','outcome'])
        requests = r.get('llm',{}).get('requests',[])
        edits = [q for q in requests if q.get('mode') == 'edit']
        try:
            events = jsonl(folder / r.get('events_file','events.jsonl'))
            updates = jsonl(folder / asr.get('updates_file','asr_updates.jsonl'))
        except Exception:
            events, updates = [], []
            flag('event_or_asr_jsonl_unreadable')
        if not (folder / r.get('events_file','events.jsonl')).is_file(): flag('missing_events_file')
        if not (folder / asr.get('updates_file','asr_updates.jsonl')).is_file(): flag('missing_asr_updates_file')
        audio_path = folder / audio.get('file','audio.wav')
        if not audio_path.is_file():
            flag('missing_audio')
        else:
            try:
                with wave.open(str(audio_path),'rb') as w:
                    frames=w.getnframes(); rate=w.getframerate()
                    if (rate,w.getnchannels(),w.getsampwidth()) != (16000,1,2): flag('unexpected_audio_format')
                    if frames == 0: flag('empty_audio')
                    if frames:
                        w.setpos(frames-1)
                        if len(w.readframes(1)) != w.getsampwidth()*w.getnchannels(): flag('truncated_audio')
                    if audio.get('sample_count') is not None and audio['sample_count'] != frames: flag('audio_sample_count_mismatch')
                    if audio.get('duration_ms') is not None and abs(audio['duration_ms'] - round(frames/rate*1000)) > 1: flag('audio_duration_mismatch')
            except Exception: flag('unreadable_audio')
            if not audio.get('available'): flag('audio_available_flag_false')
        finals = [u for u in updates if u.get('kind') == 'final']
        if not asr.get('final_recorded'): flag('no_final_asr')
        if not asr.get('final_text','').strip(): flag('empty_final_asr_text')
        if asr.get('error'): flag('asr_error')
        if asr.get('final_recorded') and (not finals or finals[-1].get('text','') != asr.get('final_text','')): flag('asr_final_mismatch')
        imu=r.get('imu',{})
        if imu.get('sample_count',0):
            try:
                imu_rows=jsonl(folder / imu.get('samples_file','imu.jsonl'))
                if len(imu_rows) != imu['sample_count']: flag('imu_sample_count_mismatch')
            except Exception: flag('imu_unreadable')
        else: flag('no_imu')
        if not mode.get('training_target'): flag('no_mode_training_target')
        if not (mode.get('final_applied') or mode.get('selected')): flag('no_display_mode')
        if edits: flag('has_edit_request')
        if edits and mode.get('training_target') == 'dictation': flag('edit_attempt_now_labeled_dictation')
        if edits and not (mode.get('final_applied') == 'edit' or mode.get('selected') == 'edit'): flag('edit_attempt_not_displayed_as_edit')
        if edits and outcome.get('status') == 'apply_failed' and mode.get('selected') == 'dictation': flag('failed_edit_selected_dictation')
        if any(q.get('status') == 'processing' for q in requests): flag('unfinished_llm_request')
        if mode.get('training_target') and (not audio_path.is_file() or not asr.get('final_text','').strip() or asr.get('error')): flag('mode_target_with_unusable_input')
        if mode.get('training_target') and outcome.get('status') not in ('applied','confirm'): flag('mode_target_non_success_outcome')
        if any(e.get('type') == 'application' and e.get('action') in ('applied','undone','cancelled','apply_failed') for e in events) and outcome.get('status') == 'recognized': flag('terminal_event_but_recognized_status')
        if not asr.get('context'): flag('no_asr_context')
        if edits and any('target_text' not in q.get('input',{}) for q in edits): flag('edit_missing_context_field')
        if edits and any(q.get('status') == 'completed' and 'candidate_text' not in q for q in edits): flag('edit_missing_result_field')
        applications=[e for e in events if e.get('type') == 'application']
        if edits and any(e.get('action')=='applied' and e.get('mode')=='edit' and not e.get('request_id') for e in applications): flag('edit_application_missing_request_link')
        counts['records'] += 1
        counts.update(issues)
        if is_recent:
            recent['records'] += 1
            recent.update(issues)
        outcomes[outcome.get('status','missing')] += 1
        targets[str(mode.get('training_target'))] += 1
        rows.append({'path':str(path), 'recent':is_recent, 'mode':mode, 'outcome':outcome.get('status'),
                     'edit_requests':len(edits), 'asr_characters':len(asr.get('final_text','')), 'issues':issues})
    summaries[location]={'counts':dict(counts), 'since_20260917':dict(recent), 'outcomes':dict(outcomes),'training_targets':dict(targets)}
output=Path(__file__).parent
(output/'audit.json').write_text(json.dumps({'summary':summaries,'records':rows},ensure_ascii=False,indent=2))
print(json.dumps(summaries,ensure_ascii=False,indent=2))
