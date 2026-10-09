"""Explicit applications with discovered and user-recorded shortcut bindings."""
from __future__ import annotations

import json
import hashlib
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, Property, Signal, Slot, Qt

from ..app_gestures import AppBinding, normalize_shortcut
from ..gesture_settings import GESTURE_LABELS, VOICE_GESTURE_GROUP, GLOBAL_ACTION_LABELS
from ..native_access import NativeAccessChannel
from ..application_catalog import installed_applications
from ..scenes.policy import primary_scene, enabled_profiles, scene_choices
from ..scenes.registry import PENDING_TEMPLATES as TEMPLATES, action_label, SCENE_LABELS
from ..scenes.defaults import (SCENE_DEFAULTS_VERSION, pending_scene_defaults as scene_defaults,
    chat_actions, default_mappings, default_profile, DEFAULTS_VERSION, AUTO_ADD_PROFILES)
from ..scenes.menus import start_action
from ..scenes.migrations import (LEGACY_MUSIC_VOLUME, migrate_enabled_scenes,
    migrate_music_volume_defaults, migrate_presentation_presets, retire_browser_scene_navigation)
from ..scenes.adapters.browser import NAVIGATION, shared_navigation
from ..scenes.adapters.presentation import PROFILES
from ..scenes.capabilities import (presentation_profile, installed_presentation_profile,
    application_scene_profiles, installed_scene_profiles, BROWSERS, MUSIC_APPS)
from ..scenes.models import PRESENTATION, MUSIC, VIDEO
from ..scenes.resolver import catalog_actions
from ..scenes import configuration
from ..scenes.configuration import SETTINGS_KEY


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
        self._defaults_scan_started = False
        self._defaults_dirty = False
        self._config_valid = True
        self._request_lock = threading.Lock()
        self._result.connect(self._apply_result, Qt.QueuedConnection)
        self._sceneObserved.connect(self._set_scene_hint, Qt.QueuedConnection)
        try:
            original_settings = str(service.owner._settings.value(SETTINGS_KEY, "{}"))
            raw = json.loads(original_settings)
            if not isinstance(raw, dict):
                raise ValueError()
            for bundle, data in raw.items():
                if not isinstance(bundle, str) or not bundle or not isinstance(data["label"], str):
                    raise ValueError()
                bindings = {}
                for gesture, item in data["bindings"].items():
                    if gesture not in GESTURE_LABELS:
                        raise ValueError()
                    bindings[gesture] = {key: str(item[key]) for key in ("id", "label", "path", "shortcut")}
                    bindings[gesture]["shortcut"] = normalize_shortcut(item["shortcut"])
                removed = data.get("removed") is True
                self._apps[bundle] = dict(label=data["label"], path=str(data.get("path", "")),
                                         bindings={} if removed else bindings,
                                         defaultsVersion=int(data.get("defaultsVersion", 0)),
                                         pendingStart=data.get("pendingStart") is True)
                initialized = data.get("initializedScenes", [])
                pending = data.get("pendingDefaults", {})
                if (not isinstance(initialized, list) or any(scene not in TEMPLATES for scene in initialized)
                        or not isinstance(pending, dict)):
                    raise ValueError()
                for scene, items in pending.items():
                    if scene not in TEMPLATES or not isinstance(items, dict):
                        raise ValueError()
                    if any(TEMPLATES[scene].get(gesture) != action and not (
                            scene == MUSIC and int(data.get("sceneDefaultsVersion", 0)) < 2
                            and LEGACY_MUSIC_VOLUME.get(gesture) == action)
                           for gesture, action in items.items()):
                        raise ValueError()
                self._apps[bundle].update(
                    initializedScenes=list(initialized), pendingDefaults={} if removed else pending,
                    sceneDefaultsVersion=int(data.get("sceneDefaultsVersion", 0)),
                    defaultsCleared=data.get("defaultsCleared") is True or (
                        "defaultsCleared" not in data and data.get("defaultsVersion", 0) >= 2
                        and not bindings and not any(data.get("scenes", {}).values())
                        and not data.get("pendingStart")))
                profile = presentation_profile(bundle) or data.get("presentationProfile", "")
                if profile not in PROFILES:
                    profile = installed_presentation_profile(bundle, str(data.get("path", "")))
                if profile:
                    self._apps[bundle]["presentationProfile"] = profile
                saved_profiles = data.get("sceneProfiles", {})
                if not isinstance(saved_profiles, dict):
                    saved_profiles = {}
                capabilities = {key: value for key, value in saved_profiles.items()
                                if key in SCENE_LABELS and isinstance(value, str)}
                capabilities.update(installed_scene_profiles(bundle, str(data.get("path", ""))))
                if profile:
                    capabilities[PRESENTATION] = profile
                self._apps[bundle]["sceneProfiles"] = capabilities
                self._apps[bundle]["primaryScene"] = primary_scene(bundle, capabilities, data.get("primaryScene", ""))
                scenes = {}
                for scene, items in data.get("scenes", {}).items():
                    if scene not in SCENE_LABELS or not isinstance(items, dict):
                        raise ValueError()
                    # Retain bindings when a known application is uninstalled;
                    # native capture still verifies capability independently.
                    capabilities.setdefault(scene, "generic")
                    scenes[scene] = {}
                    for gesture, item in items.items():
                        if gesture not in GESTURE_LABELS:
                            raise ValueError()
                        scenes[scene][gesture] = {key: str(item[key]) for key in ("id", "label", "path", "shortcut")}
                        scenes[scene][gesture]["shortcut"] = normalize_shortcut(item["shortcut"])
                if scenes and not removed:
                    self._apps[bundle]["scenes"] = scenes
                app = self._apps[bundle]
                app["enabledScenes"] = migrate_enabled_scenes(bundle, capabilities, data)
                if "enabledScenes" not in data:
                    self._defaults_dirty = True
                    if bundle.casefold() in MUSIC_APPS and MUSIC in capabilities:
                        app["primaryScene"] = MUSIC

                # Preserve an inert one-time backup before retiring site overrides
                # and per-scene circles. Nothing reads this key for dispatch.
                if data.get("websites") or (bundle.casefold() in BROWSERS and any(
                        gesture in items for items in scenes.values() for gesture in NAVIGATION)):
                    backup = SETTINGS_KEY + "/beforeAutomaticBrowserScenes"
                    if service.owner._settings.value(backup, None) is None:
                        service.owner._settings.setValue(backup, original_settings)
                    self._defaults_dirty = True
                if migrate_music_volume_defaults(bundle, app):
                    self._defaults_dirty = True
                retire_browser_scene_navigation(bundle, app, scenes)
                if removed:
                    self._apps[bundle]["removed"] = True
                if migrate_presentation_presets(profile, bindings):
                    self._defaults_dirty = True
        except (ValueError, TypeError, KeyError):
            self._apps = {}
            self._config_valid = False
            self._message = "应用配置无法读取；原记录已保留，请重新添加应用"
        if self._config_valid:
            self._upgrade_defaults()
            self._upgrade_scene_defaults()

    def _defaults_for(self, bundle, app, menus=()):
        return configuration.defaults_for(bundle, app, menus)

    def _upgrade_defaults(self):
        if configuration.upgrade_defaults(self._apps, self._defaults_for):
            self._defaults_dirty = True

    def _upgrade_scene_defaults(self):
        if configuration.initialize_scene_defaults(self._apps, self._profiles_for):
            self._defaults_dirty = True

    def _resolve_scene_defaults(self, bundle):
        return configuration.resolve_pending_defaults(bundle, self._apps[bundle], self._profiles_for(bundle), self._menus)

    def _refresh_pristine_scene_defaults(self, bundle):
        return configuration.refresh_pristine_defaults(bundle, self._apps[bundle], self._profiles_for(bundle), self._menus)

    def _new_application(self, candidate):
        return configuration.new_application(candidate)

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
        return {bundle: {g: dict(item) for g, item in self._bindings_for(bundle).items()}
                for bundle, app in self._apps.items() if not app.get("removed")}

    def _bindings_for(self, bundle, scene="regular"):
        app = self._apps.get(bundle, {})
        if not self._is_added(bundle):
            return {}
        bindings = app.get("bindings", {}) if scene == "regular" else app.get("scenes", {}).get(scene, {})
        return {**bindings, **shared_navigation(bundle, app.get("bindings", {}))}

    @Property(str, notify=changed)
    def selectedScopeLabel(self):
        return self.selectedSceneLabel

    @Slot(str, result=bool)
    def isSharedNavigation(self, gesture):
        return self.shared_navigation_for(self._bundle, gesture)

    def shared_navigation_for(self, bundle, gesture):
        return self._is_added(bundle) and bundle.casefold() in BROWSERS and gesture in NAVIGATION

    def scene_bindings_for_target(self, target, scene):
        return self._bindings_for(target.bundle, scene)

    @Property("QVariantMap", notify=changed)
    def globalOccupancy(self):
        bindings = self.service.owner._global_gesture_bindings
        return {gesture: GLOBAL_ACTION_LABELS[action] for action, gesture in bindings.as_dict().items() if gesture}

    @Property("QVariantList", notify=changed)
    def voiceGestures(self):
        return [g for g in GESTURE_LABELS if g in self.voice_group()]

    def voice_group(self):
        # Custom voice aliases must obey the same all-or-nothing policy.
        return VOICE_GESTURE_GROUP | {g for pair in self.service.owner._gesture_bindings.as_dict().values()
                                      for g in pair if g}

    def voice_overridden(self, bundle):
        return any(g in self.voice_group() and self._can_bind_regular(g)
                   for g in self._bindings_for(bundle))

    def voice_disabled_bundles(self):
        return [bundle for bundle in self._apps if self.voice_overridden(bundle)]

    @Property(str, notify=changed)
    def voiceOverrideNotice(self):
        if not self._is_added(self._bundle):
            return ""
        if self._scene != "regular":
            return f"{self.selectedSceneLabel}模式下停用手势语音输入，仅使用本场景的手势配置。退出场景后恢复应用常规配置。"
        overridden = [GESTURE_LABELS[g] for g in self._bindings_for(self._bundle)
                      if g in self.voice_group() and self._can_bind_regular(g)]
        return ("已覆盖语音组手势：" + "、".join(overridden)
                + "。该应用常规状态下无法使用手势语音输入；未绑定的语音组手势不执行操作。移除全部覆盖后自动恢复，其他应用不受影响。") if overridden else ""

    @Property(str, notify=changed)
    def globalConflictNotice(self):
        conflicts = [GESTURE_LABELS[g] for g in self._bindings_for(self._bundle, self._scene)
                     if g in self.globalOccupancy]
        return ("以下绑定因全局占用已暂停：" + "、".join(conflicts)
                + "。原绑定保留，释放全局占用后恢复。") if conflicts else ""

    @Property(str, notify=changed)
    def selectedScene(self):
        return self._scene

    @Property(bool, notify=changed)
    def supportsPresentation(self):
        return PRESENTATION in self._profiles_for(self._bundle) and self._is_added(self._bundle)

    def _profile_for(self, bundle):
        return presentation_profile(bundle) or self._apps.get(bundle, {}).get("presentationProfile", "")

    def _all_profiles_for(self, bundle):
        profiles = dict(self._apps.get(bundle, {}).get("sceneProfiles", {}))
        profiles.update(application_scene_profiles(bundle))
        if self._profile_for(bundle):
            profiles[PRESENTATION] = self._profile_for(bundle)
        return {scene: profiles[scene] for scene in SCENE_LABELS if scene in profiles}

    def _profiles_for(self, bundle):
        app = self._apps.get(bundle, {})
        return enabled_profiles(bundle, self._all_profiles_for(bundle), app.get("primaryScene", ""), app.get("enabledScenes"))

    @Property(bool, notify=changed)
    def isBrowser(self):
        return self._bundle.casefold() in BROWSERS

    @Property(str, notify=changed)
    def primaryScene(self):
        return primary_scene(self._bundle, self._all_profiles_for(self._bundle), self._apps.get(self._bundle, {}).get("primaryScene", ""))

    @Property("QVariantList", notify=changed)
    def primarySceneOptions(self):
        return [dict(value=scene, label=SCENE_LABELS[scene]) for scene in scene_choices(self._all_profiles_for(self._bundle))]

    @Property("QVariantList", notify=changed)
    def addableScenes(self):
        if not self._is_added(self._bundle):
            return []
        enabled = self._profiles_for(self._bundle)
        return [dict(value=scene, label=SCENE_LABELS[scene]) for scene in self._all_profiles_for(self._bundle)
                if scene not in enabled]

    @Slot(str, result=bool)
    def addScene(self, scene):
        if not self._is_added(self._bundle) or scene not in self._all_profiles_for(self._bundle):
            return False
        app = self._apps[self._bundle]
        if scene not in self._profiles_for(self._bundle):
            enabled = list(self._profiles_for(self._bundle)) + [scene]
            app["enabledScenes"] = [name for name in SCENE_LABELS if name in enabled]
            # Re-enabling keeps saved keys, including deliberate empty scopes.
            if scene not in app.get("scenes", {}) and scene not in app.get("initializedScenes", []):
                profiles = {scene: self._all_profiles_for(self._bundle)[scene]}
                defaults = default_mappings(self._bundle, app["label"],
                    presentation=profiles.get(PRESENTATION, ""), scene_profiles=profiles, menus=self._menus)
                app.setdefault("scenes", {})[scene] = defaults.get(scene, {})
                _, pending = scene_defaults(self._bundle, profiles, self._menus)
                if pending.get(scene):
                    app.setdefault("pendingDefaults", {})[scene] = pending[scene]
                if scene in TEMPLATES:
                    app.setdefault("initializedScenes", []).append(scene)
                if scene == PRESENTATION and not app.get("defaultsCleared"):
                    for gesture, action in defaults.get("regular", {}).items():
                        app["bindings"].setdefault(gesture, action)
                    if "snap" not in app["bindings"]:
                        app["pendingStart"] = True
            self._save()
        self.selectScene(scene)
        return True

    @Slot(str, result=bool)
    def disableScene(self, scene):
        if scene not in self._profiles_for(self._bundle) or not self._is_added(self._bundle):
            return False
        app = self._apps[self._bundle]
        # A previously cleared scope may have no record, especially presentation
        # (not part of the media template initialization list). Remember empty.
        app.setdefault("scenes", {}).setdefault(scene, {})
        app["enabledScenes"] = [name for name in self._profiles_for(self._bundle) if name != scene]
        if self._scene == scene:
            self._scene = "regular"
        self._save()  # Retain bindings, pending actions and editor drafts for re-add.
        return True

    @Slot(str, result=bool)
    def setPrimaryScene(self, scene):
        # Compatibility for older callers: choosing a preferred scene now adds
        # it, never silently retires the other enabled scenes.
        if not self.addScene(scene):
            return False
        app = self._apps[self._bundle]
        if app.get("primaryScene") != scene:
            app["primaryScene"] = scene
            self._save()
        return True

    @Property("QVariantList", notify=changed)
    def availableScenes(self):
        scenes = [dict(value=scene, label=SCENE_LABELS[scene]) for scene in self._profiles_for(self._bundle)
                  if self._is_added(self._bundle)]
        return [dict(value="regular", label="默认配置")] + scenes

    @Property(str, notify=changed)
    def selectedSceneLabel(self):
        return SCENE_LABELS.get(self._scene, "默认配置")

    @Property(str, notify=discoveryChanged)
    def sceneHint(self):
        return self._scene_hints.get(self._bundle, "进入目标应用的对应场景后自动生效；使用全局“手势提示”可查看当前用途。")

    def observe_scene(self, target):
        bundle = target.bundle if target is not None else self._bundle
        if target is None or not self._is_added(bundle):
            message = "前台不是已配置的应用，或暂时无法读取窗口"
        elif target.blocked:
            message = "当前为菜单或对话框，暂停场景动作"
        elif not target.scene:
            message = "未识别到对应场景，使用应用常规配置"
        elif target.scene not in self._profiles_for(bundle):
            message = "当前场景未启用，使用应用常规配置"
        elif target.input_context == "text":
            message = "当前为文字输入区域，使用应用常规配置"
        elif target.input_context != "nontext":
            message = "焦点状态待确认，使用应用常规配置"
        else:
            label = SCENE_LABELS.get(target.scene, target.scene)
            message = f"已识别到{label}，可使用{label}配置"
        self._sceneObserved.emit(bundle, "上次检测 " + datetime.now().strftime("%H:%M:%S") + " · " + message)

    @Slot(str, str)
    def _set_scene_hint(self, bundle, message):
        if not self._closed:
            self._scene_hints[bundle] = message
            self.discoveryChanged.emit()

    @Slot(str)
    def selectScene(self, scene):
        if scene != "regular" and (scene not in self._profiles_for(self._bundle) or not self._is_added(self._bundle)):
            return
        if scene != self._scene:
            self._scene = scene
            # Editor selection never changes runtime scene, settings or epochs.
            self.changed.emit()
            self.discoveryChanged.emit()

    @Slot(str, result=int)
    def bindingCount(self, bundle):
        app = self._apps.get(bundle, {})
        return len(self._bindings_for(bundle)) + sum(len(items) for items in app.get("scenes", {}).values())

    @Property(bool, notify=changed)
    def hasDefaultMappings(self):
        app = self._apps.get(self._bundle, {})
        return self._is_added(self._bundle) and bool(self._defaults_for(self._bundle, app))

    @Property("QVariantMap", notify=discoveryChanged)
    def pendingBindings(self):
        return {gesture: action_label(self._scene, action) for gesture, action in
                self._apps.get(self._bundle, {}).get("pendingDefaults", {}).get(self._scene, {}).items()}

    @Property(int, notify=changed)
    def pendingDefaultCount(self):
        return sum(len(items) for scene, items in self._apps.get(self._bundle, {}).get("pendingDefaults", {}).items()
                   if scene in self._profiles_for(self._bundle))

    @Property(str, notify=discoveryChanged)
    def defaultMappingNotice(self):
        if self.supportsPresentation and self._apps.get(self._bundle, {}).get("pendingStart"):
            return "放映中已默认设置左右翻页、响指结束。响指开始放映等待读取此应用的快捷键：打开应用后刷新快捷键，或手动绑定。"
        if self.pendingBindings:
            return "场景预设已保存，等待读取此应用的快捷键：" + "、".join(self.pendingBindings.values()) + "。打开应用后刷新快捷键，或手动绑定。"
        return ""

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
        presets = catalog_actions(self._bundle, self._scene, self._profiles_for(self._bundle),
                                  presentation=self._profile_for(self._bundle))
        chat = chat_actions(self._bundle, app.get("label", ""), self._scene)
        known_ids = {item["id"] for item in presets + chat}
        presets += [{**item, "available": None, "preset": True}
                    for item in self._defaults_for(self._bundle, app).get(self._scene, {}).values()
                    if item["id"] not in known_ids]
        return presets + chat + self._menus + list(custom.values())

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
                    if kind == "apps":
                        try:
                            result = self.channel.call("application_candidates")
                        except Exception:
                            result = []  # Installed-app discovery needs no AX permission.
                    else:
                        result = self.channel.call("application_menu", bundle=bundle)
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
    def initializeInstalledApplications(self):
        if self._defaults_scan_started or self._closed or not self._config_valid or sys.platform != "darwin":
            return
        self._defaults_scan_started = True
        self.refreshApplications()

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
                                              "presentationProfile": profile if profile in PROFILES else "",
                                              "sceneProfiles": {**saved.get("sceneProfiles", {}),
                                                                **app.get("sceneProfiles", {}),
                                                                **application_scene_profiles(app["value"])}}
                elif self._config_valid and default_profile(app["value"], app["label"]) in AUTO_ADD_PROFILES:
                    # Do not select/launch apps or read their menus during startup.
                    # Tombstones are already in _apps and are never re-added.
                    self._apps[app["value"]] = self._new_application(app)
                    self._defaults_dirty = True
            if self._config_valid:
                self._upgrade_scene_defaults()
            if self._defaults_dirty:
                self._save()
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
            defaults_changed = not error and self._resolve_scene_defaults(bundle)
            if not error and not result.get("partial"):
                defaults_changed = self._refresh_pristine_scene_defaults(bundle) or defaults_changed
            if defaults_changed:
                self._save()
            if not error and self._apps[bundle].get("pendingStart"):
                action = start_action(self._menus)
                if action and "snap" not in self._apps[bundle]["bindings"]:
                    self._apps[bundle]["bindings"]["snap"] = action
                    self._apps[bundle]["pendingStart"] = False
                    self._save()
        self.discoveryChanged.emit()

    @Slot(str, result=bool)
    def addApplication(self, bundle):
        candidate = next((app for app in self._candidates if app["value"] == bundle), None)
        if not candidate:
            return False
        if not self._is_added(bundle):
            self._apps = {**self._apps, bundle: self._new_application(candidate)}
            self._save()
        self.selectApplication(bundle)
        return True

    def addDiscoveredApplication(self, candidate):
        """Called only after opt-in; never resurrect removals or replace mappings."""
        if self._closed or not self._config_valid or self.owns(candidate["value"]):
            return False
        self._candidates = [item for item in self._candidates if item["value"] != candidate["value"]] + [dict(candidate)]
        return self.addApplication(candidate["value"])

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
        if (self.bindingCount(bundle) or self._apps[bundle].get("pendingStart")
                or self._apps[bundle].get("pendingDefaults") or not self._apps[bundle].get("defaultsCleared")):
            self._apps = {**self._apps, bundle: {**self._apps[bundle], "bindings": {}, "scenes": {},
                "pendingStart": False, "pendingDefaults": {}, "defaultsCleared": True, "defaultsVersion": DEFAULTS_VERSION}}
            self._save()
        self.applicationBindingsCleared.emit(bundle)
        return True

    @Slot(str, result=bool)
    def restoreDefaultMappings(self, bundle):
        if not self._is_added(bundle):
            return False
        app = self._apps[bundle]
        menus = self._menus if bundle == self._bundle else ()
        defaults = self._defaults_for(bundle, app, menus)
        if not defaults:
            return False
        _, pending = scene_defaults(bundle, self._profiles_for(bundle), menus)
        self._apps = {**self._apps, bundle: {**app, "bindings": defaults.get("regular", {}),
            "initializedScenes": [scene for scene in TEMPLATES if scene in self._profiles_for(bundle)],
            "pendingDefaults": pending, "sceneDefaultsVersion": SCENE_DEFAULTS_VERSION, "defaultsCleared": False,
            "scenes": {scene: items for scene, items in defaults.items() if scene != "regular"},
            "defaultsVersion": DEFAULTS_VERSION,
            "pendingStart": bool(PRESENTATION in self._profiles_for(bundle) and "snap" not in defaults.get("regular", {}))}}
        self._save()
        self.applicationBindingsCleared.emit(bundle)  # Discard this app's old drafts too.
        return True

    @Slot(str, result=bool)
    def removeApplication(self, bundle):
        if not self._is_added(bundle):
            return False
        # Keep an empty ownership marker so removing a new configuration never
        # reactivates this bundle's older, explicitly saved shortcut profile.
        self._apps = {**self._apps, bundle: {**self._apps[bundle], "bindings": {}, "scenes": {}, "pendingDefaults": {}, "removed": True}}
        if self._bundle == bundle:
            self.selectApplication("")  # Invalidates any in-flight menu reply.
        self._save()
        self.applicationBindingsCleared.emit(bundle)
        return True

    @Slot(str, result=bool)
    def canBind(self, gesture):
        return self._can_bind_regular(gesture) and not (self._scene != "regular" and self.isSharedNavigation(gesture))

    def _can_bind_regular(self, gesture):
        return gesture in GESTURE_LABELS and gesture not in self.service.owner._global_gesture_bindings.reserved

    @Slot(str, str, str, result=bool)
    def setBinding(self, bundle, gesture, action_id):
        if not self._is_added(bundle) or not self.canBind(gesture):
            self._message = "此手势已被全局功能占用，请先修改全局配置"
            self.discoveryChanged.emit()
            return False
        if not action_id and self.shared_navigation_for(bundle, gesture):
            self._message = "浏览器转圈用于切换 Chat / 标签页，在所有场景中共用。"
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
        if self._scene != "regular" and bundle.casefold() in BROWSERS:
            for shared in NAVIGATION:
                bindings.pop(shared, None)
        updated = {**app, "bindings": bindings} if self._scene == "regular" else {
            **app, "scenes": {**app.get("scenes", {}), self._scene: bindings}}
        if self._scene == "regular" and gesture == "snap":
            updated["pendingStart"] = False  # Explicit edits/deletions win over discovery.
        if self._scene in TEMPLATES:
            pending = {scene: dict(items) for scene, items in app.get("pendingDefaults", {}).items()}
            pending.get(self._scene, {}).pop(gesture, None)
            updated["pendingDefaults"] = {scene: items for scene, items in pending.items() if items}
        self._apps = {**self._apps, bundle: updated}
        self._save()
        self._message = (f"已保存，仅在此应用前台处于{self.selectedSceneLabel}且未输入文字时生效" if self._scene != "regular"
                         else "已保存，仅在该应用位于前台时生效")
        self.discoveryChanged.emit()
        return True

    def _save(self):
        self.service.owner._settings.setValue(SETTINGS_KEY, json.dumps(self._apps, ensure_ascii=False))
        self._defaults_dirty = False
        self._config_valid = True
        self.service._generation += 1
        self.service.cancel_pending("应用映射已变化，请重新触发手势")
        self.changed.emit()
        self.service.changed.emit()
        self.discoveryChanged.emit()  # Saved custom actions also appear in the picker.

    def owns(self, bundle):
        return bundle in self._apps

    def uses(self, gesture):
        return self._can_bind_regular(gesture) and any(gesture in self._bindings_for(bundle) for bundle in self._apps)

    def for_target(self, bundle):
        return {"menu:" + gesture: AppBinding(gesture, item["shortcut"])
                for gesture, item in self._bindings_for(bundle).items()
                if self._can_bind_regular(gesture)}

    def scene_bundles(self):
        return [bundle for bundle in self._apps if self._is_added(bundle) and self._profiles_for(bundle)]

    def configured_scenes(self):
        return {bundle: list(self._profiles_for(bundle))
                for bundle in self.scene_bundles()}

    def uses_scene(self, gesture):
        return self._can_bind_regular(gesture) and any(gesture in self._bindings_for(bundle, scene)
            for bundle, scenes in self.configured_scenes().items() for scene in scenes)

    def for_scene(self, bundle, scene):
        if scene not in self._profiles_for(bundle):
            return {}
        return {"scene:" + gesture: AppBinding(gesture, item["shortcut"])
                for gesture, item in self._bindings_for(bundle, scene).items() if self._can_bind_regular(gesture)}

    def close(self):
        self._closed = True
        # A read may be in flight; do not join its native channel on the GUI thread.
        threading.Thread(target=self.channel.close, daemon=True).start()
