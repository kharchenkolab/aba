# SOURCE — Pool A (`gaia-bh1`)

## Selected paper

- **Citation:** El-Badry, K., Rix, H.-W., Quataert, E., Howard, A. W., Isaacson, H.,
  Fuller, J., Hawkins, K., Breivik, K., Wong, K. W. K., Rodriguez, A. C., Conroy, C.,
  Shahaf, S., Mazeh, T., Arenou, F., Burdge, K. B., Bashi, D., Faigler, S.,
  El-Badry, R., et al. (2023). *A Sun-like star orbiting a black hole.*
  Monthly Notices of the Royal Astronomical Society, 518(1), 1057–1085.
  doi:10.1093/mnras/stac3140. arXiv:2209.06833.
- **PDF:** https://arxiv.org/pdf/2209.06833 — verified HTTP 200, `application/pdf` (~4.3 MB).
- **Replication:**
  - Gaia DR3 archive (source astrometric data, public queries, no registration):
    https://gea.esac.esa.int/archive/ — verified HTTP 200.
  - Author's public reproduction notebook (joint orbit constraints, includes the
    follow-up RV data): https://gist.github.com/kareemelbadry/73d30fa3fe38bb4b2c0a54a671415079
    — verified HTTP 200; linked from the author's "Public Code and Data" page
    (kareemelbadry.github.io/code_data/).
  - The follow-up radial velocities are also tabulated in the paper itself.

## Accessibility check

Both the PDF and the replication surfaces were verified by header fetches only
(per HANDOFF §0 — no dataset downloads): arXiv PDF 200/application-pdf; Gaia
archive 200, open access; gist 200, public. No login required anywhere in the
chain — the only candidate set for which this was true end to end.

## Selection rationale (HANDOFF §4 criteria)

1. **≥15 distinct transcribable results** — ~30–40: Gaia astrometric-solution
   parameters, the joint RV+astrometry orbit (P ≈ 185.6 d, e ≈ 0.45,
   M₂ = 9.62 ± 0.18 M☉), stellar characterization (~8 parameters), flux-ratio
   limits on luminous companions, X-ray/radio non-detections, Galactic-orbit
   kinematics, and quantitative rule-outs of alternative configurations.
2. **Legible investigation arc** — genuinely chronological: candidate selected
   from Gaia DR3 astrometric binaries → (most such candidates known spurious) →
   multi-instrument RV campaign over ~4 months → joint fit confirms and revises
   the orbit → alternatives (luminous companion, inner double-dwarf binary,
   NS/WD) quantitatively excluded → dormant ~9.6 M☉ BH concluded → formation-
   channel tension emerges (common envelope cannot easily produce the wide orbit).
3. **Internal revision** — the pure-Gaia orbital solution is superseded by the
   joint RV+astrometry fit; the inner-binary alternative is seriously
   entertained, then bounded out; the favored formation channels are admitted
   not to comfortably work.
4. **Non-specialist legibility** — masses, a period, an eccentricity, a
   distance, brightness limits; the formation section is model-heavy but
   self-contained.

## Scouting notes — candidates considered (stage A1)

Four scouts (economics / ML systems / astronomy / energy-transport-materials)
returned 11 verified candidates. All PDFs and replication landing pages were
header-checked. Summary, with one-line disposition:

| candidate | domain | verdict |
|---|---|---|
| **El-Badry et al. 2023, Gaia BH1** (MNRAS) | astronomy | **selected** — see above |
| Autor, Dorn & Hanson 2013, "The China Syndrome" (AER) | economics | runner-up: 35–50 results, two textbook reversals (migration null; SSDI vs TAA), login-free mirror at ddorn.net; arc is argument-structured rather than temporal |
| Fetzer 2019, "Did Austerity Cause Brexit?" (AER) | economics | 40+ results, clean single-thread arc; openICPSR needs free login; individual-level data needs UK Data Service registration |
| Donaldson 2018, "Railroads of the Raj" (AER) | economics | elegant arc + ghost-railroads placebo; thinnest main-text result count (~18–25); mild agriculture-adjacency flag |
| Recht et al. 2019, ImageNetV2 (ICML) | ML | richest grid + textbook overturned hypothesis, but ImageNet class vocabulary is heavily animal — excluded under the no-organisms constraint |
| Liu et al. 2024, "Lost in the Middle" (TACL) | ML | crisp hypothesis-elimination chain, fully open; fewer results (~25–35); headline numbers from deprecated closed APIs |
| Coleman et al. 2019, DAWNBench analysis (SIGOPS OSR) | ML | good systems units; shorter arc; entries repo unlicensed |
| Boyajian et al. 2016, KIC 8462852 "Where's the Flux?" (MNRAS) | astronomy | best detective-story narrative; no authors' code exists; second choice within astronomy |
| Shallue & Vanderburg 2018, Kepler-90i (AJ) | astronomy | strongest code story; mildest revision moments |
| Davis & Hausman 2016, SONGS closure (AEJ:Applied) | energy | clean arc, overturned import expectation; openICPSR free-login |
| Cicala 2022, markets vs regulation in dispatch (AER) | energy | explicit estimate-revision moment; PDF extraction unfriendly, more technical |
| Severson et al. 2019, battery cycle life (Nature Energy) | energy/materials | fully open data+code; shallower dependency structure (prediction paper) |

## Transcription notes

*(stage A2, single transcriber; source: arXiv:2209.06833v3 full text incl. appendices)*

### Mapping of findings to the paper

45 findings, F01–F45, in the investigation's discovery order (which is close to,
but not identical to, the paper's section order — see "ordering liberties").

| findings | paper location | arc phase |
|---|---|---|
| F01–F02 | §2, App. E (Fig. E1, Table E1) | candidate selection from DR3; 4/6 spurious |
| F03–F06 | §2.1, Table 1 (astrometry-only block), Fig. 1 | Gaia solution; SED/M★; AMRF; photometric quiet |
| F07–F09 | §3.1, §4.2.1, App. B (Fig. B1), App. G | archival RVs; apastron/scanning-law trap; σ_a0 anomaly |
| F10–F11 | §3.2, App. A, Table D1, Fig. 2 | first discrepant RV; 39-spectrum campaign |
| F12–F17 | §4.1–4.3.2, Table 1, Figs. 3–4, App. C (Table C1, Figs. C1–C2) | joint orbit; RV-only orbit; mass floor; robustness + consistency checks |
| F18–F25 | §5–5.1.1, Tables 1–2, Fig. 5, App. F (Table F1) | G-star characterization; abundances; Li/rotation ages; no luminous companion; not stripped |
| F26–F28 | §6 (Fig. 6), §7–7.1 | Galactic orbit; X-ray/radio limits; accretion expectation |
| F29–F31 | §8.1–8.3 (Fig. 7) | imposter contrast; multiplicity; population context |
| F32–F38 | §8.4.1–8.4.5 (Fig. 8), §10 | formation: CE default → CE ruled out → alternatives |
| F39–F42 | §9–9.1 (Fig. 9), App. G | selection-cut marginality; occurrence rate; DR4 forecast |
| F43–F45 | §9.3, App. E (Table E1) | drive-by results on other candidates (no-question findings) |

### How the DAG was reconstructed

From the paper's own narrative logic, not its section order:

- The spine is chronological and explicit in the text: selection (F01–F02) →
  Gaia solution (F03) → archival RVs uninformative *because* of the sampling
  coincidence (F07→F08, "We thus initiated a spectroscopic follow-up campaign")
  → first discrepant RV (F10) → campaign (F11) → joint fit (F12). Everything
  downstream (mass floor, alternatives, formation, population) hangs off F12/F13.
- Stellar characterization splits in two: the SED/M★ result (F04) uses only the
  Gaia parallax and so precedes the campaign; the spectroscopic results
  (F18–F25) require the HIRES/X-shooter spectra and so depend on F11.
- Every alternative-scenario rule-out (F23–F25, F29–F30) depends on both the
  dynamical mass (F13/F14) and the characterization findings, mirroring §8.1–8.2
  which argues from exactly those two inputs.
- Formation findings (F32–F38) depend on the joint orbit (F12), metallicity
  (F18), abundances-as-no-mass-transfer (F20 feeds §8.4's argument via F32's
  progenitor logic), and the Galactic orbit (F26) — again as argued in §8.4.
- Population findings (F39–F42) depend on the DR3-cut statistics of the original
  solution (F03) and the confirmed system (F12).
- Longest depends_on chain: F01→F02→F03→F07→F08→F10→F11→F12→F32→F33→F34→F35
  (depth 12; the validator's computed longest chain).

### Overturns edges (3, all from the paper's own text)

1. **F10 → F07** (§3.1–3.2): the archival LAMOST RVs were "consistent with no
   RV variation at all"; the first follow-up RV (63.8 km/s) "was clearly
   different from the LAMOST and Gaia RVs". The face-value no-variability
   reading is genuinely revised (the sampling coincidence F08 explains why it
   was wrong).
2. **F12 → F03** (§4.1–4.2, App. C): the joint RV+astrometry fit supersedes the
   pure-Gaia parameters — a0 3.00→2.67 mas (>1σ), e 0.49→0.451, i 121.2→126.6°,
   M2 12.8→9.62 M☉; Table C1 quantifies the revision ("consistent at the 1.6σ
   level", "much tighter"). Mid-arc, as required by the contradiction scenario.
3. **F35 → F32** (§8.4.1): §8.4 opens with CE as the expected channel ("this
   interaction is expected to lead to a common envelope episode"); the COSMIC
   analysis concludes it works "only under extreme (and likely unphysical)
   assumptions" (α ≥ 5, preferred α ≈ 14) — the default formation hypothesis is
   overturned, which is what makes Q4 end unresolved (as the paper's §10 admits).

### Question structure

Q4 (formation) *emerges late*: its first finding is F26 (Galactic orbit, §6) and
it only becomes the driving question at F31–F32, matching the paper — formation
is not on the table until the BH is secure. Q5 (population) emerges even later
(F39), triggered by the observation that Gaia BH1 barely survived the DR3 cuts.
16/45 findings bear on ≥2 questions; exactly 3 bear on none (F43–F45 — the
paper's §9.3/App. E drive-by re-characterizations of *other* candidates, which
end up bearing on nothing in this investigation).

### Ordering liberties taken

- The paper reports the SED fit and AMRF (§2.1) before the archival RVs (§3.1);
  we kept that order (F04–F05 before F07) since §2.1 explicitly feeds candidate
  vetting. The σ_a0 anomaly (§4.2.1/App. B/App. G) is presented after the joint
  fit in the paper but was clearly *investigated* when the solution was being
  vetted — we place it (F09) before the campaign, with the scanning-law finding
  (F08). Conversely the RV-only fit (F13) is placed after the joint fit (F12)
  because the paper fixes its period from the joint solution.
- X-ray/radio limits (§7) are archival lookups keyed only to the source position;
  we let them depend on F02 (candidate identified) rather than the orbit, but
  placed them after the orbit work where the paper reports them (F27–F28).
- The other-candidate follow-ups (F43–F45) happened concurrently with the main
  campaign but are reported in §9.3/App. E; we place them at the end as
  tangential findings. F45's dependence on F02 reflects that the deferred giant
  was flagged during the original vetting.

### Reported results deliberately left out

- Per-instrument observing/reduction detail (App. A) and the full RV table
  (Table D1) — folded into F11's evidence stub rather than one finding per epoch.
- Per-element abundances (Table 2) and Cannon labels (Table F1) — folded into
  F18/F20; one finding per element would be filler.
- The Thiele-Innes parameterization mechanics (§4.1), the astrometric mass
  function f_ast = 11.2 ± 1.9 / 8.00 ± 0.16 M☉ (§4.3.1; the joint value is kept
  in F12's values), and the ˜M selection statistic derivation (App. E) — method,
  not results.
- Predicted self-lensing (~5% brightening, §9.2), future ellipsoidal variability,
  interferometric follow-up prospects, and the long-term fate of the system
  (§10) — forecasts about *other* instruments/epochs with no evidential content
  in this investigation; F42 already carries the one forecast (DR4 yield) the
  paper itself quantifies from its own search-volume analysis.
- The Chakrabarti et al. (2022) concurrent independent analysis (M2 = 11.9
  +2.0/−1.6 M☉, §9.3) — external corroboration published while the paper was in
  review; noted here because a scenario author could stage it as an instruction/
  discussion beat, but it is not a finding *of this paper's* investigation.
- Comparisons to the broader imposter literature beyond the four named systems,
  and the pulsar-binary analogy (§8.4.1) — context, not claims with values.

### Numeric consistency

All shared numbers were cross-checked across findings against Table 1 and the
text: M2 = 9.62 ± 0.18 (F12 = F15's joint reference = F29/F30), P = 185.59
± 0.05 / 185.77 ± 0.31 (joint vs Gaia-only, F12 vs F03), e = 0.451/0.49/0.447
(joint/astrometric/RV-only, F12/F03/F13), f(M) = 4.08 ± 0.08 (F13 = F14),
M★ = 0.93 ± 0.05 (F04 = F14/F15/F18), [Fe/H] = −0.2 ± 0.05 (F18 = F36/F38),
a = 1.40 au ↔ 300 R☉ ↔ periastron 0.77 au (F12/F32/F33/F38), d = 477–480 pc.
PDF-extraction ambiguities (lost minus signs/decimal points in pypdf output)
were resolved against Table D1, the corner-plot annotations (Fig. C2), and
internal arithmetic (e.g. T_p = 2411.8 = −1.1 + 13 × 185.6; thresholds
20000/185.77 = 107.7 and 158/√185.77 = 11.6).
