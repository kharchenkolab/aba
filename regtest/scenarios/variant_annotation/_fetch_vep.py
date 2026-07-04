"""BUILD-TIME fetch of VEP consequence annotations for the variant_annotation scenario.

Fetch-ONCE at build time. This script hits the Ensembl VEP REST API
(rest.ensembl.org for GRCh38, grch37.rest.ensembl.org for GRCh37) for the 24
variants in this scenario, on BOTH assemblies, and writes two small local tables:

    data/vep_grch38.tsv   — consequences if the file's positions are read as GRCh38
                            (the WRONG build the header declares; scrambled biology)
    data/vep_grch37.tsv   — consequences on the correct GRCh37 build (clean biology)

Each table: variant_id, chrom, pos, ref, alt, gene, consequence, impact.

The SCENARIO RUN is then fully LOCAL: the agent reads the VCF + these provided
annotation tables (a scientist who already ran annotation), never the network.

    tools/scenario-venv/bin/python regtest/scenarios/variant_annotation/_fetch_vep.py

No third-party deps (stdlib urllib only); does NOT pip install anything.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"

# Import the canonical variant list from the data generator so the two stay in sync.
import importlib.util

_spec = importlib.util.spec_from_file_location("_make_data", HERE / "_make_data.py")
_md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_md)
VARIANTS = _md.VARIANTS  # (chrom, pos, id, ref, alt, gene, label, tier, c37, c38)

GRCH38 = "https://rest.ensembl.org"
GRCH37 = "https://grch37.rest.ensembl.org"

# Genes the panel is supposed to cover (used only to PREFER a panel-gene transcript
# when VEP returns several overlapping genes — never to override the consequence).
PANEL_GENES = {"BRCA1", "TP53", "CFTR", "EGFR"}

# Rank consequence severity so we can pick the representative transcript line.
SEVERITY = [
    "transcript_ablation", "splice_acceptor_variant", "splice_donor_variant",
    "stop_gained", "frameshift_variant", "stop_lost", "start_lost",
    "transcript_amplification", "inframe_insertion", "inframe_deletion",
    "missense_variant", "protein_altering_variant", "splice_region_variant",
    "incomplete_terminal_codon_variant", "start_retained_variant",
    "stop_retained_variant", "synonymous_variant", "coding_sequence_variant",
    "mature_miRNA_variant", "5_prime_UTR_variant", "3_prime_UTR_variant",
    "non_coding_transcript_exon_variant", "intron_variant",
    "NMD_transcript_variant", "non_coding_transcript_variant",
    "upstream_gene_variant", "downstream_gene_variant", "TFBS_ablation",
    "TFBS_amplification", "TF_binding_site_variant",
    "regulatory_region_ablation", "regulatory_region_amplification",
    "feature_elongation", "regulatory_region_variant", "feature_truncation",
    "intergenic_variant",
]
SEV_RANK = {c: i for i, c in enumerate(SEVERITY)}


def _vep(base: str, vcf_line: str) -> dict:
    url = base + "/vep/human/region"
    payload = json.dumps({"variants": [vcf_line]}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    last = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())[0]
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 503):
                time.sleep(2 + 3 * attempt)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            time.sleep(2 + 3 * attempt)
    raise RuntimeError(f"VEP failed for {vcf_line!r} on {base}: {last}")


def _pick(res: dict) -> tuple[str, str, str]:
    """Return (gene, consequence, impact) for the representative transcript line.

    Use VEP's own most_severe_consequence as the consequence. For the gene, prefer
    a transcript carrying that consequence; among those prefer a panel gene, then a
    named gene. Falls back to most-severe transcript / regulatory / intergenic.
    """
    most_severe = res.get("most_severe_consequence", "")
    tcs = res.get("transcript_consequences", []) or []

    def rank(t):
        terms = t.get("consequence_terms", []) or []
        best = min((SEV_RANK.get(x, 999) for x in terms), default=999)
        return best

    if tcs:
        # transcripts that carry the most-severe consequence
        carriers = [t for t in tcs if most_severe in (t.get("consequence_terms") or [])]
        pool = carriers or sorted(tcs, key=rank)
        # prefer a panel gene among the pool
        panel = [t for t in pool if (t.get("gene_symbol") or "") in PANEL_GENES]
        chosen = (panel or [t for t in pool if t.get("gene_symbol")] or pool)[0]
        gene = chosen.get("gene_symbol") or chosen.get("gene_id") or "."
        terms = chosen.get("consequence_terms") or [most_severe]
        cons = most_severe if most_severe in terms else terms[0]
        impact = chosen.get("impact") or _impact_of(cons)
        return gene, cons, impact

    # no transcript consequences — regulatory or intergenic
    for key in ("regulatory_feature_consequences", "motif_feature_consequences"):
        feats = res.get(key) or []
        if feats:
            cons = (feats[0].get("consequence_terms") or [most_severe])[0]
            return ".", cons, feats[0].get("impact") or _impact_of(cons)
    return ".", most_severe or "intergenic_variant", _impact_of(most_severe or "intergenic_variant")


_HIGH = {"transcript_ablation", "splice_acceptor_variant", "splice_donor_variant",
         "stop_gained", "frameshift_variant", "stop_lost", "start_lost",
         "transcript_amplification"}
_MOD = {"inframe_insertion", "inframe_deletion", "missense_variant",
        "protein_altering_variant"}
_LOW = {"splice_region_variant", "incomplete_terminal_codon_variant",
        "start_retained_variant", "stop_retained_variant", "synonymous_variant"}


def _impact_of(cons: str) -> str:
    if cons in _HIGH:
        return "HIGH"
    if cons in _MOD:
        return "MODERATE"
    if cons in _LOW:
        return "LOW"
    return "MODIFIER"


def _write(path: Path, rows: list[tuple]) -> None:
    header = "variant_id\tchrom\tpos\tref\talt\tgene\tconsequence\timpact"
    lines = [header]
    for r in rows:
        lines.append("\t".join(str(x) for x in r))
    path.write_text("\n".join(lines) + "\n")
    print(f"wrote {path}  ({len(rows)} variants, {path.stat().st_size} bytes)")


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    # tidy order: numeric chrom then pos
    ordered = sorted(VARIANTS, key=lambda v: (int(v[0]), v[1]))

    for base, out_name, build in (
        (GRCH38, "vep_grch38.tsv", "GRCh38"),
        (GRCH37, "vep_grch37.tsv", "GRCh37"),
    ):
        print(f"--- fetching {build} from {base} ---")
        rows = []
        for chrom, pos, vid, ref, alt, *_ in ordered:
            vcf_line = f"{chrom} {pos} {vid} {ref} {alt} . . ."
            res = _vep(base, vcf_line)
            gene, cons, impact = _pick(res)
            rows.append((vid, chrom, pos, ref, alt, gene, cons, impact))
            print(f"  {vid:12s} {chrom}:{pos:<9} {ref}>{alt:<4} -> {gene:10s} {cons:32s} {impact}")
            time.sleep(0.34)  # ~3 req/s, well under Ensembl's 15 req/s cap
        _write(DATA / out_name, rows)


if __name__ == "__main__":
    main()
