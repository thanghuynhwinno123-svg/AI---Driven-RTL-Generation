"""Deterministic prompt composition for RTL and TB agents.

This module translates frozen architectural IR and build DAG data into concise,
human-readable prompt sections. It does not call an LLM and must not mutate the
IR. JSON remains the machine contract; summaries and checklists make that
contract operational for code-generation agents.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

RULE_DIR = Path("knowledge_base/rules")
RTL_CHECKLIST_FILE = RULE_DIR / "RTL_GENERATION_CHECKLIST.md"
TB_CHECKLIST_FILE = RULE_DIR / "TB_GENERATION_CHECKLIST.md"


def _read_optional(path: Path, fallback: str) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text if text else fallback
    except Exception:
        return fallback


def _compact_checklist(path: Path, fallback: str, title: str, keywords: List[str], max_items: int = 36) -> str:
    text = _read_optional(path, fallback)
    selected: List[str] = []
    seen = set()
    lowered_keywords = [kw.lower() for kw in keywords]

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        lower = line.lower()
        if not any(keyword in lower for keyword in lowered_keywords):
            continue
        if line in seen:
            continue
        selected.append(line)
        seen.add(line)
        if len(selected) >= max_items:
            break

    if not selected:
        selected = [
            line.strip()
            for line in fallback.splitlines()
            if line.strip().startswith("- ")
        ][:max_items]

    return "\n".join([title, f"Source: {path.as_posix()} (compact prompt subset)"] + selected)


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("module") or value.get("id") or value.get("port")
    return str(value or "").strip()


def _width_text(width: Any) -> str:
    if width is None:
        return "1"
    if isinstance(width, int):
        return "1" if width == 1 else f"[{width - 1}:0]"
    return str(width).strip() or "1"


def _port_line(port: Dict[str, Any]) -> str:
    name = _name(port)
    direction = str(port.get("direction") or "").strip() or "unknown"
    width = _width_text(port.get("width"))
    desc = str(port.get("description") or "").strip()
    suffix = f" - {desc}" if desc else ""
    return f"- {direction} {width} {name}{suffix}"


def split_ports(ir: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    grouped = {"input": [], "output": [], "inout": [], "unknown": []}
    for port in ir.get("ports") or []:
        if not isinstance(port, dict):
            continue
        direction = str(port.get("direction") or "unknown").lower()
        grouped.setdefault(direction, grouped["unknown"]).append(port)
    return grouped


def infer_clock_reset(ir: Dict[str, Any]) -> Dict[str, Optional[str]]:
    ports = [p for p in ir.get("ports") or [] if isinstance(p, dict)]
    inputs = [p for p in ports if str(p.get("direction") or "").lower() == "input"]
    names = [_name(p) for p in inputs]

    clock = next((n for n in names if n.lower() in {"clk", "clock", "i_clk", "clk_i"}), None)
    if clock is None:
        clock = next((n for n in names if "clk" in n.lower() or "clock" in n.lower()), None)

    reset = next((n for n in names if n.lower() in {"reset_n", "rst_n", "resetn", "rstn", "aresetn"}), None)
    if reset is None:
        reset = next((n for n in names if "reset" in n.lower() or "rst" in n.lower()), None)

    reset_active = None
    reset_assert = None
    reset_deassert = None
    reset_wait = None
    if reset:
        lname = reset.lower()
        active_low = lname.endswith("_n") or lname.endswith("n") or "reset_n" in lname or "rst_n" in lname
        if active_low:
            reset_active = "active-low"
            reset_assert = f"{reset} = 1'b0"
            reset_deassert = f"{reset} = 1'b1"
            reset_wait = f"@(posedge {reset}) or wait({reset} == 1'b1)"
        else:
            reset_active = "active-high"
            reset_assert = f"{reset} = 1'b1"
            reset_deassert = f"{reset} = 1'b0"
            reset_wait = f"@(negedge {reset}) or wait({reset} == 1'b0)"

    return {
        "clock": clock,
        "reset": reset,
        "reset_active": reset_active,
        "reset_assert": reset_assert,
        "reset_deassert": reset_deassert,
        "reset_wait": reset_wait,
    }


def _dag_entries(dag: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    entries: Dict[str, Dict[str, Any]] = {}
    for entry in dag.get("dag") or []:
        if isinstance(entry, dict):
            module = _name(entry.get("module") or entry.get("id") or entry.get("name"))
            if module:
                entries[module] = {
                    "depends_on": [_name(dep) for dep in _as_list(entry.get("depends_on")) if _name(dep)],
                    "level": entry.get("level"),
                }
    if entries:
        return entries

    for node in dag.get("nodes") or []:
        module = _name(node)
        if module:
            level = node.get("level") if isinstance(node, dict) else None
            entries.setdefault(module, {"depends_on": [], "level": level})
    for edge in dag.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = _name(edge.get("from"))
        dst = _name(edge.get("to"))
        if src and dst:
            entries.setdefault(dst, {"depends_on": [], "level": None})["depends_on"].append(src)
            entries.setdefault(src, {"depends_on": [], "level": None})
    return entries


def dag_context_summary(module_name: str, dag: Dict[str, Any]) -> str:
    entries = _dag_entries(dag or {})
    current = entries.get(module_name, {"depends_on": [], "level": None})
    depends_on = sorted(set(current.get("depends_on") or []))
    used_by = sorted(module for module, entry in entries.items() if module_name in set(entry.get("depends_on") or []))
    level = current.get("level")
    build_order = dag.get("build_order") or list(entries)

    lines = ["[BUILD CONTEXT SUMMARY]", f"Current module: {module_name}"]
    if level is not None:
        lines.append(f"Build level: {level}")
    lines.append(f"Direct dependencies: {', '.join(depends_on) if depends_on else 'none'}")
    lines.append(f"Direct dependents/users: {', '.join(used_by) if used_by else 'none'}")
    if build_order:
        nearby = [m for m in build_order if m in set(depends_on + [module_name] + used_by)]
        lines.append(f"Relevant build order slice: {', '.join(nearby) if nearby else module_name}")
    lines.append("Dependency rule: a parent/top depends on child modules; child modules must not depend on parent/top just because signals flow between them.")
    return "\n".join(lines)


def contract_summary(ir: Dict[str, Any], dag: Optional[Dict[str, Any]] = None) -> str:
    module = ir.get("module") or ir.get("module_name") or "<unknown>"
    ports = split_ports(ir)
    cr = infer_clock_reset(ir)
    timing = ir.get("timing_contract") if isinstance(ir.get("timing_contract"), dict) else {}
    params = ir.get("parameters") if isinstance(ir.get("parameters"), list) else []
    ownership = ir.get("signal_ownership") if isinstance(ir.get("signal_ownership"), list) else []

    lines = ["[CONTRACT SUMMARY]", f"Module: {module}"]
    if params:
        param_text = ", ".join(_name(p) or json.dumps(p, ensure_ascii=False) for p in params[:12])
        lines.append(f"Parameters: {param_text}")
    else:
        lines.append("Parameters: none")

    if cr["clock"]:
        lines.append(f"Clock: {cr['clock']} (initialize in TB before toggling)")
    else:
        lines.append("Clock: not explicit in IR")
    if cr["reset"]:
        lines.append(
            f"Reset: {cr['reset']} ({cr['reset_active']}); assert with `{cr['reset_assert']}`, "
            f"deassert with `{cr['reset_deassert']}`, wait for deassertion using `{cr['reset_wait']}`"
        )
    else:
        lines.append("Reset: not explicit in IR")

    lines.append("DUT inputs driven by TB: " + (", ".join(_name(p) for p in ports.get("input", [])) or "none"))
    lines.append("DUT outputs observed by TB: " + (", ".join(_name(p) for p in ports.get("output", [])) or "none"))
    if ports.get("inout"):
        lines.append("DUT inouts: " + ", ".join(_name(p) for p in ports.get("inout", [])))

    lines.append("Ports:")
    for direction in ("input", "output", "inout", "unknown"):
        for port in ports.get(direction, []):
            lines.append(_port_line(port))

    if timing:
        lines.append("Timing contract: " + json.dumps(timing, ensure_ascii=False, sort_keys=True))
    else:
        lines.append("Timing contract: unspecified; use conservative synchronous behavior if clock/reset exist.")
    if ownership:
        compact = ", ".join(
            f"{item.get('signal') or item.get('name')}->{item.get('owner') or item.get('module')}"
            for item in ownership[:16]
            if isinstance(item, dict)
        )
        if compact:
            lines.append(f"Signal ownership: {compact}")

    if dag:
        lines.append(dag_context_summary(str(module), dag))
    return "\n".join(lines)


def _notes_summary(ir: Dict[str, Any], max_chars: int = 900) -> str:
    notes = ir.get("notes", "")
    if isinstance(notes, list):
        text = " ".join(str(item).strip() for item in notes if str(item).strip())
    else:
        text = str(notes or "").strip()
    if not text:
        return "Notes: none"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    return f"Notes: {text}"


def tb_architectural_context(frozen_ir: Dict[str, Any], dag: Dict[str, Any]) -> str:
    ir = frozen_ir.get("ir", {})
    module = ir.get("module") or frozen_ir.get("module") or "<unknown>"
    sha = frozen_ir.get("sha256", "<unknown>")
    return "\n".join([
        "[TB ARCHITECTURAL CONTEXT - COMPACT]",
        f"Module: {module}",
        f"Frozen IR SHA256: {sha}",
        "Use this compact contract for TB generation. Do not invent, remove, or rename DUT ports.",
        contract_summary(ir, dag),
        _notes_summary(ir),
    ])


def immutable_ir_block(frozen_ir: Dict[str, Any]) -> str:
    ir = frozen_ir.get("ir", {})
    module = ir.get("module") or frozen_ir.get("module") or "<unknown>"
    sha = frozen_ir.get("sha256", "<unknown>")
    return (
        "[IMMUTABLE ARCHITECTURAL IR - DO NOT CHANGE]\n"
        f"Module: {module}\nSHA256: {sha}\n"
        f"{json.dumps(ir, indent=2, ensure_ascii=False)}\n"
        "RTL Agent and TB Agent may implement internal logic only. They must not invent, remove, or rename interface ports defined by this IR."
    )


def rtl_checklist() -> str:
    fallback = """[RTL GENERATION CHECKLIST]
- Implement exactly the module and ports defined by the frozen IR.
- Do not add, remove, rename, reorder semantically, or change direction of any IR port.
- Never assign to input ports inside RTL.
- Every output/internal net must have exactly one driver.
- Do not connect multiple module output ports to the same logic net.
- Do not mix procedural and structural drivers on the same signal.
- In always_comb, do not assign a selector/control signal based on itself.
- Case labels with multiple statements must use begin/end.
- Use conservative synchronous behavior when clock/reset exist in IR.
- Output the entire SystemVerilog file only."""
    return _compact_checklist(
        RTL_CHECKLIST_FILE,
        fallback,
        "[RTL GENERATION CHECKLIST - COMPACT]",
        [
            "frozen ir",
            "port",
            "input",
            "driver",
            "assign",
            "always_ff",
            "always_comb",
            "reset",
            "case",
            "declaration",
            "timescale",
            "literal",
            "output the entire",
        ],
    )


def tb_checklist() -> str:
    fallback = """[TB GENERATION CHECKLIST]
- Create a self-contained module named <dut>_tb with no input/output/inout ports.
- Declare all DUT inputs as internal logic driven by the TB.
- Declare all DUT outputs as observed internal logic.
- Initialize clock before any toggling, e.g. initial clk = 0; always #5 clk = ~clk;
- If reset is reset_n/rst_n active-low: assert 0, deassert 1, wait @(posedge reset_n) or wait(reset_n == 1'b1). Never wait @(negedge reset_n) for deassertion.
- If reset is active-high: assert 1, deassert 0, wait @(negedge reset) or wait(reset == 1'b0).
- After @(posedge clk) or any wait-clock task, insert #1 before checking registered DUT outputs, or use a clocking block with input skew.
- One-cycle shifted actual values, previous-beat data, or reset/default values immediately after a posedge check indicate possible TB sampling timing bugs.
- Normal test path must reach $finish before watchdog.
- For functional mismatches, do not call $error directly.
- On every functional mismatch, increment an integer error counter and print a line beginning with "FAIL_FUNC:" with module=, test_id=, test_name=, feature=, phase=, signal=, expected=, actual=, time=, cycle=, spec_rule=, inputs=, timing_context=, mismatch_type=.
- Print "PASS:" only at the end when errors == 0.
- If errors > 0, print a final "FAIL:" summary and call $fatal(1).
- Maintain watchdog progress variables current_test_id, current_test_name, current_phase, last_completed_test_id, waiting_for, and a cycle counter.
- Watchdog is a failure path only: print "FAIL_WATCHDOG:" with module=, current_test_id=, current_test_name=, current_phase=, last_completed_test_id=, cycle=, waiting_for=, timeout_cycles=, status_outputs=, then call $fatal(1).
- Include self-checking comparisons; do not only $display values.
- Do not emit duplicate or malformed directives such as a timescale line without backtick.
- Output the entire SystemVerilog testbench file only."""
    return _compact_checklist(
        TB_CHECKLIST_FILE,
        fallback,
        "[TB GENERATION CHECKLIST - COMPACT]",
        [
            "self-contained",
            "black box",
            "dut input",
            "dut output",
            "clock",
            "reset",
            "posedge",
            "#1",
            "sampling",
            "handshake",
            "fail_func",
            "mismatch",
            "error counter",
            "fail_watchdog",
            "current_test_id",
            "status_outputs",
            "watchdog",
            "$finish",
            "output the complete",
        ],
    )


def _diagnosis_block(diagnosis: str) -> str:
    diagnosis = (diagnosis or "").strip()
    return f"[STRUCTURED DIAGNOSIS JSON]\n{diagnosis}" if diagnosis else "[STRUCTURED DIAGNOSIS JSON]\nnone"


def _guidelines_block(coding_guidelines: str) -> str:
    return f"[SYSTEMVERILOG CODING GUIDELINES & RULES]\n{coding_guidelines.strip()}" if coding_guidelines else "[SYSTEMVERILOG CODING GUIDELINES & RULES]\nnone"


def compose_rtl_prompt(
    *,
    mode: str,
    module_name: str,
    frozen_ir: Dict[str, Any],
    dag: Dict[str, Any],
    instantiation_guide: str,
    coding_guidelines: str,
    current_rtl: str = "",
    diagnosis: str = "",
    error_rag: str = "",
    extra_hints: str = "",
) -> Tuple[str, str]:
    mode = mode.lower()
    if mode == "generate":
        system_prompt = "You are RTLAgent, a strict SystemVerilog RTL implementer. Follow the frozen IR exactly."
        task = f"Write SystemVerilog RTL ONLY for '{module_name}'. Output the entire file."
    else:
        system_prompt = "You are RTLAgent in repair mode. Fix only RTL issues proven by structured diagnosis."
        task = f"Repair the SystemVerilog RTL for '{module_name}'. Keep the frozen interface unchanged. Output the entire fixed file."

    ir = frozen_ir.get("ir", {})
    sections = [
        task,
        immutable_ir_block(frozen_ir),
        contract_summary(ir, dag),
        rtl_checklist(),
        instantiation_guide.strip() or "[DIRECT DEPENDENCY SUBMODULES FULL SOURCE CODE]\nnone",
        _diagnosis_block(diagnosis),
    ]
    if current_rtl:
        sections.append(f"[CURRENT RTL]\n{current_rtl}")
    if error_rag:
        sections.append(f"[RAG ERROR FIX HINTS]\n{error_rag}")
    if coding_guidelines:
        sections.append(_guidelines_block(coding_guidelines))
    if extra_hints:
        sections.append(f"[EXTRA REPAIR HINTS]\n{extra_hints}")
    sections.append("[OUTPUT REQUIREMENT]\nReturn SystemVerilog code only. Do not include Markdown fences, prose, or explanations.")
    return system_prompt, "\n\n".join(sections)


def compose_tb_prompt(
    *,
    mode: str,
    module_name: str,
    frozen_ir: Dict[str, Any],
    dag: Dict[str, Any],
    rtl_code: str = "",
    coding_guidelines: str,
    current_tb: str = "",
    diagnosis: str = "",
) -> Tuple[str, str]:
    mode = mode.lower()
    if mode == "generate":
        system_prompt = (
            "You are TBAgent, a strict SystemVerilog verification engineer. "
            "Build a self-contained self-checking TB from the spec, frozen IR, and black-box DUT interface only. "
            "Do not rely on or mirror RTL implementation source."
        )
        task = f"Write SystemVerilog testbench module '{module_name}_tb' for DUT '{module_name}'."
    else:
        system_prompt = (
            "You are TBAgent in repair mode. Fix only the testbench from the spec, frozen IR, diagnosis, "
            "and black-box DUT interface. Do not rewrite RTL and do not rely on RTL implementation source."
        )
        task = f"Repair the SystemVerilog testbench '{module_name}_tb' for DUT '{module_name}'. Output the entire fixed TB."

    ir = frozen_ir.get("ir", {})
    sections = [
        task,
        "[CRITICAL TESTBENCH FLOW]\nThe TB is the VCS simulation top. It must be self-contained with no input/output/inout ports. Instantiate the DUT inside the TB. Declare all DUT input stimulus and output observations as internal logic.",
        "[RTL SOURCE POLICY]\nNo DUT RTL implementation source is provided to this agent. Treat the DUT as a black box. Build stimulus, reference models, scoreboards, and assertions from the immutable IR, contract summary, architecture/spec notes, and diagnosis only. Never copy or reimplement internal RTL expressions as the expected-value oracle.",
        "[MANDATORY SYNCHRONOUS SAMPLING RULE]\nFor synchronous DUTs, never check registered outputs immediately after `@(posedge clk)` or immediately after a task that waits for a clock edge. Insert `#1;` after the clock edge before comparing outputs, or use a clocking block with input skew. If FAIL_FUNC values look one cycle late, previous-beat, or reset/default immediately after a posedge check, treat TB sampling timing as suspect and repair the TB timing first.",
        "[MANDATORY FUNCTIONAL FAILURE DIAGNOSTIC FORMAT]\nEvery functional mismatch MUST print exactly one parseable line beginning with `FAIL_FUNC:`. Use key=value fields and include all of these keys on the same line: module=, test_id=, test_name=, feature=, phase=, signal=, expected=, actual=, time=, cycle=, spec_rule=, inputs=, timing_context=, mismatch_type=. Generic functional failure messages such as `FAIL`, `Mismatch`, or `Wrong result` are not acceptable. For multi-signal checks, print one `FAIL_FUNC:` line for each failed signal.",
        "[MANDATORY WATCHDOG PROGRESS FORMAT]\nMaintain progress tracking variables in the TB: current_test_id, current_test_name, current_phase, last_completed_test_id, waiting_for, and a cycle counter. Update them before every stimulus, wait, handshake, check, and cleanup step. On watchdog timeout, print exactly one parseable line beginning with `FAIL_WATCHDOG:` and include all of these keys on the same line: module=, current_test_id=, current_test_name=, current_phase=, last_completed_test_id=, cycle=, waiting_for=, timeout_cycles=, status_outputs=. Then call $fatal(1).",
        tb_architectural_context(frozen_ir, dag),
        tb_checklist(),
        _diagnosis_block(diagnosis),
    ]
    if current_tb:
        sections.append(f"[CURRENT TESTBENCH]\n{current_tb}")
    if coding_guidelines:
        sections.append(_guidelines_block(coding_guidelines))
    sections.append("[OUTPUT REQUIREMENT]\nReturn SystemVerilog testbench code only. Do not include Markdown fences, prose, or explanations.")
    return system_prompt, "\n\n".join(sections)
