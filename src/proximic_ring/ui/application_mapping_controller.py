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
from ..scene_defaults import (TEMPLATES, SCENE_DEFAULTS_VERSION, scene_defaults, resolve_action, action_label)
from ..application_defaults import (chat_actions, default_mappings, default_profile,
                                    DEFAULTS_VERSION, AUTO_ADD_PROFILES, start_action)
from ..scene_recognition.websites import BILIBILI, website_domain, matches_website
from ..website_defaults import video_actions, website_defaults
from ..gesture_scenes import (PRESENTATION, PROFILES, scene_actions,
                             presentation_profile, installed_presentation_profile)
from ..scene_capabilities import (SCENE_LABELS, application_scene_profiles,
                                 installed_scene_profiles, activity_actions, BROWSERS, VIDEO)

SETTINGS_KEY = "gestures/applicationMenusV1"


class ApplicationMappingController(QObject):
    changed = Signal()
    discoveryChanged = Signal()
    applicationBindingsCleared = Signal(str)
    websiteBindingsCleared = Signal(str, str)
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
        self._website = ""
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
            raw = json.loads(str(service.owner._settings.value(SETTINGS_KEY, "{}")))
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
                    if any(TEMPLATES[scene].get(gesture) != action for gesture, action in items.items()):
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
                websites = {}
                for domain, site in data.get("websites", {}).items():
                    if website_domain(domain) != domain or not isinstance(site, dict):
                        raise ValueError()
                    items = {}
                    for gesture, item in site.get("bindings", {}).items():
                        if gesture not in GESTURE_LABELS:
                            raise ValueError()
                        items[gesture] = {key: str(item[key]) for key in ("id", "label", "path", "shortcut")}
                        items[gesture]["shortcut"] = normalize_shortcut(item["shortcut"])
                    websites[domain] = dict(label=str(site.get("label", domain)), bindings=items)
                if websites and not removed:
                    self._apps[bundle]["websites"] = websites
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
            self._config_valid = False
            self._message = "应用配置无法读取；原记录已保留，请重新添加应用"
        if self._config_valid:
            self._upgrade_defaults()
            self._upgrade_scene_defaults()

    def _defaults_for(self, bundle, app, menus=()):
        return default_mappings(bundle, app.get("label", ""),
                                presentation=presentation_profile(bundle) or app.get("presentationProfile", ""),
                                scene_profiles=app.get("sceneProfiles", {}), menus=menus)

    def _upgrade_defaults(self):
        # One-time fill for existing populated records. Custom keys win, and
        # explicitly empty scopes remain empty. Future clears never refill.
        for bundle, app in self._apps.items():
            if app.get("removed") or app.get("defaultsVersion", 0) >= DEFAULTS_VERSION:
                continue
            defaults = {scene: items for scene, items in self._defaults_for(bundle, app).items()
                        if scene in {"regular", PRESENTATION}}
            if not defaults:
                continue
            if app.get("bindings") or any(app.get("scenes", {}).values()):
                for scene, items in defaults.items():
                    existing = app.get("bindings") if scene == "regular" else app.get("scenes", {}).get(scene)
                    if existing == {}:
                        continue
                    if scene == "regular":
                        target = app["bindings"]
                    else:
                        target = app.setdefault("scenes", {}).setdefault(scene, {})
                    for gesture, action in items.items():
                        target.setdefault(gesture, action)
            app["defaultsVersion"] = DEFAULTS_VERSION
            self._defaults_dirty = True

    def _upgrade_scene_defaults(self):
        # A scope is initialized once, even if its shortcuts have not been read.
        # Existing scopes (including empty ones) are owned by the user.
        for bundle, app in self._apps.items():
            if app.get("removed"):
                continue
            profiles = self._profiles_for(bundle)
            modes = [scene for scene in profiles if scene in TEMPLATES]
            if not modes:
                continue
            initialized = set(app.get("initializedScenes", []))
            resolved, pending = scene_defaults(bundle, profiles)
            for scene in modes:
                if scene in initialized:
                    continue
                if not app.get("defaultsCleared") and scene not in app.get("scenes", {}):
                    app.setdefault("scenes", {})[scene] = resolved[scene]
                    if pending.get(scene):
                        app.setdefault("pendingDefaults", {})[scene] = pending[scene]
                initialized.add(scene)
                self._defaults_dirty = True
            app["initializedScenes"] = [scene for scene in TEMPLATES if scene in initialized]
            if app.get("sceneDefaultsVersion", 0) != SCENE_DEFAULTS_VERSION:
                app["sceneDefaultsVersion"] = SCENE_DEFAULTS_VERSION
                self._defaults_dirty = True

    def _resolve_scene_defaults(self, bundle):
        app = self._apps[bundle]
        pending = {scene: dict(items) for scene, items in app.get("pendingDefaults", {}).items()}
        changed = False
        for scene, items in pending.items():
            target = app.setdefault("scenes", {}).setdefault(scene, {})
            for gesture, action in list(items.items()):
                if gesture in target:
                    del items[gesture]  # A saved user action always wins.
                    changed = True
                    continue
                record = resolve_action(bundle, scene, action, self._profiles_for(bundle).get(scene, ""), self._menus)
                if record:
                    target[gesture] = record
                    del items[gesture]
                    changed = True
        if changed:
            app["pendingDefaults"] = {scene: items for scene, items in pending.items() if items}
        return changed

    def _new_application(self, candidate):
        bundle = candidate["value"]
        capabilities = {**candidate.get("sceneProfiles", {}),
                        **installed_scene_profiles(bundle, candidate.get("path", ""))}
        profile = presentation_profile(bundle) or candidate.get("presentationProfile", "") or capabilities.get(PRESENTATION, "")
        app = dict(label=candidate["label"], path=candidate.get("path", ""),
                   sceneProfiles=capabilities, presentationProfile=profile if profile in PROFILES else "")
        defaults = self._defaults_for(bundle, app)
        _, pending = scene_defaults(bundle, capabilities)
        app.update(initializedScenes=[scene for scene in TEMPLATES if scene in capabilities],
                   pendingDefaults=pending, sceneDefaultsVersion=SCENE_DEFAULTS_VERSION, defaultsCleared=False,
                   bindings=defaults.get("regular", {}),
                   scenes={scene: items for scene, items in defaults.items() if scene != "regular"},
                   defaultsVersion=DEFAULTS_VERSION,
                   pendingStart=bool(app["presentationProfile"] and "snap" not in defaults.get("regular", {})))
        return app

    @Property("QVariantList", notify=changed)
    def apps(self):
        return [dict(value=bundle, label=app["label"], path=app.get("path", ""))
                for bundle, app in self._apps.items() if not app.get("removed")]

    @Property("QVariantMap", notify=changed)
    def bindings(self):
        return {bundle: {g: dict(item) for g, item in self._bindings_for(bundle, self._scene, self._website).items()}
                for bundle, app in self._apps.items() if not app.get("removed")}

    @Property("QVariantMap", notify=changed)
    def regularBindings(self):
        return {bundle: {g: dict(item) for g, item in app["bindings"].items()}
                for bundle, app in self._apps.items() if not app.get("removed")}

    def _bindings_for(self, bundle, scene="regular", website=""):
        app = self._apps.get(bundle, {})
        if app.get("removed"):
            return {}
        if scene == VIDEO and website:
            return app.get("websites", {}).get(website, {}).get("bindings", {})
        return app.get("bindings", {}) if scene == "regular" else app.get("scenes", {}).get(scene, {})

    @Property(bool, notify=changed)
    def browserVideoScope(self):
        return self._is_added(self._bundle) and self._bundle.casefold() in BROWSERS and self._scene == VIDEO

    @Property(str, notify=changed)
    def selectedWebsite(self):
        return self._website

    @Property("QVariantList", notify=changed)
    def websites(self):
        return [dict(value="", label="通用视频")] + [dict(value=domain, label=site["label"])
            for domain, site in self._apps.get(self._bundle, {}).get("websites", {}).items()]

    @Property(str, notify=changed)
    def selectedScopeLabel(self):
        if not self.browserVideoScope:
            return self.selectedSceneLabel
        label = self._apps[self._bundle].get("websites", {}).get(self._website, {}).get("label", "通用视频")
        return self.selectedSceneLabel + " · " + label

    @Slot(str)
    def selectWebsite(self, domain):
        if not self.browserVideoScope or (domain and domain not in self._apps[self._bundle].get("websites", {})):
            return
        if self._website != domain:
            self._website = domain
            self.changed.emit()
            self.discoveryChanged.emit()

    @Slot(str, result=bool)
    def addWebsite(self, value):
        if not self.browserVideoScope:
            return False
        try:
            domain = website_domain(value)
        except (ValueError, UnicodeError):
            self._message = "请输入完整的网站域名，例如 bilibili.com"
            self.discoveryChanged.emit()
            return False
        self._message = ""
        app = self._apps[self._bundle]
        if domain not in app.get("websites", {}):
            self._apps = {**self._apps, self._bundle: {**app, "websites": {**app.get("websites", {}),
                domain: dict(label="哔哩哔哩" if domain == BILIBILI else domain, bindings=website_defaults(domain))}}}
            self._save()
        self.selectWebsite(domain)
        self.discoveryChanged.emit()
        return True

    @Slot(str, str, result=bool)
    def removeWebsite(self, bundle, domain):
        if not self._is_added(bundle) or domain not in self._apps[bundle].get("websites", {}):
            return False
        app = self._apps[bundle]
        sites = dict(app["websites"])
        del sites[domain]
        self._apps = {**self._apps, bundle: {**app, "websites": sites}}
        if self._bundle == bundle and self._website == domain:
            self._website = ""
        self._save()
        self.websiteBindingsCleared.emit(bundle, domain)
        return True

    def website_for_target(self, bundle, host):
        sites = self._apps.get(bundle, {}).get("websites", {})
        return next(iter(sorted((domain for domain in sites if matches_website(host, domain)), key=len, reverse=True)), "")

    def scene_bindings_for_target(self, target, scene):
        website = self.website_for_target(target.bundle, target.website) if scene == VIDEO else ""
        return self._bindings_for(target.bundle, scene, website)

    @Property("QVariantMap", notify=changed)
    def globalOccupancy(self):
        bindings = self.service.owner._global_gesture_bindings
        return {gesture: GLOBAL_ACTION_LABELS[action] for action, gesture in bindings.as_dict().items()}

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
        conflicts = [GESTURE_LABELS[g] for g in self._bindings_for(self._bundle, self._scene, self._website)
                     if g in self.globalOccupancy]
        return ("以下绑定因全局占用已暂停：" + "、".join(conflicts)
                + "。原绑定保留，释放全局占用后恢复。") if conflicts else ""

    @Property(str, notify=changed)
    def selectedScene(self):
        return self._scene

    @Property(bool, notify=changed)
    def supportsPresentation(self):
        return bool(self._profile_for(self._bundle)) and self._is_added(self._bundle)

    def _profile_for(self, bundle):
        return presentation_profile(bundle) or self._apps.get(bundle, {}).get("presentationProfile", "")

    def _profiles_for(self, bundle):
        profiles = dict(self._apps.get(bundle, {}).get("sceneProfiles", {}))
        profiles.update(application_scene_profiles(bundle))
        if self._profile_for(bundle):
            profiles[PRESENTATION] = self._profile_for(bundle)
        return {scene: profiles[scene] for scene in SCENE_LABELS if scene in profiles}

    @Property("QVariantList", notify=changed)
    def availableScenes(self):
        return [dict(value="regular", label="常规")] + [dict(value=scene, label=SCENE_LABELS[scene])
            for scene in self._profiles_for(self._bundle) if self._is_added(self._bundle)]

    @Property(str, notify=changed)
    def selectedSceneLabel(self):
        return SCENE_LABELS.get(self._scene, "常规")

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
        elif target.input_context == "text":
            message = "当前为文字输入区域，使用应用常规配置"
        elif target.input_context != "nontext":
            message = "焦点状态待确认，使用应用常规配置"
        else:
            label = SCENE_LABELS.get(target.scene, target.scene)
            if target.website:
                label += " · " + target.website
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
            self._website = ""
            # Editor selection never changes runtime scene, settings or epochs.
            self.changed.emit()
            self.discoveryChanged.emit()

    @Slot(str, result=int)
    def bindingCount(self, bundle):
        app = self._apps.get(bundle, {})
        return (len(app.get("bindings", {})) + sum(len(items) for items in app.get("scenes", {}).values())
                + sum(len(site["bindings"]) for site in app.get("websites", {}).values()))

    @Property(bool, notify=changed)
    def hasDefaultMappings(self):
        app = self._apps.get(self._bundle, {})
        return self._is_added(self._bundle) and bool(self._defaults_for(self._bundle, app))

    @Property("QVariantMap", notify=discoveryChanged)
    def pendingBindings(self):
        if self._website:
            return {}
        return {gesture: action_label(self._scene, action) for gesture, action in
                self._apps.get(self._bundle, {}).get("pendingDefaults", {}).get(self._scene, {}).items()}

    @Property(int, notify=changed)
    def pendingDefaultCount(self):
        return sum(len(items) for items in self._apps.get(self._bundle, {}).get("pendingDefaults", {}).values())

    @Property(str, notify=discoveryChanged)
    def defaultMappingNotice(self):
        if self._apps.get(self._bundle, {}).get("pendingStart"):
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
        groups += [site["bindings"] for site in app.get("websites", {}).values()]
        custom = {item["id"]: {**item, "available": None, "custom": True}
                  for group in groups for item in group.values()
                  if item["id"].startswith("custom:")}
        presets = (scene_actions(self._bundle, self._scene, profile=self._profile_for(self._bundle))
                   if self._scene in {"regular", PRESENTATION} else
                   activity_actions(self._bundle, self._scene, self._profiles_for(self._bundle).get(self._scene, "")))
        if self.browserVideoScope:
            presets = video_actions(self._website)
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
            if not error and self._resolve_scene_defaults(bundle):
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

    @Slot(str)
    def selectApplication(self, bundle):
        self._generation["menu"] += 1
        selected = bundle if self._is_added(bundle) else ""
        if selected != self._bundle:
            self._scene = "regular"
            self._website = ""
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
                "pendingStart": False, "pendingDefaults": {}, "defaultsCleared": True, "defaultsVersion": DEFAULTS_VERSION,
                "websites": {domain: {**site, "bindings": {}} for domain, site in self._apps[bundle].get("websites", {}).items()}}}
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
            "pendingStart": bool(self._profile_for(bundle) and "snap" not in defaults.get("regular", {}))}}
        self._save()
        self.applicationBindingsCleared.emit(bundle)  # Discard this app's old drafts too.
        return True

    @Slot(str, result=bool)
    def removeApplication(self, bundle):
        if not self._is_added(bundle):
            return False
        # Keep an empty ownership marker so removing a new configuration never
        # reactivates this bundle's older, explicitly saved shortcut profile.
        self._apps = {**self._apps, bundle: {**self._apps[bundle], "bindings": {}, "scenes": {}, "websites": {}, "pendingDefaults": {}, "removed": True}}
        if self._bundle == bundle:
            self.selectApplication("")  # Invalidates any in-flight menu reply.
        self._save()
        self.applicationBindingsCleared.emit(bundle)
        return True

    @Slot(str, result=bool)
    def canBind(self, gesture):
        return self._can_bind_regular(gesture)

    def _can_bind_regular(self, gesture):
        return gesture in GESTURE_LABELS and gesture not in self.service.owner._global_gesture_bindings.reserved

    @Slot(str, str, str, result=bool)
    def setBinding(self, bundle, gesture, action_id):
        if not self._is_added(bundle) or not self.canBind(gesture):
            self._message = "此手势已被全局功能占用，请先修改全局配置"
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
        bindings = dict(self._bindings_for(bundle, self._scene, self._website))
        if action:
            bindings[gesture] = {key: action[key] for key in ("id", "label", "path", "shortcut")}
        else:
            bindings.pop(gesture, None)
        app = self._apps[bundle]
        updated = {**app, "bindings": bindings} if self._scene == "regular" else {
            **app, "scenes": {**app.get("scenes", {}), self._scene: bindings}}
        if self._scene == "regular" and gesture == "snap":
            updated["pendingStart"] = False  # Explicit edits/deletions win over discovery.
        if self.browserVideoScope and self._website:
            updated = {**app, "websites": {**app["websites"],
                self._website: {**app["websites"][self._website], "bindings": bindings}}}
        if self._scene in TEMPLATES and not self._website:
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
        return self._can_bind_regular(gesture) and any(gesture in app["bindings"] for app in self._apps.values())

    def for_target(self, bundle):
        return {"menu:" + gesture: AppBinding(gesture, item["shortcut"])
                for gesture, item in self._apps.get(bundle, {}).get("bindings", {}).items()
                if self._can_bind_regular(gesture)}

    def scene_bundles(self):
        return [bundle for bundle in self._apps if self._is_added(bundle) and self._profiles_for(bundle)]

    def configured_scenes(self):
        return {bundle: list(self._profiles_for(bundle))
                for bundle in self.scene_bundles()}

    def uses_scene(self, gesture):
        return self._can_bind_regular(gesture) and (any(gesture in self._bindings_for(bundle, scene)
            for bundle, scenes in self.configured_scenes().items() for scene in scenes)
            or any(gesture in site["bindings"] for app in self._apps.values() if not app.get("removed")
                   for site in app.get("websites", {}).values()))

    def for_scene(self, bundle, scene, website=""):
        if scene not in self._profiles_for(bundle):
            return {}
        domain = self.website_for_target(bundle, website) if scene == VIDEO else ""
        return {"scene:" + gesture: AppBinding(gesture, item["shortcut"])
                for gesture, item in self._bindings_for(bundle, scene, domain).items() if self._can_bind_regular(gesture)}

    def close(self):
        self._closed = True
        # A read may be in flight; do not join its native channel on the GUI thread.
        threading.Thread(target=self.channel.close, daemon=True).start()
