# aba

AI-orchestrated bioinformatics workspace. Chat with the Guide agent (Claude), explore CSV data, and generate plots — all in a persistent, Slack-like interface.

## Running the app

**Requires:** Python 3.8+, Node 20 (via nvm), an Anthropic API key.

```sh
export ANTHROPIC_API_KEY=sk-ant-...
./start.sh
# open http://localhost:5173
```

The chat history persists in `backend/aba.db`. Closing and reopening the browser picks up exactly where you left off.

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
mockup/                  Original static HTML mockups (reference)
```

---

## Static mockups

## Layout

```
mockup/
  index.html      Chat view (data ingestion / dataset structure)
  figure.html     Figure view (per-cell depth distribution)
  finding.html    Finding view (IFN-high monocyte state)
  styles.css      Shared styles, design tokens, grid layout
  icons.svg       Shared inline-SVG icon sprite
  resizers.js     Draggable column + pane dividers
  assets/         Cropped figure / thumbnail bitmaps used inside the HTML
mocks/            Original static PNG mockups for reference
```

The three pages share the same shell (left nav rail, project tree, main column, right advisor rail) and link to each other via the tree.

## Running locally

The pages reference `icons.svg` via `<use href="icons.svg#...">`, which most browsers will only resolve over HTTP (not `file://`). Start any static server in the `mockup/` directory:

```sh
cd mockup
python3 -m http.server 8000
# then open http://localhost:8000/index.html
```

## Interaction

- Drag the divider between the project tree and the main column to resize it.
- Drag the divider between the main column and the advisor rail to resize it.
- On the Figure and Finding pages, drag the horizontal divider in the middle column to resize the figure / finding pane vs. the chat pane below.
- The leftmost dark rail stays a fixed width.
