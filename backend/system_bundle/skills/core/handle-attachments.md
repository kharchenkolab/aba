---
name: handle-attachments
description: Decide how to use a file the user attached (uploaded / pasted) — read it, view it, analyze it, or register it — by file type and the user's intent
when_to_use: The user attached / uploaded / pasted a file with their message (the turn notes "The user attached the following file(s)…") and you must decide what to do with it
keywords: [attachment, upload, attached file, uploaded file, pasted image, paperclip, view_file, inspect_upload, pdf, paper, screenshot, image, figure, summarize the paper, what is this file, unknown file, binary file, register dataset]
---

# Handling an attached file

A file the user attaches is **just a file on disk** — it does **NOT** auto-enter
your context. You're told its name, type, size, and **path**. *You* decide how to
use each one, based on the path + **what the user is asking**. Never assume a file
must go to the model: a data file may only ever be read by code.

## The decision (sniff → intent → cheapest handling)

| The file is… | and the user wants… | do this | reaches the model? |
|---|---|---|---|
| data / matrix / sequencing (csv, tsv, h5ad, parquet, fastq, bam, vcf…) | analysis / a pipeline | **`run_python`** with the path (`DATA_DIR`-relative or absolute) | no — only your results do |
| a **PDF / paper / doc** | "summarize / what does it say" | **`view_file(path)`** → its text (then summarize) | yes (the text) |
| an **image / screenshot / figure** | "what's in this / look at it" | **`view_file(path)`** → you **see** it (vision) | yes (the image) |
| code / text / config (py, R, md, json, yaml…) | read it | **`view_file`** or **`read_file`** | yes (the text) |
| **unrecognized / binary** | "what is this?" | **`view_file(path)`** → a hex+ascii head + a magic-byte **type guess** → tell the user what you think it is, or ask | only what you pull |

## Rules
- **Pull explicitly.** To read or see a file's content, call `view_file` (or
  `read_file` / `run_python`). Nothing about an attachment is in your context until
  you do.
- **Bulk data → `run_python`, not `view_file`.** `view_file` is for *reading /
  seeing* (a paper, a screenshot, a small table). For a big h5ad / fastq / matrix
  you'll *process*, load it in `run_python` from the path.
- **Unknown is normal, not an error.** `view_file` on an unrecognized file returns
  a type guess + a byte head. Use it to tell the user what it likely is, or ask
  what they want done. If it needs a library to read, `ensure_capability` first,
  then `run_python`.
- **Register only on request.** Attachments are scratch uploads. Turn one into a
  durable **dataset** (`register_dataset`) **only if the user asks** to keep / save
  / register it.
- **Multiple files / ambiguous intent** → handle each by its type; if it's unclear
  what the user wants done with a file, ask rather than guess.
