.PHONY: install lint format typecheck test test-core test-cli test-fast test-slow \
        test-security coverage-tools done pre-commit check

install:
	uv sync --all-packages --all-extras

# --- lint & format ---

lint:
	uv run ruff check .

format:
	uv run ruff format .

lint-fix:
	uv run ruff check --fix .

# dry-run: check without modifying files
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright packages/arcana-core/arcana packages/arcana-cli/arcana_cli

# --- type checking ---

typecheck:
	uv run pyright packages/arcana-core/arcana packages/arcana-cli/arcana_cli

# --- tests ---

test: test-core test-cli

test-core:
	uv run pytest packages/arcana-core/tests/ -v -m "not llm_eval"

test-cli:
	uv run pytest packages/arcana-cli/tests/ -v -m "not llm_eval"

# --- tool-gateway test tiers ---
# The fast default tier is fully mocked and deterministic (no real process,
# network, or MCP server); the slow tier spawns real sandbox processes and is
# gated out of it. `test-security` is the merge-gating security catalog + the
# guard-has-test presence check; `coverage-tools` enforces the line floor on the
# tool surface (measured over the whole tool suite, slow tier included).

test-fast:
	uv run pytest packages/arcana-core/tests/ -m "not llm_eval and not slow"
	uv run pytest packages/arcana-cli/tests/ -m "not llm_eval and not slow"

test-slow:
	uv run pytest packages/arcana-core/tests/ -m "slow"

test-security:
	uv run pytest packages/arcana-core/tests/tools/security/ -m "security"

coverage-tools:
	uv run pytest packages/arcana-core/tests/tools/ packages/arcana-core/tests/agents/ \
		-m "not llm_eval" -o addopts="" \
		--cov=arcana.tools --cov-report=term-missing --cov-fail-under=90

# --- pre-merge gate ---
# Everything that must be green before a merge: lint + format + types, the full
# test suite (fast + slow), the security catalog, and the tool coverage floor.
done: check test test-security coverage-tools

# --- pre-commit ---

pre-commit:
	uv run pre-commit run --all-files

pre-commit-dry:
	uv run pre-commit run --all-files --dry-run
