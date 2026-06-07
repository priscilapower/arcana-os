.PHONY: install lint format typecheck test test-core test-cli pre-commit check

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

# --- pre-commit ---

pre-commit:
	uv run pre-commit run --all-files

pre-commit-dry:
	uv run pre-commit run --all-files --dry-run
