# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260614-001] best_practice

**Logged**: 2026-06-14T14:15:00Z
**Priority**: high
**Status**: pending
**Area**: infra

### Summary
Parallel agent dispatch for independent fix branches is 5-10x faster than serial implementation

### Details
When implementing 6 independent security fixes (each on its own git branch), dispatching them as parallel Task agents completed all 6 in the time it would take to do ~1 serially. Key requirements for this to work:
- Each agent needs a COMPLETE self-contained prompt (no dependencies on other agents' output)
- All agents must branch from the SAME base commit
- Merge conflicts are minimal when fixes touch different code regions
- The orchestrator handles conflict resolution during integration

This pattern worked for: fix implementation, PoC exploit writing, code review/validation, and Rust protocol implementation.

### Suggested Action
Default to parallel agent dispatch for any task involving 3+ independent code changes. Reserve serial execution for dependent chains only.

### Metadata
- Source: conversation
- Related Files: protocols/wslink/protocol/wslink.py, crates/pyprotocols-core/src/
- Tags: parallelism, agents, workflow, performance
- Pattern-Key: parallel.agent.dispatch

---

## [LRN-20260614-002] best_practice

**Logged**: 2026-06-14T14:15:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Security fixes cascade across protocol implementations — audit one, fix all

### Details
The same vulnerability classes (CWE-22 path traversal, CWE-400 unbounded buffers, CWE-841 state machine gaps) appeared identically across WSLink, HSLink, and ZMODEM. The pattern is: protocols that trust peer-supplied metadata without bounds checking.

Once a fix is designed for one protocol, it can be mechanically applied to all others. The `common/file_safety.py` extraction demonstrates the ideal: a single security boundary shared across all protocol receivers.

For the Rust rewrite, this means `crate::file_safety::validate_receive_path` is the ONE place path traversal is handled — not per-protocol.

### Suggested Action
When fixing a vulnerability in one protocol, immediately check all sibling protocols for the same class. Extract to shared module where possible.

### Metadata
- Source: conversation
- Related Files: protocols/common/file_safety.py, crates/pyprotocols-core/src/file_safety.rs
- Tags: security, cross-cutting, shared-module, defense-in-depth
- Pattern-Key: security.cascade.fix

---

## [LRN-20260614-003] best_practice

**Logged**: 2026-06-14T14:15:00Z
**Priority**: medium
**Status**: pending
**Area**: infra

### Summary
Three-pass review pattern: implement → validate → optimize finds bugs that single-pass misses

### Details
The council pattern of dispatching 3 agents with different perspectives (security, correctness, performance) after fixes are applied found 5 CRITICAL issues that the implementation pass missed:
1. Missing `import struct` (dead code path never tested)
2. No state machine enforcement (accepted packets in wrong state)
3. VERIFY handler on None fd (unsolicited packet crash)
4. Unbounded VERIFY loop (CPU/disk DoS)
5. Termination deadlock (one-way transfer hangs 60s)

Plus the validation council found a BUG in our own fix (OPEN_FILE never flushed for empty files) and a signal race in the event-driven sender.

The pattern: implementation agents are optimistic (they write code that handles happy paths). Validation agents are adversarial (they think about what breaks).

### Suggested Action
Always dispatch a validation pass after implementation. The validation prompt should explicitly ask "what did we miss?" and provide specific edge cases to trace through.

### Metadata
- Source: conversation
- Related Files: protocols/wslink/protocol/wslink.py
- Tags: code-review, validation, adversarial-thinking, quality
- Pattern-Key: validate.after.implement

---

## [LRN-20260614-004] insight

**Logged**: 2026-06-14T14:15:00Z
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Unimind council dispatch fails on model name mismatch — needs Copilot-specific model IDs

### Details
The Unimind council_dispatch tool attempted to use models `claude-sonnet-4-20250514` and `claude-sonnet-4` against the GitHub Copilot API, which returned HTTP 400 "model_not_supported". The council infrastructure itself works correctly (4-round deliberation protocol fires: response→critique→revision→vote), but every LLM call fails at the API layer.

The workaround was using the Task tool to dispatch 3 parallel general agents instead — this gives equivalent coverage without needing the council infrastructure's model routing.

### Suggested Action
Fix the council dispatcher's model name mapping for Copilot API, or add a fallback that auto-discovers available models via the Copilot API before dispatching.

### Metadata
- Source: error
- Tags: unimind, council, copilot-api, model-routing
- Pattern-Key: council.model.mismatch

---

## [LRN-20260614-005] best_practice

**Logged**: 2026-06-14T14:15:00Z
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
Keep exploit PoC code on fix/ branches, not on main — git supports this naturally

### Details
Git's branch model naturally supports keeping documentation artifacts (exploit PoCs) separate from production code. The approach:
1. Each fix/ branch contains BOTH the fix AND its PoC exploit
2. The clean integration branch merges all fixes, then `git rm -r exploits/` in a final commit
3. PoCs remain accessible via `git show fix/wslink-path-traversal:protocols/wslink/exploits/poc_path_traversal.py`
4. `git log --all -- protocols/wslink/exploits/` shows where they lived

This gives you audit documentation without polluting production, and without needing a separate repo or submodule.

### Suggested Action
Use this pattern for any security fix that warrants a demonstration. The fix branch is the "security advisory" — the clean branch is "what ships."

### Metadata
- Source: conversation
- Related Files: protocols/wslink/exploits/
- Tags: git, security, documentation, branching
- Pattern-Key: exploit.branch.isolation

---

## [LRN-20260614-006] best_practice

**Logged**: 2026-06-14T14:15:00Z
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Rust trait definitions ARE the .h equivalent — define API contracts before implementation

### Details
In Rust, the equivalent of C/C++ header files (.h) is:
- `trait` definitions (interface contracts)
- `struct` declarations with `pub` fields
- `enum` for error types
- `const` for protocol constants

The pattern for a multi-protocol crate:
1. Define shared traits first (`Framer`, `Transport`)
2. Define error enums (`FrameError`, `ZdleError`)
3. Each protocol implements the trait
4. PyO3 `#[pyclass]` wrappers delegate to the trait impl

This gives you:
- Compile-time enforcement that all protocols share the same interface
- Ability to write generic code over any framer
- Clean separation of public API (trait) from implementation detail

The entire Rust crate (3 protocol framers, CRC module, file safety, transport traits, PyO3 bindings, 38 tests) was built in 9 minutes by parallel agents because the trait contracts were defined first.

### Suggested Action
When starting a Rust crate, define traits and error types first. Implementation can be parallelized once the contract is set.

### Metadata
- Source: conversation
- Related Files: crates/pyprotocols-core/src/framer.rs, crates/pyprotocols-core/src/transport.rs
- Tags: rust, architecture, traits, pyo3, parallel
- Pattern-Key: rust.traits.first

---
