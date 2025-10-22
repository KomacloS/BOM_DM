# BOM_DB ↔ CE Bridge Integration Guide

## Ranked Search Workflow
1. **User input / automation target:** BOM_DB gathers a part number from the UI
   or background workflow.
2. **Validation:** Empty, whitespace, or wildcard-only strings are rejected
   before contacting the bridge.
3. **Feature probe:** `GET /state` is called (with an `X-Trace-Id`) to ensure
   `features.search_match_kind == true` and
   `features.normalization_rules_version == "v1"`.
4. **Ranked lookup:** `select_best_match()` issues
   `GET /complexes/search?pn=<value>&limit=<N>&analyze=true`.
5. **Decision:** Results are ranked by `match_kind` with the following priority:
   `exact_pn → exact_alias → normalized_pn → normalized_alias → like`. Ties keep
   the incoming order and mark the decision as `needs_review`.
6. **Surface analysis:** The top candidate’s `match_kind`, `reason`,
   `normalized_input`, and `normalized_targets` are displayed in the UI for
   operator review.

The `LinkerDecision` object contains the full response list, a `best`
`LinkCandidate`, the generated `trace_id`, and a `needs_review` flag that callers
can use to halt automation when ambiguity is detected.

## Attach / Link Flow
* `auto_link_by_pn()` uses `select_best_match()` with a reduced limit (default
  10). When `needs_review` is `False` **and** `match_kind` is either `exact_pn`
  or `exact_alias`, BOM_DB auto-attaches the complex.
* Manual linking in the Complex Panel displays the ranked results and lets the
  user confirm attachment, even when review is needed.
* Alias updates flow through `POST /complexes/{id}/aliases`, returning the new
  alias list and `source_hash` for auditing.

## Alias Proof and Tests
Integration tests exercise alias add/remove round-trips and ensure normalized
alias handling by stubbing CE bridge responses. These tests guarantee that both
UI and automation recognize when normalized aliases match.

## Logging and Traceability
* Every outbound request carries an `X-Trace-Id` header.
* The trace id is logged with the user action (search, normalization diagnostics,
  auto-link attempt). Support can grep for the id in CE bridge logs to inspect
  server-side behavior.
* Include the HTTP status, response body, and trace id when escalating issues to
  CE bridge maintainers.

## CE Ops-Kit Checks
The CE Ops-Kit validates the CE exporter environment. Run it from the BOM_DB
checkout to reproduce CI smoke/regression gates:

```bash
# run CE ops gate from BOM_DB checkout
cd comm/ce_ops_kit
source ./ce_env
./ce_gate.sh
```

`ce_gate.sh` prints a summary table with `PASS`, `FAIL`, or `SKIP` per probe.
Follow the instructions in the failure output to resolve environment issues
before retrying exporter-driven flows.
