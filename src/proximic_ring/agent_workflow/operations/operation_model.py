"""后台模型边界：独立请求、思考开启、Agent 忙碌时不开始新请求。

已有请求由后台进程收完并落盘（避免丢结果后再次计费），不占用 UI/Agent RPC
线程，不修改 Agent 会话。新一轮入组或修订等到空闲再开始；本地 BGE 仍可中断。
失败不自动重发。不能承诺远端服务并发或系统资源绝对零影响。
"""
from proximic_ring.agent_workflow.operations.operation_embeddings import agent_busy, EmbeddingDeferred


def call(packet,settings,*,prompt,model=None,cancelled=lambda:False):
    from proximic_ring.agent_workflow.extraction.workflow_model import request_json
    if cancelled() or agent_busy():raise EmbeddingDeferred('Agent/文本编辑忙碌，后台模型请求推迟')
    # Operation inputs already contain concise workflows. Never apply the raw
    # trajectory projection: it would discard candidates/current_workflow/sources.
    limits={}
    if packet.get('material_information',{}).get('kind')=='generalization':
        # A full coverage audit is larger than a normal incremental revision.
        # Reuse the existing text service budget; never change foreground calls.
        from proximic_ring.text_processing.llm import TEXT_PROCESSING_MAX_OUTPUT_TOKENS
        limits=dict(max_output_tokens=TEXT_PROCESSING_MAX_OUTPUT_TOKENS,request_timeout=300)
    value,generation=(model or request_json)(packet,settings,system_prompt=prompt,**limits)
    return value,dict(generation)
