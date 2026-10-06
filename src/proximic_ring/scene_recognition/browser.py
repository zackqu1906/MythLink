"""Current-page adapter for the shared PDF/image/video/music scene contract.

No tab titles, document text, input values, browser scripting or background-tab
scan. Only document/player structure and controls identify the current scene.
"""
from collections import deque
import hashlib
import time
from urllib.parse import urlsplit

from .focus import TEXT_ROLES, inspect_focus
from .models import PDF, IMAGE, VIDEO, MUSIC, SceneResult
from .documents import document_kind
from .playback import PlaybackControls
from .diagnostics import explain, window_rejection

STRUCTURAL = {'AXWebArea', 'AXGroup', 'AXScrollArea', 'AXLayoutArea', 'AXToolbar',
              'AXList', 'AXRow', 'AXCell', 'AXDocument', 'AXUnknown', 'AXSplitGroup',
              'AXTabGroup', 'AXGrid'}
PLAYER_CLASSES = {'html5-video-player', 'video-js', 'plyr', 'jwplayer', 'bpx-player-primary-area'}
PDF_ROLES = {'AXPDF', 'AXPDFDocument', 'AXPDFPluginSubrole'}
# Real pages include hundreds of caption, timeline-segment and recommendation
# nodes. Keep a wall-clock deadline and finite tree limits, without the old 220
# node ceiling that rejected a player already found on ordinary Safari pages.
MAX_PAGE_NODES = 1200
MAX_PAGE_DEPTH = 24


def _classes(node, read):
    values = read(node, 'AXDOMClassList') or []
    return set(values.split() if isinstance(values, str) else values)


def _pdf_surface(node, read):
    role, subrole = read(node, 'AXRole'), read(node, 'AXSubrole')
    if role in PDF_ROLES or subrole in PDF_ROLES:
        return True
    if role in {'AXGroup', 'AXDocument', 'AXUnknown', 'AXScrollArea'}:
        description = str(read(node, 'AXRoleDescription') or '').casefold().strip()
        if description in {'pdf document', 'pdf文稿', 'pdf 文稿', 'pdf文档', 'pdf 文档'}:
            return True
        # PDF.js uses both markers, never the title/filename of a web article.
        if read(node, 'AXDOMIdentifier') == 'viewer' and 'pdfViewer' in _classes(node, read):
            parent = read(node, 'AXParent')
            return parent is not None and read(parent, 'AXDOMIdentifier') == 'viewerContainer'
    return False


def _window_page(window, read, *, include_web=False):
    """Find one visible content root without entering background tab controls.

    Search only the current window's chrome/content containers. Never enter a
    web area, tab button, sidebar list or a document's text to find another page.
    """
    queue, found, seen = deque([(window, 0)]), [], []
    while queue and len(seen) < 80:
        item, depth = queue.popleft()
        if any(item == previous for previous in seen):
            continue
        seen.append(item)
        if read(item, 'AXHidden') or read(item, 'AXEnabled') is False or read(item, 'AXElementBusy'):
            continue
        if (read(item, 'AXSubrole') == 'AXPDFPluginSubrole'
                or (include_web and read(item, 'AXRole') == 'AXWebArea')):
            found.append(item)
            continue
        if read(item, 'AXRole') in {'AXWindow', 'AXGroup', 'AXSplitGroup', 'AXTabGroup', 'AXScrollArea'}:
            children = list(read(item, 'AXChildren') or [])
            if depth >= 12 and children:
                return None
            queue.extend((child, depth + 1) for child in children)
    return found[0] if len(found) == 1 and not queue else None


def _pdf_pages(plugin, read, *, verify_readonly=False):
    """Native page objects provide identity when Safari omits URL/document.

    Form fields or incomplete scans prevent inference of a nontext responder.
    Do not read document titles, text values or selected content.
    """
    children = list(read(plugin, 'AXChildren') or [])
    pages = [child for child in children if read(child, 'AXRole') == 'AXPage']
    if not verify_readonly:
        # An explicit owned focus is already classified by inspect_focus. Do
        # not rescan a long PDF body just to recover its document identity.
        return (pages[0] if pages else None), None
    queue, visited = deque(children), 0
    while queue and visited < MAX_PAGE_NODES:
        item = queue.popleft(); visited += 1
        role = read(item, 'AXRole')
        if (role in TEXT_ROLES | {'AXSheet', 'AXDialog'}
                or (role == 'AXUnknown' and read(item, 'AXValueSettable') is not False)
                or read(item, 'AXSubrole') in {'AXDialog', 'AXSystemDialog'}
                or read(item, 'AXEditable') or read(item, 'AXIsEditable')
                or read(item, 'AXValueSettable') is True):
            return (pages[0] if pages else None), False
        if role in STRUCTURAL | {'AXPage'}:
            queue.extend(read(item, 'AXChildren') or [])
    return (pages[0] if pages else None), bool(pages) and not queue


def detect_browser(window, focus, attr, *, budget=.30, profiles=None, observation=False):
    details = {}
    supported = set(profiles) if profiles is not None else {PDF, IMAGE, VIDEO, MUSIC}
    def done(result, reason, **facts):
        if result.scene and result.scene not in supported:
            result = SceneResult(input_context=result.input_context,
                page_key=result.page_key, web_area=result.web_area)
            reason = 'unsupported_media_kind'
        return explain(result, 'browser', reason, **details, **facts)
    deadline = time.monotonic() + budget
    def read(node, key):
        details['last_attribute'] = key
        details['metadata_reads'] = details.get('metadata_reads', 0) + 1
        if time.monotonic() >= deadline:
            raise TimeoutError()
        return attr(node, key) if node is not None else None
    try:
        rejection = window_rejection(window, read)
        if rejection:
            return done(SceneResult(), rejection)
        snapshot = inspect_focus(window, focus, read, window_reference=False, page_focus=True)
        ancestors, context = snapshot.ancestors, snapshot.context
        details['focus_reason'] = snapshot.reason
        # The innermost focused frame owns the target, not another tab or iframe.
        web = next((node for node in ancestors if read(node, 'AXRole') == 'AXWebArea'), None)
        if web is None:
            web = next((node for node in ancestors if _pdf_surface(node, read)), None)
        focused_page = web is not None
        window_pdf = False
        if web is None and (focus is None or focus == window) and not snapshot.blocked:
            web = _window_page(window, read, include_web=observation)
            window_pdf = web is not None and read(web, 'AXRole') != 'AXWebArea'
        if web is None and observation and not snapshot.blocked and context != 'text':
            # Tab selection can leave keyboard focus in browser chrome. Only
            # the read-only notice observer may inspect the unique visible
            # page; shortcut dispatch still requires its original focus proof.
            web = _window_page(window, read, include_web=True)
            window_pdf = web is not None and read(web, 'AXRole') != 'AXWebArea'
        if observation:
            details['page_observation'] = True
            details['inferred_page'] = web is not None and not focused_page
        if web is None:
            return done(SceneResult(), 'no_focused_webpage')
        if read(web, 'AXElementBusy'):
            return done(SceneResult(), 'page_loading')
        url = str(read(web, 'AXURL') or read(web, 'AXDocument') or '')
        native_pdf = read(web, 'AXRole') != 'AXWebArea' and _pdf_surface(web, read)
        if native_pdf and not url:
            url = str(read(window, 'AXDocument') or '')
        if native_pdf and context == 'unknown' and snapshot.reason == 'unknown_focus_role':
            def pdf_focus_read(node, key):
                return 'AXGroup' if key == 'AXRole' and (node == web or read(node, key) == 'AXPage') else read(node, key)
            context = inspect_focus(window, focus, pdf_focus_read, window_reference=False).context
        first_page, readonly_pdf = _pdf_pages(web, read, verify_readonly=window_pdf) if native_pdf else (None, False)
        if native_pdf and first_page is not None and not url:
            # Unlike a URL, this constant never selects a site or document.
            # Dispatch compares both web_area AND first_page object identity.
            base = dict(page_key='native-pdf', web_area=web, player=first_page,
                        input_context=('nontext' if readonly_pdf else 'unknown') if window_pdf else context)
            return done(SceneResult(**base, scene=PDF), 'recognized', evidence='native_pdf_pages',
                        inferred_pdf_focus=window_pdf, readonly_pdf=readonly_pdf)
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https', 'file', 'blob', 'chrome-extension', 'moz-extension'}:
            return done(SceneResult(), 'page_url_unavailable')
        if parsed.scheme in {'http', 'https'} and not parsed.hostname:
            return done(SceneResult(), 'page_url_unavailable')
        if window_pdf:
            context = 'nontext' if readonly_pdf else 'unknown'
        base = dict(page_key=hashlib.sha256(url.encode()).hexdigest(),
                    web_area=web, input_context=context)
        document = PDF if native_pdf else document_kind(url)
        details['document_kind'] = document
        if document in {PDF, IMAGE}:
            return done(SceneResult(**base, scene=document), 'recognized', evidence='page_document_type')

        # Each media root/wrapper gets its own control evidence. A Play button in
        # one widget and a progress slider in another never count as one player.
        queue = deque([(web, 0, None)])
        players, documents, visited, limited = [], [], 0, False
        while queue and visited < MAX_PAGE_NODES:
            item, depth, group = queue.popleft(); visited += 1
            details['scanned_nodes'] = visited
            if read(item, 'AXHidden') or read(item, 'AXEnabled') is False:
                continue
            role = read(item, 'AXRole')
            if role == 'AXWebArea' and item != web:
                continue  # Only enter an iframe when it owns the actual focus.
            if _pdf_surface(item, read):
                documents.append(item)
                continue  # PDF body text never participates in scene recognition.
            classes = _classes(item, read) if role in STRUCTURAL else set()
            if classes & PLAYER_CLASSES:
                group = dict(root=item, controls=PlaybackControls())
            subrole = read(item, 'AXSubrole')
            kind = VIDEO if subrole == 'AXVideo' or role == 'AXVideo' else MUSIC if subrole == 'AXAudio' or role == 'AXAudio' else ''
            if kind and read(item, 'AXEnabled') is True:
                if group is None:
                    group = dict(root=item, controls=PlaybackControls())
                players.append(dict(node=item, group=group, scene=kind,
                    focused=any(group['root'] == node or item == node for node in ancestors)))
            if group is not None:
                group['controls'].observe(item, read, use_title=False, web_structure=True)
            if role in STRUCTURAL or kind:
                children = list(read(item, 'AXChildren') or [])
                if depth >= MAX_PAGE_DEPTH:
                    limited |= bool(children)
                    continue
                remaining = max(0, MAX_PAGE_NODES - visited - len(queue))
                limited |= len(children) > remaining
                queue.extendleft((child, depth + 1, group) for child in reversed(children[:remaining]))
        limited |= bool(queue)
        details.update(scanned_nodes=visited, player_count=len(players), scan_limited=limited)
        details['player_evidence'] = [dict(kind=p['scene'], focused=p['focused'],
            play=p['group']['controls'].play, seek=p['group']['controls'].seek,
            structural_seek=p['group']['controls'].structural_seek) for p in players[:4]]
        focused_documents = [item for item in documents if any(item == node for node in ancestors)]
        selected_pdf = (focused_documents[0] if len(focused_documents) == 1 else
                        documents[0] if len(documents) == 1 and not players and not limited and focus == web else None)
        if selected_pdf is not None:
            return done(SceneResult(**base, scene=PDF, player=selected_pdf), 'recognized', evidence='pdf_surface')
        if documents and focus == web:
            return done(SceneResult(**base), 'multiple_content_surfaces')
        focused = [player for player in players if player['focused']]
        eligible = focused if focused else [player for player in players
            if player['group']['controls'].loaded and not limited]
        if len(eligible) == 1:
            player = eligible[0]
            if context == 'nontext' and focus != web and not player['focused']:
                base['input_context'] = 'unknown'
                details['focus_reason'] = 'focus_outside_player'
            controls = player['group']['controls']
            return done(SceneResult(**base, player=player['node'], scene=player['scene']), 'recognized',
                evidence='focused_web_player' if player['focused'] else 'web_player_controls',
                play_control=controls.play, seek_control=controls.seek)
        reason = 'multiple_players' if len(eligible) > 1 else 'page_scan_incomplete' if limited else 'no_web_player'
        return done(SceneResult(**base), reason)
    except TimeoutError:
        return done(SceneResult(), 'recognition_timeout')
    except (TypeError, ValueError, UnicodeError) as exc:
        return done(SceneResult(), 'invalid_metadata', error_type=type(exc).__name__)
