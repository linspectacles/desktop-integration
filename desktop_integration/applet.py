#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LinSpectacles - Linux Inspection Suite
# Copyright (C) 2026 brunonlinespace
# GPL-3.0-or-later

APPLET_ID = "linspectacles-desktop-integration"
APPLET_NAME = "Desktop Integration"
APPLET_VERSION = "0.0.4"
APPLET_AUTHOR = "brunonlinespace"
APPLET_EDITOR = "brunonlinespace"
APPLET_REPOSITORY = "https://github.com/linspectacles/desktop-integration"
APPLET_LAYER = "Desktop Environment"

import configparser
import os
import shutil
from collections import defaultdict, deque
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt, QUrl
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from ._support import (
    BaseInspectorTab,
    export_records_dialog,
    metadata_font_qss,
)


VIEW_DESKTOP_ENTRIES = "Desktop Entries"
VIEW_ACTIONS = "Actions"
VIEW_MIME_ASSOCIATIONS = "MIME Associations"
VIEW_MIME_CONFIGURATION = "MIME Configuration"
VIEWS = (
    VIEW_DESKTOP_ENTRIES,
    VIEW_ACTIONS,
    VIEW_MIME_ASSOCIATIONS,
    VIEW_MIME_CONFIGURATION,
)

VIEW_COLUMNS = {
    VIEW_DESKTOP_ENTRIES: [
        ("desktop_id", "Desktop ID", 220, "left"),
        ("name", "Name", 180, "left"),
        ("type", "Type", 90, "left"),
        ("exec", "Exec", 260, "left"),
        ("visible", "Visible", 150, "left"),
        ("mime_types_display", "MIME Types", 240, "left"),
        ("source", "Source", 320, "left"),
    ],
    VIEW_ACTIONS: [
        ("desktop_id", "Desktop ID", 220, "left"),
        ("application", "Application", 180, "left"),
        ("action", "Action", 120, "left"),
        ("name", "Name", 160, "left"),
        ("exec", "Exec", 280, "left"),
        ("source", "Source", 320, "left"),
    ],
    VIEW_MIME_ASSOCIATIONS: [
        ("mime_type", "MIME Type", 200, "left"),
        ("default_application", "Default Application", 220, "left"),
        ("associated_applications", "Associated Applications", 340, "left"),
        ("added", "Added", 240, "left"),
        ("removed", "Removed", 240, "left"),
        ("source_resolution", "Source / Resolution", 260, "left"),
    ],
    VIEW_MIME_CONFIGURATION: [
        ("file", "File", 190, "left"),
        ("scope", "Scope", 180, "left"),
        ("desktop", "Desktop", 120, "left"),
        ("defaults", "Defaults", 80, "right"),
        ("added", "Added", 80, "right"),
        ("removed", "Removed", 80, "right"),
        ("effective", "Effective / Shadowed", 250, "left"),
    ],
}


# ---- XDG / freedesktop parsing -------------------------------------------------

def _dedupe_paths(paths):
    seen = set()
    result = []
    for item in paths:
        text = str(Path(item).expanduser())
        if text in seen:
            continue
        seen.add(text)
        result.append(Path(text))
    return result


def _xdg_data_roots():
    home = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")).expanduser()
    raw_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs = [Path(part).expanduser() for part in raw_dirs.split(":") if part]
    return _dedupe_paths([home, *dirs])


def _xdg_config_roots():
    home = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")).expanduser()
    raw_dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    dirs = [Path(part).expanduser() for part in raw_dirs.split(":") if part]
    return _dedupe_paths([home, *dirs])


def _desktop_names():
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "")
    result = []
    for item in raw.split(":"):
        value = item.strip()
        if value and value.lower() not in [x.lower() for x in result]:
            result.append(value)
    return result


def _desktop_specific_names():
    return [name.lower() for name in _desktop_names()]


def _new_parser():
    parser = configparser.ConfigParser(
        interpolation=None,
        strict=True,
        delimiters=("=",),
        comment_prefixes=("#",),
        inline_comment_prefixes=None,
        empty_lines_in_values=False,
    )
    parser.optionxform = str
    return parser


def _read_ini(path):
    parser = _new_parser()
    try:
        with Path(path).open("r", encoding="utf-8-sig", errors="strict") as handle:
            parser.read_file(handle)
        return parser, ""
    except (OSError, configparser.Error) as exc:
        return parser, str(exc)


# Desktop-entry string escapes and the escaped semicolon list delimiter are
# specified by freedesktop.org. Preserve raw groups for provenance/details.
_DESKTOP_ESCAPES = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\", ";": ";"}
_BOOLEAN_KEYS = (
    "Hidden", "NoDisplay", "Terminal", "DBusActivatable", "StartupNotify",
    "PrefersNonDefaultGPU", "SingleMainWindow",
)


def _unescape_desktop(value):
    source = str(value or "")
    result = []
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\\" and index + 1 < len(source):
            next_char = source[index + 1]
            if next_char in _DESKTOP_ESCAPES:
                result.append(_DESKTOP_ESCAPES[next_char])
                index += 2
                continue
        result.append(char)
        index += 1
    return "".join(result)


def _bool_value(value, default=False):
    text = str(value or "").strip()
    if text == "true":
        return True
    if text == "false":
        return False
    return bool(default)


def _semicolon_list(value):
    # Split on unescaped ';' only, decoding standard desktop-entry escapes.
    value = str(value or "")
    result, token = [], []
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\" and index + 1 < len(value):
            next_char = value[index + 1]
            if next_char in _DESKTOP_ESCAPES:
                token.append(_DESKTOP_ESCAPES[next_char])
                index += 2
                continue
        if char == ";":
            if token:
                result.append("".join(token))
            token = []
        else:
            token.append(char)
        index += 1
    if token:
        result.append("".join(token))
    return result


def _locale_candidates():
    raw = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    raw = raw.strip()
    if not raw or raw in {"C", "POSIX"}:
        return []
    # LC_MESSAGES: lang[_COUNTRY][.ENCODING][@MODIFIER]; the encoding is
    # removed *without* throwing away the modifier.
    left, separator, modifier = raw.partition("@")
    left = left.split(".", 1)[0]
    language, country_sep, country = left.partition("_")
    if not language:
        return []
    candidates = []
    if country_sep and country and separator and modifier:
        candidates.append(f"{language}_{country}@{modifier}")
    if country_sep and country:
        candidates.append(f"{language}_{country}")
    if separator and modifier:
        candidates.append(f"{language}@{modifier}")
    candidates.append(language)
    return candidates


def _localized_value(section, key):
    if section is None:
        return ""
    for locale_name in _locale_candidates():
        localized_key = f"{key}[{locale_name}]"
        if localized_key in section:
            return _unescape_desktop(section.get(localized_key, ""))
    return _unescape_desktop(section.get(key, ""))


def _desktop_entry_validation_error(section):
    if section is None:
        return "Missing [Desktop Entry] group"
    for key in _BOOLEAN_KEYS:
        if key in section and section.get(key) not in {"true", "false"}:
            return f"Invalid boolean {key}={section.get(key)!r}; required true or false"
    # Hidden=true is an intentional tombstone; its presence must continue to
    # mask a lower-precedence desktop ID even when it has no launch fields.
    if section.get("Hidden", "false") == "true":
        return ""
    only_show = set(_semicolon_list(section.get("OnlyShowIn", "")))
    not_show = set(_semicolon_list(section.get("NotShowIn", "")))
    if only_show & not_show:
        return "The same desktop name is listed in OnlyShowIn and NotShowIn"
    entry_type = section.get("Type", "")
    if entry_type not in {"Application", "Link", "Directory"}:
        return "Missing or unsupported Type"
    if not section.get("Name", "").strip():
        return "Missing required unlocalized Name"
    if entry_type == "Application":
        if not section.get("Exec", "").strip() and section.get("DBusActivatable") != "true":
            return "Missing required Exec (DBusActivatable is not true)"
    elif entry_type == "Link" and not section.get("URL", "").strip():
        return "Missing required URL for Link entry"
    return ""


def _desktop_id(applications_root, path):
    try:
        relative = Path(path).relative_to(applications_root)
    except ValueError:
        return Path(path).name
    return str(relative).replace(os.sep, "-")


def _tryexec_available(value):
    value = str(value or "").strip()
    if not value:
        return None
    if os.path.isabs(value):
        return Path(value).is_file() and os.access(value, os.X_OK)
    return shutil.which(value) is not None


def _visibility(entry, current_desktops):
    if entry.get("parse_error"):
        return "Invalid", "Desktop entry could not be parsed."
    if entry.get("hidden"):
        return "Hidden", "Hidden=true; this desktop ID is treated as deleted at this precedence level."
    if entry.get("no_display"):
        return "NoDisplay", "NoDisplay=true; the entry may be used for associations but is not normally shown in menus."

    only_show_in = entry.get("only_show_in", [])
    not_show_in = entry.get("not_show_in", [])
    desktops = [item.lower() for item in current_desktops]
    only_lower = [item.lower() for item in only_show_in]
    not_lower = [item.lower() for item in not_show_in]

    # Per Desktop Entry spec, the first current-desktop name matching either
    # OnlyShowIn or NotShowIn determines visibility. The default is hidden
    # only when OnlyShowIn is present and nothing matches.
    matched_only = False
    for desktop in desktops:
        if desktop in only_lower:
            matched_only = True
            break
        if desktop in not_lower:
            return "NotShowIn", "The first matching current desktop is denied by NotShowIn."
    if only_show_in and not matched_only:
        return "OnlyShowIn mismatch", "No current desktop name matches OnlyShowIn."

    tryexec = entry.get("try_exec", "")
    if tryexec and entry.get("try_exec_available") is False:
        return "TryExec unavailable", "TryExec is not currently resolvable/executable; it was checked but never executed."

    if entry.get("type") not in {"Application", "Link", "Directory"}:
        return "Invalid type", "The desktop entry Type is missing or not a recognized standard type."
    return "Visible", "No inspected visibility key suppresses this entry for the current desktop."


def _scan_desktop_entries():
    current_desktops = _desktop_names()
    roots = []
    variants = defaultdict(list)
    by_root = []

    for root_index, data_root in enumerate(_xdg_data_roots()):
        applications_root = data_root / "applications"
        roots.append({
            "root_index": root_index,
            "data_root": data_root,
            "applications_root": applications_root,
            "scope": "User Data" if root_index == 0 else "System Data",
        })
        root_records = {}
        if applications_root.is_dir():
            try:
                paths = sorted(applications_root.rglob("*.desktop"), key=lambda p: str(p).lower())
            except OSError:
                paths = []
            for path in paths:
                if not path.is_file():
                    continue
                did = _desktop_id(applications_root, path)
                parser, parse_error = _read_ini(path)
                section = parser["Desktop Entry"] if "Desktop Entry" in parser else None
                if not parse_error:
                    parse_error = _desktop_entry_validation_error(section)

                entry = {
                    "desktop_id": did,
                    "source": str(path),
                    "source_scope": "User Data" if root_index == 0 else "System Data",
                    "root_index": root_index,
                    "parse_error": parse_error,
                    "name": _localized_value(section, "Name"),
                    "generic_name": _localized_value(section, "GenericName"),
                    "type": section.get("Type", "") if section else "",
                    "exec": _unescape_desktop(section.get("Exec", "")) if section else "",
                    "try_exec": _unescape_desktop(section.get("TryExec", "")) if section else "",
                    "icon": _unescape_desktop(section.get("Icon", "")) if section else "",
                    "terminal": _bool_value(section.get("Terminal", "false")) if section else False,
                    "no_display": _bool_value(section.get("NoDisplay", "false")) if section else False,
                    "hidden": _bool_value(section.get("Hidden", "false")) if section else False,
                    "only_show_in": _semicolon_list(section.get("OnlyShowIn", "")) if section else [],
                    "not_show_in": _semicolon_list(section.get("NotShowIn", "")) if section else [],
                    "categories": _semicolon_list(section.get("Categories", "")) if section else [],
                    "mime_types": _semicolon_list(section.get("MimeType", "")) if section else [],
                    "actions": _semicolon_list(section.get("Actions", "")) if section else [],
                    "dbus_activatable": _bool_value(section.get("DBusActivatable", "false")) if section else False,
                    "groups": {group: dict(parser[group].items()) for group in parser.sections()},
                }
                entry["try_exec_available"] = _tryexec_available(entry["try_exec"])
                visible, reason = _visibility(entry, current_desktops)
                entry["visible"] = visible
                entry["visibility_reason"] = reason
                entry["mime_types_display"] = ";".join(entry["mime_types"])
                variants[did].append(entry)
                # Duplicate IDs within one applications root are undefined by the spec.
                # Keep the first deterministic path for that root while preserving all variants globally.
                root_records.setdefault(did, entry)
        by_root.append(root_records)

    effective = []
    effective_by_id = {}
    for did in sorted(variants, key=str.lower):
        ordered = sorted(variants[did], key=lambda rec: (rec["root_index"], rec["source"].lower()))
        chosen = ordered[0]
        chosen_root = chosen["root_index"]
        chosen["same_precedence_collisions"] = [
            rec["source"] for rec in ordered[1:] if rec["root_index"] == chosen_root
        ]
        chosen["shadowed_sources"] = [
            rec["source"] for rec in ordered[1:] if rec["root_index"] > chosen_root
        ]
        chosen["variant_count"] = len(ordered)
        effective.append(chosen)
        effective_by_id[did] = chosen

    return roots, by_root, variants, effective, effective_by_id


def _parse_mimeapps_file(path, scope, desktop_name, desktop_specific, lookup_index, data_root_index=None):
    parser, parse_error = _read_ini(path)

    def read_group(group):
        result = {}
        if group not in parser:
            return result
        for mime_type, value in parser[group].items():
            result[mime_type] = _semicolon_list(value)
        return result

    defaults = read_group("Default Applications")
    added = read_group("Added Associations")
    removed = read_group("Removed Associations")
    return {
        "path": str(path),
        "file": Path(path).name,
        "scope": scope,
        "desktop": desktop_name or "All",
        "desktop_specific": bool(desktop_specific),
        "lookup_index": lookup_index,
        "data_root_index": data_root_index,
        "parse_error": parse_error,
        "defaults_map": defaults,
        "added_map": added,
        "removed_map": removed,
        "groups": {group: dict(parser[group].items()) for group in parser.sections()},
        "defaults": sum(len(items) for items in defaults.values()),
        "added": sum(len(items) for items in added.values()),
        "removed": sum(len(items) for items in removed.values()),
        "effective": "Pending resolution",
    }


def _discover_mime_configuration(data_roots):
    desktops = _desktop_specific_names()
    records = []
    lookup_index = 0

    config_roots = _xdg_config_roots()
    for config_index, root in enumerate(config_roots):
        scope = "User Config" if config_index == 0 else "System Config"
        for desktop in desktops:
            path = root / f"{desktop}-mimeapps.list"
            if path.is_file():
                records.append(_parse_mimeapps_file(path, scope, desktop, True, lookup_index))
            lookup_index += 1
        path = root / "mimeapps.list"
        if path.is_file():
            records.append(_parse_mimeapps_file(path, scope, "", False, lookup_index))
        lookup_index += 1

    for root_index, root_info in enumerate(data_roots):
        app_root = root_info["applications_root"]
        scope = "User Data (deprecated)" if root_index == 0 else "System Data"
        for desktop in desktops:
            path = app_root / f"{desktop}-mimeapps.list"
            if path.is_file():
                records.append(_parse_mimeapps_file(path, scope, desktop, True, lookup_index, root_index))
            lookup_index += 1
        path = app_root / "mimeapps.list"
        if path.is_file():
            records.append(_parse_mimeapps_file(path, scope, "", False, lookup_index, root_index))
        lookup_index += 1

    records.sort(key=lambda rec: rec["lookup_index"])
    return records


def _normal_desktop_record(desktop_id, variants, minimum_root_index=0):
    options = sorted(
        (rec for rec in variants.get(desktop_id, []) if rec["root_index"] >= minimum_root_index),
        key=lambda rec: (rec["root_index"], rec["source"].lower()),
    )
    if not options:
        return None, "missing"
    chosen = options[0]
    same_precedence = [rec for rec in options if rec["root_index"] == chosen["root_index"]]
    ambiguous = len(same_precedence) > 1
    if chosen.get("parse_error"):
        return None, "invalid"
    if chosen.get("hidden"):
        return None, "hidden"
    if chosen.get("type") != "Application":
        return None, "not-application"
    return chosen, "ambiguous-same-precedence" if ambiguous else "available"


def _association_contexts(data_roots, mime_config_records):
    plain_by_path = {
        rec["path"]: rec for rec in mime_config_records if not rec["desktop_specific"]
    }
    contexts = []
    for config_index, root in enumerate(_xdg_config_roots()):
        path = root / "mimeapps.list"
        contexts.append({
            "scope": "User Config" if config_index == 0 else "System Config",
            "path": str(path),
            "record": plain_by_path.get(str(path)),
            "minimum_root_index": 0,
            "data_root_index": None,
        })
    for root_index, root_info in enumerate(data_roots):
        path = root_info["applications_root"] / "mimeapps.list"
        contexts.append({
            "scope": "User Data (deprecated)" if root_index == 0 else "System Data",
            "path": str(path),
            "record": plain_by_path.get(str(path)),
            "minimum_root_index": root_index,
            "data_root_index": root_index,
        })
    return contexts


def _load_mime_hierarchy(data_roots):
    # Read the already installed shared-mime-info text indexes, NEVER regenerate
    # caches, call update-mime-database, or run a desktop application.
    aliases = {}
    parents = defaultdict(list)
    for root_info in data_roots:
        mime_root = root_info["data_root"] / "mime"
        for filename, target in (("aliases", aliases), ("subclasses", parents)):
            path = mime_root / filename
            try:
                with path.open("r", encoding="utf-8", errors="replace") as handle:
                    for raw in handle:
                        parts = raw.strip().split()
                        if len(parts) != 2 or parts[0].startswith("#"):
                            continue
                        child, ancestor = parts
                        if filename == "aliases":
                            target.setdefault(child, ancestor)
                        elif ancestor not in target[child]:
                            target[child].append(ancestor)
            except OSError:
                continue
    return aliases, parents


def _canonical_mime_type(mime_type, aliases):
    visited = set()
    while mime_type in aliases and mime_type not in visited:
        visited.add(mime_type)
        mime_type = aliases[mime_type]
    return mime_type


def _mime_resolution_order(mime_type, aliases, parents):
    # Breadth-first traversal prevents a generic parent from outranking a more
    # specific one. Cycles/duplicates in imperfect installed indexes are safe.
    queue = deque([mime_type])
    seen = set()
    ordered = []
    generic_fallback = False
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        ordered.append(current)
        canonical = _canonical_mime_type(current, aliases)
        if canonical != current:
            queue.appendleft(canonical)
            continue
        for parent in parents.get(current, []):
            if parent == "application/octet-stream":
                generic_fallback = True
            elif parent not in seen:
                queue.append(parent)
        if current.startswith("text/") and current != "text/plain":
            queue.append("text/plain")
        if current != "application/octet-stream" and not current.startswith("inode/"):
            generic_fallback = True
    # application/octet-stream is a generic last resort, not a competing
    # parent of the same specificity as a declared sub-class-of relation.
    if generic_fallback and "application/octet-stream" not in seen:
        ordered.append("application/octet-stream")
    return ordered


def _resolve_mime_type_exact(mime_type, contexts, by_root, variants, mime_config_records):
    results = []
    blacklist = set()
    trace = []
    raw_added = []
    raw_removed = []
    missing_refs = []
    effective_sources = set()

    for context in contexts:
        record = context["record"]
        minimum = context["minimum_root_index"]
        if record and not record.get("parse_error"):
            for desktop_id in record["added_map"].get(mime_type, []):
                raw_added.append(desktop_id)
                resolved, state = _normal_desktop_record(desktop_id, variants, minimum)
                if resolved is None:
                    missing_refs.append(("Added", desktop_id, record["path"], state))
                    trace.append(f"Ignored Added {desktop_id} from {record['path']}: {state} at this precedence or lower.")
                    continue
                if desktop_id in blacklist:
                    trace.append(f"Added {desktop_id} from {record['path']} was blocked by a higher-precedence removal/file.")
                    continue
                if desktop_id not in results:
                    results.append(desktop_id)
                    effective_sources.add(record["path"])
                    trace.append(f"Added {desktop_id} from {record['path']}.")

            for desktop_id in record["removed_map"].get(mime_type, []):
                raw_removed.append(desktop_id)
                resolved, state = _normal_desktop_record(desktop_id, variants, minimum)
                if resolved is None:
                    missing_refs.append(("Removed", desktop_id, record["path"], state))
                    trace.append(f"Ignored Removed {desktop_id} from {record['path']}: {state} at this precedence or lower.")
                    continue
                if desktop_id not in blacklist:
                    blacklist.add(desktop_id)
                    effective_sources.add(record["path"])
                    trace.append(f"Blacklisted {desktop_id} from Removed Associations in {record['path']}.")

        root_index = context["data_root_index"]
        if root_index is not None and 0 <= root_index < len(by_root):
            root_records = by_root[root_index]
            for desktop_id, desktop_record in sorted(root_records.items(), key=lambda item: item[0].lower()):
                if desktop_id in blacklist:
                    continue
                if desktop_record.get("parse_error") or desktop_record.get("hidden"):
                    continue
                if desktop_record.get("type") != "Application":
                    continue
                if mime_type in desktop_record.get("mime_types", []) and desktop_id not in results:
                    results.append(desktop_id)
                    trace.append(f"Declared by {desktop_id} in {desktop_record['source']}.")
            # A desktop file at this precedence masks the same desktop ID below it.
            blacklist.update(root_records.keys())

    default_application = ""
    default_source = ""
    default_trace = []
    for record in mime_config_records:
        if record.get("parse_error"):
            continue
        candidates = record["defaults_map"].get(mime_type, [])
        if not candidates:
            continue
        default_trace.append(f"{record['path']}: " + ";".join(candidates))
        for desktop_id in candidates:
            resolved, state = _normal_desktop_record(desktop_id, variants, 0)
            if resolved is None:
                missing_refs.append(("Default", desktop_id, record["path"], state))
                trace.append(f"Default candidate {desktop_id} from {record['path']} is not resolvable: {state}.")
                continue
            if desktop_id not in results:
                trace.append(f"Default candidate {desktop_id} from {record['path']} is installed but not associated with {mime_type}.")
                continue
            default_application = desktop_id
            default_source = record["path"]
            effective_sources.add(record["path"])
            trace.append(f"Selected default {desktop_id} from {record['path']}.")
            break
        if default_application:
            break

    if not default_application and results:
        default_application = results[0]
        default_source = "Association fallback"
        trace.append(f"No valid explicit default remained; selected the most-preferred association {results[0]}.")

    # Report unresolved references even when a higher-precedence default already
    # resolved the MIME type and lower files were therefore not needed for selection.
    seen_missing = {(kind, did, source, state) for kind, did, source, state in missing_refs}
    for record in mime_config_records:
        minimum = record["data_root_index"] if record.get("data_root_index") is not None else 0
        for group_label, mapping in (
            ("Default", record["defaults_map"]),
            ("Added", record["added_map"] if not record["desktop_specific"] else {}),
            ("Removed", record["removed_map"] if not record["desktop_specific"] else {}),
        ):
            for desktop_id in mapping.get(mime_type, []):
                resolved, state = _normal_desktop_record(desktop_id, variants, minimum if group_label != "Default" else 0)
                if resolved is None:
                    item = (group_label, desktop_id, record["path"], state)
                    if item not in seen_missing:
                        missing_refs.append(item)
                        seen_missing.add(item)

    return {
        "mime_type": mime_type,
        "default_application": default_application,
        "associated_ids": results,
        "associated_applications": ";".join(results),
        "added_ids": raw_added,
        "added": ";".join(raw_added),
        "removed_ids": raw_removed,
        "removed": ";".join(raw_removed),
        "default_source": default_source,
        "source_resolution": default_source or "No resolvable handler",
        "resolution_trace": trace,
        "default_candidate_chain": default_trace,
        "missing_refs": missing_refs,
        "effective_sources": effective_sources,
        "status": (
            "Resolved with missing references" if default_application and missing_refs
            else "Resolved" if default_application
            else "Missing References" if missing_refs
            else "No Default"
        ),
    }


def _resolve_mime_type(mime_type, contexts, by_root, variants, mime_config_records, aliases, parents):
    types = _mime_resolution_order(mime_type, aliases, parents)
    exact = _resolve_mime_type_exact(mime_type, contexts, by_root, variants, mime_config_records)
    if len(types) == 1:
        return exact

    ordered_associations = list(exact["associated_ids"])
    seen = set(ordered_associations)
    selected_type = mime_type if exact["default_application"] else ""
    source = exact["default_source"]
    default = exact["default_application"]
    effective_sources = set(exact["effective_sources"])
    resolution_trace = list(exact["resolution_trace"])
    candidate_chain = list(exact["default_candidate_chain"])
    missing_refs = list(exact["missing_refs"])
    for ancestor in types[1:]:
        inherited = _resolve_mime_type_exact(ancestor, contexts, by_root, variants, mime_config_records)
        resolution_trace.append(f"Inherited MIME candidate: {ancestor} (from {mime_type}).")
        for desktop_id in inherited["associated_ids"]:
            if desktop_id not in seen:
                seen.add(desktop_id)
                ordered_associations.append(desktop_id)
                resolution_trace.append(f"Inherited association {desktop_id} via {ancestor}.")
        if not default and inherited["default_application"]:
            default = inherited["default_application"]
            source = f"{inherited['default_source']} (via {ancestor})"
            selected_type = ancestor
            effective_sources.update(inherited["effective_sources"])
            resolution_trace.append(f"Selected inherited handler {default} via {ancestor}.")
        for item in inherited["missing_refs"]:
            if item not in missing_refs:
                missing_refs.append(item)
        candidate_chain.extend(f"[{ancestor}] {item}" for item in inherited["default_candidate_chain"])
    exact["associated_ids"] = ordered_associations
    exact["associated_applications"] = ";".join(ordered_associations)
    exact["default_application"] = default
    exact["default_source"] = source
    exact["source_resolution"] = source or "No resolvable handler"
    exact["resolution_trace"] = resolution_trace
    exact["default_candidate_chain"] = candidate_chain
    exact["missing_refs"] = missing_refs
    exact["effective_sources"] = effective_sources
    exact["status"] = (
        "Resolved with missing references" if default and missing_refs else
        "Resolved" if default else "Missing References" if missing_refs else "No Default"
    )
    return exact


def collect_desktop_integration():
    data_roots, by_root, variants, entries, effective_by_id = _scan_desktop_entries()
    mime_config = _discover_mime_configuration(data_roots)
    contexts = _association_contexts(data_roots, mime_config)
    mime_aliases, mime_parents = _load_mime_hierarchy(data_roots)

    actions = []
    for entry in entries:
        groups = entry.get("groups", {})
        for action_id in entry.get("actions", []):
            group_name = f"Desktop Action {action_id}"
            group = groups.get(group_name, {})
            actions.append({
                "desktop_id": entry["desktop_id"],
                "application": entry.get("name") or entry["desktop_id"],
                "action": action_id,
                "name": _localized_value(group, "Name") if group else "",
                "exec": _unescape_desktop(group.get("Exec", "")) if group else "",
                "source": entry["source"],
                "group_present": bool(group),
                "icon": _unescape_desktop(group.get("Icon", "")) if group else "",
                "parent_visibility": entry.get("visible", ""),
                "declared": True,
            })
    actions.sort(key=lambda row: (row["application"].lower(), row["action"].lower()))

    mime_types = set()
    for entry in entries:
        mime_types.update(entry.get("mime_types", []))
    for rec in mime_config:
        mime_types.update(rec["defaults_map"].keys())
        mime_types.update(rec["added_map"].keys())
        mime_types.update(rec["removed_map"].keys())

    associations = []
    all_effective_sources = set()
    for mime_type in sorted(mime_types, key=str.lower):
        resolved = _resolve_mime_type(
            mime_type, contexts, by_root, variants, mime_config,
            mime_aliases, mime_parents,
        )
        associations.append(resolved)
        all_effective_sources.update(resolved["effective_sources"])

    for rec in mime_config:
        if rec.get("parse_error"):
            rec["effective"] = "Error"
        elif rec["desktop_specific"] and (rec["added"] or rec["removed"]):
            if rec["path"] in all_effective_sources:
                rec["effective"] = "Effective defaults; invalid add/remove ignored"
            else:
                rec["effective"] = "Defaults only; add/remove ignored"
        elif rec["path"] in all_effective_sources:
            rec["effective"] = "Effective contribution"
        elif rec["defaults"] or rec["added"] or rec["removed"]:
            rec["effective"] = "Shadowed / no effective contribution"
        else:
            rec["effective"] = "No relevant entries"

    return {
        "data_roots": data_roots,
        "desktop_variants": variants,
        "effective_by_id": effective_by_id,
        VIEW_DESKTOP_ENTRIES: entries,
        VIEW_ACTIONS: actions,
        VIEW_MIME_ASSOCIATIONS: associations,
        VIEW_MIME_CONFIGURATION: mime_config,
    }


# ---- Options dialog -------------------------------------------------------------

class DesktopIntegrationOptionsDialog(QDialog):
    """Kernel Modules-authoritative LinSpectacles Options treatment."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Options — Desktop Integration")
        self.resize(980, 720)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        # Appearance
        appearance_page = QWidget()
        appearance_layout = QVBoxLayout(appearance_page)
        appearance_layout.setContentsMargins(12, 12, 12, 12)
        appearance_layout.setSpacing(10)

        appearance_heading = QLabel("Metadata display")
        heading_font = appearance_heading.font()
        heading_font.setBold(True)
        appearance_heading.setFont(heading_font)
        appearance_layout.addWidget(appearance_heading)

        appearance_note = QLabel(
            "Choose how the inventory and Metadata Details panes are arranged. "
            "This Desktop Integration setting is remembered."
        )
        appearance_note.setWordWrap(True)
        appearance_layout.addWidget(appearance_note)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Record details layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["Horizontal", "Vertical"])
        self.layout_combo.setCurrentText(
            "Vertical" if owner.details_orientation == "vertical" else "Horizontal"
        )
        self.layout_combo.currentTextChanged.connect(self._layout_changed)
        layout_row.addWidget(self.layout_combo)
        layout_row.addStretch(1)
        appearance_layout.addLayout(layout_row)

        self.show_metadata_panel = QCheckBox("Show Metadata Details panel (F3)")
        self.show_metadata_panel.setChecked(owner.metadata_panel_visible)
        self.show_metadata_panel.toggled.connect(self._metadata_panel_changed)
        appearance_layout.addWidget(self.show_metadata_panel)

        shortcut_note = QLabel(
            "F3 hides/restores Metadata Details. Shift+F5 refreshes the inventory. "
            "Ctrl+L toggles Horizontal/Vertical layout. Ctrl+/ opens Options."
        )
        shortcut_note.setWordWrap(True)
        appearance_layout.addWidget(shortcut_note)
        appearance_layout.addStretch(1)
        tabs.addTab(appearance_page, "Appearance")

        # Sources
        sources_page = QWidget()
        sources_layout = QVBoxLayout(sources_page)
        sources_layout.setContentsMargins(12, 12, 12, 12)
        sources_layout.setSpacing(10)

        sources_heading = QLabel("Data Sources")
        sources_heading_font = sources_heading.font()
        sources_heading_font.setBold(True)
        sources_heading_font.setPointSize(max(sources_heading_font.pointSize(), 11))
        sources_heading.setFont(sources_heading_font)
        sources_layout.addWidget(sources_heading)

        sources_note = QLabel(
            "Freedesktop/XDG interfaces used by this read-only desktop-registration inspector."
        )
        sources_note.setWordWrap(True)
        sources_layout.addWidget(sources_note)

        sources_text = QTextEdit()
        sources_text.setReadOnly(True)
        sources_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        chosen_font = getattr(owner, "_metadata_font", None)
        if chosen_font is not None:
            sources_text.setFont(chosen_font)
            sources_text.document().setDefaultFont(chosen_font)
            sources_text.setCurrentFont(chosen_font)
            font_rule = metadata_font_qss(chosen_font)
        else:
            font_rule = ""
        sources_text.setStyleSheet(
            "QTextEdit { "
            + font_rule
            + " background-color: #151719; color: #dcdcdc; "
              "border: 1px solid #333333; }"
        )

        source_key_format = QTextCharFormat()
        source_key_format.setForeground(QColor("#9cdcfe"))
        source_key_format.setFontWeight(600)
        source_value_format = QTextCharFormat()
        source_value_format.setForeground(QColor("#dcdcdc"))

        cursor = sources_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in owner.sources_text().splitlines():
            if line and not line[:1].isspace():
                cursor.insertText(line + "\n", source_key_format)
            else:
                cursor.insertText(line + "\n", source_value_format)
        sources_text.setTextCursor(cursor)
        sources_text.moveCursor(QTextCursor.MoveOperation.Start)
        sources_layout.addWidget(sources_text, 1)
        tabs.addTab(sources_page, "Sources")

        # Export
        export_page = QWidget()
        export_layout = QVBoxLayout(export_page)
        export_layout.setContentsMargins(12, 12, 12, 12)
        export_layout.setSpacing(10)

        export_heading = QLabel("Export details")
        export_heading_font = export_heading.font()
        export_heading_font.setBold(True)
        export_heading.setFont(export_heading_font)
        export_layout.addWidget(export_heading)

        export_note = QLabel("Select the Desktop Integration details to include in the export.")
        export_note.setWordWrap(True)
        export_layout.addWidget(export_note)

        self.export_inventory = QCheckBox("Current View inventory")
        self.export_inventory.setChecked(True)
        self.export_metadata = QCheckBox("Metadata Details")
        self.export_metadata.setChecked(False)
        export_layout.addWidget(self.export_inventory)
        export_layout.addWidget(self.export_metadata)
        export_layout.addStretch(1)

        export_row = QHBoxLayout()
        export_row.addStretch(1)
        export_button = QPushButton("Export...")
        export_button.clicked.connect(self._export_selected)
        export_row.addWidget(export_button)
        export_layout.addLayout(export_row)
        tabs.addTab(export_page, "Export")

        # About
        about_page = QWidget()
        about_layout = QVBoxLayout(about_page)
        about_layout.setContentsMargins(24, 20, 24, 18)
        about_layout.setSpacing(8)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(112, 112)
        icon_path = Path(__file__).resolve().parent / "icon.png"
        if icon_path.is_file():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        112,
                        112,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        about_layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        name = QLabel("<h2 style='margin:0'>Desktop Integration</h2>")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(name)

        version = QLabel(f"Version {APPLET_VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(version)

        slogan = QLabel("<b>Expose. Explore. Explain.</b>")
        slogan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(slogan)

        description = QLabel(
            "A read-only LinSpectacles applet for inspecting freedesktop desktop-entry "
            "registration and MIME association/default resolution."
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(description)

        details = QLabel(
            "<b>Build:</b> Standalone LinSpectacles applet<br>"
            "<b>Organisation ID:</b> "
            "<a href='https://github.com/linspectacles/'>linspectacles</a><br>"
            f"<b>Applet ID:</b> {APPLET_ID}"
        )
        details.setWordWrap(True)
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        details.setOpenExternalLinks(True)
        details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(details)

        copyright_label = QLabel(
            "Copyright © 2026 "
            "<a href='https://github.com/brunonlinespace'>brunonlinespace</a>"
        )
        copyright_label.setOpenExternalLinks(True)
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(copyright_label)

        license_label = QLabel("Licensed under GNU GPLv3 or later.")
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(license_label)

        applet_root = Path(__file__).resolve().parent.parent
        readme_url = QUrl.fromLocalFile(str(applet_root / "README.md")).toString()
        license_url = QUrl.fromLocalFile(str(applet_root / "LICENSE")).toString()
        links = QLabel(
            f"<a href='{readme_url}'>README</a> "
            "&nbsp;·&nbsp; "
            f"<a href='{license_url}'>License</a> "
            "&nbsp;·&nbsp; "
            f"<a href='{APPLET_REPOSITORY}'>Repository</a>"
        )
        links.setOpenExternalLinks(True)
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(links)
        about_layout.addStretch(1)
        tabs.addTab(about_page, "About")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _layout_changed(self, text):
        self.owner.set_details_orientation(str(text).strip().lower(), persist=True)

    def _metadata_panel_changed(self, visible):
        self.owner.set_metadata_panel_visible(bool(visible), persist=True)

    def _export_selected(self):
        if not self.export_inventory.isChecked() and not self.export_metadata.isChecked():
            QMessageBox.information(self, "Export", "Select at least one detail to export.")
            return

        records = []
        columns = []
        source_records = self.owner.get_export_records() if self.export_inventory.isChecked() else []
        if self.export_inventory.isChecked():
            columns.extend(self.owner.columns)
            records = [dict(record) for record in source_records]

        if self.export_metadata.isChecked():
            if self.export_inventory.isChecked():
                for exported, source in zip(records, source_records):
                    exported["metadata_details"] = self.owner.metadata_details_for_export(source)
            else:
                selected = self.owner.current_record()
                if selected is None:
                    QMessageBox.information(self, "Export", "Select a record before exporting Metadata Details.")
                    return
                records = [{"metadata_details": self.owner.metadata_details_for_export(selected)}]
            columns.append(("metadata_details", "Metadata Details", 520, "left"))

        if not records:
            QMessageBox.information(self, "Export", "There is no collected data to export yet.")
            return

        path = export_records_dialog(
            self,
            f"{APPLET_NAME} — {self.owner.current_view}",
            records,
            columns=columns,
        )
        if path is not None:
            self.owner.set_status(f"Exported data to {path}")


# ---- Main applet ----------------------------------------------------------------

class DesktopIntegrationTab(BaseInspectorTab):
    NUMERIC_COLUMNS = {"defaults", "added", "removed"}

    def __init__(self, parent=None):
        super().__init__(
            "Desktop Entries",
            VIEW_COLUMNS[VIEW_DESKTOP_ENTRIES],
            ["All"],
            parent,
        )
        self.export_title = APPLET_NAME
        self.current_view = VIEW_DESKTOP_ENTRIES
        self.datasets = {view: [] for view in VIEWS}
        self.collection = {}
        self._host_column_visibility_callback = None

        # Kernel Modules-authoritative compact top bar: Refresh List | Options...
        self.export_btn.setText("Options...")
        try:
            self.export_btn.clicked.disconnect()
        except TypeError:
            pass
        self.export_btn.clicked.connect(self.show_options)
        self.export_btn.setEnabled(True)

        # View selector belongs on the top-right action row, immediately before
        # Refresh List and Options..., without changing the authoritative shell.
        self.view_label = QLabel("View:")
        self.view_label.setStyleSheet("font-weight: 600;")
        self.view_combo = QComboBox()
        self.view_combo.addItems(VIEWS)
        self.view_combo.setMinimumWidth(180)
        action_position = self.dash_layout.indexOf(self.refresh_btn)
        self.dash_layout.insertWidget(action_position, self.view_label)
        self.dash_layout.insertWidget(action_position + 1, self.view_combo)
        self.view_combo.currentTextChanged.connect(self.switch_view)

        self.details_orientation = "horizontal"
        self._load_details_orientation()
        self.metadata_panel_visible = True
        self._load_metadata_panel_visibility()

        self._shortcuts = []
        self._install_shortcuts()
        self.load_modules()

    def load_modules(self):
        try:
            self.set_status("Refreshing desktop registration and MIME configuration...")
            self.collection = collect_desktop_integration()
            for view in VIEWS:
                self.datasets[view] = list(self.collection.get(view, []))
            self.switch_view(self.current_view, refresh_only=True)
            self.set_status("Successfully refreshed desktop integration data.")
        except Exception as exc:
            self.collection = {}
            for view in VIEWS:
                self.datasets[view] = []
            self.modules_data = []
            self.table.setRowCount(0)
            self.clear_info()
            self.stats_label.setText(f"{self.current_view}: 0")
            self.set_status(f"Error reading desktop integration data: {exc}")

    def switch_view(self, view, refresh_only=False):
        view = str(view or VIEW_DESKTOP_ENTRIES)
        if view not in VIEWS:
            view = VIEW_DESKTOP_ENTRIES
        self.current_view = view
        self.modules_data = list(self.datasets.get(view, []))
        self.columns = list(VIEW_COLUMNS[view])
        self._configure_table_for_view()
        self._configure_filter_for_view()
        self.stats_label.setText(f"{view}: {len(self.modules_data)}")
        self.filter_modules()
        if not refresh_only:
            self.set_status(f"Showing {view}.")

    def _configure_table_for_view(self):
        self.table.setColumnCount(len(self.columns))
        self.table.setHorizontalHeaderLabels([column[1] for column in self.columns])
        for index, (_column_id, _label, width, _alignment) in enumerate(self.columns):
            self.table.setColumnWidth(index, width)
        self.column_visibility.columns = list(self.columns)
        hidden = QSettings("brunonlinespace", "Desktop Integration").value(
            f"hidden_columns/{self.current_view}", []
        )
        if isinstance(hidden, str):
            hidden = [hidden] if hidden else []
        self.column_visibility.set_changed_callback(self._column_visibility_changed)
        self.column_visibility.apply_hidden(hidden or [])

    def _column_visibility_changed(self, columns):
        settings = QSettings("brunonlinespace", "Desktop Integration")
        settings.setValue(f"hidden_columns/{self.current_view}", list(columns or []))
        settings.sync()
        if callable(self._host_column_visibility_callback):
            self._host_column_visibility_callback(list(columns or []))

    def apply_host_settings(self, settings=None, column_visibility_callback=None):
        self._host_column_visibility_callback = column_visibility_callback if callable(column_visibility_callback) else None
        super().apply_host_settings(settings=settings, column_visibility_callback=self._column_visibility_changed)

    def _configure_filter_for_view(self):
        blocked = self.category_dropdown.blockSignals(True)
        try:
            self.category_dropdown.clear()
            if self.current_view == VIEW_DESKTOP_ENTRIES:
                self.category_label.setText("Visibility:")
                values = ["All"] + sorted(
                    {row.get("visible", "") for row in self.modules_data if row.get("visible")},
                    key=str.lower,
                )
            elif self.current_view == VIEW_ACTIONS:
                self.category_label.setText("Application:")
                values = ["All"] + sorted(
                    {row.get("application", "") for row in self.modules_data if row.get("application")},
                    key=str.lower,
                )
            elif self.current_view == VIEW_MIME_ASSOCIATIONS:
                self.category_label.setText("Status:")
                values = ["All"] + sorted(
                    {row.get("status", "") for row in self.modules_data if row.get("status")},
                    key=str.lower,
                )
            else:
                self.category_label.setText("Scope:")
                values = ["All"] + sorted(
                    {row.get("scope", "") for row in self.modules_data if row.get("scope")},
                    key=str.lower,
                )
            self.category_dropdown.addItems(values)
            self.category_dropdown.setMinimumWidth(190)
        finally:
            self.category_dropdown.blockSignals(blocked)

    def _filter_match(self, record, selected):
        if selected == "All":
            return True
        if self.current_view == VIEW_DESKTOP_ENTRIES:
            return record.get("visible") == selected
        if self.current_view == VIEW_ACTIONS:
            return record.get("application") == selected
        if self.current_view == VIEW_MIME_ASSOCIATIONS:
            return record.get("status") == selected
        return record.get("scope") == selected

    def filter_modules(self, *_):
        if not hasattr(self, "current_view"):
            return
        query = self.search_entry.text().strip().lower()
        selected = self.category_dropdown.currentText() or "All"
        matches = []
        for record in self.modules_data:
            if not self._filter_match(record, selected):
                continue
            searchable = []
            for key, value in record.items():
                if str(key).startswith("_") or isinstance(value, (dict, set)):
                    continue
                searchable.append(str(value))
            if query and not any(query in value.lower() for value in searchable):
                continue
            matches.append(record)

        blocked = self.table.blockSignals(True)
        try:
            self.clear_info()
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self.table.setSortingEnabled(False)
            self.table.setRowCount(len(matches))
            for row_index, record in enumerate(matches):
                for column_index, (column_id, _label, _width, alignment) in enumerate(self.columns):
                    value = record.get(column_id, "")
                    display = f"{value:,}" if column_id in self.NUMERIC_COLUMNS and isinstance(value, int) else str(value)
                    item = QTableWidgetItem(display)
                    if column_index == 0:
                        item.setData(Qt.ItemDataRole.UserRole, record)
                    item.setTextAlignment(
                        (Qt.AlignmentFlag.AlignRight if alignment == "right" else Qt.AlignmentFlag.AlignLeft)
                        | Qt.AlignmentFlag.AlignVCenter
                    )
                    self.table.setItem(row_index, column_index, item)
            if matches:
                self.table.setCurrentCell(0, 0)
                self.table.selectRow(0)
        finally:
            self.table.blockSignals(blocked)

        if matches:
            self.on_module_select()
        else:
            self.clear_info()

        if query or selected != "All":
            self.filter_status_lbl.setText(f"(Showing {len(matches)} of {len(self.modules_data)} items)")
        else:
            self.filter_status_lbl.clear()

    def on_module_select(self):
        record = self.current_record()
        if record is None:
            self.clear_info()
            return
        self._render_record_details(record)
        self.set_status(f"Inspecting {self.current_view}: {self._record_identity(record)}")

    def _record_identity(self, record):
        if self.current_view == VIEW_DESKTOP_ENTRIES:
            return record.get("desktop_id", "record")
        if self.current_view == VIEW_ACTIONS:
            return f"{record.get('desktop_id', '')} / {record.get('action', '')}".strip(" /")
        if self.current_view == VIEW_MIME_ASSOCIATIONS:
            return record.get("mime_type", "record")
        return record.get("path", record.get("file", "record"))

    def _render_record_details(self, record):
        self.clear_info()
        if self.current_view == VIEW_DESKTOP_ENTRIES:
            self._render_desktop_entry(record)
        elif self.current_view == VIEW_ACTIONS:
            self._render_action(record)
        elif self.current_view == VIEW_MIME_ASSOCIATIONS:
            self._render_mime_association(record)
        else:
            self._render_mime_configuration(record)
        self.info_text.moveCursor(QTextCursor.MoveOperation.Start)
        self.info_text.verticalScrollBar().setValue(0)

    def _render_desktop_entry(self, record):
        self.append_info("--- Desktop Entry ---\n")
        self.append_key_value("Desktop File ID", record.get("desktop_id", ""))
        self.append_key_value("Source Path", record.get("source", ""))
        self.append_key_value("Source Scope", record.get("source_scope", ""))
        self.append_key_value("Precedence Index", record.get("root_index", 0))
        self.append_key_value("Visibility", record.get("visible", ""))
        self.append_key_value("Visibility Explanation", record.get("visibility_reason", ""))
        self.append_key_value("Current Desktop", ":".join(_desktop_names()) or "Not set")
        if record.get("parse_error"):
            self.append_key_value("Parse Error", record["parse_error"])
        if record.get("same_precedence_collisions"):
            self.append_key_value("Same-precedence ID Collisions", "; ".join(record["same_precedence_collisions"]))
            self.append_info("Desktop ID note: when two paths in the same XDG applications root collapse to the same desktop-file ID, the specification leaves which file is selected undefined; this inspector shows one deterministic copy and reports the collision.\n")
        if record.get("shadowed_sources"):
            self.append_key_value("Shadowed Copies", "; ".join(record["shadowed_sources"]))

        self.append_info("\n--- Reported [Desktop Entry] Fields ---\n")
        group = record.get("groups", {}).get("Desktop Entry", {})
        if group:
            for key, value in group.items():
                self.append_key_value(key, value)
        else:
            self.append_info("No valid [Desktop Entry] group was parsed.\n")

        self.append_info("\n--- Interpretation ---\n")
        self.append_key_value("Localized Name", record.get("name") or "Not reported")
        self.append_key_value("Localized GenericName", record.get("generic_name") or "Not reported")
        if record.get("try_exec"):
            available = record.get("try_exec_available")
            self.append_key_value("TryExec Availability", "Available" if available else "Unavailable")
            self.append_info("TryExec note: availability is checked without executing the command.\n")
        self.append_key_value("D-Bus Activatable", "Yes" if record.get("dbus_activatable") else "No")
        self.append_key_value("Declared Actions", ";".join(record.get("actions", [])) or "None reported")
        self.append_key_value("Declared MIME Types", ";".join(record.get("mime_types", [])) or "None reported")
        self.append_info("Exec note: Desktop Integration never executes Exec or TryExec.\n")

    def _render_action(self, record):
        self.append_info("--- Desktop Action ---\n")
        self.append_key_value("Desktop File ID", record.get("desktop_id", ""))
        self.append_key_value("Application", record.get("application", ""))
        self.append_key_value("Action Identifier", record.get("action", ""))
        self.append_key_value("Source Path", record.get("source", ""))
        self.append_key_value("Parent Visibility", record.get("parent_visibility", ""))
        self.append_key_value("Action Group Present", "Yes" if record.get("group_present") else "No")

        group_name = f"Desktop Action {record.get('action', '')}"
        parent = self.collection.get("effective_by_id", {}).get(record.get("desktop_id"), {})
        group = parent.get("groups", {}).get(group_name, {})
        self.append_info(f"\n--- [{group_name}] Fields ---\n")
        if group:
            for key, value in group.items():
                self.append_key_value(key, value)
        else:
            self.append_info("The action is declared by Actions= but the corresponding action group was not parsed.\n")
        if not record.get("name"):
            self.append_info("Validity note: a desktop action without Name is incomplete under the Desktop Entry specification.\n")
        self.append_info("Invocation note: this applet reports declared action registration only and never executes its Exec command.\n")

    def _render_mime_association(self, record):
        self.append_info("--- MIME Association Resolution ---\n")
        self.append_key_value("MIME Type", record.get("mime_type", ""))
        self.append_key_value("Status", record.get("status", ""))
        self.append_key_value("Default Application", record.get("default_application") or "None resolved")
        if record.get("default_application"):
            desktop = self.collection.get("effective_by_id", {}).get(record["default_application"])
            self.append_key_value("Default Application Name", (desktop or {}).get("name") or "Not reported")
        self.append_key_value("Default Source / Resolution", record.get("source_resolution", ""))
        self.append_key_value("Associated Applications", record.get("associated_applications") or "None resolved")
        self.append_key_value("Configured Added Associations", record.get("added") or "None")
        self.append_key_value("Configured Removed Associations", record.get("removed") or "None")

        self.append_info("\n--- Default Candidate Chain ---\n")
        chains = record.get("default_candidate_chain", [])
        if chains:
            for line in chains:
                self.append_info(line + "\n")
        else:
            self.append_info("No explicit default candidates were reported for this MIME type.\n")

        self.append_info("\n--- Resolution Trace ---\n")
        trace = record.get("resolution_trace", [])
        if trace:
            for line in trace:
                self.append_info(line + "\n")
        else:
            self.append_info("No association evidence was resolved.\n")

        missing = record.get("missing_refs", [])
        if missing:
            self.append_info("\n--- Missing / Unresolvable References ---\n")
            for kind, desktop_id, source, state in missing:
                self.append_info(f"{kind}: {desktop_id} — {state} — {source}\n")
        self.append_info(
            "Resolution note: defaults, added associations, removed associations and desktop-entry MimeType declarations remain distinct inputs."
            " Explicit default candidates fall through until a resolvable associated desktop ID is found; otherwise the most-preferred resolved association is used. MIME inheritance is considered from specific to generic types.\n"
        )

    def _render_mime_configuration(self, record):
        self.append_info("--- MIME Configuration Source ---\n")
        self.append_key_value("File", record.get("file", ""))
        self.append_key_value("Path", record.get("path", ""))
        self.append_key_value("Lookup Position", record.get("lookup_index", ""))
        self.append_key_value("Scope", record.get("scope", ""))
        self.append_key_value("Desktop", record.get("desktop", ""))
        self.append_key_value("Desktop-specific", "Yes" if record.get("desktop_specific") else "No")
        self.append_key_value("Effective / Shadowed", record.get("effective", ""))
        if record.get("parse_error"):
            self.append_key_value("Parse Error", record["parse_error"])

        if record.get("desktop_specific"):
            self.append_info(
                "Desktop-specific note: the MIME Applications specification permits desktop-specific files to specify defaults only; Added/Removed Associations are not applied from them.\n"
            )

        groups = record.get("groups", {})
        if not groups:
            self.append_info("\nNo recognized configuration groups were parsed.\n")
            return
        for group_name, values in groups.items():
            self.append_info(f"\n--- [{group_name}] ---\n")
            if values:
                for key, value in values.items():
                    self.append_key_value(key, value)
            else:
                self.append_info("(empty)\n")

    @staticmethod
    def sources_text():
        data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
        data_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
        config_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        config_dirs = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
        desktop = os.environ.get("XDG_CURRENT_DESKTOP") or "(not set)"
        return (
            "Desktop Integration is read-only. It parses desktop registration and MIME configuration without launching applications or changing defaults.\n\n"
            "Desktop entry inventory and desktop-file ID precedence\n"
            f"  XDG_DATA_HOME={data_home}\n"
            f"  XDG_DATA_DIRS={data_dirs}\n"
            "  applications/ directories beneath those XDG data roots\n\n"
            "MIME association/default configuration lookup\n"
            f"  XDG_CONFIG_HOME={config_home}\n"
            f"  XDG_CONFIG_DIRS={config_dirs}\n"
            f"  XDG_CURRENT_DESKTOP={desktop}\n"
            "  desktop-specific *-mimeapps.list files\n"
            "  mimeapps.list files\n"
            "  deprecated/compatibility application-directory locations defined by the MIME Applications specification\n\n"
            "Shared MIME-info inheritance (installed indexes only)\n"
            "  mime/aliases and mime/subclasses under the XDG data roots\n\n"
            "Technical reference basis\n"
            "  https://specifications.freedesktop.org/desktop-entry/latest/\n"
            "  https://specifications.freedesktop.org/mime-apps/latest-single/\n\n"
            "Important distinctions\n"
            "  Hidden, NoDisplay, OnlyShowIn and NotShowIn are reported separately and are not conflated.\n"
            "  Exec and action Exec values are displayed only; Desktop Integration never executes them.\n"
            "  TryExec may be checked for file/PATH availability but is never executed.\n"
            "  MIME defaults, added associations, removed associations and desktop-entry MimeType declarations are resolved as separate inputs.\n"
            "  Missing referenced desktop IDs are reported rather than silently removed from the inspection record."
        )

    def show_options(self):
        DesktopIntegrationOptionsDialog(self).exec()

    def _add_shortcut(self, sequence, callback):
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)
        return shortcut

    def _install_shortcuts(self):
        self._add_shortcut("F3", self.toggle_metadata_panel)
        self._add_shortcut("Ctrl+L", self.toggle_details_orientation)
        self._add_shortcut("Ctrl+/", self.show_options)
        self._add_shortcut("Shift+F5", self.load_modules)

    def toggle_details_orientation(self):
        target = "vertical" if self.details_orientation == "horizontal" else "horizontal"
        self.set_details_orientation(target, persist=True)

    def _load_metadata_panel_visibility(self):
        settings = QSettings("brunonlinespace", "Desktop Integration")
        value = settings.value("metadata_panel_visible", True)
        if isinstance(value, str):
            visible = value.strip().lower() not in {"0", "false", "no", "off"}
        else:
            visible = bool(value)
        self.set_metadata_panel_visible(visible, persist=False)

    def set_metadata_panel_visible(self, visible, *, persist=True):
        self.metadata_panel_visible = bool(visible)
        details_panel = self.splitter.widget(1)
        if details_panel is not None:
            details_panel.setVisible(self.metadata_panel_visible)
        if self.metadata_panel_visible:
            self.set_details_orientation(self.details_orientation, persist=False)
        if persist:
            settings = QSettings("brunonlinespace", "Desktop Integration")
            settings.setValue("metadata_panel_visible", self.metadata_panel_visible)
            settings.sync()

    def toggle_metadata_panel(self):
        self.set_metadata_panel_visible(not self.metadata_panel_visible, persist=True)

    def _load_details_orientation(self):
        settings = QSettings("brunonlinespace", "Desktop Integration")
        orientation = str(settings.value("details_orientation", "horizontal") or "horizontal").lower()
        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"
        self.set_details_orientation(orientation, persist=False)

    def set_details_orientation(self, orientation, *, persist=True):
        orientation = str(orientation or "horizontal").lower()
        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"
        self.details_orientation = orientation
        details_panel = self.splitter.widget(1)
        details_layout = details_panel.layout() if details_panel is not None else None
        if orientation == "vertical":
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            if details_layout is not None:
                details_layout.setContentsMargins(0, 8, 0, 0)
            self.splitter.setSizes([430, 330])
        else:
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            if details_layout is not None:
                details_layout.setContentsMargins(8, 0, 0, 0)
            self.splitter.setSizes([480, 700])
        if persist:
            settings = QSettings("brunonlinespace", "Desktop Integration")
            settings.setValue("details_orientation", orientation)
            settings.sync()

    def metadata_details_for_export(self, record):
        original_info = self.info_text
        temp = QTextEdit()
        self.info_text = temp
        try:
            self._render_record_details(record)
            return temp.toPlainText()
        finally:
            self.info_text = original_info


def create_applet(parent=None):
    return DesktopIntegrationTab(parent=parent)


def run_standalone():
    import sys
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMainWindow
    from ._support import STANDALONE_STYLESHEET

    app = QApplication(sys.argv)
    app.setApplicationName(APPLET_NAME)
    app.setOrganizationName(APPLET_AUTHOR)
    icon_path = Path(__file__).resolve().parent / "icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(STANDALONE_STYLESHEET)
    window = QMainWindow()
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowTitle(f"{APPLET_NAME} {APPLET_VERSION} — LinSpectacles Applet")
    window.resize(1200, 780)
    window.setCentralWidget(create_applet())
    window.show()
    return app.exec()
