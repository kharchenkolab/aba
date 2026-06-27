# Installing ABA

Pick the guide that matches your setup:

| Your setup | Guide |
|---|---|
| **A Mac** (just you) | [mac_personal.md](mac_personal.md) |
| **A Linux machine or server** (just you) | [linux_personal.md](linux_personal.md) |
| **An account on a Slurm cluster** — run ABA yourself, offload heavy jobs to Slurm | [cluster_personal.md](cluster_personal.md) |
| **A cluster to set up for many users** via Open OnDemand (administrators) | [cluster_open_ondemand.md](cluster_open_ondemand.md) |

Every install is self-contained (it never touches your system Python/PATH), pulls
the latest code + curated recipe library, and updates in place (`aba update`, or the
Mac menu-bar app). When in doubt, `aba doctor` checks the install and tells you how
to fix any problem.
