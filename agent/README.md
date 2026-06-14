# Multi-Agent RTL Workflow

`agent_main.py` is now the orchestrator. The domain work is split into small agents:

- `planner_agent.py`: reads `knowledge_base/specs` and `knowledge_base/rules`, then creates/loads `PLAN_DAG.json`. Edges point from dependency to dependent.
- `PLAN.md` is not part of the workflow; prompts use `PLAN_DAG.json`, frozen IR, targeted RAG, and structured diagnosis only.
- `architect_agent.py`: creates an immutable module IR JSON, validates it with `jsonschema` when available, computes SHA256, freezes it into `build_state/build_state.db` plus `build_state/ir/*.json`, and provides the RTL-vs-IR contract gate.
- `prompt_composer.py`: deterministically converts frozen IR, DAG subgraph, rules, and diagnosis into concise RTL/TB prompts with contract summaries and checklists. It does not call an LLM or modify IR.
- `rtl_agent.py`: generates or repairs RTL from composed prompts grounded in the frozen IR.
- `tb_agent.py`: generates or repairs self-contained TB from composed prompts grounded in the frozen IR.
- `vcs_agent.py`: calls VCS through MCP and returns raw log plus structured diagnosis.
- `vcs_log_parser.py`: regex parser that extracts compact diagnosis JSON from VCS logs.
- `debug_agent.py`: classifies whether RTL, TB, or both should be repaired from structured diagnosis.
- `review_agent.py`: writes the final Vietnamese run summary.

Per-agent model selection is controlled by environment variables:

```bash
OPENAI_TEXT_ENDPOINT=chat_completions
OPENAI_CHAT_COMPLETIONS_FALLBACK=1
MODEL_NAME=gpt-5.4-mini
PLANNER_MODEL=gpt-5.4
ARCHITECT_MODEL=gpt-5.4
RTL_MODEL=gpt-5.4
TB_MODEL=gpt-5.4
VCS_MODEL=gpt-5.4-mini
DEBUG_MODEL=gpt-5.4
REVIEW_MODEL=gpt-5.4-mini
```

If an environment variable is missing, `agents/model_config.py` supplies a conservative default.
