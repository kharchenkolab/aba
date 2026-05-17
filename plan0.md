# Plan 0 — ABA Guide Chat MVP

**Goal:** A working web app where you can open a browser, chat with the Guide agent (Claude),
ask it to look at CSV files in a local data folder, generate matplotlib plots, and pick up exactly
where you left off after closing the browser. Visually close to the `mockup/index.html` screen.

---

## Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI (Python) | On-prem, matches arch spec; easy subprocess/tool execution |
| Frontend | Vite + React (TypeScript) | Structured, reusable; import CSS tokens from existing `styles.css` |
| Database | SQLite (single file) | Zero-config for single user; swap to Postgres later |
| AI | Claude API (`claude-sonnet-4-6`) with streaming tool use | Guide agent; structured tool calls |
| Streaming | Server-Sent Events (SSE) | Simple, works without WebSocket infrastructure |
| Plots | matplotlib → PNG → served statically | Python subprocess saves to `artifacts/`; img shown inline |

---

## Directory layout

```
aba/
  backend/
    main.py          # FastAPI app, routes, static serving
    guide.py         # Claude API loop: streaming, tool dispatch, persistence
    tools.py         # Tool definitions + executors (list_files, read_csv, run_python)
    db.py            # SQLite schema + helpers
    config.py        # DATA_DIR, ARTIFACTS_DIR, ANTHROPIC_API_KEY (from env)
    data/            # The pre-defined data folder (CSV files go here)
    artifacts/       # Generated plots (served at /artifacts/<uuid>.png)
    aba.db           # SQLite database (created on first run)
  frontend/
    index.html
    src/
      main.tsx
      App.tsx
      components/
        Layout.tsx       # 4-column grid (rail / tree / chat / right)
        ChatPane.tsx     # Message list + scroll + composer
        Message.tsx      # Renders a single message (text | image | tool-indicator | card)
        Composer.tsx     # Input box, send button
        ProjectTree.tsx  # Left tree panel (static structure for MVP)
        AdvisorRail.tsx  # Right panel (Guide online, others quiet)
      hooks/
        useChat.ts       # Fetch history, send message, consume SSE stream
      styles/
        tokens.css       # Copied / imported from mockup/styles.css design tokens
    vite.config.ts   # Proxy /api and /artifacts to backend
  mockup/            # Existing static mockup (untouched, kept for reference)
  misc/
```

---

## Backend

### `db.py` — message persistence

Single table. Stores the **full Claude API message format** (role + content as JSON) so the
conversation can be replayed exactly when the user returns. Tool-use and tool-result messages
are stored alongside text messages; the frontend filters for display.

```sql
CREATE TABLE IF NOT EXISTS messages (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  role      TEXT    NOT NULL,   -- 'user' | 'assistant'
  content   TEXT    NOT NULL,   -- JSON: Claude API content block array
  ts        TEXT    NOT NULL    -- ISO timestamp
);
```

Helpers: `append_message(role, content_blocks)`, `get_all_messages() → list`.

### `config.py`

```python
DATA_DIR      = Path(os.getenv("DATA_DIR", "backend/data"))
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "backend/artifacts"))
API_KEY       = os.environ["ANTHROPIC_API_KEY"]
MODEL         = "claude-sonnet-4-6"
SYSTEM_PROMPT = """..."""  # see below
```

### `tools.py` — three tools for MVP

**`list_data_files`** — no input; returns names + sizes of all CSV files in DATA_DIR.

**`read_csv_info`** — input: `filename (str)`; reads CSV with pandas, returns shape, column
names + dtypes, first 5 rows as a markdown table.

**`run_python`** — input: `code (str)`; runs the code in a subprocess:
- Working directory: a fresh temp dir per call
- `MPLBACKEND=Agg` set in env (so matplotlib doesn't try to open a GUI)
- Timeout: 30 s
- After execution: collect any `.png` files created in the temp dir, move to `ARTIFACTS_DIR`
  with UUID filenames
- Returns: `{stdout, stderr, plots: [{url: "/artifacts/<uuid>.png", filename}]}`
- Claude is told in the system prompt to save plots as `plt.savefig("output.png")` (or any
  `.png` name); they will be captured automatically.

Tool schemas are passed to the Claude API in the standard `tools=` list.

### `guide.py` — the Claude loop

**`send_message(user_text) → AsyncGenerator[SSEEvent]`**

1. Append `{role: user, content: [{type: text, text: user_text}]}` to DB.
2. Load all messages from DB → `history`.
3. Open a streaming Claude API call with `history`, `system`, and `tools`.
4. Consume the stream, yielding SSE events to the client:
   - `{"type": "delta", "text": "..."}` — text chunk from Claude
   - `{"type": "tool_start", "name": "...", "input": {...}}` — Claude is calling a tool
   - `{"type": "tool_result", "name": "...", "plots": [...], "text": "..."}` — tool finished
   - `{"type": "done"}` — stream complete
5. When `stop_reason == "tool_use"`:
   - Execute each requested tool (in `tools.py`)
   - Append assistant message (with tool_use blocks) to DB
   - Append tool_result message to DB
   - Loop: make another Claude API call to continue
6. When done: append final assistant message to DB.

**History reconstruction:** DB rows are fed directly to the Claude API as-is (they are already
in API format). This is what makes "resume after closing the browser" work — the entire
conversation is replayed to Claude with no information loss.

### `main.py` — FastAPI routes

```
GET  /api/history          → list of display-ready messages (strip tool internals, add plot URLs)
POST /api/chat             → body: {text}, response: SSE stream of events
GET  /artifacts/{filename} → static serve of generated PNGs
GET  /                     → serve frontend build (index.html)
```

Vite dev proxy routes `/api` and `/artifacts` to `http://localhost:8000` during development.

---

## System prompt (Guide)

```
You are Guide, an AI bioinformatics assistant embedded in a research workspace.
You help scientists explore data, run analyses, and interpret results.

Your data access:
- You have a set of tools to list and read CSV files from the local data folder,
  and to execute Python code.
- When asked to make a plot, write self-contained matplotlib code and save figures
  with plt.savefig("output.png") — the system will capture any .png files automatically.
- pandas and matplotlib are available. Do not import other libraries unless told they exist.

Behavior:
- Be direct and concise. Lead with the finding, not the method.
- When you read data, summarize what you found before asking what to do with it.
- When you make a plot, briefly describe what it shows after sharing it.
- Ask before running large or destructive operations.
- Use markdown for structure (bold, lists, code blocks).
```

---

## Frontend

### Message rendering (`Message.tsx`)

Each message in the display has a `role` and a list of typed **blocks**:

| Block type | Rendered as |
|---|---|
| `text` | Markdown (react-markdown) |
| `image` | Inline `<img>` with the plot URL |
| `tool_indicator` | Small italicised status line: *"Guide ran `run_python`"* |
| `card` (future) | Bordered dataset card |

The SSE stream builds up the current assistant message block-by-block in local React state.
When `done` arrives, the complete message is committed to the display list.

### `useChat.ts` hook

```
state: { messages: DisplayMessage[], streaming: boolean, streamingContent: Block[] }

loadHistory()       → GET /api/history, set messages
sendMessage(text)   → optimistically append user msg, open EventSource to POST /api/chat,
                       accumulate SSE events into streamingContent,
                       on "done" push final assistant message into messages
```

### Layout

Four-column CSS grid matching the mockup tokens exactly:
- **Rail** (70 px, dark) — brand + nav icons + user avatar
- **Tree** (264 px, light grey) — project name + DATA / FIGURES / FINDINGS / MANUSCRIPT sections;
  DATA section is dynamic (populated from `/api/history` metadata); others static for MVP
- **Chat** (flex 1) — tabs row (Guide active), message list, composer
- **Right rail** (294 px) — Advisor Team panel; Guide shows "online", others show "quiet"

Draggable dividers (`resizers.js` logic ported to a React `useResizer` hook).

---

## Data folder

`backend/data/` ships with two sample CSV files for immediate demo:
- `cells.csv` — synthetic single-cell metadata (sample, n_genes, n_counts, mt_fraction)
- `samples.csv` — sample metadata (sample_id, condition, donor, timepoint)

These are enough to let Guide demonstrate `list_data_files`, `read_csv_info`, and a depth
distribution violin plot via `run_python` — matching the spirit of the mockup conversation.

---

## What is NOT in scope for this plan

- Real scRNA-seq / h5ad data (scanpy not installed)
- Advisor agents (Methodologist, Skeptic, etc.) — rail is present but static
- Figure / finding views (existing mockup pages remain separate)
- User authentication (single user, localhost only)
- lakeFS / MinIO / Nextflow integration
- Branch creation or provenance tracking
- Project-scoped conversations (one global conversation for now)

---

## Implementation phases

| Phase | Work | Output |
|---|---|---|
| 1 — Scaffold | `backend/` with FastAPI + SQLite shell; `frontend/` with Vite + React + 4-column layout matching mockup tokens | App loads, layout renders, no real chat yet |
| 2 — Chat loop | Claude API call (no tools), SSE streaming, message persistence, history load on reload | Real streaming chat with Guide; reconnect works |
| 3 — Tool system | `list_data_files`, `read_csv_info`, `run_python` with subprocess + artifact capture; tool indicators in UI | Guide can explore and describe the data folder |
| 4 — Plot rendering | Frontend renders image blocks; Guide makes a matplotlib violin/scatter plot inline in chat | End-to-end: "show me a plot of n_genes by condition" → inline PNG |
| 5 — Polish | Composer UX (Enter to send, loading state), scroll-to-bottom, error handling, sample data files | Demo-ready |

Each phase is independently shippable. Phases 1–2 can be done in a day; 3–5 add another day.
