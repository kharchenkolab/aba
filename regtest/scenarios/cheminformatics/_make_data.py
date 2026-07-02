#!/usr/bin/env python3
"""Deterministic generator for the cheminformatics scenario.

Writes data/drugs.csv: a tiny LOCAL table of small-molecule drugs with their
canonical SMILES strings (literals — NO network). The scenario computes
physicochemical descriptors (MW, logP, TPSA, HBD, HBA) and Lipinski rule-of-five
status from these SMILES with RDKit, so the only input that must be staged is the
SMILES table itself.

Planted, checkable truth (RDKit 2024+ Descriptors / Crippen / Lipinski):
  imatinib       MW 493.62  logP 4.59  HBD 2  HBA 7  TPSA  86.3  -> 0 RO5 violations -> PASS
  atorvastatin   MW 558.65  logP 6.31  HBD 4  HBA 4  TPSA 111.8  -> 2 RO5 violations -> FAIL
                 (MW > 500 AND logP > 5 -- yet a blockbuster ORAL statin: RO5 is a
                  guideline, not a hard cutoff.)
  aspirin        MW 180.16  logP 1.31  HBD 1  HBA 3  TPSA  63.6  -> 0 violations -> PASS
  caffeine       MW 194.19  logP -1.03 HBD 0  HBA 3  TPSA  61.8  -> 0 violations -> PASS
  ibuprofen      MW 206.28  logP 3.07  HBD 1  HBA 1  TPSA  37.3  -> 0 violations -> PASS

Lipinski rule of five: a violation is MW > 500, logP > 5, HBD > 5, or HBA > 10;
a compound "passes" with at most one violation.
"""
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# name, drug_class, canonical SMILES (literals; verified to parse with RDKit)
ROWS = [
    ("imatinib", "kinase inhibitor",
     "Cc1ccc(NC(=O)c2ccc(CN3CCN(C)CC3)cc2)cc1Nc1nccc(-c2cccnc2)n1"),
    ("atorvastatin", "statin",
     "CC(C)c1c(C(=O)Nc2ccccc2)c(-c2ccccc2)n(CC[C@@H](O)C[C@@H](O)CC(=O)O)c1-c1ccc(F)cc1"),
    ("aspirin", "NSAID",
     "CC(=O)Oc1ccccc1C(=O)O"),
    ("caffeine", "stimulant",
     "Cn1c(=O)c2c(ncn2C)n(C)c1=O"),
    ("ibuprofen", "NSAID",
     "CC(C)Cc1ccc(C(C)C(=O)O)cc1"),
]


def main():
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "drugs.csv")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "drug_class", "smiles"])
        for name, klass, smi in ROWS:
            w.writerow([name, klass, smi])
    print(f"wrote {path} ({len(ROWS)} compounds)")


if __name__ == "__main__":
    main()
