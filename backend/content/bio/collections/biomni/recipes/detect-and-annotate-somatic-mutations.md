---
name: detect-and-annotate-somatic-mutations
description: Call somatic variants from tumor/normal BAM pairs with GATK Mutect2, filter with FilterMutectCalls, and annotate functional impact with SnpEff.
when_to_use: Given aligned tumor and matched-normal BAM files, detect somatic SNVs and indels and annotate their predicted functional consequences.
requires_tools: [run_python]
capabilities_needed: [gatk, snpEff, samtools]
keywords: [somatic mutation, variant calling, Mutect2, GATK, SnpEff, VCF, tumor-normal, SNV, indel, cancer genomics]
produces: [unfiltered VCF, filtered VCF, SnpEff-annotated VCF, mutation summary TXT]
domain: cancer_biology
source: biomni:tool/cancer_biology.py::detect_and_annotate_somatic_mutations
---
# Detect and Annotate Somatic Mutations

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. **Run Mutect2** (GATK somatic variant caller) with tumor and normal BAMs:
   ```
   gatk Mutect2 -R <ref.fa> -I <tumor.bam> -I <normal.bam> \
     -normal <normal_sample_name> -O <prefix>.unfiltered.vcf
   ```
   Normal sample name is derived from the normal BAM basename (split on `.`).

2. **Filter somatic calls** with FilterMutectCalls to apply orientation bias, strand artifact, and other soft-filter annotations:
   ```
   gatk FilterMutectCalls -R <ref.fa> -V <prefix>.unfiltered.vcf \
     -O <prefix>.filtered.vcf
   ```

3. **Functional annotation** with SnpEff using the specified database (default `GRCh38.105`):
   ```
   snpEff -v GRCh38.105 <prefix>.filtered.vcf > <prefix>.annotated.vcf
   ```
   Uses shell redirection (`shell=True`).

4. **Summary statistics** via shell commands on the annotated VCF:
   - Total variant count: `grep -v '^#' | wc -l`
   - Per-type counts for SNP, INS, DEL via `grep`
   - High-impact variant count: `grep 'HIGH' | wc -l`

5. Write a plain-text summary file (`<prefix>_mutation_summary.txt`).

## Key decisions
- Normal sample name inferred from BAM filename (first `.`-delimited token of basename); must match the read group `@SM` tag.
- No panel-of-normals (PON) or germline resource provided by default; add `-pon` and `--germline-resource` for production runs.
- SnpEff run via shell redirection rather than subprocess list to handle `>` operator.
- Variant-type counting is string-match based (`grep SNP/INS/DEL`) — catches SnpEff `ANN` field tokens, not VCF `TYPE` INFO field.

## Caveats
- No GetPileupSummaries / CalculateContamination step; contamination is not estimated.
- No `--f1r2-tar-gz` / LearnReadOrientationModel pass; FFPE/oxidation artifacts may inflate false positives.
- SnpEff version and database must be pre-installed and match the reference genome build.
- Variant-type grep counts may overcount if term appears in INFO/FORMAT fields outside the TYPE field.

## In ABA
Implement with `run_python` (subprocess calls to `gatk`, `snpEff`); `ensure_capability(gatk, snpEff, samtools)`. For production add contamination estimation and orientation-bias filtering steps. Original impl: `source` -> lift to lakeFS later.
