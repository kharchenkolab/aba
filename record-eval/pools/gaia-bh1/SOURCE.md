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

*(completed at stage A2 by the transcriber)*
