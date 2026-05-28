---
name: bayesian-finemapping-with-deep-vi
description: Bayesian fine-mapping of GWAS loci using a deep neural-network variational inference model to compute posterior inclusion probabilities and credible sets
when_to_use: When given GWAS summary statistics and an LD matrix and asked to prioritise causal variants within an associated locus
requires_tools: [run_python]
capabilities_needed: [torch, pandas, scipy, matplotlib, numpy]
keywords: [fine-mapping, GWAS, posterior inclusion probability, PIP, credible set, variational inference, deep learning, LD matrix, causal variant]
produces: [pip_scores_csv, credible_set_csv, pip_bar_plot_png]
domain: genetics
source: biomni:tool/genetics.py::bayesian_finemapping_with_deep_vi
---
# Bayesian Fine-Mapping with Deep Variational Inference

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load GWAS summary statistics (CSV/TSV) with columns `variant_id`, `effect_size`, `pvalue`, and optionally `se`.
2. Compute Z-scores: `effect_size / se` when SE is present; otherwise approximate from p-values via `scipy.stats.norm.ppf`.
3. Convert Z-scores and the LD matrix to `torch.FloatTensor`.
4. Define a `VariationalFineMapping` neural network: two-layer ReLU encoder (input dim = n_variants, hidden dim configurable) followed by a linear layer outputting log-alpha; apply sigmoid to get PIPs.
5. ELBO loss: Bernoulli samples drawn from PIPs; likelihood = negative squared residual between observed and LD-projected Z-scores; sparsity prior = `-0.01 * sum(PIPs)`; minimise with `torch.optim.Adam`.
6. Train for `n_iterations` (default 5000) at `learning_rate=0.01`; log loss every 20 % of iterations.
7. Extract final PIPs (`torch.no_grad`); sort variants descending; compute cumulative PIPs; include variants until cumulative sum exceeds `credible_threshold` (default 0.95) to form the credible set.
8. Save full results CSV, credible-set CSV, and a bar plot of top-50 PIPs.

## Key decisions
- Approximating Z-scores from p-values requires the sign of the effect size to set direction.
- LD matrix shape must match n_variants; validate before training.
- If no variants clear the cumulative threshold alone, include at least the top-ranked variant.

## Caveats
- The EM loop is a simplified homemade ELBO; production use should validate against SuSiE or FINEMAP.
- Assumes a single causal signal; multi-signal loci need extension (e.g. iterative masking).
- Large loci (>5k variants) will be slow on CPU; consider GPU or reducing region.

## In ABA
Implement with `run_python`; `ensure_capability("torch", "scipy", "pandas", "matplotlib")`. Original impl: `source` -> lift to lakeFS later.
