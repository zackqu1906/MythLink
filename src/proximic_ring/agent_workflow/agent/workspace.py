"""Request-bound cards and drafts, independent of Qt and the Agent transport."""
from copy import deepcopy
from proximic_ring.agent_workflow.agent.choice_text import match_choice


def bounded_frame(frame, bounds, inset=8):
    """Clamp every animation sample to the current display's usable rectangle."""
    x,y,w,h=frame
    bx,by,bw,bh=bounds
    pad=max(0,min(inset,(bw-1)//2,(bh-1)//2))
    w=max(1,min(w,bw-2*pad));h=max(1,min(h,bh-2*pad))
    return (max(bx+pad,min(x,bx+bw-pad-w)),
            max(by+pad,min(y,by+bh-pad-h)),w,h)


def stack_frames(sizes, bounds, focused='task', margin=24, gap=12):
    """Bottom anchored, non-overlapping targets; keep the focused request reachable.

    The model retains every request. When the screen cannot hold them all, focus
    navigation pages the visible cards while preserving the task at the bottom.
    Bounds are logical pixels and may have negative origins on another display.
    """
    bx,by,bw,bh=bounds
    margin=max(0,min(margin,(bw-1)//2,(bh-1)//2))
    usable=max(1,bh-2*margin)
    sizes={k:(max(1,min(w,bw-2*margin)),max(1,min(h,usable))) for k,(w,h) in sizes.items()}
    if not sizes: return {}
    anchor = next(iter(sizes))
    if focused != anchor and focused in sizes and usable > gap+1:
        if sizes[anchor][1]+sizes[focused][1]+gap > usable:
            task_height=min(sizes[anchor][1],max(1,min(92,usable//3)))
            sizes[anchor]=(sizes[anchor][0],task_height)
            sizes[focused]=(sizes[focused][0],max(1,usable-task_height-gap))
    chosen={anchor}
    remaining=usable-sizes[anchor][1]
    candidates=list(sizes)[1:]
    if focused in candidates:
        center=candidates.index(focused)
        candidates=sorted(candidates,key=lambda k:abs(list(sizes).index(k)-center-1))
    for key in candidates:
        if sizes[key][1]+gap <= remaining:
            chosen.add(key)
            remaining-=sizes[key][1]+gap
    bottom=by+bh-margin
    frames={}
    for key,(width,height) in sizes.items():
        if key in chosen:
            frames[key]=(bx+bw-margin-width,bottom-height,width,height)
            bottom-=height+gap
    return frames


class Workspace:
    def __init__(self):
        self.state = {}
        self.requests = []
        self.focus = 'task'
        self.drafts = {}
        self.attachments = {}
        self.draft_turns = {}
        self.selections = {}
        self.input_key = 'task'
        self.submitted = set()
        self._input_request = None

    @property
    def keys(self):
        # Questions live inside the task window; permissions remain mouse-only.
        return ['task'] + [r['token'] for r in self.requests if r['kind'] != 'question']

    def update(self, state, *, hold_input=False):
        self.state = deepcopy(state)
        self.requests = deepcopy(state.get('requests', [state['request']] if state.get('request') else []))
        for r in self.requests:
            key = r['token']
            if key not in self.selections:
                enabled = [o for o in r['options'] if o['enabled']]
                self.selections[key] = next((o['index'] for o in enabled if o.get('preferred')),
                                            enabled[0]['index'] if enabled else -1)
        if self.focus not in self.keys:
            self.focus = 'task'
        live = {'task', *(r['token'] for r in self.requests)}
        self.submitted.intersection_update(live)
        self.reconcile_input(hold_input=hold_input)
        # Never retain answer drafts or selected options after a request expires.
        for mapping in (self.drafts, self.draft_turns, self.selections):
            for key in list(mapping):
                if key not in live and key != self.input_key:
                    del mapping[key]

    def reconcile_input(self, *, hold_input=False):
        # A task draft is parked while questions take priority. An in-progress
        # answer keeps its own request (even if expired), never a new recipient.
        if hold_input or (self.input_key != 'task' and self.drafts.get(self.input_key)):
            return
        question = next((r for r in self.requests if r['kind'] == 'question'), None)
        self.input_key = question['token'] if question else 'task'
        self._input_request = deepcopy(question)

    @property
    def input_request(self):
        request = self.card(self.input_key)
        if request:
            return {**request, 'submitting': self.input_key in self.submitted}
        if self.input_key != 'task' and self._input_request:
            return {**self._input_request, 'expired': True}
        return None

    def sent(self, key):
        self.drafts[key] = ''
        self.attachments.pop(key, None)
        self.draft_turns.pop(key, None)
        if key != 'task':
            self.submitted.add(key)
        self.reconcile_input()

    def card(self, key):
        return next((r for r in self.requests if r['token'] == key), None)

    def edit(self, key, text):
        if key != 'task' and key != self.input_key and not self.card(key):
            return
        if key not in self.draft_turns or not (self.drafts.get(key) or self.attachments.get(key)):
            self.draft_turns[key] = self.state.get('turnId', '') if key == 'task' else ''
        self.drafts[key] = text

    def attach(self, key, attachments, target_turn=None):
        if key != 'task' or key != self.input_key or not attachments:
            return
        if not (self.drafts.get(key) or self.attachments.get(key)):
            self.draft_turns[key] = self.state.get('turnId', '') if target_turn is None else target_turn
        current = self.attachments.setdefault(key, [])
        paths = {item['path'] for item in current}
        for item in attachments:
            if item['path'] not in paths:
                current.append(deepcopy(item))
                paths.add(item['path'])

    def remove_attachment(self, key, path):
        self.attachments[key] = [item for item in self.attachments.get(key, []) if item['path'] != path]
        if not self.attachments[key] and not self.drafts.get(key):
            self.draft_turns.pop(key, None)

    def move(self, direction):
        i = self.keys.index(self.focus)
        self.focus = self.keys[max(0, min(len(self.keys)-1, i+direction))]
        return self.focus

    def select(self, key, direction):
        card = self.card(key)
        if not card:
            return
        enabled = [o['index'] for o in card['options'] if o['enabled']]
        if enabled:
            i = enabled.index(self.selections[key]) if self.selections.get(key) in enabled else 0
            self.selections[key] = enabled[max(0, min(len(enabled)-1, i+direction))]

    def submission(self, key):
        if key != self.input_key:
            return  # A queued action cannot submit a hidden draft.
        text = self.drafts.get(key, '').strip()
        attachments = self.attachments.get(key, []) if key == 'task' else []
        if not (text or attachments) or key in self.submitted:
            return None
        if key != 'task':
            card = self.card(key)
            if not card:
                raise ValueError('刚才的问题已结束，文字已保留。清空输入后会显示当前问题。')
            if card['kind'] != 'question' or card.get('blocked'):
                return None
            index = match_choice(text, card['options'])
            if index is not None:
                return self.confirm(key, index)
            return {'type':'answer', 'questionToken':key, 'text':text}
        target = self.draft_turns.get(key, '')
        if target and target != self.state.get('turnId', ''):
            raise ValueError('原任务已结束。草稿已保留，请确认内容后再作为新任务发送。')
        command = {'type':'steer' if target else 'text', 'text':text, 'targetTurn':target}
        if attachments:
            command['attachments'] = deepcopy(attachments)
        return command

    def confirm(self, key, index=None):
        card = self.card(key)
        if not card:
            return None
        index = self.selections.get(key, -1) if index is None else index
        option = next((o for o in card['options'] if o['index'] == index and o['enabled']), None)
        if not option:
            return None
        return {'type':'choose', 'token':key, 'index':index}
