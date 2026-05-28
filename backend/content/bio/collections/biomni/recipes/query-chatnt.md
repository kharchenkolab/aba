---
name: query-chatnt
description: Answer a natural-language question about a DNA sequence using the ChatNT multimodal language model
when_to_use: Given a plain-English question and a DNA sequence, generate a natural-language answer using the InstaDeepAI/ChatNT model
requires_tools: [run_python]
capabilities_needed: [transformers]
keywords: [ChatNT, DNA, genomics, natural language, sequence understanding, HuggingFace, question answering]
produces: [natural language answer string]
domain: systems_biology
source: biomni:tool/systems_biology.py::query_chatnt
---
# Query ChatNT

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the `InstaDeepAI/ChatNT` pipeline via `transformers.pipeline(model="InstaDeepAI/ChatNT", trust_remote_code=True, device=device)`.
2. Format the prompt as `"{question} <DNA> ?"` where `<DNA>` is a placeholder token; pass the DNA sequence separately as `dna_sequences=[sequence]`.
3. Call the pipeline with `inputs={"english_sequence": english_sequence, "dna_sequences": dna_sequences}`.
4. Return the generated English answer string.

## Key decisions
- Device defaults to -1 (CPU); pass a GPU index for faster inference on long sequences.
- The `<DNA>` token count in the English prompt must equal the number of sequences in `dna_sequences` (always 1 here).
- `trust_remote_code=True` is required because ChatNT uses custom model code on the Hub.

## Caveats
- Model download (~several GB) required on first use; ensure sufficient disk space and internet access.
- ChatNT is trained on specific genomic tasks; accuracy depends heavily on question type and sequence length.
- Long sequences may exceed the model's context window; consider chunking or summarizing input sequences.

## In ABA
Implement with `run_python`; `ensure_capability("transformers")`. Original impl: `source` -> lift to lakeFS later.
