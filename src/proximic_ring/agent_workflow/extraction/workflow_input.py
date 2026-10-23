"""Compose conservative selection, task trace projection and exact dedup.

All entry points (enqueue guard, persisted inputs, HTTP model adapter) must use
this function so the checked and sent inputs cannot silently diverge. The old
compact_packet remains available as the dedup-only measurement baseline.
"""
from dataclasses import dataclass

from proximic_ring.agent_workflow.extraction.workflow_compaction import compact_packet, encoded
from proximic_ring.agent_workflow.extraction.workflow_trajectory import select_trajectory
from proximic_ring.agent_workflow.extraction.workflow_trace import project_trace


@dataclass(frozen=True)
class PreparedInput:
    model_packet: dict
    trajectory: dict
    selection: dict
    stats: dict


def prepare_model_input(packet):
    trajectory,selection=select_trajectory(packet)
    trace,trace_manifest=project_trace(trajectory)
    selection['trace']=trace_manifest
    model_packet,dedup=compact_packet(trace)
    original_chars=len(encoded({k:v for k,v in packet.items() if k not in ('fingerprint','fingerprint_version')}))
    after=dedup['model_input_chars']
    stats={**dedup,'version':2,'original_chars':original_chars,
        'trajectory_chars':dedup['original_chars'],'dedup_saved_chars':dedup['saved_chars'],
        'saved_chars':original_chars-after,
        'reduction_percent':round(100*(original_chars-after)/original_chars,2) if original_chars else 0,
        'trajectory_selection':{k:v for k,v in selection.items() if k not in ('changes','source_location','source_fingerprint','trace')},
        'trace_version':trace_manifest['version'],'trace_record_counts':trace_manifest['record_counts']}
    return PreparedInput(model_packet,trajectory,selection,stats)
