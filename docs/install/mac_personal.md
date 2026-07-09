# Installing ABA on a Mac

ABA is a local workspace where an AI agent does bioinformatics for you — loads
data, runs analyses, makes figures — through a chat in your browser. It runs
entirely on your Mac; nothing leaves your machine except the conversation with
Claude.

For a Linux machine see [linux_personal.md](linux_personal.md); to use a Slurm
cluster see [cluster_personal.md](cluster_personal.md).

## What you'll need

- macOS 13+ (Apple Silicon or Intel) and ~5 GB free disk.
- A credential — either a **Claude.ai subscription** (Pro/Max) or an **Anthropic
  API key**. You'll choose during install; you can change it later.

## Install

The installer is **AI-guided**: it inspects your Mac, installs everything into a
self-contained folder (your system Python, R, and PATH are never touched), and an
assistant walks you through anything unexpected instead of dumping an error.

1. Run the ABA installer (double-click `setup.command` from the download, or paste
   the one-line command from the ABA site into Terminal).
2. **Choose how to sign in** when asked:
   - *Claude.ai subscription* — a browser opens to sign in (recommended).
   - *Anthropic API key* — paste your `sk-ant-…` key.
3. The assistant installs everything (~10–15 min): a private environment with
   Python + R + the bioinformatics stack, the curated **recipe library**, the web
   interface, and the `aba` launcher. When it finishes, your browser opens to
   **http://localhost:8000**.

If a step ever fails, the assistant explains what's wrong and tries the next thing
— you won't be left with a stack trace.

## Using ABA

- Open **http://localhost:8000** any time — ABA runs quietly in the background.
- The **menu-bar icon** is the simplest control: Start / Stop, status, and
  **⤓ Check for updates**.
- Or from Terminal:

  | command | what it does |
  |---|---|
  | `aba up` / `aba stop` | start / stop ABA |
  | `aba status` | is it running? |
  | `aba update` | pull the latest ABA + recipe library, refresh the environment |
  | `aba doctor` | diagnose problems and suggest fixes |
  | `aba auth` | change your credential |

## Keeping it up to date

Click **Check for updates** in the menu-bar app (or run `aba update`). It pulls the
newest ABA and recipe library and refreshes the environment — usually a few
seconds when nothing changed.

## If something goes wrong

Run **`aba doctor`** (or open the menu-bar app's help). It checks your install and
tells you exactly what to fix. Re-running the installer is always safe — it skips
whatever is already in place.

## Uninstall

`aba uninstall` removes ABA — the `~/.aba` folder (environment, repo, runtime)
plus the `aba` launcher and its menu-bar/login agents. Nothing else is left
behind.
