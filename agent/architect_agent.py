import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage

try:
    import jsonschema
except Exception:  # pragma: no cover - optional dependency fallback
    jsonschema = None

from .common import extract_json_object, stable_json
from .model_config import model_for

IR_DIR = Path("build_state/ir")
DB_PATH = Path("build_state/build_state.db")

IR_SCHEMA = {
    "type": "object",
    "required": [
        "schema_version",
        "module",
        "module_name",
        "ports",
        "parameters",
        "timing_contract",
        "signal_ownership",
    ],
    "properties": {
        "schema_version": {"type": "string", "minLength": 1},
        "module": {"type": "string", "minLength": 1},
        "module_name": {"type": "string", "minLength": 1},
        "ports": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["name", "direction", "width"],
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "direction": {"type": "string", "enum": ["input", "output", "inout"]},
                    "width": {
                        "anyOf": [
                            {"type": "integer", "minimum": 1},
                            {"type": "string", "minLength": 1},
                        ]
                    },
                    "type": {"type": "string"},
                    "signed": {"type": "boolean"},
                    "description": {"type": "string"},
                },
                "additionalProperties": True,
            },
        },
        "parameters": {"type": "array"},
        "timing_contract": {"type": "object"},
        "signal_ownership": {"type": "array"},
        "dependencies": {"type": "array"},
        "notes": {"type": "string"},
    },
    "additionalProperties": True,
}

FALLBACK_SCHEMA = {
    "required": ["schema_version", "module", "module_name", "ports", "parameters", "timing_contract", "signal_ownership"],
    "ports_required": ["name", "direction", "width"],
    "directions": {"input", "output", "inout"},
}


def ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS frozen_ir (module TEXT PRIMARY KEY, sha256 TEXT NOT NULL, json TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        conn.commit()



def normalize_ir(ir):
    clean = dict(ir or {})
    clean.setdefault("schema_version", "1.0")

    module_name = clean.get("module_name") or clean.get("module") or clean.get("name")
    if module_name is not None:
        module_name = str(module_name).strip()
        clean["module"] = module_name
        clean["module_name"] = module_name

    clean.setdefault("ports", [])
    clean.setdefault("parameters", [])
    clean.setdefault("timing_contract", {})
    clean.setdefault("signal_ownership", [])

    normalized_ports = []
    for port in clean.get("ports") or []:
        if isinstance(port, str):
            name = port.strip()
            if name:
                normalized_ports.append({"name": name, "direction": "input", "width": 1, "description": "normalized from string"})
            continue
        if not isinstance(port, dict):
            continue
        name = port.get("name") or port.get("port") or port.get("signal")
        if not name:
            continue
        direction = str(port.get("direction") or port.get("dir") or "input").lower()
        if direction not in {"input", "output", "inout"}:
            direction = "input"
        if "width" in port:
            width = port.get("width")
        elif "bits" in port:
            width = port.get("bits")
        elif "packed" in port:
            width = port.get("packed")
        else:
            width = 1
        normalized_ports.append({**port, "name": str(name).strip(), "direction": direction, "width": width})
    clean["ports"] = normalized_ports

    ownership = clean.get("signal_ownership")
    if isinstance(ownership, dict):
        clean["signal_ownership"] = [
            {"signal": str(signal), "owner": str(owner)} for signal, owner in ownership.items()
        ]
    elif isinstance(ownership, list):
        normalized_ownership = []
        for item in ownership:
            if isinstance(item, dict):
                signal = item.get("signal") or item.get("name") or item.get("port")
                owner = item.get("owner") or item.get("module") or clean.get("module")
                if signal:
                    normalized_ownership.append({**item, "signal": str(signal), "owner": str(owner)})
            elif isinstance(item, str):
                normalized_ownership.append({"signal": item, "owner": clean.get("module")})
        clean["signal_ownership"] = normalized_ownership
    else:
        clean["signal_ownership"] = []

    if not isinstance(clean.get("parameters"), list):
        params = clean.get("parameters") or {}
        clean["parameters"] = [{"name": str(k), "value": v} for k, v in params.items()] if isinstance(params, dict) else []
    if not isinstance(clean.get("timing_contract"), dict):
        clean["timing_contract"] = {"notes": str(clean.get("timing_contract"))}
    return clean

def validate_ir(ir):
    if jsonschema is not None:
        jsonschema.validate(instance=ir, schema=IR_SCHEMA)
    else:
        for key in FALLBACK_SCHEMA["required"]:
            if key not in ir:
                raise ValueError(f"IR missing required key: {key}")
        if not isinstance(ir["ports"], list):
            raise ValueError("IR ports must be a list")
        if len(ir["ports"]) < 1:
            raise ValueError("IR ports must contain at least one port")
        if not isinstance(ir.get("parameters"), list):
            raise ValueError("IR parameters must be a list")
        if not isinstance(ir.get("signal_ownership"), list):
            raise ValueError("IR signal_ownership must be a list")
        if not isinstance(ir.get("timing_contract"), dict):
            raise ValueError("IR timing_contract must be an object")
        for port in ir["ports"]:
            if not isinstance(port, dict):
                raise ValueError(f"IR port must be an object: {port}")
            for key in FALLBACK_SCHEMA["ports_required"]:
                if key not in port:
                    raise ValueError(f"IR port missing {key}: {port}")
            if port["direction"] not in FALLBACK_SCHEMA["directions"]:
                raise ValueError(f"Invalid port direction: {port['direction']}")

    module = str(ir.get("module") or "").strip()
    module_name = str(ir.get("module_name") or "").strip()
    if not module or module == "unknown_module":
        raise ValueError("IR module must be a non-empty real module name")
    if not module_name or module_name == "unknown_module":
        raise ValueError("IR module_name must be a non-empty real module name")
    if module != module_name:
        raise ValueError(f"IR module/module_name mismatch: module={module} module_name={module_name}")
    if not isinstance(ir.get("ports"), list) or len(ir["ports"]) < 1:
        raise ValueError("IR ports must contain at least one explicit port")
    if not isinstance(ir.get("parameters"), list):
        raise ValueError("IR parameters must be an array")
    if not isinstance(ir.get("signal_ownership"), list):
        raise ValueError("IR signal_ownership must be an array")
    if not isinstance(ir.get("timing_contract"), dict):
        raise ValueError("IR timing_contract must be an object")

    names = set()
    for port in ir["ports"]:
        name = str(port.get("name") or "").strip()
        if not name:
            raise ValueError(f"IR port name must be non-empty: {port}")
        if name in names:
            raise ValueError(f"Duplicate IR port name: {name}")
        names.add(name)
        direction = port.get("direction")
        if direction not in FALLBACK_SCHEMA["directions"]:
            raise ValueError(f"Invalid port direction: {direction}")
        width = port.get("width")
        if isinstance(width, int):
            if width < 1:
                raise ValueError(f"IR port width must be >= 1: {port}")
        elif isinstance(width, str):
            if not width.strip():
                raise ValueError(f"IR port width expression must be non-empty: {port}")
        else:
            raise ValueError(f"IR port width must be integer >= 1 or non-empty expression string: {port}")
    return True

def freeze_ir(ir):
    ir = normalize_ir(ir)
    validate_ir(ir)
    ensure_db()
    IR_DIR.mkdir(parents=True, exist_ok=True)
    canonical = stable_json(ir)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    module = ir["module"]
    path = IR_DIR / f"{module}.{digest}.json"
    path.write_text(json.dumps(ir, indent=2, ensure_ascii=False), encoding="utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO frozen_ir(module, sha256, json, created_at) VALUES (?, ?, ?, ?)",
            (module, digest, canonical, time.time()),
        )
        conn.commit()
    return {"module": module, "sha256": digest, "path": str(path), "ir": ir}


def load_frozen_ir(module):
    ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT sha256, json FROM frozen_ir WHERE module=?", (module,)).fetchone()
    if not row:
        return None
    ir = normalize_ir(json.loads(row[1]))
    validate_ir(ir)
    return {"module": module, "sha256": row[0], "ir": ir}



class ArchitectAgent:
    def __init__(self, invoke_llm, log, model=None):
        self.invoke_llm = invoke_llm
        self.log = log
        self.model = model or model_for("architect")
        self.max_retries = int(os.getenv("ARCHITECT_IR_RETRIES", "3"))

    async def create_or_load_ir(self, module_name: str, user_prompt: str, dag: dict, rag_context: str):
        try:
            existing = load_frozen_ir(module_name)
        except Exception as exc:
            existing = None
            await self.log(f"[!] ArchitectAgent: frozen IR cũ cho {module_name} không hợp lệ theo schema mới: {exc}. Sẽ tạo lại IR.")
        if existing:
            await self.log(f"[*] ArchitectAgent: dùng frozen IR cho {module_name} sha256={existing['sha256'][:12]}")
            return existing

        node = next((n for n in dag.get("nodes", []) if n.get("id") == module_name), {"id": module_name})
        sys_msg = (
            "You are ArchitectAgent. Produce immutable SystemVerilog module IR as strict JSON. Do not write RTL. "
            "Every field must match the requested JSON types exactly; especially, notes must be one string, not an array."
        )
        last_error = None
        retry_feedback = ""

        for attempt in range(1, self.max_retries + 1):
            feedback_block = f"\n[PREVIOUS IR ERROR - MUST FIX]\n{retry_feedback}\n" if retry_feedback else ""
            hum_msg = (
                f"Request: {user_prompt}\nModule: {module_name}\nDAG node: {json.dumps(node, ensure_ascii=False)}\n\n"
                f"Build DAG: {json.dumps(dag, ensure_ascii=False)}\n\nRAG Context:\n{rag_context}\n"
                f"{feedback_block}\n"
                "Return ONLY JSON with keys: schema_version='1.0', module, module_name, ports, parameters, timing_contract, signal_ownership, notes. "
                f"Both module and module_name MUST equal '{module_name}'. "
                "ports MUST be a non-empty array. Each port MUST be {name,direction,width,description}; direction is input/output/inout; "
                "width is either an integer >= 1 or a non-empty expression string such as '[31:0]' or 'DATA_WIDTH'. "
                "parameters MUST be an array, signal_ownership MUST be an array, timing_contract MUST be an object. "
                "notes MUST be a single string paragraph. Do not return notes as an array/list of strings; join multiple notes into one string. "
                "Example notes value: \"This module controls reset handoff. It exposes deterministic status for debug.\" "
                "This IR is architectural truth and will be frozen. Do not leave the interface ambiguous or empty."
            )
            try:
                response = await self.invoke_llm([SystemMessage(content=sys_msg), HumanMessage(content=hum_msg)], model=self.model)
                ir = extract_json_object(response.content)
                ir.setdefault("module", module_name)
                ir.setdefault("module_name", module_name)
                ir = normalize_ir(ir)
                frozen = freeze_ir(ir)
                await self.log(f"[*] ArchitectAgent: frozen IR {module_name} sha256={frozen['sha256'][:12]} path={frozen['path']}")
                return frozen
            except Exception as exc:
                last_error = exc
                retry_feedback = (
                    f"IR attempt {attempt} failed schema/semantic validation: {exc}. "
                    "You must return a complete IR with a non-empty ports array and all required fields. "
                    "The notes field must be a single JSON string, never an array. "
                    "Do not return fallback, empty ports, prose, or Markdown."
                )
                await self.log(f"[!] ArchitectAgent: IR attempt {attempt}/{self.max_retries} không hợp lệ cho {module_name}: {exc}. Retry IR.")

        raise RuntimeError(f"ArchitectAgent could not create valid frozen IR for {module_name} after {self.max_retries} attempts: {last_error}")

def _strip_sv_comments(code):
    import re
    code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
    return re.sub(r'//.*?$', '', code, flags=re.MULTILINE)


def _split_port_items(port_text):
    items = []
    current = []
    bracket_depth = 0
    paren_depth = 0
    for ch in port_text:
        if ch == '[':
            bracket_depth += 1
        elif ch == ']' and bracket_depth:
            bracket_depth -= 1
        elif ch == '(':
            paren_depth += 1
        elif ch == ')' and paren_depth:
            paren_depth -= 1
        if ch == ',' and bracket_depth == 0 and paren_depth == 0:
            item = ''.join(current).strip()
            if item:
                items.append(item)
            current = []
        else:
            current.append(ch)
    item = ''.join(current).strip()
    if item:
        items.append(item)
    return items


def parse_sv_ports(code, module_name):
    import re
    clean = _strip_sv_comments(code)
    match = re.search(rf'\bmodule\s+{re.escape(module_name)}\s*(?:#\s*\([^;]*?\)\s*)?\((.*?)\)\s*;', clean, re.DOTALL)
    if not match:
        return None
    ports = []
    last_direction = ''
    last_width = '1'
    for raw in _split_port_items(match.group(1)):
        item = re.sub(r'\s*=\s*.*$', '', raw.strip())
        item = re.sub(r'\b(wire|reg|logic|signed|unsigned)\b', ' ', item)
        item = re.sub(r'\s+', ' ', item).strip()
        direction_match = re.match(r'\b(input|output|inout)\b\s*(.*)$', item, re.IGNORECASE)
        if direction_match:
            direction = direction_match.group(1).lower()
            rest = direction_match.group(2).strip()
            last_direction = direction
        else:
            direction = last_direction
            rest = item
        ranges = re.findall(r'\[[^\]]+\]', rest)
        width = ' '.join(ranges) if ranges else last_width
        if direction_match:
            last_width = width
        name_match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*$', rest)
        if name_match:
            ports.append({'name': name_match.group(1), 'direction': direction, 'width': width or '1'})
    return ports


def format_contract_validation_report(code, ir):
    module_name = ir.get('module') or ir.get('module_name')
    expected_ports = ir.get('ports') or []
    title = f"[IR CONTRACT VALIDATION]"
    if not module_name:
        return f"\n{title} FAIL: <unknown> | errors=1\n  ERROR 1: Frozen IR has no module/module_name.\n", False
    if not expected_ports:
        return f"\n{title} FAIL: {module_name} | errors=1\n  ERROR 1: Frozen IR has no explicit ports; refusing to validate an empty architectural contract.\n", False
    actual_ports = parse_sv_ports(code, module_name)
    errors = []
    if actual_ports is None:
        errors.append(f"Cannot find RTL module declaration for frozen IR module '{module_name}'.")
    else:
        expected_by_name = {p['name']: p for p in expected_ports}
        actual_by_name = {p['name']: p for p in actual_ports}
        missing = [name for name in expected_by_name if name not in actual_by_name]
        extra = [name for name in actual_by_name if name not in expected_by_name]
        if missing:
            errors.append(f"Missing IR port(s): {', '.join(missing)}.")
        if extra:
            errors.append(f"RTL added non-IR port(s): {', '.join(extra)}.")
        for name, expected in expected_by_name.items():
            actual = actual_by_name.get(name)
            if actual and actual.get('direction') != expected.get('direction'):
                errors.append(f"Port '{name}' direction mismatch: IR={expected.get('direction')} RTL={actual.get('direction')}.")
    status = 'PASS' if not errors else 'FAIL'
    report = f"\n{title} {status}: {module_name} | errors={len(errors)}\n"
    for idx, err in enumerate(errors, 1):
        report += f"  ERROR {idx}: {err}\n"
    if not errors:
        report += "  - RTL interface matches frozen IR port ownership.\n"
    return report, not errors
