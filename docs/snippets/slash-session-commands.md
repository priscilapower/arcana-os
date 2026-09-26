<!-- Generated from the slash-command registry by packages/arcana-cli/tests/test_docs_reference.py.
     Don't edit by hand: run `ARCANA_RECORD_GOLDENS=1 uv run pytest packages/arcana-cli/tests/test_docs_reference.py` to regenerate. -->
| Command | What it does |
|---|---|
| `/help` | list these commands |
| `/memory` | show what this agent recalls from this session |
| `/card` | print the resolved card config — temperature, tone, weights |
| `/switch [name]` | load another agent in a new session (no name: pick one) |
| `/retry` | re-run your last message |
| `/save` | force a session snapshot to disk now |
| `/clear` | clear the screen (the session is kept) |
| `/fresh` | start a new session |
| `/no-memory` | start a new stateless session (memory off) |
| `/exit` | close the session and quit |
