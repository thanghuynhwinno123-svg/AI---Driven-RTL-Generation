import re


def classify_repair_targets_from_diagnosis(diagnosis, rtl_file, tb_file, evaluator_note=""):
    text = f"{diagnosis}\n{evaluator_note}".lower()
    rtl_name = rtl_file.lower()
    tb_name = tb_file.lower()
    diag_errors = diagnosis.get("errors", []) if isinstance(diagnosis, dict) else []
    files = {str(err.get("file") or "").lower() for err in diag_errors}
    has_oracle_mismatch = any(
        str(err.get("kind") or "").lower() == "tb_oracle_mismatch"
        or re.search(r"^\s*FAIL\s*:|\b(expected|expect)\b.*\b(got|actual)\b|\bmismatch\b", str(err.get("message") or ""), re.IGNORECASE)
        for err in diag_errors
    )
    rtl_hit = any(name.endswith(rtl_name) or name == rtl_name for name in files) or rtl_name in text
    tb_hit = any(name.endswith(tb_name) or name == tb_name for name in files) or tb_name in text
    if has_oracle_mismatch:
        return True, True, "TB oracle reported a functional mismatch; expected/got mismatch can be RTL or TB until the oracle is reviewed."
    if rtl_hit and tb_hit:
        return True, True, "Structured diagnosis references both RTL and TB."
    if rtl_hit:
        return True, False, f"Structured diagnosis references RTL file {rtl_file}."
    if tb_hit:
        return False, True, f"Structured diagnosis references TB file {tb_file}."
    if any(marker in text for marker in ["expected", "checker", "assertion", "testbench"]):
        return False, True, "Diagnosis implicates checker/testbench behavior."
    if any(marker in text for marker in ["dut", "rtl", "design", "implementation"]):
        return True, False, "Diagnosis implicates DUT/RTL behavior."
    if any(marker in text for marker in ["timeout", "mismatch", "fail", "fatal", "x propagation"]):
        return True, True, "Ambiguous structured diagnosis; repair both."
    return True, True, "Unable to classify structured diagnosis; repair both."


def _extract_line_from_text(text):
    patterns = [
        r'"[^"]+\.sv"\s*,\s*(\d+)',
        r'\bline\s*(\d+)\b',
        r'\.sv\s*[: ,]+\s*(\d+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def validation_diagnosis(kind, filename, report):
    errors = []
    lines = report.splitlines()
    for idx, line in enumerate(lines):
        if not re.match(r"\s*(?:ERROR\s+\d+\s*:|%Error(?:[-:]|\b)|Error(?:[-:]|\b))", line):
            continue
        line_no = _extract_line_from_text(line)
        start = max(0, idx - 2)
        end = min(len(lines), idx + 4)
        errors.append({
            "kind": kind,
            "source": f"{kind}_validator",
            "severity": "error",
            "file": filename or "",
            "line": line_no,
            "is_source_location": bool(line_no),
            "is_infrastructure": False,
            "message": line.strip(),
            "context": "\n".join(lines[start:end]).strip(),
        })
    return {"schema_version": "1.0", "tool": f"{kind}_validator", "passed": not errors, "errors": errors}


def _strip_sv_comments(text):
    text = re.sub(r"//.*", "", text or "")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return text


def tb_has_self_checking_oracle(tb_code):
    clean = _strip_sv_comments(tb_code)
    has_compare = bool(re.search(r"(!==|!=|===|==)", clean))
    has_fail_path = bool(re.search(r'"FAIL\s*:', clean, re.IGNORECASE) and re.search(r"\$fatal\s*\(", clean))
    return bool(has_compare and has_fail_path) or bool(re.search(r"\bassert\s*\(", clean))


def vcs_log_has_clean_completion(vcs_log):
    text = vcs_log or ""
    has_finish = bool(re.search(r"\$finish\b", text, re.IGNORECASE))
    has_report = bool(
        re.search(r"V\s*C\s*S\s+S\s*i\s*m\s*u\s*l\s*a\s*t\s*i\s*o\s*n\s+R\s*e\s*p\s*o\s*r\s*t", text, re.IGNORECASE)
        or re.search(r"VCS\s+Simulation\s+Report", text, re.IGNORECASE)
    )
    return has_finish and has_report


def deterministic_vcs_debug_decision(diagnosis, vcs_log, tb_code):
    errors = diagnosis.get("errors", []) if isinstance(diagnosis, dict) else []
    if errors or not diagnosis.get("passed", False):
        return "FAIL", "Structured VCS diagnosis contains real errors or did not complete cleanly.", "AUTO"
    if not vcs_log_has_clean_completion(vcs_log):
        return "INCONCLUSIVE", "VCS log lacks both $finish and VCS Simulation Report; do not mark PASS.", "AUTO"
    if not tb_has_self_checking_oracle(tb_code):
        return "INCONCLUSIVE", "VCS completed cleanly, but the testbench has no self-checking oracle with compare plus FAIL:/fatal path.", "TB"
    return "PASS", "VCS completed cleanly and TB contains a self-checking oracle.", "NONE"


def parse_debug_eval(text):
    raw = text or ""
    status_match = re.search(r"EVALUATION:\s*(PASS|FAIL|INCONCLUSIVE)", raw, re.IGNORECASE)
    target_match = re.search(r"REPAIR_TARGET:\s*(RTL|TB|BOTH|NONE|AUTO)", raw, re.IGNORECASE)
    reason_match = re.search(r"REASON:\s*(.*)", raw, re.IGNORECASE | re.DOTALL)
    status = status_match.group(1).upper() if status_match else "FAIL"
    target = target_match.group(1).upper() if target_match else "AUTO"
    reason = reason_match.group(1).strip() if reason_match else raw.strip()
    return status, target, reason
