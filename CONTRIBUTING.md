# Contributing to Arcana OS

Thanks for your interest in contributing! Arcana OS is open source under the
[Apache License 2.0](LICENSE), and contributions of all kinds — code, docs, bug
reports, card ideas — are welcome.

## Project stance

Arcana OS is, and will remain, open source under Apache 2.0. A hosted commercial
edition (`arcana.cloud`) may be offered in the future; the Apache license
permits this for us and for anyone else. Contributing under Apache 2.0 means your
contributions can be used in both the open-source project and any such hosted
edition. There is no separate copyright assignment — see the
[DCO sign-off](#developer-certificate-of-origin-dco) below.

## Ways to contribute

- **Report a bug** — open an issue with steps to reproduce, expected vs. actual
  behaviour, and your environment (OS, Python version, model provider).
- **Suggest a feature** — open an issue describing the problem first, not just
  the solution. For larger ideas, start a discussion before opening a PR.
- **Improve docs** — the docs live in `docs/` and build with MkDocs; fixes and
  clarifications are always welcome.
- **Write code** — see the workflow below.

## Development setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/priscilapower/arcana-os.git
cd arcana-os
uv sync --all-packages --all-extras
uv run pre-commit install
```

Common tasks (run from the repo root):

```bash
uv run ruff check .                                    # lint
uv run ruff format .                                   # format
uv run pyright packages/arcana-core/arcana             # type check core
uv run pyright packages/arcana-cli/arcana_cli          # type check CLI
uv run pytest packages/arcana-core/tests/ -v -m "not llm_eval"
uv run pytest packages/arcana-cli/tests/ -v
```

`pre-commit` runs lint, format, and type checks on staged files; please make
sure it passes before pushing.

## Conventions

- **Import Pydantic models from `arcana.types`**, never from their submodules
  (`from arcana.types import Card`, not `from arcana.types.card import Card`).
- **Type checking is strict** on `arcana-core`. New code in core must pass
  `pyright` in strict mode.
- **Adding a card?** Create `arcana/cards/definitions/<name>.py` following
  `fool.py`, register it in `definitions/__init__.py` in canonical order, and add
  the `Card` enum value in `arcana/types/card.py`.
- **Never edit existing eval prompts** in `arcana/evals/fixtures/prompts.py` once
  they have results — add new ones instead, so regression baselines stay valid.

## Pull request workflow

1. Fork the repo and create a branch from `main`
   (`git checkout -b fix/short-description`).
2. Make your change with tests and docs where relevant.
3. Ensure lint, type checks, and tests pass locally.
4. Commit with a clear message and a **DCO sign-off** (see below).
5. Open a PR against `main`. Describe what changed and why, and link any related
   issue. Keep PRs focused — one logical change per PR is easier to review.

## Developer Certificate of Origin (DCO)

Arcana OS uses the [Developer Certificate of Origin](https://developercertificate.org/)
instead of a CLA. The DCO is a lightweight statement that you wrote the
contribution or otherwise have the right to submit it under the project's
license. There is no paperwork.

You certify the DCO by adding a `Signed-off-by` line to each commit, which Git
adds automatically with the `-s` flag:

```bash
git commit -s -m "Fix temperature blending for modifier cards"
```

This appends a line using your configured name and email:

```
Signed-off-by: Your Name <you@example.com>
```

Make sure your Git identity is set so the sign-off is valid:

```bash
git config user.name  "Your Name"
git config user.email "you@example.com"
```

Forgot to sign off your last commit? Amend it:

```bash
git commit --amend -s --no-edit
```

For the full text of what you're certifying, see
<https://developercertificate.org/>.

## Code of conduct

Be respectful and constructive. Harassment or hostile behaviour isn't welcome in
issues, PRs, or discussions.

## Questions

Open a [GitHub Discussion](https://github.com/priscilapower/arcana-os/discussions)
or an issue. Thanks for helping give agents a soul. 🌌
