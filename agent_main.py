import os
import re
import sys
import shutil
import json
import asyncio
import aiofiles
import aiohttp
from dotenv import load_dotenv
from fastmcp import Client
from langchain_core.messages import HumanMessage, SystemMessage


# Nhúng RAG Engine
import rag_engine
from agents.planner_agent import PlannerAgent
from agents.architect_agent import ArchitectAgent, format_contract_validation_report
from agents.rtl_agent import RTLAgent
from agents.tb_agent import TBAgent
from agents.vcs_agent import VCSAgent
from agents.review_agent import ReviewAgent
from agents.vcs_log_parser import diagnosis_to_text, parse_vcs_log
from agents.debug_agent import classify_repair_targets_from_diagnosis, deterministic_vcs_debug_decision, parse_debug_eval
from agents.prompt_composer import compose_rtl_prompt, compose_tb_prompt
from agents.model_config import load_model_config
from agents.error_memory import (
    add_debug_error,
    archive_raw_error_log,
    compact_diagnosis_packet,
    format_repair_context,
    record_error_packet,
    retrieve_related_memories,
    summarize_tb_oracle,
)


# --- 1. CẤU HÌNH HỆ THỐNG & API M7AI ---
load_dotenv()
MCP_URL = os.getenv("MCP_URL", "http://127.0.0.1:5000/mcp")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://yh.m7ai.com/v1").rstrip("/")
OPENAI_TEXT_ENDPOINT = os.getenv("OPENAI_TEXT_ENDPOINT", "chat_completions").strip().lower().replace("-", "_")
OPENAI_CHAT_COMPLETIONS_FALLBACK = os.getenv("OPENAI_CHAT_COMPLETIONS_FALLBACK", "1").strip().lower() not in {"0", "false", "no"}


if "sk-" in MCP_URL:
    raise ValueError("\n[!!!] LỖI NGHIÊM TRỌNG: Bạn đã dán nhầm API Key vào biến MCP_URL trong file .env!")


if not OPENAI_API_KEY:
    raise ValueError("Thiếu OPENAI_API_KEY trong file .env. Vui lòng thêm vào!")


MODEL_NAME = os.getenv("MODEL_NAME", "gpt-5.4-mini")


class FatalAPIError(Exception):
    pass


class APIStatusError(Exception):
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP Lỗi {status_code}: {body}")


class EmptyLLMResponseError(Exception):
    pass


RESPONSES_EMPTY_MODELS = set()


# --- 2. CÔNG CỤ HỖ TRỢ (HELPERS) ---
async def log_and_print(message):
    print(message)
    async with aiofiles.open("run.log", mode="a", encoding="utf-8") as f:
        await f.write(f"{message}\n")


def artifact_summary(path, content):
    line_count = len((content or "").splitlines())
    byte_count = len((content or "").encode("utf-8"))
    return f"path={path}, lines={line_count}, bytes={byte_count}"


async def archive_error_log(title, content):
    return await asyncio.to_thread(archive_raw_error_log, title, content)


async def remember_error_packet(packet, raw_archive_path=""):
    await asyncio.to_thread(record_error_packet, packet, raw_archive_path)


async def related_error_memories(packet, limit=3):
    return await asyncio.to_thread(retrieve_related_memories, packet, limit)


async def safe_llm_invoke(messages, model=None):
    max_retries = 3
    attempts = 0
    model_name = model or MODEL_NAME
    response_input = []
    chat_messages = []
   
    for msg in messages:
        if isinstance(msg, SystemMessage):
            response_input.append({"role": "system", "content": msg.content})
            chat_messages.append({"role": "system", "content": msg.content})
        else:
            response_input.append({"role": "user", "content": msg.content})
            chat_messages.append({"role": "user", "content": msg.content})
           
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    if OPENAI_TEXT_ENDPOINT in {"chat", "chat_completion", "chat_completions"}:
        return await invoke_chat_completion(chat_messages, model_name, headers, max_retries)

    if OPENAI_TEXT_ENDPOINT != "responses":
        raise FatalAPIError(
            "OPENAI_TEXT_ENDPOINT phải là 'chat_completions' hoặc 'responses'. "
            f"Giá trị hiện tại: {OPENAI_TEXT_ENDPOINT}"
        )

    endpoint = f"{OPENAI_BASE_URL}/responses"
    payload = {"model": model_name, "input": response_input}

    if model_name in RESPONSES_EMPTY_MODELS and OPENAI_CHAT_COMPLETIONS_FALLBACK:
        return await invoke_chat_completion(chat_messages, model_name, headers, max_retries)


    while attempts < max_retries:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, headers=headers, json=payload, timeout=120) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = extract_responses_text(data)
                        class DummyResponse:
                            def __init__(self, content): self.content = content
                        return DummyResponse(content)
                    else:
                        error_text = await resp.text()
                        raise APIStatusError(resp.status, error_text)
        except EmptyLLMResponseError as e:
            if not OPENAI_CHAT_COMPLETIONS_FALLBACK:
                raise e
            RESPONSES_EMPTY_MODELS.add(model_name)
            await log_and_print(
                f"[!] Responses API không trả text cho model {model_name}. "
                "Fallback sang /chat/completions cho các lần gọi text-only."
            )
            return await invoke_chat_completion(chat_messages, model_name, headers, max_retries)
        except APIStatusError as e:
            if e.status_code in {429, 500, 502, 503, 504}:
                attempts += 1
                await log_and_print(f"[!] API giới hạn/quá tải. Chờ 15 giây ({attempts}/{max_retries})... Lỗi: {e}")
                await asyncio.sleep(15)
            else:
                raise e
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            attempts += 1
            await log_and_print(f"[!] API timeout/kết nối lỗi. Chờ 15 giây ({attempts}/{max_retries})... Lỗi: {e}")
            await asyncio.sleep(15)
    raise FatalAPIError("KHÔNG THỂ KẾT NỐI API SAU NHIỀU LẦN THỬ! Dừng chương trình.")


async def invoke_chat_completion(messages, model_name, headers, max_retries):
    endpoint = f"{OPENAI_BASE_URL}/chat/completions"
    payload = {"model": model_name, "messages": messages}
    attempts = 0
    while attempts < max_retries:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(endpoint, headers=headers, json=payload, timeout=120) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = extract_chat_completion_text(data)
                        class DummyResponse:
                            def __init__(self, content): self.content = content
                        return DummyResponse(content)
                    error_text = await resp.text()
                    raise APIStatusError(resp.status, error_text)
        except APIStatusError as e:
            if e.status_code in {429, 500, 502, 503, 504}:
                attempts += 1
                await log_and_print(f"[!] Chat completions API giới hạn/quá tải. Chờ 15 giây ({attempts}/{max_retries})... Lỗi: {e}")
                await asyncio.sleep(15)
            else:
                raise e
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            attempts += 1
            await log_and_print(f"[!] Chat completions API timeout/kết nối lỗi. Chờ 15 giây ({attempts}/{max_retries})... Lỗi: {e}")
            await asyncio.sleep(15)
    raise FatalAPIError("KHÔNG THỂ KẾT NỐI CHAT COMPLETIONS API SAU NHIỀU LẦN THỬ! Dừng chương trình.")


def compact_response_summary(data):
    if not isinstance(data, dict):
        return repr(data)
    return {
        "id": data.get("id"),
        "status": data.get("status"),
        "model": data.get("model"),
        "error": data.get("error"),
        "incomplete_details": data.get("incomplete_details"),
        "output_len": len(data.get("output") or []),
        "usage": data.get("usage"),
    }


def extract_responses_text(data):
    if isinstance(data, dict) and isinstance(data.get("output_text"), str) and data["output_text"].strip():
        return data["output_text"]

    chunks = []
    for item in data.get("output", []) if isinstance(data, dict) else []:
        for content in item.get("content", []) if isinstance(item, dict) else []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
            elif isinstance(content.get("content"), str):
                chunks.append(content["content"])
    chunks = [chunk for chunk in chunks if chunk.strip()]
    if chunks:
        return "\n".join(chunks).strip()

    # Compatibility fallback for providers that keep chat-completions response shape.
    try:
        return extract_chat_completion_text(data)
    except (KeyError, IndexError, TypeError):
        raise EmptyLLMResponseError(f"Responses API returned no text output: {compact_response_summary(data)}")


def extract_chat_completion_text(data):
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                chunks.append(item["text"])
        chunks = [chunk for chunk in chunks if chunk.strip()]
        if chunks:
            return "\n".join(chunks).strip()
    raise EmptyLLMResponseError(f"Chat completions API returned no text output: {compact_response_summary(data)}")


def extract_code(text):
    bt = chr(96) * 3
    pattern = rf"{bt}(?:systemverilog|verilog|sv|v)?(.*?){bt}"
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    if match:
        code = match.group(1).strip()
    else:
        lines = text.split('\n')
        code = "\n".join([l for l in lines if not l.lower().startswith(("here is", "sure", "i will", "```"))]).strip()
    if "`timescale" not in code:
        code = "`timescale 1ns/1ps\n" + code
    return code.strip()


async def read_vcs_log_from_url(mcp_output):
    match = re.search(r'["\']run_log["\']\s*:\s*["\']([^"\']+)["\']', str(mcp_output))
    if match:
        log_path = match.group(1).strip()
        if os.path.exists(log_path):
            async with aiofiles.open(log_path, mode='r', encoding='utf-8') as f:
                return await f.read()
    return f"LỖI: Không tìm thấy file log."


def get_direct_dependencies_from_dag(dag, module_name):
    def _node_name(value):
        if isinstance(value, dict):
            value = value.get("module") or value.get("id") or value.get("name")
        return str(value or "").strip()

    dependencies = []
    seen = set()

    for entry in (dag or {}).get("dag") or []:
        if not isinstance(entry, dict):
            continue
        entry_module = _node_name(entry)
        if entry_module != module_name:
            continue
        for dep in entry.get("depends_on") or []:
            dep_name = _node_name(dep)
            if dep_name and dep_name not in seen:
                dependencies.append(dep_name)
                seen.add(dep_name)
        return dependencies

    for edge in (dag or {}).get("edges") or []:
        if not isinstance(edge, dict):
            continue
        src = _node_name(edge.get("from"))
        dst = _node_name(edge.get("to"))
        if src and dst == module_name and src not in seen:
            dependencies.append(src)
            seen.add(src)

    return dependencies


async def get_completed_rtl_context(current_module_name=None, required_modules=None):
    context = ""
    rtl_dir = "output_pass"
    required = set(required_modules or [])
    missing = []

    if not required:
        return context

    if os.path.exists(rtl_dir):
        for module_name in sorted(required):
            f_name = f"{module_name}.sv"
            if current_module_name and f_name == f"{current_module_name}.sv":
                continue
            path = os.path.join(rtl_dir, f_name)
            if not os.path.exists(path):
                missing.append(module_name)
                continue
            async with aiofiles.open(path, 'r', encoding='utf-8') as f_read:
                code_context = await f_read.read()
                context += f"\n// ====== DIRECT DEPENDENCY MODULE: {f_name} ======\n{code_context}\n"
    else:
        missing = sorted(required)

    if missing:
        context += (
            "\n// ====== MISSING DIRECT DEPENDENCY SOURCES ======\n"
            "// The following direct dependencies were listed in the DAG but no passed RTL source was found in output_pass: "
            f"{', '.join(sorted(missing))}\n"
        )
    return context



def save_pass_artifacts(artifact_pairs):
    os.makedirs("output_pass", exist_ok=True)
    saved_paths = []
    for source_path, file_name in artifact_pairs:
        if not source_path or not os.path.exists(source_path):
            continue
        dest_path = os.path.join("output_pass", file_name)
        shutil.copy2(source_path, dest_path)
        saved_paths.append(dest_path)
    return saved_paths


def validate_tb_diagnostic_quality(tb_code):
    required_func_tokens = [
        "FAIL_FUNC:",
        "module=",
        "test_id=",
        "test_name=",
        "feature=",
        "phase=",
        "signal=",
        "expected=",
        "actual=",
        "time=",
        "cycle=",
        "spec_rule=",
        "inputs=",
        "timing_context=",
        "mismatch_type=",
    ]
    required_watchdog_tokens = [
        "FAIL_WATCHDOG:",
        "current_test_id",
        "current_test_name",
        "current_phase",
        "last_completed_test_id",
        "waiting_for",
        "current_test_id=",
        "current_test_name=",
        "current_phase=",
        "last_completed_test_id=",
        "cycle=",
        "waiting_for=",
        "timeout_cycles=",
        "status_outputs=",
    ]
    code = tb_code or ""
    missing_func = [token for token in required_func_tokens if token not in code]
    missing_watchdog = [token for token in required_watchdog_tokens if token not in code]

    errors = []
    if missing_func:
        errors.append("Missing required FAIL_FUNC diagnostic tokens: " + ", ".join(missing_func))
    if missing_watchdog:
        errors.append("Missing required FAIL_WATCHDOG progress tokens: " + ", ".join(missing_watchdog))

    if not errors:
        return (
            "[TB DIAGNOSTIC QUALITY GATE] PASS: functional mismatch and watchdog logs include required diagnostic fields.",
            True,
        )
    return (
        "[TB DIAGNOSTIC QUALITY GATE] FAIL:\n"
        + "\n".join(f"  ERROR {idx}: {err}" for idx, err in enumerate(errors, 1)),
        False,
    )


def extract_fail_func_lines(vcs_log, max_lines=20, max_chars=12000):
    lines = [line.strip() for line in (vcs_log or "").splitlines() if "FAIL_FUNC:" in line]
    if not lines:
        return "none"

    excerpt = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        excerpt += f"\n... truncated {len(lines) - max_lines} additional FAIL_FUNC line(s) ..."
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "\n... truncated FAIL_FUNC excerpt by character limit ..."
    return excerpt


def extract_fail_watchdog_lines(vcs_log, max_lines=10, max_chars=6000):
    lines = [line.strip() for line in (vcs_log or "").splitlines() if "FAIL_WATCHDOG:" in line]
    if not lines:
        return "none"

    excerpt = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        excerpt += f"\n... truncated {len(lines) - max_lines} additional FAIL_WATCHDOG line(s) ..."
    if len(excerpt) > max_chars:
        excerpt = excerpt[:max_chars] + "\n... truncated FAIL_WATCHDOG excerpt by character limit ..."
    return excerpt




def classify_repair_targets(vcs_log, rtl_file, tb_file, evaluator_note=""):
    """
    Decide which generated artifact should be repaired on the next attempt.
    Returns: (repair_rtl, repair_tb, reason)
    """
    combined = f"{vcs_log}\n{evaluator_note}".lower()
    rtl_name = rtl_file.lower()
    tb_name = tb_file.lower()

    infra_markers = [
        "timeout error",
        "assocmaxsubmitjoblimit",
        "job violates",
        "server slurm limit",
    ]
    if any(marker in combined for marker in infra_markers):
        return False, False, "Infrastructure/server issue; retry VCS without regenerating RTL/TB."

    lines = combined.splitlines()
    relevant_markers = [
        "error",
        "fatal",
        "mismatch",
        "fail",
        "failed",
        "source info",
        "watchdog_timeout",
        "assert",
    ]
    relevant_indices = {
        idx + offset
        for idx, line in enumerate(lines)
        if any(marker in line for marker in relevant_markers)
        for offset in range(-1, 5)
        if 0 <= idx + offset < len(lines)
    }
    relevant_text = "\n".join(lines[idx] for idx in sorted(relevant_indices))

    rtl_hit = rtl_name in relevant_text
    tb_hit = tb_name in relevant_text

    note_tb_markers = ["testbench", "expected", "expectation", "checker", "assertion in tb"]
    note_rtl_markers = ["rtl", "dut", "design", "implementation", "module logic"]
    note_says_tb = any(marker in combined for marker in note_tb_markers)
    note_says_rtl = any(marker in combined for marker in note_rtl_markers)

    if rtl_hit and tb_hit:
        return True, True, "VCS/evaluator references both RTL and testbench."
    if rtl_hit:
        return True, False, f"VCS/evaluator references RTL file {rtl_file}."
    if tb_hit:
        return False, True, f"VCS/evaluator references testbench file {tb_file}."

    if note_says_tb and note_says_rtl:
        return True, True, "Evaluator reason implicates both RTL and testbench."
    if note_says_tb:
        return False, True, "Evaluator reason implicates testbench/checker/expected values."
    if note_says_rtl:
        return True, False, "Evaluator reason implicates RTL/DUT behavior."

    ambiguous_markers = [
        "mismatch",
        "fail",
        "failed",
        "fatal",
        "watchdog_timeout",
        "x propagation",
        "unknown",
    ]
    if any(marker in combined for marker in ambiguous_markers):
        return True, True, "Failure is ambiguous; repair both RTL and testbench."

    return True, True, "Unable to classify failure; repair both RTL and testbench."


# --- 3. LUỒNG LÀM VIỆC CHÍNH (WORKING FLOW) ---
async def run_rtl_agent(user_prompt):
    model_config = load_model_config()
    await log_and_print(f"\n{'='*60}\nBẮT ĐẦU PHIÊN FULL SYSTEM: {user_prompt}\nSử dụng M7AI/OpenAI-compatible multi-agent models: {model_config}\n{'='*60}")
   
    # -------------------------------------------------------------
    # 1. KHỞI ĐỘNG RAG ENGINE VÀ TRUY VẤN SPEC TỔNG QUAN
    # -------------------------------------------------------------
    await asyncio.to_thread(rag_engine.build_or_update_vector_db, False)
    planner_rag_context = await asyncio.to_thread(rag_engine.retrieve_context, f"General Architecture and RISC-V specification for: {user_prompt}", 5)


    # -------------------------------------------------------------
    # 2. PLANNER AGENT: SPEC/RULES -> BUILD ORDER DAG
    # -------------------------------------------------------------
    planner_agent = PlannerAgent(safe_llm_invoke, log_and_print, model=model_config.planner)
    architect_agent = ArchitectAgent(safe_llm_invoke, log_and_print, model=model_config.architect)
    rtl_gen_agent = RTLAgent(safe_llm_invoke, extract_code, model=model_config.rtl)
    tb_gen_agent = TBAgent(safe_llm_invoke, extract_code, model=model_config.tb)
    review_agent = ReviewAgent(safe_llm_invoke, model=model_config.review)

    try:
        build_dag = await planner_agent.create_or_load_dag(user_prompt, planner_rag_context)
        submodules_list = build_dag.get("build_order") or []
    except Exception as e:
        await log_and_print(f"[!] PlannerAgent lỗi: {e}. Dừng workflow vì chưa có DAG hợp lệ.")
        raise

    if not submodules_list:
        await log_and_print("[!] Build Order DAG rỗng. Dừng workflow vì chưa có module để build.")
        raise RuntimeError("Build Order DAG is empty")

    await log_and_print(f"[*] Build Order DAG: {json.dumps(build_dag, ensure_ascii=False)}")
    await log_and_print(f"[*] Thứ tự build toàn bộ hệ thống: {submodules_list}\n")


    # -------------------------------------------------------------
    # 3. TIẾN HÀNH CODE TỪNG MODULE
    # -------------------------------------------------------------
    async with Client(MCP_URL) as mcp_client:
        vcs_agent = VCSAgent(mcp_client, read_vcs_log_from_url, log_and_print, archive_error_log)
        for base_name in submodules_list:
            rtl_file = f"{base_name}.sv"
            tb_file = f"{base_name}_tb.sv"
            rtl_path = os.path.join("output_rtl", rtl_file)
            tb_path = os.path.join("output_rtl", tb_file)
            pass_rtl_path = os.path.join("output_pass", rtl_file)
            pass_tb_path = os.path.join("output_pass", tb_file)


            if os.path.exists(pass_rtl_path) and os.path.exists(pass_tb_path):
                await log_and_print(f"[-] SKIP: Module '{base_name}' đã PASS trong output_pass. Bỏ qua gen/verify.")
                continue


            direct_dependencies = get_direct_dependencies_from_dag(build_dag, base_name)
            completed_modules_context = await get_completed_rtl_context(
                base_name,
                required_modules=direct_dependencies,
            )
            instantiation_guide = ""
            if completed_modules_context.strip():
                instantiation_guide = (
                    "\n[DIRECT DEPENDENCY SUBMODULES FULL SOURCE CODE]\n"
                    "Below is the FULL source code only for direct dependency submodules listed in the DAG. "
                    "Use these sources to instantiate direct child modules in the current module. "
                    "Do not assume unrelated passed modules are available unless they are listed in the DAG dependency summary:\n"
                    f"{completed_modules_context}\n"
                )
                await log_and_print(
                    f"[*] RTL context for {base_name}: direct deps with full source = "
                    f"{', '.join(direct_dependencies) if direct_dependencies else 'none'}"
                )
            else:
                await log_and_print(f"[*] RTL context for {base_name}: no direct dependency full source needed.")

            module_rag_context = await asyncio.to_thread(rag_engine.retrieve_context, f"Architecture IR, interface, timing contract, and verification context for module {base_name}", 4)
            frozen_ir = await architect_agent.create_or_load_ir(base_name, user_prompt, build_dag, module_rag_context)
            frozen_ir_json = json.dumps(frozen_ir["ir"], indent=2, ensure_ascii=False)
            architectural_contract = (
                f"[IMMUTABLE ARCHITECTURAL IR - DO NOT CHANGE]\n"
                f"Module: {base_name}\nSHA256: {frozen_ir['sha256']}\n"
                f"{frozen_ir_json}\n"
                "RTL Agent and TB Agent may implement internal logic only. They must not invent, remove, or rename interface ports defined by this IR.\n"
            )


            passed = False
            attempt = 0
            rtl_code, tb_code, repair_context = "", "", ""
            current_error_packet = None
            repair_rtl, repair_tb = True, True
            repair_reason = "Initial generation"


            while not passed:
                attempt += 1
                await log_and_print(f"\n{'*'*40}\n[XỬ LÝ MODULE: {base_name}] - Lần thử {attempt}\n{'*'*40}")
                await log_and_print(f"[*] Repair target: RTL={repair_rtl}, TB={repair_tb}. Lý do: {repair_reason}")


                # Prompt composer injects compact agent-specific checklist rules.
                coding_guidelines = ""


                structural_hint = ""
                if "illegal combination of structural drivers" in repair_context.lower() or "icsd" in repair_context.lower():
                    structural_hint = (
                        "VCS log shows 'Illegal combination of structural drivers'. This means the same signal is connected to multiple module outputs. "
                        "Use unique internal nets for each module output and explicit muxing logic."
                    )
               
                procedural_structural_hint = ""
                if "illegal combination of drivers" in repair_context.lower() and "procedural" in repair_context.lower():
                    procedural_structural_hint = (
                        "VCS log shows 'Illegal combination of structural and procedural drivers'. "
                        "This means a signal is both driven by a module output port (structural) and assigned in always_comb/always_ff/assign (procedural). "
                        "SOLUTION: Create separate signals for module outputs and mux logic. NEVER write to a signal that is a module output port."
                    )


                # Bước 3A: MÃ RTL
                if repair_rtl:
                    rtl_prompt_mode = "generate" if attempt == 1 or not rtl_code else "repair"
                    error_rag = ""
                    extra_hints = f"{structural_hint}\n{procedural_structural_hint}".strip()
                    if rtl_prompt_mode == "repair":
                        error_rag = await asyncio.to_thread(rag_engine.retrieve_context, f"How to fix SystemVerilog error: {repair_context[:500]}", 2)
                    sys_msg_rtl, hum_msg_rtl = compose_rtl_prompt(
                        mode=rtl_prompt_mode,
                        module_name=base_name,
                        frozen_ir=frozen_ir,
                        dag=build_dag,
                        instantiation_guide=instantiation_guide,
                        coding_guidelines=coding_guidelines,
                        current_rtl=rtl_code if rtl_prompt_mode != "generate" else "",
                        diagnosis=repair_context,
                        error_rag=error_rag,
                        extra_hints=extra_hints,
                    )

                    try:
                        rtl_code = await rtl_gen_agent.invoke(sys_msg_rtl, hum_msg_rtl)

                        contract_report, is_contract_valid = format_contract_validation_report(rtl_code, frozen_ir["ir"])
                        await log_and_print(contract_report)
                        if not is_contract_valid:
                            await log_and_print(f"[⚠️] Frozen IR contract validation FAILED! RTL must keep the immutable interface.")

                        os.makedirs("output_rtl", exist_ok=True)
                        async with aiofiles.open(rtl_path, "w", encoding='utf-8') as f: await f.write(rtl_code)
                        await log_and_print(f"[RTL CODE - {rtl_file}]: Đã sinh/sửa code thành công. {artifact_summary(rtl_path, rtl_code)}")

                        if not is_contract_valid:
                            rtl_diagnosis = {
                                "schema_version": "1.0",
                                "tool": "ir_contract",
                                "passed": False,
                                "errors": [{
                                    "kind": "ir_contract",
                                    "source": "ir_contract",
                                    "severity": "error",
                                    "file": rtl_file,
                                    "line": 0,
                                    "is_source_location": False,
                                    "is_infrastructure": False,
                                    "message": "RTL does not satisfy frozen IR contract.",
                                    "context": contract_report,
                                }],
                            }
                            current_error_packet = compact_diagnosis_packet(
                                source="ir_contract",
                                diagnosis=rtl_diagnosis,
                                module=base_name,
                                repair_target_hint="RTL",
                            )
                            related = await related_error_memories(current_error_packet)
                            repair_context = format_repair_context(current_error_packet, related)
                            await remember_error_packet(current_error_packet)
                            repair_rtl, repair_tb = True, False
                            repair_reason = "Frozen IR contract failed; retry RTL before generating TB."
                            await log_and_print(f"[DEBUG DIAGNOSIS - IR CONTRACT] {current_error_packet.get('summary')} | details=memory/errors/events.jsonl")
                            await log_and_print(f"[!] RTL chưa khớp frozen IR contract. Không sinh TB ở lần này. Lần sau sửa RTL only.")
                            continue
                    except Exception as e:
                        await log_and_print(f"[!] Lỗi Gen RTL: {e}")
                else:
                    await log_and_print(f"[-] GIỮ NGUYÊN RTL: {rtl_file} không nằm trong repair target.")

                # ---------------- BƯỚC 3B: MÃ TESTBENCH ----------------
                if repair_tb:
                    tb_prompt_mode = "generate" if attempt == 1 or not tb_code else "repair"
                    sys_msg_tb, hum_msg_tb = compose_tb_prompt(
                        mode=tb_prompt_mode,
                        module_name=base_name,
                        frozen_ir=frozen_ir,
                        dag=build_dag,
                        rtl_code=rtl_code,
                        coding_guidelines=coding_guidelines,
                        current_tb=tb_code if tb_prompt_mode == "repair" else "",
                        diagnosis=repair_context,
                    )
                    try:
                        tb_code = await tb_gen_agent.invoke(sys_msg_tb, hum_msg_tb)
                    except Exception as e:
                        await log_and_print(f"[!] Lỗi Gen TB: {e}")

                    wd = (
                        "\n  // --- AUTO-INJECTED WATCHDOG ---\n"
                        "  initial begin\n"
                        "    #1000000;\n"
                        f"    $display(\"FAIL_WATCHDOG: module={base_name} current_test_id=-1 current_test_name=unknown current_phase=unknown last_completed_test_id=-1 cycle=-1 waiting_for=unknown timeout_cycles=1000000 status_outputs=\\\"{{auto_injected=1}}\\\"\");\n"
                        "    $display(\"FAIL: WATCHDOG_TIMEOUT simulation hung\");\n"
                        "    $fatal(1);\n"
                        "  end\n"
                    )
                    if "WATCHDOG_TIMEOUT" not in tb_code and "FAIL_WATCHDOG:" not in tb_code:
                        parts = tb_code.rsplit('endmodule', 1)
                        tb_code = (parts[0] + wd + "\nendmodule" + parts[1]) if len(parts) == 2 else (tb_code + "\n" + wd)

                    async with aiofiles.open(tb_path, "w", encoding='utf-8') as f: await f.write(tb_code)
                    await log_and_print(f"[TESTBENCH CODE - {tb_file}]: Đã sinh/sửa TB thành công. {artifact_summary(tb_path, tb_code)}")
                else:
                    await log_and_print(f"[-] GIỮ NGUYÊN TESTBENCH: {tb_file} không nằm trong repair target.")

                # ---------------- BƯỚC 3C: SAFETY GATE TRƯỚC KHI CHẠY VCS ----------------
                contract_report, is_contract_valid = format_contract_validation_report(rtl_code, frozen_ir["ir"])
                if not is_contract_valid:
                    repair_rtl = True
                    repair_tb = False
                    safety_diagnosis = {
                        "schema_version": "1.0",
                        "tool": "ir_contract",
                        "passed": False,
                        "errors": [{
                            "kind": "ir_contract",
                            "source": "ir_contract",
                            "severity": "error",
                            "file": rtl_file,
                            "line": 0,
                            "is_source_location": False,
                            "is_infrastructure": False,
                            "message": "RTL does not satisfy frozen IR contract.",
                            "context": contract_report,
                        }],
                    }
                    current_error_packet = compact_diagnosis_packet(
                        source="ir_contract",
                        diagnosis=safety_diagnosis,
                        module=base_name,
                        repair_target_hint="RTL",
                    )
                    related = await related_error_memories(current_error_packet)
                    repair_context = format_repair_context(current_error_packet, related)
                    await remember_error_packet(current_error_packet)
                    repair_reason = "Frozen IR contract failed before VCS."
                    await log_and_print(f"[DEBUG DIAGNOSIS - IR CONTRACT GATE] {current_error_packet.get('summary')} | details=memory/errors/events.jsonl")
                    await log_and_print(f"[!] IR contract chưa pass, bỏ qua VCS. Lần sau sửa: RTL={repair_rtl}, TB={repair_tb}.")
                    continue

                tb_quality_report, is_tb_quality_valid = validate_tb_diagnostic_quality(tb_code)
                await log_and_print(tb_quality_report)
                if not is_tb_quality_valid:
                    repair_rtl = False
                    repair_tb = True
                    tb_quality_diagnosis = {
                        "schema_version": "1.0",
                        "tool": "tb_diagnostic_quality",
                        "passed": False,
                        "errors": [{
                            "kind": "tb_diagnostic_quality",
                            "source": "tb_diagnostic_quality",
                            "severity": "error",
                            "file": tb_file,
                            "line": 0,
                            "is_source_location": False,
                            "is_infrastructure": False,
                            "message": "Testbench functional failure diagnostics do not satisfy required FAIL_FUNC key=value format.",
                            "context": tb_quality_report,
                        }],
                    }
                    current_error_packet = compact_diagnosis_packet(
                        source="tb_diagnostic_quality",
                        diagnosis=tb_quality_diagnosis,
                        module=base_name,
                        repair_target_hint="TB",
                    )
                    related = await related_error_memories(current_error_packet)
                    repair_context = format_repair_context(current_error_packet, related)
                    await remember_error_packet(current_error_packet)
                    repair_reason = "TB diagnostic quality gate failed before VCS."
                    await log_and_print(f"[DEBUG DIAGNOSIS - TB QUALITY GATE] {current_error_packet.get('summary')} | details=memory/errors/events.jsonl")
                    await log_and_print(f"[!] TB chưa có FAIL_FUNC diagnostics đủ chi tiết. Bỏ qua VCS. Lần sau sửa TB only.")
                    continue

                # ---------------- BƯỚC 3D: VERIFY (CHẠY VCS, TB LÀ TOP) ----------------
                await asyncio.sleep(3)
                vcs_success = False
                retry_server = 0


                sv_files_to_compile = [
                    f for f in os.listdir("output_rtl")
                    if f.endswith(".sv") and not f.endswith("_tb.sv") and not f.startswith("top_")
                ]
                if tb_file not in sv_files_to_compile:
                    sv_files_to_compile.append(tb_file)
                while not vcs_success and retry_server < 3:
                    retry_server += 1
                    try:
                        raw_vcs_log, vcs_diagnosis, raw_archive_path = await vcs_agent.run(
                            os.getcwd() + "/output_rtl",
                            sv_files_to_compile,
                            f"{base_name}_tb",
                            f"{base_name} - Lần thử {attempt}",
                            timeout=120.0,
                        )
                        current_error_packet = compact_diagnosis_packet(
                            source="vcs",
                            diagnosis=vcs_diagnosis,
                            module=base_name,
                            repair_target_hint="AUTO",
                        )
                        diagnosis_text = diagnosis_to_text(current_error_packet)
                        await log_and_print(f"[VCS STRUCTURED DIAGNOSIS] {current_error_packet.get('summary')} | raw={raw_archive_path}")
                       
                        if "AssocMaxSubmitJobLimit" in raw_vcs_log or "Job violates" in raw_vcs_log:
                            await log_and_print(f"[!] Server Slurm Limit. Chờ 60s để xả tải...")
                            await asyncio.sleep(60); continue
                       
                        vcs_success = True
                        det_status, det_reason, det_target = deterministic_vcs_debug_decision(vcs_diagnosis, raw_vcs_log, tb_code)

                        if det_status == "PASS":
                            eval_text = (
                                "EVALUATION: PASS\n"
                                "REPAIR_TARGET: NONE\n"
                                f"REASON: {det_reason}"
                            )
                            await log_and_print(f"\n[DEBUG AGENT OUTPUT - DETERMINISTIC]\n{eval_text}\n{'-'*30}")
                        elif det_status == "INCONCLUSIVE" and det_target == "TB":
                            eval_text = (
                                "EVALUATION: INCONCLUSIVE\n"
                                "REPAIR_TARGET: TB\n"
                                f"REASON: {det_reason}"
                            )
                            await log_and_print(f"\n[DEBUG AGENT OUTPUT - DETERMINISTIC]\n{eval_text}\n{'-'*30}")
                        else:
                            await log_and_print("-> Đang gửi kết quả VCS sạch/diag cho DebugAgent đánh giá...")
                            fail_func_excerpt = extract_fail_func_lines(raw_vcs_log)
                            fail_watchdog_excerpt = extract_fail_watchdog_lines(raw_vcs_log)
                            debug_sys = SystemMessage(content=(
                                "You are DebugAgent. Decide whether a SystemVerilog VCS run is a true PASS, FAIL, or INCONCLUSIVE. "
                                "Return exactly this format:\n"
                                "EVALUATION: PASS|FAIL|INCONCLUSIVE\n"
                                "REPAIR_TARGET: NONE|RTL|TB|BOTH|AUTO\n"
                                "REASON: <short reason>\n"
                                "Rules: PASS only when structured diagnosis has passed=true, the log shows clean simulation completion, and there is no real VCS/runtime/assertion/mismatch failure. "
                                "Do not treat command prelude text like `echo ERROR` or `exit 2` as a failure if the simulation later reaches $finish and VCS Simulation Report. "
                                "A TB line beginning with FAIL_FUNC: is a functional oracle mismatch, not proof that the TB itself is wrong. "
                                "A TB line beginning with FAIL_WATCHDOG: is a watchdog timeout with test progress context; use current_test_id, current_phase, waiting_for, and status_outputs to locate where the hang occurred. "
                                "Choose TB only if the expected value, timing, stimulus, or checker is demonstrably wrong; choose RTL if DUT behavior violates the spec; choose BOTH/AUTO if ambiguous. "
                                "If the run is clean but the TB has no self-checking oracle with compare plus FAIL:/fatal path, return INCONCLUSIVE with REPAIR_TARGET: TB."
                            ))
                            debug_hum = HumanMessage(content=(
                                f"Module: {base_name}\n"
                                f"Deterministic precheck: {det_status} - {det_reason} - target={det_target}\n\n"
                                f"Diagnosis packet JSON:\n{diagnosis_text}\n\n"
                                f"FAIL_FUNC lines from raw VCS log:\n{fail_func_excerpt}\n\n"
                                f"FAIL_WATCHDOG lines from raw VCS log:\n{fail_watchdog_excerpt}\n\n"
                                f"Testbench oracle summary:\n{summarize_tb_oracle(tb_code)}"
                            ))
                            try:
                                eval_text = (await safe_llm_invoke([debug_sys, debug_hum], model=model_config.debug)).content.strip()
                            except Exception as exc:
                                eval_text = (
                                    f"EVALUATION: {det_status}\n"
                                    f"REPAIR_TARGET: {det_target}\n"
                                    f"REASON: DebugAgent LLM unavailable ({exc}); used deterministic VCS debug decision: {det_reason}"
                                )
                            await log_and_print(f"\n[DEBUG AGENT OUTPUT]\n{eval_text}\n{'-'*30}")

                        eval_status, eval_target, fail_reason = parse_debug_eval(eval_text)
                        if det_status == "INCONCLUSIVE" and det_target == "TB":
                            eval_status, eval_target, fail_reason = det_status, det_target, det_reason

                        if eval_status == "PASS":
                            passed = True
                            await log_and_print(f"[*] KẾT QUẢ CUỐI CÙNG: '{base_name}' PASSED!\n")
                            # CHỈ LƯU ARTIFACT VÀO output_pass/RAG SAU KHI VCS PASS
                            pass_paths = await asyncio.to_thread(save_pass_artifacts, [(rtl_path, rtl_file), (tb_path, tb_file)])
                            await log_and_print(f"[*] OUTPUT_PASS: Đã lưu artifact PASS: {rtl_file}, {tb_file}")
                            await log_and_print(f"[*] RAG UPDATE: Lưu artifact đã PASS từ output_pass vào vector DB: {rtl_file}, {tb_file}")
                            await asyncio.to_thread(rag_engine.build_or_update_vector_db, True, pass_paths)
                        else:
                            passed = False
                            await log_and_print(f"[!] KẾT QUẢ CUỐI CÙNG: '{base_name}' {eval_status}.\nLý do: {fail_reason}\n-> Chuyển code sửa lỗi...\n")
                            current_error_packet["debug_note"] = fail_reason
                            current_error_packet["repair_target_hint"] = eval_target
                            if not current_error_packet.get("errors"):
                                add_debug_error(
                                    current_error_packet,
                                    kind=eval_status.lower(),
                                    message=fail_reason,
                                    repair_target_hint=eval_target,
                                )
                            elif current_error_packet.get("passed"):
                                current_error_packet["passed"] = False
                                current_error_packet["status"] = eval_status.lower()
                            if eval_target == "RTL":
                                repair_rtl, repair_tb, repair_reason = True, False, "DebugAgent selected RTL repair."
                            elif eval_target == "TB":
                                repair_rtl, repair_tb, repair_reason = False, True, "DebugAgent selected TB repair."
                            elif eval_target == "BOTH":
                                repair_rtl, repair_tb, repair_reason = True, True, "DebugAgent selected RTL+TB repair."
                            else:
                                repair_rtl, repair_tb, repair_reason = classify_repair_targets_from_diagnosis(vcs_diagnosis, rtl_file, tb_file, fail_reason)
                            await log_and_print(f"[*] Phân loại lỗi cho lần sau: RTL={repair_rtl}, TB={repair_tb}. Lý do: {repair_reason}")
                            related = await related_error_memories(current_error_packet)
                            await remember_error_packet(current_error_packet, raw_archive_path)
                            repair_context = format_repair_context(current_error_packet, related)
                    except asyncio.TimeoutError:
                        timeout_diagnosis = {"schema_version": "1.0", "tool": "vcs", "passed": False, "errors": [{"kind": "timeout", "source": "vcs", "severity": "error", "file": "", "line": 0, "is_source_location": False, "is_infrastructure": False, "message": "TIMEOUT ERROR: Quá trình chạy VCS vượt quá 120 giây.", "context": ""}]}
                        current_error_packet = compact_diagnosis_packet(
                            source="vcs",
                            diagnosis=timeout_diagnosis,
                            module=base_name,
                            repair_target_hint="AUTO",
                        )
                        repair_rtl, repair_tb, repair_reason = classify_repair_targets_from_diagnosis(timeout_diagnosis, rtl_file, tb_file)
                        related = await related_error_memories(current_error_packet)
                        await remember_error_packet(current_error_packet)
                        repair_context = format_repair_context(current_error_packet, related)
                        await log_and_print(f"[*] Timeout VCS, lần sau không regen code nếu không có bằng chứng lỗi RTL/TB. Lý do: {repair_reason}")
                        vcs_success = True
                    except Exception as e:
                        vcs_exception_diagnosis = {
                            "schema_version": "1.0",
                            "tool": "vcs",
                            "passed": False,
                            "status": "infrastructure_error",
                            "errors": [{
                                "kind": "tool_exception",
                                "source": "vcs",
                                "severity": "error",
                                "file": "",
                                "line": 0,
                                "is_source_location": False,
                                "is_infrastructure": True,
                                "message": f"VCS tool exception on retry {retry_server}/3: {e}",
                                "context": repr(e),
                            }],
                        }
                        current_error_packet = compact_diagnosis_packet(
                            source="vcs",
                            diagnosis=vcs_exception_diagnosis,
                            module=base_name,
                            repair_target_hint="AUTO",
                        )
                        await remember_error_packet(current_error_packet)
                        await log_and_print(f"[!] VCS tool exception retry {retry_server}/3: {e}")
                        if retry_server >= 3:
                            repair_context = format_repair_context(current_error_packet, await related_error_memories(current_error_packet))
                            raise RuntimeError(f"VCS failed for module {base_name} after {retry_server} retries: {e}") from e
                        await asyncio.sleep(5)
                        continue
                if not vcs_success:
                    raise RuntimeError(f"VCS did not complete for module {base_name}; stopping before moving to next module.")


    # -------------------------------------------------------------
    # 4. TỔNG HỢP VÀ IN BÁO CÁO CUỐI CÙNG
    # -------------------------------------------------------------
    await log_and_print("\n-> Đang nhờ AI đọc run.log và viết báo cáo tóm tắt cho REPORT.md...")
    try:
        async with aiofiles.open("run.log", "r", encoding='utf-8') as f: full_log = await f.read()
        ai_summary = await review_agent.summarize_run(full_log[-60000:])
    except Exception as e:
        ai_summary = f"*Lỗi: {e}*"


    async with aiofiles.open("REPORT.md", "w", encoding='utf-8') as f:
        await f.write(f"# RTL GENERATION REPORT\n\n- **Prompt:** {user_prompt}\n- **Submodules Planned:** {submodules_list}\n\n## TÓM TẮT QUÁ TRÌNH\n\n{ai_summary}\n\n---\n*Chi tiết tại `run.log`.*")
       
    await log_and_print("\n=== HOÀN THÀNH TOÀN BỘ QUÁ TRÌNH ===")


if __name__ == "__main__":
    query = "Design a complete RV32EC_Zmmul core including all sub-components and a top-level module."
    asyncio.run(run_rtl_agent(query))
