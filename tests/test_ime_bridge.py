import json
import os
import socket
import stat
import threading
import tempfile
from pathlib import Path
import time

import pytest

from proximic_ring.voice.ime.ime_bridge import IMEBridge


def receive(stream):
    return json.loads(stream.readline())


def send(stream, message):
    stream.write(json.dumps(message).encode() + b"\n")
    stream.flush()


def connect(bridge, token=None):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2)
    sock.connect(str(bridge.path))
    stream = sock.makefile("rwb")
    send(stream, {"type": "hello", "protocol": 1, "token": token or (bridge.path.parent / "bridge.token").read_text()})
    return sock, stream


@pytest.fixture
def bridge(tmp_path):
    messages = []
    with tempfile.TemporaryDirectory(prefix="ime-test-", dir="/private/tmp") as directory:
        value = IMEBridge(messages.append, path=Path(directory) / "bridge.sock")
        value.start()
        yield value, messages
        value.close()


def wait_until(predicate):
    for _ in range(100):
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.mark.parametrize('selection', [[1, 0], [-1, -1]])
def test_context_key_is_armed_before_event_and_waits_for_new_client(bridge, selection):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)['epoch']
        origin = ('com.netease.macmail', 504)
        posted, result = [], []
        worker = threading.Thread(target=lambda: result.append(server.refresh_input_context(
            origin, lambda tag, **_: posted.append(tag), timeout=1)))
        worker.start()
        arm = receive(stream)
        assert arm['type'] == 'prepare_input_context' and not posted
        assert arm['foreground_pid'] == 504
        send(stream, dict(type='input_context_prepared', request_id=arm['request_id'], epoch=epoch, armed=True))
        wait_until(lambda: posted)
        assert posted == [arm['event_tag']] and not result
        assert not server._state  # An arm receipt is not text focus.
        send(stream, dict(type='state', request_id=arm['request_id'], epoch=epoch,
                          application=origin[0], client_id='new-body', utterance_id='',
                          ready=True, selection=selection))
        worker.join(2)
        assert result == [True]
        assert receive(stream)['type'] == 'cancel_input_context'
        assert not server._control_replies and not server._context_replies
        assert not any(m.get('request_id') == arm['request_id'] for m in messages)
    finally:
        stream.close(); sock.close()


@pytest.mark.parametrize('failure', ['denied', 'timeout', 'post_error', 'wrong_app', 'disconnect'])
def test_context_key_failure_cannot_reuse_cached_success(bridge, failure):
    server, _ = bridge
    sock, stream = connect(server)
    epoch = receive(stream)['epoch']
    posted, result = [], []
    def post(tag, **_):
        posted.append(tag)
        if failure == 'post_error':
            raise RuntimeError('source changed')
    def run():
        try:
            result.append(server.refresh_input_context(('mail', 504), post, timeout=.15))
        except RuntimeError:
            result.append(False)
    worker = threading.Thread(target=run)
    try:
        worker.start()
        arm = receive(stream)
        if failure != 'timeout':
            send(stream, dict(type='input_context_prepared', request_id=arm['request_id'], epoch=epoch,
                              armed=failure != 'denied'))
        if failure == 'wrong_app':
            wait_until(lambda: posted)
            send(stream, dict(type='state', request_id=arm['request_id'], epoch=epoch,
                              application='other', client_id='other', ready=True))
        if failure == 'disconnect':
            sock.shutdown(socket.SHUT_RDWR)
        worker.join(2)
        assert result == [False]
        if failure in {'denied', 'timeout'}:
            assert not posted
        assert not server._context_replies and not server._control_replies
    finally:
        stream.close(); sock.close()


def test_context_key_accepts_fresh_activation_without_function_key_callback(bridge):
    # MailMaster activates a new IMK client for F20 but does not deliver the
    # function key to handle(). Its real activate_prepared state is the receipt.
    server, _ = bridge
    sock, stream = connect(server)
    posted, result = [], []
    try:
        epoch = receive(stream)['epoch']
        old = dict(type='state', epoch=epoch, application='mail', client_id='old',
                   ready=True, utterance_id='', selection=[-1, -1])
        send(stream, old)
        wait_until(lambda: server._state.get('client_id') == 'old')
        worker = threading.Thread(target=lambda: result.append(server.refresh_input_context(
            ('mail', 504), lambda tag, **_: posted.append(tag), timeout=.5)))
        worker.start()
        arm = receive(stream)
        send(stream, dict(type='input_context_prepared', request_id=arm['request_id'], epoch=epoch, armed=True))
        wait_until(lambda: posted)
        send(stream, old)  # Cached/repeated readiness is insufficient.
        time.sleep(.02)
        assert not result
        send(stream, {**old, 'client_id': 'new-body', 'selection': [0, 0]})
        worker.join(.2)
        assert result == [True]
        assert receive(stream)['type'] == 'cancel_input_context'
    finally:
        worker.join(1)
        stream.close(); sock.close()


@pytest.mark.parametrize('change', ['', 'client', 'utterance', 'foreground'])
def test_context_capture_requests_text_directly_and_keeps_original_target(bridge, change):
    server, _ = bridge
    sock, stream = connect(server)
    origin = ('com.openai.codex', 42)
    front = [origin]
    result = []
    epoch = receive(stream)['epoch']
    base = dict(type='state', epoch=epoch, ready=True, application=origin[0], client_id='old',
                utterance_id='', selection=[3, 0], context_key_supported=True)
    send(stream, base)
    wait_until(lambda: server._state.get('client_id') == 'old')
    worker = threading.Thread(target=lambda: result.append(server.asr_context(timeout=1, foreground=lambda: front[0])))
    try:
        worker.start()
        document = receive(stream)
        assert document['type'] == 'ping' and document['capture_context'] is True
        if change == 'foreground':
            front[0] = (origin[0], 43)
        if change == 'client':
            base['client_id'] = 'another-field'
        if change == 'utterance':
            base['utterance_id'] = 'next'
        send(stream, {**base, 'request_id': document['request_id'], 'context_complete': True, 'original': '正文'})
        worker.join(2)
        if change:
            assert result[0]['status'] == 'unavailable' and 'text' not in result[0]
        else:
            assert result[0]['text'] == '正文' and result[0]['target_key'] == epoch + ':old'
    finally:
        worker.join(2)
        stream.close(); sock.close()


def test_context_key_without_receipt_posts_once_and_does_not_spend_callers_long_timeout(bridge):
    server, _ = bridge
    sock, stream = connect(server)
    posted, result = [], []
    epoch = receive(stream)['epoch']
    def run():
        started = time.monotonic()
        refreshed = server.refresh_input_context(('mail', 42), lambda tag, **_: posted.append(tag), timeout=1)
        result.append((refreshed, time.monotonic() - started))
    worker = threading.Thread(target=run)
    try:
        worker.start()
        arm = receive(stream)
        send(stream, dict(type='input_context_prepared', epoch=epoch, request_id=arm['request_id'], armed=True))
        worker.join(.4)
        assert len(posted) == 1 and result[0][0] is False
        assert result[0][1] < .25  # 120 ms budget plus scheduling allowance.
        assert receive(stream)['type'] == 'cancel_input_context'
    finally:
        worker.join(1)
        stream.close(); sock.close()


@pytest.mark.mac_only
def test_authenticated_epoch_and_private_files(bridge):
    server, messages = bridge
    assert stat.S_IMODE(server.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((server.path.parent / "bridge.token").stat().st_mode) == 0o600
    bad, bad_stream = connect(server, token="invalid")
    assert bad_stream.readline() == b""
    bad_stream.close(); bad.close()
    sock, stream = connect(server)
    welcome = receive(stream)
    epoch = welcome["epoch"]
    assert welcome["protocol"] == 1
    send(stream, {"type": "state", "epoch": "old", "client_id": "wrong"})
    send(stream, {"type": "state", "epoch": epoch, "client_id": "real", "ready": True})
    wait_until(lambda: any(m.get("client_id") == "real" for m in messages))
    assert not any(m.get("client_id") == "wrong" for m in messages)
    assert server.send({"type": "begin", "client_id": "real"})
    assert receive(stream)["epoch"] == epoch
    stream.close(); sock.close()
    wait_until(lambda: not server.connected)
    newer, newer_stream = connect(server)
    assert receive(newer_stream)["epoch"] != epoch
    newer_stream.close(); newer.close()


@pytest.mark.mac_only
def test_second_listener_does_not_remove_live_endpoint_or_rotate_token(bridge):
    first, _ = bridge
    token = (first.path.parent / "bridge.token").read_text()
    inode = first.path.stat().st_ino
    other = IMEBridge(lambda _: None, path=first.path)
    with pytest.raises(BlockingIOError):
        other.start()
    assert first.path.stat().st_ino == inode
    assert (first.path.parent / "bridge.token").read_text() == token
    sock, stream = connect(first)
    assert receive(stream)["type"] == "welcome"
    stream.close(); sock.close()


@pytest.mark.mac_only
def test_wechat_command_survives_transport_with_epoch_fencing(bridge):
    server, messages = bridge
    sock, stream = connect(server)
    epoch = receive(stream)["epoch"]
    command = dict(type="wechat_key", epoch=epoch, client_id="client", utterance_id="utterance",
                   request_id="request", application="com.tencent.xinWeChat", command="select_all",
                   count=0, event_tag=123)
    send(stream, {**command, "epoch": "stale", "request_id": "old"})
    send(stream, command)
    wait_until(lambda: any(m.get("request_id") == "request" for m in messages))
    assert not any(m.get("request_id") == "old" for m in messages)
    server.send(dict(type="wechat_key_result", request_id="request", error=""))
    assert receive(stream)["epoch"] == epoch
    stream.close(); sock.close()


@pytest.mark.mac_only
def test_activation_trace_does_not_replace_focus_state(bridge):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)['epoch']
        trace = dict(type='activation_trace', epoch=epoch, event='activate_callback',
                     client_id='pending', activated=False)
        send(stream, {**trace, 'epoch': 'stale', 'event': 'old_callback'})
        send(stream, trace)
        wait_until(lambda: trace in messages)
        assert not server._state
        assert not any(m.get('event') == 'old_callback' for m in messages)
    finally:
        stream.close(); sock.close()


@pytest.mark.mac_only
@pytest.mark.parametrize("kind", ["edit_requested", "polish_requested"])
def test_model_request_crosses_real_socket_and_result_returns_to_same_sentence(bridge, kind):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)["epoch"]
        request = dict(type=kind, epoch=epoch, client_id="editor", utterance_id="sentence",
                       revision=7, text="本句中文😀", instruction="本句中文😀")
        send(stream, {**request, "epoch": "stale", "utterance_id": "old"})
        send(stream, request)
        wait_until(lambda: request in messages)
        assert not any(m.get("utterance_id") == "old" for m in messages)
        assert not server._state  # Model requests do not replace the editor state.
        result = dict(type="polish_result" if kind == "polish_requested" else "edit_result",
                      client_id="editor", utterance_id="sentence", revision=7, text="润色后的本句😀", error="")
        assert server.send(result)
        assert receive(stream) == {**result, "epoch": epoch}
    finally:
        stream.close(); sock.close()


@pytest.mark.posix_only
@pytest.mark.parametrize("name", ["bridge.sock", "bridge.token", "bridge.lock"])
def test_reject_symlink_files(tmp_path, name):
    directory = tmp_path / "ime"
    directory.mkdir(mode=0o700)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("untouched")
    (directory / name).symlink_to(sentinel)
    bridge = IMEBridge(lambda _: None, path=directory / "bridge.sock")
    with pytest.raises((RuntimeError, OSError)):
        bridge.start()
    assert sentinel.read_text() == "untouched"


@pytest.mark.mac_only
def test_context_is_fresh_and_truncated_only_for_asr(bridge):
    server, messages = bridge
    sock, stream = connect(server)
    epoch = receive(stream)["epoch"]
    original = "完整原文" * 700
    result = []
    worker = threading.Thread(target=lambda: result.append(server.asr_context(timeout=1)))
    worker.start()
    ping = receive(stream)
    assert ping["capture_context"] is True
    send(stream, {"type": "state", "epoch": epoch, "request_id": ping["request_id"], "client_id": "a", "phase": "idle", "ready": True, "original": original, "selection": [0, 0], "context_complete": True, "application": "example"})
    worker.join(2)
    assert result[0]["text"] == original[-1600:]
    assert result[0]["source_char_count"] == len(original)
    assert server._state["original"] == original
    # Cached full context must never stand in for a new authoritative response.
    assert server.asr_context(timeout=.02)["status"] == "unavailable"
    receive(stream)
    stream.close(); sock.close()


def test_clipboard_palette_ack_works_without_active_input_method_client(bridge):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)['epoch']
        offer = dict(type='clipboard_offer', token='offer', revision=17,
                     foreground_pid=42, rect=[10, 20, 40, 20])
        assert server.send(offer)
        assert receive(stream) == {**offer, 'epoch': epoch}
        acknowledgement = dict(type='clipboard_offer_state', epoch=epoch, token='offer', visible=True)
        send(stream, {**acknowledgement, 'epoch': 'stale', 'token': 'wrong'})
        send(stream, acknowledgement)
        wait_until(lambda: acknowledgement in messages)
        assert not server._state and not any(m.get('token') == 'wrong' for m in messages)
        assert server.send(dict(type='clipboard_dismiss', token='offer'))
        assert receive(stream)['type'] == 'clipboard_dismiss'
    finally:
        stream.close(); sock.close()


@pytest.mark.mac_only
def test_unavailable_context_does_not_reuse_previous_field(bridge):
    server, _ = bridge
    sock, stream = connect(server)
    epoch = receive(stream)["epoch"]
    result = []
    worker = threading.Thread(target=lambda: result.append(server.asr_context(timeout=1)))
    worker.start()
    ping = receive(stream)
    send(stream, {"type": "state", "epoch": epoch, "request_id": ping["request_id"], "client_id": "b", "ready": True, "context_complete": False, "original": "partial"})
    worker.join(2)
    assert result[0]["status"] == "unavailable"
    assert "text" not in result[0]
    stream.close(); sock.close()


@pytest.mark.mac_only
def test_focus_requeries_newly_activated_client_without_waiting_for_old_ping(bridge):
    server, _ = bridge
    sock, stream = connect(server)
    result = []
    worker = threading.Thread(target=lambda: result.append(server.focus_state(timeout=1)))
    try:
        epoch = receive(stream)["epoch"]
        worker.start()
        first = receive(stream)
        # A controller still preparing drops this ping, then publishes its activation.
        active = dict(type="state", epoch=epoch, ready=True, lifecycle_event="activate",
                      application="test.editor", client_id="new", utterance_id="", selection=[4, 0])
        send(stream, active)
        retry = receive(stream)
        assert retry["type"] == "ping" and retry["capture_context"] is False
        assert retry["request_id"] != first["request_id"]
        send(stream, {**active, "request_id": first["request_id"], "selection": [5, 0]})
        send(stream, {**active, "request_id": retry["request_id"], "selection": [6, 0]})
        worker.join(1)
        assert result[0]["client_id"] == "new" and result[0]["selection"] == [6, 0]
    finally:
        stream.close()
        sock.close()
        worker.join(1)


@pytest.mark.mac_only
def test_gesture_focus_uses_correlated_live_imk_reply_without_requiring_document_text(bridge):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)["epoch"]
        result = []
        worker = threading.Thread(target=lambda: result.append(server.gesture_target(timeout=1)))
        worker.start()
        ping = receive(stream)
        assert ping["capture_context"] is False
        reply = dict(type="state", epoch=epoch, request_id=ping["request_id"],
                     ready=True, client_id="wechat-editor", utterance_id="sentence",
                     selection=[3, 0],
                     application="com.tencent.xinWeChat", context_complete=False,
                     original="private text", phase="dictated")
        send(stream, {**reply, "epoch": "stale", "client_id": "wrong"})
        send(stream, reply)
        worker.join(2)
        assert result == [(epoch, "wechat-editor", "sentence", "com.tencent.xinWeChat")]
        assert not any(m.get("request_id") == ping["request_id"] for m in messages)
        # Last ready state cannot authorize a later gesture with no live reply.
        assert server.gesture_target(timeout=.02) is None
        receive(stream)
    finally:
        stream.close(); sock.close()


@pytest.mark.parametrize("change", [{"ready": False}, {"client_id": ""}, {"selection": [-1, -1]},
                                  {"selection": None}, {"selection": [True, 0]},
                                  {"selection": [2**63 - 1, 0]}])
@pytest.mark.mac_only
def test_gesture_focus_rejects_inactive_or_unbound_client(bridge, change):
    server, _ = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)["epoch"]
        result = []
        worker = threading.Thread(target=lambda: result.append(server.gesture_target(timeout=1)))
        worker.start()
        ping = receive(stream)
        send(stream, dict(type="state", epoch=epoch, request_id=ping["request_id"],
                          **{**dict(ready=True, client_id="editor", utterance_id="sentence", selection=[0, 0]), **change}))
        worker.join(2)
        assert result == [None]
    finally:
        stream.close(); sock.close()


@pytest.mark.mac_only
def test_gesture_focus_accepts_idle_client_before_first_sentence(bridge):
    server, _ = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)['epoch']
        result = []
        worker = threading.Thread(target=lambda: result.append(server.gesture_target(timeout=1)))
        worker.start()
        ping = receive(stream)
        send(stream, dict(type='state', epoch=epoch, request_id=ping['request_id'],
                          ready=True, client_id='idle-editor', utterance_id='', selection=[0, 0],
                          application='com.tencent.xinWeChat', phase='idle'))
        worker.join(2)
        assert result == [(epoch, 'idle-editor', '', 'com.tencent.xinWeChat')]
    finally:
        stream.close(); sock.close()


@pytest.mark.mac_only
def test_activation_recovery_reply_reaches_host_and_does_not_replace_context(bridge):
    server, messages = bridge
    sock, stream = connect(server)
    epoch = receive(stream)['epoch']
    send(stream, dict(type='activation_recovery', epoch='old', request_id='old', result='refreshed'))
    send(stream, dict(type='activation_recovery', epoch=epoch, request_id='tap', result='not_selected'))
    wait_until(lambda: any(m.get('request_id') == 'tap' for m in messages))
    assert not any(m.get('request_id') == 'old' for m in messages)
    assert not server._state
    stream.close(); sock.close()


@pytest.mark.mac_only
@pytest.mark.parametrize('prefix', ['stroke:', 'start:', 'resume:'])
def test_ui_ready_probe_reaches_host_but_asr_context_stays_private(bridge, prefix):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)["epoch"]
        server.send(dict(type="ping", request_id=prefix + "session-one"))
        ping = receive(stream)
        response = dict(type="state", epoch=epoch, client_id="editor", ready=True,
                        phase="idle", application="test.editor", request_id=ping["request_id"])
        send(stream, {**response, "epoch": "old", "request_id": prefix + "old"})
        send(stream, {**response, "request_id": "asr-context-request"})
        send(stream, response)
        wait_until(lambda: response in messages)
        assert not any(m.get("request_id") in {prefix + "old", "asr-context-request"} for m in messages)
    finally:
        stream.close(); sock.close()


@pytest.mark.mac_only
@pytest.mark.parametrize("kind", ["stroke_ready", "stroke_result", "stroke_cleared", "stroke_ink_cleared",
                                  "stroke_correction_state", "stroke_correction_requested", "stroke_correction_key"])
def test_stroke_replies_cross_transport_with_request_and_epoch_intact(bridge, kind):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)["epoch"]
        response = dict(type=kind, epoch=epoch, client_id="editor", stroke_session="session-one",
                        success=True, request_id="commit-one")
        send(stream, {**response, "epoch": "old", "stroke_session": "old-session"})
        send(stream, response)
        wait_until(lambda: response in messages)
        assert not any(m.get("stroke_session") == "old-session" for m in messages)
        assert not server._state  # Lifecycle/commit replies cannot become voice context.
    finally:
        stream.close(); sock.close()


@pytest.mark.mac_only
@pytest.mark.parametrize("kind", ["input_suspended", "input_resumed"])
def test_suspension_replies_reach_ui_with_request_id_without_replacing_context(bridge, kind):
    server, messages = bridge
    sock, stream = connect(server)
    try:
        epoch = receive(stream)["epoch"]
        response = dict(type=kind, epoch=epoch, client_id="editor", utterance_id="sentence",
                        token="suspension-one", success=True, request_id="transition-one")
        if kind == "input_resumed":
            response["state"] = dict(type="state", client_id="editor", utterance_id="sentence",
                                     phase="dictated", ready=True)
        send(stream, {**response, "epoch": "old", "token": "old-suspension"})
        send(stream, response)
        wait_until(lambda: response in messages)
        assert not any(m.get("token") == "old-suspension" for m in messages)
        assert not server._state  # Even a nested resume snapshot belongs to the UI handshake.
    finally:
        stream.close(); sock.close()
