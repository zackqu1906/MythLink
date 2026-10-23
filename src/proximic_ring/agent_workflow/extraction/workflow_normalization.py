"""Lossless repair of one known model formatting error; never rewrite steps.

Some responses put extraction_notes inside a workflow instead of at the root.
Move only arrays of strings, preserving order/content and recording each path.
All other unexpected fields remain errors. No inference or second model call.
The caller must save the original model JSON whenever this returns changes.
"""
import copy
from proximic_ring.agent_workflow.task_metadata import COMPACT_FORMATS


def normalize(value):
    result=copy.deepcopy(value);changes=[]
    if not isinstance(result,dict) or result.get('format') not in COMPACT_FORMATS:return result,changes
    workflows=result.get('workflows')
    if not isinstance(workflows,list):return result,changes
    root=result.get('extraction_notes',[])
    if not isinstance(root,list) or any(not isinstance(n,str) for n in root):return result,changes
    for i,w in enumerate(workflows):
        if not isinstance(w,dict) or 'extraction_notes' not in w:continue
        notes=w['extraction_notes']
        if not isinstance(notes,list) or any(not isinstance(n,str) for n in notes):continue
        start=len(root);root.extend(w.pop('extraction_notes'))
        changes.append(dict(rule='move_extraction_notes_to_root',source=f'/workflows/{i}/extraction_notes',
                            destination='/extraction_notes',start=start,count=len(notes)))
    if changes:result['extraction_notes']=root
    return result,changes
