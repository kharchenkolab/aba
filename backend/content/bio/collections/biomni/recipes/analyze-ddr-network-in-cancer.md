---
name: analyze-ddr-network-in-cancer
description: Reconstruct and analyze the DNA Damage Response gene co-expression network in cancer samples, identify hub/bottleneck genes, community sub-pathways, and synthetic lethality candidates.
when_to_use: When given per-sample gene expression and mutation data and asked to characterize DDR pathway disruption or therapeutic vulnerabilities in cancer.
requires_tools: [run_python]
capabilities_needed: [pandas, scipy, networkx, gseapy]
keywords: [DDR, DNA damage response, cancer, network analysis, synthetic lethality, BRCA1, ATM, homologous recombination, NHEJ, BER, NER, MMR, pathway enrichment]
produces: [graphml network file, enrichment CSV, research log string]
domain: cancer_biology
source: biomni:tool/cancer_biology.py::analyze_ddr_network_in_cancer
---
# Analyze DDR Network in Cancer

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load gene expression CSV (genes x samples) and binary mutation matrix CSV.
2. Filter both matrices to a curated list of ~34 DDR genes spanning sensors (ATM, ATR, MRE11), transducers (CHEK1/2, TP53, BRCA1/2), HR, NHEJ, BER, NER, and MMR effectors.
3. Build an undirected NetworkX graph: nodes = DDR genes annotated with per-sample mutation frequency; edges added for gene pairs with |Pearson r| > 0.4 and p < 0.05 across samples.
4. Compute degree centrality and betweenness centrality; report top-5 hub genes and top-5 bottleneck genes.
5. Run Louvain community detection (python-louvain) to cluster the network into DDR sub-pathways.
6. Run gseapy Enrichr against GO_Biological_Process_2021 on the DDR gene list; filter results for "dna repair", "damage", "recombination", "checkpoint" terms.
7. Identify synthetic lethality candidates: edge pairs where |r| > 0.6 and one gene has mutation frequency > 10% while the other has < 5%.
8. Save network as GraphML and enrichment table as CSV; return a structured research log.

## Key decisions
- Pearson correlation threshold 0.4 / p < 0.05 to define co-expression edges.
- Mutation frequency derived as row mean of the binary mutation matrix.
- Louvain is optional; gracefully skips if python-louvain is absent.
- Synthetic lethality criterion: strong co-expression + asymmetric mutation burden.

## Caveats
- Thresholds (0.4 correlation, 80th-percentile for gating) are heuristic; adjust to dataset size.
- GSEA step requires internet access (Enrichr API via gseapy).
- Small sample sizes make Pearson correlations unreliable; check n before trusting edges.

## In ABA
Implement with `run_python`; `ensure_capability("pandas", "scipy", "networkx", "gseapy")`. GraphML output can be stored as a browsable artifact. Original impl: `biomni:tool/cancer_biology.py::analyze_ddr_network_in_cancer` -> lift to lakeFS later.
