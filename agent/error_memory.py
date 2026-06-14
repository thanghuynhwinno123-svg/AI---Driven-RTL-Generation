"""Compact error packets and long-term error memory for multi-agent runs."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


MEMORY_DIR = Path("memory/errors")
RAW_DIR = MEMORY_DIR / "raw"
EVENTS_FILE = MEMORY_DIR / "events.jsonl"
INDEX_FILE = MEMORY_DIR / "index.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: Any, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def _fingerprint_error(source: str, error: Dict[str, Any]) -> str:
    stable = "|".join(
        [
            source,
            str(error.get("source") or ""),
            str(error.get("kind") or ""),
            str(error.get("file") or ""),
            str(error.get("line") or 0),
            _normalize_text(error.get("message")),
        ]
    )
    return hashlib.sha1(stable.encode("utf-8")).hexdigest()[:16]


def _summary_from_errors(source: str, diagnosis: Dict[str, Any], errors: List[Dict[str, Any]]) -> str:
    if diagnosis.get("passed"):
        return f"{source} passed."
    if not errors:
        return f"{source} did not pass, but no concrete error was extracted."
    first = errors[0]
    location = str(first.get("file") or "").strip()
    line = int(first.get("line") or 0)
    if location and line:
        location = f"{location}:{line}"
    message = _normalize_text(first.get("message"), 180)
    return f"{source} failed at {location or 'unknown location'}: {message}"


def compact_diagnosis_packet(
    *,
    source: str,
    diagnosis: Dict[str, Any],
    module: str = "",
    repair_target_hint: str = "AUTO",
    debug_note: str = "",
    max_errors: int = 5,
    max_context_chars: int = 1200,
) -> Dict[str, Any]:
    """Return a bounded packet suitable for prompts and memory indexing."""
    raw_errors = diagnosis.get("errors", []) if isinstance(diagnosis, dict) else []
    compact_errors: List[Dict[str, Any]] = []
    for error in raw_errors[:max_errors]:
        if not isinstance(error, dict):
            continue
        compact = {
            "kind": error.get("kind") or "unknown",
            "source": error.get("source") or source,
            "severity": error.get("severity") or "error",
            "file": error.get("file") or "",
            "line": int(error.get("line") or 0),
            "is_source_location": bool(error.get("is_source_location")),
            "is_infrastructure": bool(error.get("is_infrastructure")),
            "message": _normalize_text(error.get("message"), 500),
            "context_excerpt": _normalize_text(error.get("context"), max_context_chars),
        }
        compact["fingerprint"] = _fingerprint_error(source, compact)
        compact_errors.append(compact)

    packet = {
        "schema_version": "1.0",
        "source": source,
        "tool": diagnosis.get("tool") if isinstance(diagnosis, dict) else source,
        "module": module,
        "passed": bool(diagnosis.get("passed")) if isinstance(diagnosis, dict) else False,
        "status": diagnosis.get("status") if isinstance(diagnosis, dict) else "fail",
        "summary": _summary_from_errors(source, diagnosis if isinstance(diagnosis, dict) else {}, compact_errors),
        "repair_target_hint": repair_target_hint,
        "errors": compact_errors,
        "debug_note": _normalize_text(debug_note, 600),
        "created_at": _utc_now(),
    }
    for key in ("has_finish", "has_vcs_simulation_report"):
        if isinstance(diagnosis, dict) and key in diagnosis:
            packet[key] = bool(diagnosis.get(key))
    return packet


def format_repair_context(packet: Optional[Dict[str, Any]], related_memories: Optional[Iterable[Dict[str, Any]]] = None) -> str:
    if not packet:
        return ""
    lines = [
        "[COMPACT ERROR PACKET]",
        f"Source: {packet.get('source')}",
        f"Tool: {packet.get('tool')}",
        f"Module: {packet.get('module')}",
        f"Passed: {packet.get('passed')}",
        f"Repair target hint: {packet.get('repair_target_hint')}",
        f"Summary: {packet.get('summary')}",
    ]
    if packet.get("debug_note"):
        lines.append(f"Debug note: {packet.get('debug_note')}")
    errors = packet.get("errors") or []
    if errors:
        lines.append("Errors:")
        for error in errors:
            location = error.get("file") or "unknown"
            if error.get("line"):
                location = f"{location}:{error.get('line')}"
            lines.append(f"- [{error.get('kind')}] {location}: {error.get('message')}")
            if error.get("context_excerpt"):
                lines.append(f"  excerpt: {error.get('context_excerpt')}")
    memories = list(related_memories or [])
    if memories:
        lines.append("Related long-term memories:")
        for memory in memories:
            lines.append(
                "- "
                f"seen={memory.get('count', 1)} "
                f"last_seen={memory.get('last_seen', '')} "
                f"summary={memory.get('summary', '')} "
                f"resolution={memory.get('resolution', '')}"
            )
    return "\n".join(lines)


def summarize_tb_oracle(tb_code: str) -> str:
    code = tb_code or ""
    checks = {
        "has_error_call": bool(re.search(r"\$error\b", code)),
        "has_fatal_call": bool(re.search(r"\$fatal\b", code)),
        "has_assert_statement": bool(re.search(r"\bassert\s*\(", code)),
        "has_compare_operator": bool(re.search(r"(!==|!=|===|==)", code)),
        "has_fail_prefix_display": bool(re.search(r'"\s*FAIL\s*:', code, re.IGNORECASE)),
        "has_pass_prefix_display": bool(re.search(r'"\s*PASS\s*:', code, re.IGNORECASE)),
        "has_error_counter": bool(re.search(r"\b(?:int|integer)\s+\w*errors?\w*\b", code, re.IGNORECASE)),
        "has_finish_call": bool(re.search(r"\$finish\b", code)),
        "has_watchdog": "WATCHDOG_TIMEOUT" in code,
    }
    return json.dumps(checks, ensure_ascii=False, indent=2)


def add_debug_error(packet: Dict[str, Any], *, kind: str, message: str, repair_target_hint: str = "AUTO") -> Dict[str, Any]:
    packet["passed"] = False
    packet["status"] = "debug_fail"
    packet["repair_target_hint"] = repair_target_hint
    error = {
        "kind": kind,
        "source": "debug_agent",
        "severity": "error",
        "file": "",
        "line": 0,
        "is_source_location": False,
        "is_infrastructure": False,
        "message": _normalize_text(message, 500),
        "context_excerpt": "",
    }
    error["fingerprint"] = _fingerprint_error(str(packet.get("source") or "debug"), error)
    packet.setdefault("errors", []).append(error)
    packet["summary"] = _summary_from_errors(str(packet.get("source") or "debug"), packet, packet["errors"])
    return packet


def _load_index() -> Dict[str, Any]:
    try:
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": "1.0", "errors": {}}


def _write_index(index: Dict[str, Any]) -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def record_error_packet(packet: Dict[str, Any], raw_archive_path: str = "") -> None:
    if not packet or packet.get("passed"):
        return
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": _utc_now(),
        "source": packet.get("source"),
        "tool": packet.get("tool"),
        "module": packet.get("module"),
        "summary": packet.get("summary"),
        "repair_target_hint": packet.get("repair_target_hint"),
        "raw_archive_path": raw_archive_path,
        "errors": packet.get("errors", []),
    }
    with EVENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    index = _load_index()
    errors_index = index.setdefault("errors", {})
    for error in packet.get("errors", []):
        fp = error.get("fingerprint")
        if not fp:
            continue
        current = errors_index.get(fp, {})
        count = int(current.get("count") or 0) + 1
        files = sorted(set((current.get("files") or []) + [error.get("file") or ""]))
        modules = sorted(set((current.get("modules") or []) + [packet.get("module") or ""]))
        errors_index[fp] = {
            "fingerprint": fp,
            "count": count,
            "first_seen": current.get("first_seen") or event["timestamp"],
            "last_seen": event["timestamp"],
            "source": error.get("source") or packet.get("source"),
            "kind": error.get("kind"),
            "message": error.get("message"),
            "summary": packet.get("summary"),
            "repair_target_hint": packet.get("repair_target_hint"),
            "files": [item for item in files if item],
            "modules": [item for item in modules if item],
            "resolution": current.get("resolution", ""),
            "raw_archive_path": raw_archive_path or current.get("raw_archive_path", ""),
        }
    _write_index(index)


def retrieve_related_memories(packet: Optional[Dict[str, Any]], limit: int = 3) -> List[Dict[str, Any]]:
    if not packet:
        return []
    index = _load_index()
    errors_index = index.get("errors", {})
    fingerprints = {error.get("fingerprint") for error in packet.get("errors", []) if error.get("fingerprint")}
    files = {error.get("file") for error in packet.get("errors", []) if error.get("file")}
    module = packet.get("module")
    scored = []
    for item in errors_index.values():
        score = 0
        if item.get("fingerprint") in fingerprints:
            score += 10
        if files.intersection(set(item.get("files") or [])):
            score += 3
        if module and module in set(item.get("modules") or []):
            score += 2
        if item.get("kind") in {error.get("kind") for error in packet.get("errors", [])}:
            score += 1
        if score:
            scored.append((score, int(item.get("count") or 0), item))
    scored.sort(key=lambda row: (row[0], row[1], row[2].get("last_seen", "")), reverse=True)
    return [item for _, _, item in scored[:limit]]


def archive_raw_error_log(title: str, content: str) -> str:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1((content or "").encode("utf-8")).hexdigest()[:12]
    safe_title = re.sub(r"[^A-Za-z0-9_.-]+", "_", title.strip())[:80] or "error"
    path = RAW_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{safe_title}_{digest}.log"
    path.write_text(content or "", encoding="utf-8")
    return str(path)
