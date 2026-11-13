"""One export-time application allowlist, shared by records and diagnostics.

Attribution is metadata, never a search through user text. A mixed record is
atomic: all evidenced applications must be selected before any of it is copied.
"""
from dataclasses import dataclass
import json
import re


ALIASES = {
    "chrome": "com.google.chrome", "google chrome": "com.google.chrome",
    "safari": "com.apple.safari", "wechat": "com.tencent.xinwechat",
    "微信": "com.tencent.xinwechat", "outlook": "com.microsoft.outlook",
    "microsoft outlook": "com.microsoft.outlook", "mail": "com.apple.mail",
    "邮件": "com.apple.mail", "textedit": "com.apple.textedit",
    "notes": "com.apple.notes", "code": "com.microsoft.vscode",
}
APP_KEYS = frozenset({"application", "app", "bundle", "bundle_id", "bundleIdentifier", "process_name",
                      "front_application", "target_application", "foreground_application", "voice_application"})
UNKNOWN = frozenset({"", "unknown", "unavailable", "none", "__unknown__"})


def application_id(value):
    if not isinstance(value, str):
        return ""
    value = value.strip().casefold()
    return "" if value in UNKNOWN else ALIASES.get(value, value)


def website(value):
    # Domain only: no URL paths, ports, query strings or credentials.
    if not isinstance(value, str):
        return ""
    value = value.lower().rstrip(".")
    return value if len(value) <= 253 and re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) else ""


def attributions(value):
    """Read explicit structured app fields, including secondary context sources."""
    found = set()
    def visit(node):
        if isinstance(node, dict):
            apps = {application_id(node[k]) for k in APP_KEYS if k in node} - {""}
            site = website(node.get("website", ""))
            primary = {application_id(node[k]) for k in APP_KEYS
                       if k in node and k not in {"front_application", "foreground_application", "voice_application"}} - {""}
            found.update((app, site if app in primary else "") for app in apps)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    return found


def scope_key(app, site=""):
    return application_id(app) + ("|" + website(site) if website(site) else "")


@dataclass(frozen=True)
class ExportScope:
    keys: frozenset[str]

    def accepts(self, value):
        return self.accepts_attributions(attributions(value))

    def accepts_attributions(self, identities):
        if not identities:
            return False
        return all(app in self.keys or scope_key(app, site) in self.keys
                   for app, site in identities)

    def as_dict(self):
        return dict(applications=sorted(self.keys), unknown="excluded", mixed="all_sources_required",
                    attachments="complete", content_redaction=False)


def scope_options(records):
    options = {}
    for record in records:
        found = attributions(record)
        options.update({app: app for app, _ in found})
        # Do not offer an unusable website choice for an otherwise unscoped
        # browser record. Another fully scoped record may still offer it.
        options.update({scope_key(app, site): f"{app} · {site}" for app, site in found
                        if site and (app, "") not in found})
    return sorted(options.items(), key=lambda item: item[1])


def filtered_associations(raw, completed):
    rows, omitted = [], 0
    for line in raw.splitlines():
        try:
            row = json.loads(line)
            references = set()
            def visit(node):
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key == "interaction_id" and isinstance(value, str):
                            references.add(value)
                        elif key == "member_interaction_ids" and isinstance(value, list):
                            references.update(v for v in value if isinstance(v, str))
                        else:
                            visit(value)
                elif isinstance(node, list):
                    for item in node:
                        visit(item)
            visit(row)
            if not references or not references <= completed:
                omitted += 1
                continue
            rows.append(line)
        except (ValueError, TypeError):
            omitted += 1
    return b"\n".join(rows) + (b"\n" if rows else b""), omitted


def attachment_scope_ok(files, scope, record):
    """Check structured event/ASR sidecars before publishing any record files."""
    identities = attributions(record)
    if record.get("events_file", "events.jsonl") not in files:
        return False  # Missing application-event evidence cannot prove ownership.
    for name, stream in files.items():
        if not name.endswith(".jsonl"):
            continue
        try:
            for line in stream:
                if line.strip():
                    identities.update(attributions(json.loads(line)))
        except (ValueError, UnicodeError):
            return False
        finally:
            stream.seek(0)
    return scope.accepts_attributions(identities)


_LOG_SCOPE = re.compile(r"^\[[^\]\n]+\] \[[^\]\n]+\] \[[^\]\n]+\] \[export=([0-9a-f]+)\] ")


def log_attributions(line):
    match = _LOG_SCOPE.match(line)
    if not match:
        return set()
    try:
        rows = json.loads(bytes.fromhex(match[1]))
        if not isinstance(rows, list):
            return set()
        return {(application_id(app), website(site)) for app, site in rows if application_id(app)}
    except (ValueError, TypeError):
        return set()
