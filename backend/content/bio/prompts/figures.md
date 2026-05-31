Figure style:
- Clean layout, white background. No dark themes, no grid clutter, no decorative borders.
- **One panel per figure.** That's the default — every figure is a single panel. Use multi-panel ONLY when (a) you're explicitly comparing methods / conditions / parameters / time points side-by-side, (b) it is a series of visually similar panels that should be viewed together (e.g. multiple marker genes), (c) panels cross-reference each other (e.g. UMAP + cluster-size bar where the relation matters), or (d) the user asked for a composite. Otherwise: separate figures, one panel each.
- **Specific common WRONG figure patterns — do NOT default to these:**
  - multi-panel "Summary dashboard" figures
  - textboxes with explanations included as panels
- In R, **prefer `ggplot2`** over base graphics for analysis figures.
- For dense scatterplots where points overlap (UMAPs, PCA, MA plots, dose-response with many points) — set point alpha to ~0.5–0.7 so overlapping regions show density, not just the topmost layer. In scanpy this is `sc.pl.umap(adata, ..., alpha=0.6)`; in ggplot2 it's `geom_point(alpha=0.6, …)`.
- In multi-step workflows, surface figures inline at key steps incrementally, as you proceed, do not save them all for the end.
