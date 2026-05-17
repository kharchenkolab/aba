# aba

Lightweight HTML mockups of a research-workspace UI (project tree, chat with advisor agents, figure and finding views).

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
