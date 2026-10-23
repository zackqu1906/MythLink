"""共享阅读样式：先统计与概括，再按需展开详情，不产生业务请求。"""
import html


def stats_cards(items):
    """Counts supplied by each exporter; labels must state their counting scope."""
    body = '''<style>
.reading-stats{display:flex;flex-wrap:wrap;gap:12px;margin:20px 0}
.reading-stat{flex:1;min-width:120px;border:1px solid #dce4e0;border-radius:12px;padding:16px 20px;background:#fff}
.reading-stat strong{display:block;font-size:28px;color:#186657}.reading-stat span{display:block;color:#626b69;margin-top:6px;font-size:13px}
.reading-summary{color:#52615b;line-height:1.7}.reading-meta{font-size:13px;color:#626b69}
</style><div class="reading-stats">'''
    for count, label in items:
        body += '<div class="reading-stat"><strong>' + html.escape(str(count)) + '</strong><span>' + html.escape(label) + '</span></div>'
    return body + '</div>'


def brief(text, limit=180):
    """Use the recorded goal as the synopsis; no model call or inferred meaning."""
    value = ' '.join(str(text).split())
    return html.escape(value if len(value) <= limit else value[:limit] + '…')
