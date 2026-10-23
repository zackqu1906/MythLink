"""Omit opaque asset bytes, never their command, status or error envelope."""
import json


def svg_result(entry,call):
    """Handle the legacy head/tail truncation of a cat/read SVG result.

The discarded region is only aggregatedOutput's entire SVG-shaped string.
Everything outside it must still parse as JSON with the known result shape.
This does NOT assert valid SVG or infer its visual content. Unknown/mixed
output, multiple reads, non-SVG targets and failed results stay verbatim.
Original bytes, truncation flag and source remain in the audit snapshot.
"""
    c=entry.get('content');data=call.get('content',{})
    if not isinstance(data,dict):return None
    reads=data.get('data',{}).get('commandActions',[])
    if len(reads)!=1 or reads[0].get('type')!='read' or not reads[0].get('path','').lower().endswith('.svg'):return None
    if not isinstance(c,str) or not entry.get('truncated'):return None
    prefix='"aggregatedOutput":"<svg '
    start=c.find(prefix);end=c.rfind('</svg>\\n"')
    if start<0 or end<start:return None
    begin=start+len('"aggregatedOutput":')
    finish=end+len('</svg>\\n"')
    marker='[SVG 文件源码已省略；原返回已截断，不代表已读取图片像素或验证其内容]'
    try:projected=json.loads(c[:begin]+json.dumps(marker,ensure_ascii=False)+c[finish:])
    except ValueError:return None
    if projected.get('reported_status')!='completed' or projected.get('data',{}).get('exitCode')!=0 or projected.get('data',{}).get('error'):return None
    return projected
