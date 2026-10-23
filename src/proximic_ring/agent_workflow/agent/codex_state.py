"""Projection of owned App Server events into the desktop's version 11 state."""
from copy import deepcopy
import time


def milliseconds():
    return int(time.time()*1000)


def desktop_input(items):
    """App Server accepts omitted text_elements; desktop renderers do not.

    Normalize at the projection boundary, including user/steering items, while
    preserving placeholders and image references. Never mutate the RPC payload.
    """
    items = deepcopy(items)
    for item in items:
        if item.get('type') == 'text':
            if item.get('text_elements') is None:
                item['text_elements'] = []
            elif not isinstance(item['text_elements'], list):
                raise ValueError('Codex 同步消息的 text_elements 必须是数组')
    return items


class DesktopState:
    def __init__(self, result):
        thread = result['thread']
        self.tid = thread['id']
        self.settings = deepcopy(result)
        self.turns = {}
        self.next_params = {}
        self.dirty = True
        self.revision = 0
        model = result.get('model', thread.get('model', ''))
        effort = result.get('reasoningEffort')
        mode = dict(mode='default', settings=dict(model=model,
                    reasoning_effort=effort, developer_instructions=None))
        permissions = {key:deepcopy(result.get(key)) for key in (
            'approvalPolicy','approvalsReviewer','activePermissionProfile','runtimeWorkspaceRoots')}
        permissions['sandboxPolicy'] = deepcopy(result.get('sandbox'))
        settings = {key:deepcopy(result.get(key)) for key in (
            'model','cwd','serviceTier','approvalPolicy','approvalsReviewer',
            'activePermissionProfile','disabledPluginIds','multiAgentMode')}
        settings.update(sandboxPolicy=permissions['sandboxPolicy'], effort=effort,
            permissions=(result.get('activePermissionProfile') or {}).get('id'), collaborationMode=mode)
        self.state = dict(
            id=self.tid, sessionId=thread.get('sessionId', self.tid), hostId='local',
            turns=[], requests=[], title=thread.get('name'), forkedFromId=thread.get('forkedFromId'),
            parentThreadId=thread.get('parentThreadId'), originator=thread.get('originator'),
            source=thread.get('source'), agentNickname=thread.get('agentNickname'),
            threadSource=thread.get('threadSource'), historyMode='paginated',
            canonicalVoiceHistory=True, mode='default', threadStartKind='default',
            createdAt=thread.get('createdAt', time.time())*1000,
            updatedAt=thread.get('updatedAt', time.time())*1000,
            recencyAt=thread.get('recencyAt', time.time())*1000,
            modelProvider=result.get('modelProvider', thread.get('modelProvider')),
            daybreakEnabled=thread.get('daybreakEnabled'), latestModel=model,
            latestReasoningEffort=effort, previousTurnModel=None, latestCollaborationMode=mode,
            hasUnreadTurn=False, threadGoal=None, threadRuntimeStatus=thread.get('status', {'type':'idle'}),
            rolloutPath=thread.get('path'), gitInfo=thread.get('gitInfo'), resumeState='resumed',
            latestTokenUsageInfo=None, workspaceKind='project', cwd=thread.get('cwd'),
            environments=thread.get('environments'), workspaceBrowserRoot=None,
            projectlessOutputDirectory=None, boundedCloudHistory=False,
            currentPermissions=permissions, latestThreadSettings=settings,
            turnsPagination=dict(olderCursor=None, oldestLoadedTurnId=None,
                                 isLoadingOlder=False, hasLoadedOldest=True))

    def prepare_turn(self, params):
        self.next_params = deepcopy(params)

    def turn(self, tid):
        if tid not in self.turns:
            settings = self.settings
            params = dict(threadId=self.tid, input=[], cwd=settings.get('cwd'),
                model=settings.get('model'), effort=settings.get('reasoningEffort'),
                approvalPolicy=settings.get('approvalPolicy'),
                approvalsReviewer=settings.get('approvalsReviewer'),
                sandboxPolicy=deepcopy(settings.get('sandbox')),
                permissions=(settings.get('activePermissionProfile') or {}).get('id'),
                runtimeWorkspaceRoots=settings.get('runtimeWorkspaceRoots'),
                summary='none', personality=None, outputSchema=None, collaborationMode=None,
                attachments=[], clientUserMessageId=None)
            params.update(self.next_params)
            params['input'] = desktop_input(params['input'])
            self.next_params = {}
            self.turns[tid] = dict(params=params, permissionParamsSource='inferred', turnId=tid,
                turnStartedAtMs=milliseconds(), durationMs=None, finalAssistantStartedAtMs=None,
                status='inProgress', error=None, diff=None, items=[], completedItemIds={},
                itemsPagination=dict(olderCursor=None, isLoadingOlder=False,
                                     hasLoadedOldest=True, newestSnapshotItemId=None))
        return self.turns[tid]

    def remove_request(self, rid):
        before = self.state['requests']
        self.state['requests'] = [r for r in before if r['id'] != rid]
        self.dirty |= len(before) != len(self.state['requests'])

    def observe(self, event):
        method, p = event.get('method',''), event.get('params') or {}
        if p.get('threadId') != self.tid:
            return
        self.dirty = True
        self.state['updatedAt'] = milliseconds()
        if method == 'thread/status/changed':
            self.state['threadRuntimeStatus'] = deepcopy(p['status'])
        elif method == 'thread/settings/updated':
            settings = deepcopy(p['threadSettings'])
            self.state['latestThreadSettings'] = settings
            for key in ('model','cwd','approvalPolicy','approvalsReviewer','activePermissionProfile'):
                if key in settings: self.settings[key] = settings[key]
            if 'sandboxPolicy' in settings: self.settings['sandbox'] = settings['sandboxPolicy']
            if 'effort' in settings: self.settings['reasoningEffort'] = settings['effort']
            self.state['latestModel'] = self.settings.get('model','')
            self.state['latestReasoningEffort'] = self.settings.get('reasoningEffort')
            if settings.get('collaborationMode'):
                self.state['latestCollaborationMode'] = settings['collaborationMode']
            for key in ('approvalPolicy','approvalsReviewer','activePermissionProfile',
                        'sandboxPolicy','runtimeWorkspaceRoots'):
                if key in settings: self.state['currentPermissions'][key] = settings[key]
        elif method == 'thread/name/updated':
            self.state['title'] = p.get('threadName')
        elif method == 'thread/tokenUsage/updated':
            self.state['latestTokenUsageInfo'] = deepcopy(p.get('tokenUsage'))
        elif method == 'serverRequest/resolved':
            self.remove_request(p['requestId'])
        elif 'id' in event:
            request = deepcopy(event)
            if method == 'item/tool/call' and p.get('tool') == 'ask_ring_user':
                args = p.get('arguments') or {}
                request['method'] = 'item/tool/requestUserInput'
                request['params'] = dict(threadId=self.tid, turnId=p.get('turnId'),
                    itemId=p.get('callId'), questions=[dict(id='ring_answer', header='Agent',
                        question=args.get('question',''), isOther=True, isSecret=False,
                        options=[dict(label=o, description='') for o in args.get('options',[])])])
            self.state['requests'].append(request)
        elif method.startswith('turn/') and p.get('turn'):
            raw = p['turn']
            turn = self.turn(raw['id'])
            turn.update(status=raw.get('status', 'inProgress'), error=raw.get('error'))
            # Completion notifications may carry no items; never erase streamed items.
            for item in raw.get('items', []):
                self._item(turn, item, method == 'turn/completed')
            active = method != 'turn/completed'
            self.state['threadRuntimeStatus'] = {'type':'active','activeFlags':[]} if active else {'type':'idle'}
            self.state['recencyAt'] = milliseconds()
            if not active:
                turn['durationMs'] = milliseconds()-turn['turnStartedAtMs']
                self.state['requests'] = [r for r in self.state['requests']
                                         if r['params'].get('turnId') != raw['id']]
        elif p.get('turnId'):
            turn = self.turn(p['turnId'])
            if method in ('item/started','item/completed'):
                self._item(turn, p['item'], method == 'item/completed')
            elif method == 'turn/diff/updated':
                turn['diff'] = p.get('diff')
            elif method == 'turn/plan/updated':
                self._item(turn, dict(type='plan', id='plan:'+p['turnId'],
                    text=p.get('explanation') or '', plan=p.get('plan', [])), False)
            elif method.endswith(('/delta','TextDelta','/textDelta')):
                item = next((i for i in turn['items'] if i['id'] == p.get('itemId')), None)
                if item is None: return
                field = {'item/agentMessage/delta':'text', 'item/plan/delta':'text',
                         'item/commandExecution/outputDelta':'aggregatedOutput'}.get(method)
                if field: item[field] = (item.get(field) or '')+p.get('delta','')
                elif method in ('item/reasoning/summaryTextDelta','item/reasoning/textDelta'):
                    field = 'summary' if 'summary' in method else 'content'
                    index = p.get('summaryIndex', p.get('contentIndex', 0))
                    parts = item.setdefault(field, [])
                    if 0 <= index < 1000:
                        while len(parts) <= index: parts.append('')
                        parts[index] += p.get('delta','')
            elif method == 'item/commandExecution/outputDelta':
                item = next((i for i in turn['items'] if i['id'] == p.get('itemId')), None)
                if item: item['aggregatedOutput'] = (item.get('aggregatedOutput') or '')+p.get('delta','')

    def _item(self, turn, item, complete):
        item = deepcopy(item)
        if not item.get('id'): return
        if item.get('type') in ('userMessage', 'steeringUserMessage'):
            item['content'] = desktop_input(item.get('content') or [])
        previous = next((i for i in turn['items'] if i['id'] == item['id']), None)
        if previous is not None: previous.update(item)
        else: turn['items'].append(item)
        if complete: turn['completedItemIds'][item['id']] = True
        turn['itemsPagination']['newestSnapshotItemId'] = item['id']

    def snapshot(self):
        state = deepcopy(self.state)
        keys = ['turn:'+tid for tid in self.turns]
        state['turnsPagination']['oldestLoadedTurnId'] = next(iter(self.turns), None)
        state['turnHistory'] = dict(kind='canonical', history=dict(
            entitiesByKey={'turn:'+tid:deepcopy(turn) for tid,turn in self.turns.items()},
            generation=0, isComplete=True, islands=[dict(id='tail:0',
                entries=[dict(key=key, value=key) for key in keys],
                olderBoundary=dict(status='exhausted', boundaryId='tail:0:older'),
                newerBoundary=dict(status='exhausted', boundaryId='tail:0:newer'))]))
        self.revision += 1
        self.dirty = False
        return dict(type='snapshot', revision=self.revision, conversationState=state)
