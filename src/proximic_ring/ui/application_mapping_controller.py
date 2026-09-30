"""Explicit applications with discovered and user-recorded shortcut bindings."""
from __future__ import annotations

import json
import hashlib
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt

from ..app_gestures import AppBinding, normalize_shortcut
from ..gesture_settings import GESTURE_LABELS, RING_RESERVED_GESTURES
from ..native_access import NativeAccessChannel
from ..application_catalog import installed_applications
from ..gesture_scenes import (PRESENTATION, SCENE_ANCHORS, PROFILES, scene_actions,
                             presentation_profile, installed_presentation_profile)

VOICE_LOCKED = frozenset({"tap", "swipe-left", "swipe-right"})
SETTINGS_KEY = "gestures/applicationMenusV1"


class ApplicationMappingController(QObject):
    changed = Signal()
    discoveryChanged = Signal()
    applicationBindingsCleared = Signal(str)
    _result = Signal(str, int, str, object, str)
    _sceneObserved = Signal(str, str)

    def __init__(self, service, *, channel=None):
        super().__init__(service)
        self.service = service
        self.channel = channel or NativeAccessChannel()  # Menu reads never block the key-posting channel.
        self._apps = {}
        self._candidates = []
        self._menus = []
        self._bundle = ""
        self._scene = "regular"
        self._scene_hints = {}
        self._message = "添加应用后，读取它暴露的菜单快捷键"
        self._busy = False
        self._menu_state = "idle"
        self._menu_read_at = ""
        self._list_busy = False
        self._list_message = ""
        self._generation = {"apps": 0, "menu": 0}
        self._closed = False
        self._request_lock = threading.Lock()
        self._result.connect(self._apply_result, Qt.QueuedConnection)
        self._sceneObserved.connect(self._set_scene_hint, Qt.QueuedConnection)
        try:
            raw = json.loads(str(service.owner._settings.value(SETTINGS_KEY, "{}")))
            if not isinstance(raw, dict):
                raise ValueError()
            for bundle, data in raw.items():
                if not isinstance(bundle, str) or not bundle or not isinstance(data["label"], str):
                    raise ValueError()
                bindings = {}
                for gesture, item in data["bindings"].items():
                    if gesture not in GESTURE_LABELS or gesture in VOICE_LOCKED | RING_RESERVED_GESTURES:
                        raise ValueError()
                    bindings[gesture] = {key: str(item[key]) for key in ("id", "label", "path", "shortcut")}
                    bindings[gesture]["shortcut"] = normalize_shortcut(item["shortcut"])
                removed = data.get("removed") is True
                self._apps[bundle] = dict(label=data["label"], path=str(data.get("path", "")),
                                         bindings={} if removed else bindings)
                profile = presentation_profile(bundle) or data.get("presentationProfile", "")
                if profile not in PROFILES:
                    profile = installed_presentation_profile(bundle, str(data.get("path", "")))
                if profile:
                    self._apps[bundle]["presentationProfile"] = profile
                scenes = {}
                for scene, items in data.get("scenes", {}).items():
                    if not profile or scene != PRESENTATION or not isinstance(items, dict):
                        raise ValueError()
                    scenes[scene] = {}
                    for gesture, item in items.items():
                        if gesture not in GESTURE_LABELS or gesture in SCENE_ANCHORS:
                            raise ValueError()
                        scenes[scene][gesture] = {key: str(item[key]) for key in ("id", "label", "path", "shortcut")}
                        scenes[scene][gesture]["shortcut"] = normalize_shortcut(item["shortcut"])
                if scenes and not removed:
                    self._apps[bundle]["scenes"] = scenes
                if removed:
                    self._apps[bundle]["removed"] = True
                if profile == "wps":
                    # Repair only our obsolete presets, never a recorded or
                    # discovered user shortcut. Persist on the next normal save.
                    for item in bindings.values():
                        old = {"wps:start-current": "Shift+F5", "wps:start-first": "F5"}
                        if item["id"] in old and item["shortcut"] == old[item["id"]]:
                            item["shortcut"] = "Cmd+Return" if item["id"].endswith("start-current") else "Cmd+Shift+Return"
        except (ValueError, TypeError, KeyError):
            self._apps = {}
            self._message = "应用配置无法读取；原记录已保留，请重新添加应用"

    @Property("QVariantList", notify=changed)
    def apps(self):
        return [dict(value=bundle, label=app["label"], path=app.get("path", ""))
                for bundle, app in self._apps.items() if not app.get("removed")]

    @Property("QVariantMap", notify=changed)
    def bindings(self):
        return {bundle: {g: dict(item) for g, item in self._bindings_for(bundle, self._scene).items()}
                for bundle, app in self._apps.items() if not app.get("removed")}

    @Property("QVariantMap", notify=changed)
    def regularBindings(self):
        return {bundle: {g: dict(item) for g, item in app["bindings"].items()}
                for bundle, app in self._apps.items() if not app.get("removed")}

    def _bindings_for(self, bundle, scene="regular"):
        app = self._apps.get(bundle, {})
        if app.get("removed"):
            return {}
        return app.get("bindings", {}) if scene == "regular" else app.get("scenes", {}).get(scene, {})

    @Property(str, notify=changed)
    def selectedScene(self):
        return self._scene

    @Property(bool, notify=changed)
    def supportsPresentation(self):
        return bool(self._profile_for(self._bundle)) and self._is_added(self._bundle)

    def _profile_for(self, bundle):
        return presentation_profile(bundle) or self._apps.get(bundle, {}).get("presentationProfile", "")

    @Property(str, notify=discoveryChanged)
    def sceneHint(self):
        return self._scene_hints.get(self._bundle, "保存后进入目标应用放映；食指捏合可查看当次识别到的手势用途。")

    def observe_scene(self, target):
        bundle = target.bundle if target is not None else self._bundle
        if target is None or not self._is_added(bundle):
            message = "前台不是已配置的演示应用，或暂时无法读取窗口"
        elif target.blocked:
            message = "当前为菜单或对话框，保留默认用途"
        elif not target.scene:
            message = "未识别到放映窗口，保留默认用途"
        elif target.input_context == "text":
            message = "正在输入文字，保留语音用途"
        elif target.input_context != "nontext":
            message = "焦点状态待确认，保留默认用途"
        else:
            message = "已识别到放映，可使用放映配置"
        self._sceneObserved.emit(bundle, "上次检测 " + datetime.now().strftime("%H:%M:%S") + " · " + message)

    @Slot(str, str)
    def _set_scene_hint(self, bundle, message):
        if not self._closed:
            self._scene_hints[bundle] = message
            self.discoveryChanged.emit()

    @Slot(str)
    def selectScene(self, scene):
        if scene not in {"regular", PRESENTATION} or (scene == PRESENTATION and not self.supportsPresentation):
            return
        if scene != self._scene:
            self._scene = scene
            # Editor selection never changes runtime scene, settings or epochs.
            self.changed.emit()
            self.discoveryChanged.emit()

    @Slot(str, result=int)
    def bindingCount(self, bundle):
        app = self._apps.get(bundle, {})
        return len(app.get("bindings", {})) + sum(len(items) for items in app.get("scenes", {}).values())

    @Property("QVariantList", notify=discoveryChanged)
    def candidates(self):
        return self._candidates

    @Property("QVariantList", notify=discoveryChanged)
    def actions(self):
        app = self._apps.get(self._bundle, {})
        groups = [app.get("bindings", {})] + list(app.get("scenes", {}).values())
        custom = {item["id"]: {**item, "available": None, "custom": True}
                  for group in groups for item in group.values()
                  if item["id"].startswith("custom:")}
        return scene_actions(self._bundle, self._scene, profile=self._profile_for(self._bundle)) + self._menus + list(custom.values())

    @Property(str, notify=discoveryChanged)
    def selectedApp(self):
        return self._bundle

    @Property(bool, notify=discoveryChanged)
    def busy(self):
        return self._busy

    @Property(bool, notify=discoveryChanged)
    def listBusy(self):
        return self._list_busy

    @Property(str, notify=discoveryChanged)
    def listMessage(self):
        return self._list_message

    @Property(str, notify=discoveryChanged)
    def message(self):
        return self._message

    @Property(str, notify=discoveryChanged)
    def menuState(self):
        return self._menu_state

    @Property(str, notify=discoveryChanged)
    def menuReadAt(self):
        return self._menu_read_at

    def _is_added(self, bundle):
        return bundle in self._apps and not self._apps[bundle].get("removed")

    def _request(self, kind, bundle=""):
        self._generation[kind] += 1
        generation = self._generation[kind]
        def work():
            try:
                with self._request_lock:
                    # Rapid selection changes skip superseded reads still waiting
                    # behind the in-flight native call, not just their replies.
                    if self._closed or generation != self._generation[kind]:
                        return
                    result = (self.channel.call("application_candidates") if kind == "apps"
                              else self.channel.call("application_menu", bundle=bundle))
                if kind == "apps" and not self._closed:
                    result = installed_applications(result)
                error = ""
            except TimeoutError:
                result = None
                error = "读取应用列表超时，请稍后刷新" if kind == "apps" else "读取菜单超时，请稍后刷新"
            except Exception as exc:
                result, error = None, str(exc)
            if not self._closed:
                self._result.emit(kind, generation, bundle, result, error)
        threading.Thread(target=work, name="ApplicationMenuDiscovery", daemon=True).start()

    @Slot()
    def refreshApplications(self):
        if sys.platform != "darwin" or self._closed:
            return
        self._list_busy = True
        self._list_message = ""
        self.discoveryChanged.emit()
        self._request("apps")

    @Slot(str, int, str, object, str)
    def _apply_result(self, kind, generation, bundle, result, error):
        if self._closed or generation != self._generation[kind]:
            return
        if kind == "apps":
            self._list_busy = False
            self._candidates = result.get("candidates", []) if isinstance(result, dict) else result or []
            self._list_message = error or ("部分位置暂时无法检索，已显示可找到的应用" if isinstance(result, dict) and result.get("partial") else "")
            for app in self._candidates:
                if app["value"] in self._apps:
                    saved = self._apps[app["value"]]
                    profile = presentation_profile(app["value"]) or app.get("presentationProfile") or saved.get("presentationProfile", "")
                    self._apps[app["value"]] = {**saved, "label": app["label"], "path": app.get("path", ""),
                                              "presentationProfile": profile if profile in PROFILES else ""}
            self.changed.emit()
        elif bundle == self._bundle and self._is_added(bundle):
            result = result or {}
            self._busy = False
            self._menus = result.get("actions", []) if result and not error else []
            self._menu_state = "error" if error else "partial" if result.get("partial") else "ready"
            self._menu_read_at = "" if error else datetime.now().strftime("%H:%M:%S")
            self._message = error or ("已读取部分菜单，可展开目标应用菜单后刷新" if result.get("partial")
                else f"已读取 {len(self._menus)} 个菜单快捷键" if self._menus
                else "应用尚未暴露可识别的菜单快捷键。可展开它的菜单后刷新")
            if not error and result.get("unresolved"):
                self._message += f"；另有 {result['unresolved']} 项按键暂无法解析"
        self.discoveryChanged.emit()

    @Slot(str, result=bool)
    def addApplication(self, bundle):
        candidate = next((app for app in self._candidates if app["value"] == bundle), None)
        if not candidate:
            return False
        if not self._is_added(bundle):
            # A re-added application starts empty and goes to the end of the icon strip.
            self._apps.pop(bundle, None)
            profile = presentation_profile(bundle) or candidate.get("presentationProfile", "")
            self._apps = {**self._apps, bundle: dict(label=candidate["label"], path=candidate.get("path", ""), bindings={},
                                                   presentationProfile=profile if profile in PROFILES else "")}
            self._save()
        self.selectApplication(bundle)
        return True

    @Slot(str)
    def selectApplication(self, bundle):
        self._generation["menu"] += 1
        selected = bundle if self._is_added(bundle) else ""
        if selected != self._bundle:
            self._scene = "regular"
        self._bundle = selected
        self._menus = []
        self._busy = bool(self._bundle)
        self._menu_state = "loading" if self._busy else "idle"
        self._menu_read_at = ""
        self._message = "正在读取应用菜单…" if self._busy else "先添加一个应用"
        self.changed.emit()
        self.discoveryChanged.emit()
        if self._busy:
            self._request("menu", self._bundle)

    @Slot()
    def refreshMenu(self):
        self.selectApplication(self._bundle)

    @Slot(str, result=bool)
    def clearApplicationBindings(self, bundle):
        if not self._is_added(bundle):
            return False
        if self.bindingCount(bundle):
            self._apps = {**self._apps, bundle: {**self._apps[bundle], "bindings": {}, "scenes": {}}}
            self._save()
        self.applicationBindingsCleared.emit(bundle)
        return True

    @Slot(str, result=bool)
    def removeApplication(self, bundle):
        if not self._is_added(bundle):
            return False
        # Keep an empty ownership marker so removing a new configuration never
        # reactivates this bundle's older, explicitly saved shortcut profile.
        self._apps = {**self._apps, bundle: {**self._apps[bundle], "bindings": {}, "scenes": {}, "removed": True}}
        if self._bundle == bundle:
            self.selectApplication("")  # Invalidates any in-flight menu reply.
        self._save()
        self.applicationBindingsCleared.emit(bundle)
        return True

    @Slot(str, result=bool)
    def canBind(self, gesture):
        if self._scene == PRESENTATION and self.supportsPresentation:
            return gesture in GESTURE_LABELS and gesture not in SCENE_ANCHORS
        return self._can_bind_regular(gesture)

    def _can_bind_regular(self, gesture):
        return (gesture in GESTURE_LABELS and gesture not in VOICE_LOCKED | RING_RESERVED_GESTURES
                and gesture not in self.service.owner._gesture_bindings.confirm
                and gesture != self.service.inputSourceGesture)

    @Slot(str, str, str, result=bool)
    def setBinding(self, bundle, gesture, action_id):
        if not self._is_added(bundle) or not self.canBind(gesture):
            self._message = "此手势保留给语音或系统交互，不能修改应用绑定"
            self.discoveryChanged.emit()
            return False
        action = next((item for item in self.actions if item["id"] == action_id), None)
        if bundle != self._bundle or (action_id and (action is None or (self._busy and not (action.get("custom") or action.get("preset"))))):
            self._message = "快捷键列表已变化，请刷新并重新选择"
            self.discoveryChanged.emit()
            return False
        return self._store_binding(bundle, gesture, action if action_id else None)

    @Slot(str, str, result="QVariantMap")
    def customAction(self, label, shortcut):
        """Validate a draft without changing any live bindings or settings."""
        label = label.strip()
        try:
            if not label or len(label) > 120:
                raise ValueError("请填写动作名称（最多 120 字）")
            shortcut = normalize_shortcut(shortcut)
        except ValueError as exc:
            self._message = str(exc)
            self.discoveryChanged.emit()
            return {}
        identity = json.dumps([label, shortcut], ensure_ascii=False)
        return dict(id="custom:" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                    label=label, path="自定义快捷键", shortcut=shortcut, custom=True, available=None)

    @Slot(str, str, str, str, result=bool)
    def setCustomBinding(self, bundle, gesture, label, shortcut):
        if not self._is_added(bundle) or bundle != self._bundle or not self.canBind(gesture):
            self._message = "应用或手势已变化，请重新选择"
            self.discoveryChanged.emit()
            return False
        action = self.customAction(label, shortcut)
        return self._store_binding(bundle, gesture, action) if action else False

    def _store_binding(self, bundle, gesture, action):
        bindings = dict(self._bindings_for(bundle, self._scene))
        if action:
            bindings[gesture] = {key: action[key] for key in ("id", "label", "path", "shortcut")}
        else:
            bindings.pop(gesture, None)
        app = self._apps[bundle]
        updated = {**app, "bindings": bindings} if self._scene == "regular" else {
            **app, "scenes": {**app.get("scenes", {}), self._scene: bindings}}
        self._apps = {**self._apps, bundle: updated}
        self._save()
        self._message = ("已保存，仅在此应用前台放映且未输入文字时生效" if self._scene == PRESENTATION
                         else "已保存，仅在该应用位于前台时生效")
        self.discoveryChanged.emit()
        return True

    def _save(self):
        self.service.owner._settings.setValue(SETTINGS_KEY, json.dumps(self._apps, ensure_ascii=False))
        self.service._generation += 1
        self.service.cancel_pending("应用映射已变化，请重新触发手势")
        self.changed.emit()
        self.service.changed.emit()
        self.discoveryChanged.emit()  # Saved custom actions also appear in the picker.

    def owns(self, bundle):
        return bundle in self._apps

    def uses(self, gesture):
        return self._can_bind_regular(gesture) and any(gesture in app["bindings"] for app in self._apps.values())

    def for_target(self, bundle):
        return {"menu:" + gesture: AppBinding(gesture, item["shortcut"])
                for gesture, item in self._apps.get(bundle, {}).get("bindings", {}).items()
                if self._can_bind_regular(gesture)}

    def scene_bundles(self):
        return [bundle for bundle in self._apps if self._profile_for(bundle) and self._bindings_for(bundle, PRESENTATION)]

    def uses_scene(self, gesture):
        return gesture not in SCENE_ANCHORS and any(gesture in self._bindings_for(bundle, PRESENTATION)
                                                   for bundle in self.scene_bundles())

    def for_scene(self, bundle, scene):
        if not self._profile_for(bundle) or scene != PRESENTATION:
            return {}
        return {"scene:" + gesture: AppBinding(gesture, item["shortcut"])
                for gesture, item in self._bindings_for(bundle, scene).items() if gesture not in SCENE_ANCHORS}

    def close(self):
        self._closed = True
        # A read may be in flight; do not join its native channel on the GUI thread.
        threading.Thread(target=self.channel.close, daemon=True).start()
