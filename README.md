# ABA

**An AI-orchestrated workspace for biological data analysis.**

ABA is a research environment where a biologist and an AI agent — **Guide** (powered by
a language model of your choice, such as Claude models) — work side by side on a real analysis project, from raw data all the way to
results and conclusions. You describe what you want in plain language; Guide plans the
analysis, runs the code, and produces results you can inspect, revise, and build on. 

Unlike a chatbot bolted onto a notebook, ABA keeps your work as **structured, typed,
persistent objects** — datasets, analyses, figures, findings — each stamped with the
provenance of how it was made. Your project is durable: close the browser, come back
tomorrow, and everything (data, results, and the reasoning behind them) is where you
left it. It's built for long-running research, not one-off questions.

## What you can do

- **Analyze your data by asking.** Import a dataset and ask Guide to run quality control,
  clustering, differential expression, annotation, and more. It picks appropriate methods
  and runs them for real.
- **Work with results, not files.** Datasets, analyses, and figures are first-class
  entities you can pin, revisit, and connect — organized by project, not scattered across
  folders.
- **Trust what you get.** Every result carries an execution record — the code, inputs, and
  environment that produced it — so any figure is reproducible and reviewable.
- **Explore interactively.** Rich built-in viewers open your results (e.g. single-cell
  data in the pagoda3 viewer) directly from a link.
- **Run heavy pipelines on HPC.** Launch large workflow pipelines — including
  **Nextflow / nf-core** — as batch jobs on a **Slurm/HPC** cluster, with results flowing
  back into your project. Work locally on a Mac or Linux machine, or offload the compute to
  a cluster without changing how you work.

ABA's analysis know-how is organized as a library of **recipes** that Guide draws on, so
its capabilities grow over time without changing the core application.

## Requirements

- A **Mac or Linux** machine (or access to a Slurm cluster / Open OnDemand).
- An **Anthropic API key/subscription ** or access to some other language model to power the Guide agent.

The installer bootstraps everything else it needs (Python, the analysis environment, and
the interface) — you don't have to set those up by hand.

## Install

Pick the guide for your setup:

| Setup | Guide |
|---|---|
| **Mac** (your laptop) | [docs/install/mac_personal.md](docs/install/mac_personal.md) |
| **Linux** (laptop, workstation, or server) | [docs/install/linux_personal.md](docs/install/linux_personal.md) |
| **Slurm cluster** (offload jobs to HPC) | [docs/install/cluster_personal.md](docs/install/cluster_personal.md) |
| **Multi-user cluster** (admin setup via Open OnDemand) | [docs/install/cluster_open_ondemand.md](docs/install/cluster_open_ondemand.md) |

Once installed, ABA opens in your browser, configure Anthropic credentials or a custom LLM, create a project, import your data, and start working with Guide.

## Learn more

- **Architecture overview** — [docs/arch/overview.md](docs/arch/overview.md)
- **All documentation** — [docs/](docs/)

## License

[MIT](LICENSE) © 2026 Peter Kharchenko
