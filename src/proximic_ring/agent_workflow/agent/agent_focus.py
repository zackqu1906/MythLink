"""Retain one exact application window for the Agent/desktop focus toggle."""
from proximic_ring.desktop.window_selector import MacWindowAX
from datetime import datetime, timezone


class AgentFocusMemory:
    def __init__(self, ax=None):
        self.ax = ax or MacWindowAX()
        self.target = None

    def capture(self, host_pid):
        front = self.ax.front() or {}
        pid = front.get('pid', 0)
        if not pid or pid == host_pid:
            return {'status':'unchanged'}
        apps = self.ax.apps()
        window = self.ax.attr(self.ax.application(pid), 'AXFocusedWindow')
        if pid not in apps or window is None:
            return {'status':'unavailable'}
        self.target = (pid, apps[pid], window)
        context = {'pid':pid, 'captured_at':datetime.now(timezone.utc).isoformat(),
                   'source':'agent_focus_capture', 'coverage':'application_window_metadata'}
        try:
            context.update(self.ax.app_info(apps[pid]))
            context['window_title'] = str(self.ax.attr(window, 'AXTitle') or '')
            # Resolve only the focused AX window. Ambiguous matches deliberately
            # carry no ID, so automatic capture cannot select another window.
            frame = self.ax.rect(window)
            matches = [w for w in self.ax.on_screen() if w['pid'] == pid and frame
                       and all(abs(a-b) <= 2 for a,b in zip(w['frame'], frame))]
            if len(matches) == 1:
                context['window_id'] = matches[0]['number']
                context['window_frame'] = list(matches[0]['frame'])
            document = self.ax.attr(window, 'AXDocument')
            if document and str(document).startswith('file://'):
                context['document_url'] = str(document)
        except Exception:
            context['metadata_incomplete'] = True
        return {'status':'remembered', 'context':context}

    def restore(self):
        if self.target is None:
            return {'status':'missing'}
        pid, app, window = self.target
        if pid not in self.ax.apps() or window not in self.ax.windows(pid):
            self.target = None
            return {'status':'missing'}
        return {'status':'restored' if self.ax.activate(app, window) else 'unavailable'}
