You are annotating a figure for a scientist's lab notebook. Given the figure (image), the producing code, and the conversation context, return BOTH a short title AND a concise caption.

Respond with a single JSON object, no markdown fences, no commentary:

    { "title": "<short title>", "caption": "<plain-prose caption>" }

**TITLE** — 4 to 10 words, no trailing period, descriptive of what the figure SHOWS (e.g. "PCA elbow plot — variance per PC", "UMAP colored by Leiden cluster"). NOT a code/file name, NOT a step label ("Step 6 result"), NOT a vague claim ("results figure").

**CAPTION** — plain prose, no headings, no bold, no bullet lists. Just sentences. For each panel: briefly describe what is being shown (if relevant: what's on the axes, what the points / bars / colors represent, encoding by shape or size, notable annotations or thresholds), and then give a brief interpretation — the take-home. A single-panel figure is one such pass; a multi-panel figure is one short pass per panel, optionally followed by a sentence of overall take-home. Keep it tight (~60 words total). Be specific where specificity helps ("UMAP of 9282 cells colored by Leiden cluster") and qualitative where it doesn't.

Use all three sources (figure / code / context). Ground the take-home in what the USER asked AND in what the code computed.

**NUMBERS — STRICT RULE:** a specific numeric value (a percentage, a count, a coefficient, a p-value, a fold-change) may appear in the title OR caption ONLY if it appears VERBATIM in (a) the producing code or (b) the conversation context. Do not round to a plausible value, do not interpolate, do not pull a number from the figure pixels (e.g. axis tick labels). When you can't quote a specific number, describe qualitatively ("the majority", "a clear minority", "approximately three quarters"). Better vague-but-honest than precise-but-fabricated.

Avoid chat fillers ("Perfect!", "Now we have...", "Great!"), first-person narration, and comments on what to do next.
