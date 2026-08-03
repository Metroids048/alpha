# Runtime Adjustment Log

## 2026-08-03

- `runtime.main()` loads the workspace environment before catalog recovery, so protected adapter configuration is available without bypassing authentication.
- Default candidate composition remains `runtime -> v50_adapter -> FactoryOrchestrator`; knowledge-capable LLM generation is explicit opt-in and has no alternate simulation or submit path.
- Catalog failure remains `CATALOG_UNAVAILABLE`; the loop backoff and operator guidance do not manufacture a cache, session, candidate, or READY row.
- Candidate budgets now consume persisted arm state before `execute_candidate` is invoked.
- NEAR_PASS settings trials now use the original request lifecycle with parent-verified identity reuse and persisted v21 lineage.

No platform request, authentication refresh, simulation, or submission was run during this implementation.
