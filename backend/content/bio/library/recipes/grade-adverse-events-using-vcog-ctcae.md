---
name: grade-adverse-events-using-vcog-ctcae
description: Grade adverse events in veterinary/oncology animal studies using VCOG-CTCAE v1.1 criteria
when_to_use: Standardized AE grading for preclinical or veterinary clinical trial data; safety signal detection
requires_tools: [run_python]
capabilities_needed: [pandas]
keywords: [adverse events, VCOG-CTCAE, toxicity grading, veterinary oncology, clinical trial safety, hematology]
produces: [graded events CSV, progression analysis CSV, VCOG criteria JSON reference]
domain: pharmacology
source: biomni:tool/pharmacology.py::grade_adverse_events_using_vcog_ctcae
---
# Grade Adverse Events Using VCOG-CTCAE

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load a CSV with columns: `subject_id`, `time_point`, `symptom`, `severity`, `measurement` (optional).
2. Apply VCOG-CTCAE v1.1 grading for 11 defined symptom categories (neutropenia, anemia, thrombocytopenia, vomiting, diarrhea, anorexia, ALT increase, creatinine increase, fever, weight loss, alopecia/neuropathy). If a quantitative `measurement` is present, use numeric range thresholds; otherwise fall back to severity text ("mild"→1, "moderate"→2, "severe"→3, "life-threatening"→4, "death"→5).
3. Append `vcog_grade` and `grading_rationale` columns to the DataFrame.
4. If `time_point` is present, pivot by subject×symptom across time points and classify progression trend as increasing/decreasing/stable/fluctuating.
5. Summarize: grade distribution, per-symptom max/mean/count, subject-level summary (Grade 3+ flag), top-10 most severe events.
6. Save graded events CSV, progression CSV, and VCOG criteria JSON.

## Key decisions
- Numeric range matching takes priority over severity text when `measurement` is provided.
- Bonferroni-style safety: unknown symptoms default to Grade 1 rather than Grade 0.

## Caveats
- VCOG-CTCAE criteria embedded in code are specific to v1.1; verify against updated versions for newer studies.
- Symptoms not in the 11 hardcoded categories fall back to generic severity mapping.

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. Original impl: `source` -> lift to lakeFS later.
