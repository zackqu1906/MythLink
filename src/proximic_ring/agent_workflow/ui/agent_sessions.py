"""One floating workspace, with isolated conversations and one input owner."""
from copy import deepcopy
from uuid import uuid4

from PySide6.QtCore import QObject, Signal


class AgentSessions(QObject):
    added = Signal(object)
    closing = Signal(object)

    def __init__(self, root):
        super().__init__(root)
        self.root = self.active = root
        self.members = [root]

    @property
    def focused(self):
        return any(s._visible and s._focused for s in self.members)

    @property
    def capture_active(self):
        return any(s.captureActive for s in self.members)

    @property
    def picking_attachments(self):
        return any(s.pickingAttachments for s in self.members)

    def notify_focus(self):
        for session in self.members:
            session.changed.emit()

    def activate(self, session):
        previous = self.active
        if previous is not session:
            previous._retire_editor_input(previous.inputKey)
            previous._focus_epoch += 1
            previous._capture_pending = False
            previous._cancel_input_action()
            previous._editor_key = ''
            previous._focused = False
            session._local_return = previous._local_return
            session._record_context = deepcopy(previous._record_context)
            self.active = session
        session._focused = True
        self.notify_focus()

    def can_switch(self, source):
        # A native sentence cannot be handed to a different conversation.
        return (not source._closed and not self.picking_attachments and not self.capture_active
                and not any(s.contextCapturing or s._hold_editor_input() for s in self.members))

    def create(self, source, *, activate=True):
        if not self.can_switch(source):
            return None
        from .agent_controller import AgentController
        session = AgentController(source.owner, flow_factory=source._flow_factory,
                                  sessions=self, session_id=uuid4().hex)
        session._auto = source._auto
        session._approval_mode = source._approval_mode
        session._auto_context_mode = source._auto_context_mode
        session._record_context = deepcopy(source._record_context)
        session._invocation_context = deepcopy(source._invocation_context)
        session._local_return = self.active._local_return
        session._visible = activate
        self.members.append(session)
        self.added.emit(session)
        if activate: self.activate(session)
        session._emit()
        if activate: session.focusRequested.emit('task')
        return session

    def replace(self, source, target):
        """Replace a visible card only after the selected conversation is ready."""
        if not self.can_switch(source) or not source.canDismiss: return False
        focused = source.focused
        target._visible = True
        # Transfer the key panel before hiding its predecessor. Dismissing the
        # active panel first releases native application focus and starts the
        # delayed return to the previous app, racing the target's activation.
        # Use the current state: a user who switched away while connecting must
        # not be pulled back when the asynchronous connection becomes ready.
        if focused:
            target.focusCard('task')
        elif self.active is source:
            self.active = target
        source.dismiss()
        target._emit()
        self.notify_focus()
        return True

    def dismissed(self, session):
        if self.active is session:
            self.active = next((s for s in reversed(self.members) if s.visible), session)
        self.notify_focus()

    def entries(self):
        for session in self.members:
            for card in session.cards:
                key = card['key'] if session is self.root else session.session_id + '/' + card['key']
                yield key, session, card

    def next_task(self, source):
        visible = [s for s in self.members if s.visible]
        if (len(visible) < 2 or source not in visible or self.picking_attachments
                or self.capture_active or any(s._hold_editor_input() for s in self.members)):
            return
        target = visible[(visible.index(source) + 1) % len(visible)]
        target.focusCard('task')

    def window_key(self, session, key):
        return key if session is self.root else session.session_id + '/' + key

