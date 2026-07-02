# aba

AI-orchestrated bioinformatics workspace. Chat with the Guide agent (Claude), explore CSV data, and generate plots — all in a persistent, Slack-like interface.

## Running the app

**Requires:** Python 3.8+, Node 20+ (via nvm), an Anthropic API key.

```sh
cp .env.example .env       # then put your key in .env
./start.sh
# open http://localhost:5173
```

The chat history persists in `backend/aba.db`. Closing and reopening the browser picks up exactly where you left off.

Defaults to `claude-haiku-4-5` (cheap). Override per-shell with `ABA_MODEL=claude-sonnet-4-6` (or any other model id) when you need smarter answers.

## Testing without spending tokens

Most dev work (UI, persistence, tool wiring, SSE plumbing) doesn't need the real model. Set `ABA_FAKE_SESSION` to a JSONL fixture of scripted assistant turns and the Guide loop replays them — tools still execute for real.

```sh
# end-to-end smoke test, no API key needed
.venv/bin/python tests/smoke_fake.py

# or run the dev servers in fake mode
ABA_FAKE_SESSION=tests/fixtures/list_files.jsonl ./start.sh
```

Fixture format (one assistant turn per line):
```json
{"blocks": [{"type": "text", "text": "..."}, {"type": "tool_use", "name": "list_data_files", "input": {}}]}
{"blocks": [{"type": "text", "text": "..."}]}
```

Guideline: fake the model unless its reasoning is what you're testing. When you do need a live model, default to Haiku; reach for Sonnet/Opus only for genuine quality checks.

### What Guide can do
- List and read CSV files from `backend/data/`
- Execute Python (pandas + matplotlib) and display plots inline in the chat
- Answer questions about the data in natural language

### Adding your own data
Drop CSV files into `backend/data/` (or set `DATA_DIR=/your/path` in the environment). Guide will find them automatically via `list_data_files`.

## Layout

```
backend/
  main.py        FastAPI app + routes
  guide.py       Claude API loop with streaming tool use
  tools.py       Tool executors: list_data_files, read_csv_info, run_python
  db.py          SQLite message persistence
  config.py      Paths + system prompt
  data/          CSV data files (add yours here)
  artifacts/     Generated plot PNGs (served at /artifacts/*)
frontend/
  src/
    App.tsx              4-column layout
    useChat.ts           SSE streaming + history hook
    components/          Rail, ProjectTree, ChatPane, Message, Composer, AdvisorRail
```
