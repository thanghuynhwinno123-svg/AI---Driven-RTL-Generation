import json
import os
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage

from .common import extract_json_object, read_text_tree
from .model_config import model_for

PLAN_DAG_FILE = Path("PLAN_DAG.json")


class DagCycleError(ValueError):
    def __init__(self, message, remaining_nodes=None, cycle_edges=None):
        super().__init__(message)
        self.remaining_nodes = remaining_nodes or []
        self.cycle_edges = cycle_edges or []


def _as_module_name(value):
    if isinstance(value, dict):
        value = value.get("module") or value.get("id") or value.get("name")
    if isinstance(value, (list, tuple)):
        return [_as_module_name(item) for item in value]
    if value is None:
        return None
    return str(value).strip()


def _flatten_names(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result = []
        for item in value:
            result.extend(_flatten_names(item))
        return result
    name = _as_module_name(value)
    if isinstance(name, list):
        return [item for item in name if item]
    return [name] if name else []


def normalize_dag(dag):
    clean = dict(dag or {})
    for key in ("markdown" + "_plan", "plan" + "_md", "plan" + "_text"):
        clean.pop(key, None)

    if isinstance(clean.get("dag"), list):
        entries = clean["dag"]
        nodes = []
        edges = []
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            module = _as_module_name(entry.get("module") or entry.get("id") or entry.get("name"))
            if not module:
                continue
            if module not in seen:
                nodes.append({"id": module, "kind": "module", "level": entry.get("level", 0)})
                seen.add(module)
            for dep in _flatten_names(entry.get("depends_on", [])):
                if dep and dep not in seen:
                    nodes.append({"id": dep, "kind": "module", "level": 0})
                    seen.add(dep)
                if dep:
                    edges.append({"from": dep, "to": module, "reason": "depends_on"})
        clean["nodes"] = nodes
        clean["edges"] = edges
        return apply_computed_levels(clean)

    raw_nodes = clean.get("nodes", [])
    nodes = []
    seen = set()
    for raw in raw_nodes:
        for name in _flatten_names(raw):
            if name and name not in seen:
                level = raw.get("level", 0) if isinstance(raw, dict) else 0
                nodes.append({"id": name, "kind": "module", "level": level})
                seen.add(name)
    raw_edges = clean.get("edges", [])
    edges = []
    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        for src in _flatten_names(edge.get("from")):
            for dst in _flatten_names(edge.get("to")):
                if src and dst:
                    edges.append({"from": src, "to": dst, "reason": edge.get("reason", "dependency")})
    clean["nodes"] = nodes
    clean["edges"] = edges
    return apply_computed_levels(clean)


def topological_sort(nodes, edges):
    node_ids = []
    seen = set()
    for node in nodes:
        node_id = node["id"] if isinstance(node, dict) else str(node)
        if node_id not in seen:
            node_ids.append(node_id)
            seen.add(node_id)
    indegree = {node: 0 for node in node_ids}
    outgoing = {node: [] for node in node_ids}
    normalized_edges = []
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src not in indegree or dst not in indegree:
            continue
        normalized_edges.append({"from": src, "to": dst, "reason": edge.get("reason", "dependency")})
        outgoing[src].append(dst)
        indegree[dst] += 1
    queue = [node for node in node_ids if indegree[node] == 0]
    result = []
    while queue:
        node = queue.pop(0)
        result.append(node)
        for dst in outgoing[node]:
            indegree[dst] -= 1
            if indegree[dst] == 0:
                queue.append(dst)
    if len(result) != len(node_ids):
        remaining = [node for node in node_ids if node not in result]
        cycle_edges = [edge for edge in normalized_edges if edge["from"] in remaining and edge["to"] in remaining]
        raise DagCycleError("Build Order DAG contains a cycle", remaining, cycle_edges)
    return result


def canonical_build_order(dag):
    normalized = apply_computed_levels(normalize_dag(dag))
    return list(normalized.get("build_order", []))


def compute_levels(nodes, edges):
    order = topological_sort(nodes, edges)
    levels = {node: 0 for node in order}
    outgoing = {node: [] for node in order}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src in levels and dst in levels:
            outgoing[src].append(dst)
    for src in order:
        for dst in outgoing[src]:
            levels[dst] = max(levels[dst], levels[src] + 1)
    return levels


def apply_computed_levels(dag):
    nodes = dag.get("nodes", [])
    edges = dag.get("edges", [])
    levels = compute_levels(nodes, edges) if nodes else {}
    for node in nodes:
        node["level"] = levels.get(node["id"], 0)
    dag["build_order"] = sorted([node["id"] for node in nodes], key=lambda name: (levels.get(name, 0), name))
    return dag


def to_preferred_dag(dag):
    dag = apply_computed_levels(normalize_dag(dag))
    incoming = {node["id"]: [] for node in dag.get("nodes", [])}
    for edge in dag.get("edges", []):
        src = edge.get("from")
        dst = edge.get("to")
        if dst in incoming and src:
            incoming[dst].append(src)
    levels = {node["id"]: node.get("level", 0) for node in dag.get("nodes", [])}
    modules = sorted(incoming, key=lambda name: (levels.get(name, 0), name))
    build_order = sorted(incoming, key=lambda name: (levels.get(name, 0), name))
    return {
        "project": dag.get("project", "RV32EC_Tiny_Core"),
        "build_order": build_order,
        "dag": [
            {
                "module": module,
                "depends_on": sorted(set(incoming[module]), key=lambda name: (levels.get(name, 0), name)),
                "level": levels.get(module, 0),
            }
            for module in modules
        ],
    }


def write_plan_dag(dag):
    PLAN_DAG_FILE.write_text(json.dumps(to_preferred_dag(dag), indent=2, ensure_ascii=False), encoding="utf-8")


def _format_cycle_feedback(error, bad_dag):
    cycle_edges = error.cycle_edges or []
    remaining = error.remaining_nodes or []
    return (
        "The previous DAG is invalid because it contains a build dependency cycle.\n"
        f"Cycle/blocked nodes: {remaining}\n"
        f"Cycle edges inside blocked set: {cycle_edges}\n"
        "Fix the DAG by removing reverse dependencies. Build dependency means: if module A instantiates or structurally needs module B, then A.depends_on includes B. "
        "Child modules must not depend on their parent/top module just because signals flow between them. Top-level modules depend on children, not the reverse.\n"
        f"Bad DAG JSON excerpt:\n{json.dumps(bad_dag, ensure_ascii=False)[:6000]}"
    )


class PlannerAgent:
    def __init__(self, invoke_llm, log, model=None):
        self.invoke_llm = invoke_llm
        self.log = log
        self.model = model or model_for("planner")
        self.max_retries = int(os.getenv("PLANNER_DAG_RETRIES", "5"))

    async def create_or_load_dag(self, user_prompt: str, rag_context: str):
        retry_feedback = ""
        if PLAN_DAG_FILE.exists() and PLAN_DAG_FILE.stat().st_size > 10:
            try:
                dag = normalize_dag(json.loads(PLAN_DAG_FILE.read_text(encoding="utf-8")))
                dag["build_order"] = canonical_build_order(dag)
                write_plan_dag(dag)
                await self.log(f"[*] PlannerAgent: dùng PLAN_DAG.json có sẵn. Build order: {dag['build_order']}")
                return dag
            except DagCycleError as exc:
                bad = json.loads(PLAN_DAG_FILE.read_text(encoding="utf-8"))
                retry_feedback = _format_cycle_feedback(exc, bad)
                Path("PLAN_DAG.invalid_existing.json").write_text(json.dumps(bad, indent=2, ensure_ascii=False), encoding="utf-8")
                await self.log(f"[!] PlannerAgent: PLAN_DAG.json có cycle. Sẽ yêu cầu planner rebuild DAG. Nodes kẹt: {exc.remaining_nodes}")
            except Exception as exc:
                retry_feedback = f"Existing PLAN_DAG.json is invalid or malformed: {exc}. Rebuild it from scratch."
                await self.log(f"[!] PlannerAgent: PLAN_DAG.json không hợp lệ. Sẽ rebuild. Lỗi: {exc}")

        specs = read_text_tree("knowledge_base/specs")
        rules = read_text_tree("knowledge_base/rules")
        sys_msg = "You are PlannerAgent. Create a CPU module Build Order DAG as strict JSON."
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            feedback_block = f"\n[PREVIOUS DAG ERROR - MUST FIX]\n{retry_feedback}\n" if retry_feedback else ""
            hum_msg = (
                f"Request: {user_prompt}\n\n"
                f"[SPECIFICATIONS]\n{specs}\n\n[RULES]\n{rules}\n\n[RAG CONTEXT]\n{rag_context}\n"
                f"{feedback_block}\n"
                "Return ONLY JSON using this schema: {\"project\":\"RV32EC_Tiny_Core\", \"dag\":["
                "{\"module\":module_name, \"depends_on\":[dependency_module...], \"level\":integer}]}. "
                "If Module A instantiates or structurally needs Module B, put B in A.depends_on. "
                "The graph must be acyclic. Do not model runtime signal flow as reverse build dependency. "
                "Top modules depend on child modules; child modules do not depend on top modules. "
                "Return the JSON object only; do not include prose."
            )
            response = await self.invoke_llm([SystemMessage(content=sys_msg), HumanMessage(content=hum_msg)], model=self.model)
            raw_data = extract_json_object(response.content)
            try:
                data = normalize_dag(raw_data)
                data["build_order"] = canonical_build_order(data)
                write_plan_dag(data)
                await self.log(f"[*] PlannerAgent: tạo PLAN_DAG.json mới sau {attempt} lần thử. Build order: {data['build_order']}")
                return data
            except DagCycleError as exc:
                last_error = exc
                invalid_path = Path(f"PLAN_DAG.invalid_attempt_{attempt}.json")
                invalid_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")
                retry_feedback = _format_cycle_feedback(exc, raw_data)
                await self.log(f"[!] PlannerAgent: DAG attempt {attempt}/{self.max_retries} có cycle. Nodes kẹt: {exc.remaining_nodes}. Gửi feedback để planner rebuild.")
            except Exception as exc:
                last_error = exc
                invalid_path = Path(f"PLAN_DAG.invalid_attempt_{attempt}.json")
                invalid_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")
                retry_feedback = f"The previous DAG JSON was malformed or failed normalization: {exc}. Return a corrected acyclic project/dag JSON."
                await self.log(f"[!] PlannerAgent: DAG attempt {attempt}/{self.max_retries} không hợp lệ: {exc}. Gửi feedback để planner rebuild.")
        raise RuntimeError(f"PlannerAgent could not create an acyclic DAG after {self.max_retries} attempts: {last_error}")
