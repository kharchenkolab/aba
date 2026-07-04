"""Resource-placement scenarios: same class of heavy pipeline step, different compute
environments + tradeoffs. `compute_env` is injected (see study.py); `prompt` is
biologist-voiced and asserts the data/size so the agent reaches the PLACEMENT decision;
`data_facts` is what an inspection cell "prints" (the execution is stubbed, so this
unblocks the agent's data-verification without real data); `approve` is the turn-2
message that answers the agent's usual blockers so it emits the run; `expected` is the
good decision a well-informed agent makes.

compute_env keys mirror core.exec.compute_env._build_compute_env():
  mode, on_slurm, node_cores, node_mem_gb, node_gpus, walltime_remaining_min,
  partitions=[{partition, cpus_per_node, gpu, wait}], partitions_source, user_access.
"""

_LAPTOP = dict(partitions_source="none", user_access={})
_CLUSTER = dict(partitions_source="live", user_access={"partitions": ["*"]})

_APPROVE_ADATA = ("Approved — go ahead and run it now. `adata` is saved at "
                  "/tmp/data/adata.h5ad, the batch key is `sample`, and adata.X holds raw "
                  "integer counts. Run the full analysis.")


SCENARIOS = [
    # 1 — laptop, moderate job that FITS: just run it inline (no false "can't").
    {
        "name": "laptop_slug_fits",
        "compute_env": {"mode": "local", "on_slurm": False, "node_cores": 8,
                        "node_mem_gb": 16, "node_gpus": 0, "walltime_remaining_min": None,
                        **_LAPTOP},
        "prompt": ("`adata` (about 30,000 cells x 20,000 genes, one 10x sample) is already "
                   "loaded and verified in the kernel. Normalize it, pick highly variable "
                   "genes, and run PCA + a UMAP so I can see the structure."),
        "data_facts": ("AnnData object with n_obs x n_vars = 30000 x 20000\n"
                       "    obs: 'sample'\n    X: raw integer counts"),
        "approve": _APPROVE_ADATA,
        "expected": "Run inline on the laptop (fits 16GB, quick). No background, no false infeasibility.",
    },
    # 2 — laptop, job that clearly WON'T fit + needs a GPU: inform the user, propose options.
    {
        "name": "laptop_infeasible_scvi_2M",
        "compute_env": {"mode": "local", "on_slurm": False, "node_cores": 8,
                        "node_mem_gb": 16, "node_gpus": 0, "walltime_remaining_min": None,
                        **_LAPTOP},
        "prompt": ("I want to run scVI integration on my full atlas — about 2 million cells "
                   "across 40 batches — and train the model properly, then give me the "
                   "integrated latent space and UMAP. `adata` is loaded."),
        "data_facts": ("AnnData object with n_obs x n_vars = 2000000 x 25000\n"
                       "    obs: 'batch' (40 categories), 'cell_type'\n"
                       "    X: raw integer counts (sparse, ~9 GB in memory)"),
        "approve": ("Yes, go ahead — do whatever is needed to get me the integrated latent "
                    "space. `adata` is at /tmp/data/atlas.h5ad, batch key `batch`, raw counts."),
        "expected": ("Recognize this won't fit a 16GB CPU-only laptop (scVI wants a GPU; 2M "
                     "cells >> 16GB). Inform the user + propose options (GPU/cluster, subsample). "
                     "Do NOT naively run it inline and OOM."),
    },
    # 3 — workstation WITH a local GPU, no cluster: run scVI inline on the local GPU.
    {
        "name": "workstation_local_gpu",
        "compute_env": {"mode": "local", "on_slurm": False, "node_cores": 32,
                        "node_mem_gb": 128, "node_gpus": 1, "walltime_remaining_min": None,
                        **_LAPTOP},
        "prompt": ("`adata` (about 300,000 cells x 20,000 genes, 12 batches) is loaded and "
                   "verified. Run scVI integration on it and give me the integrated UMAP."),
        "data_facts": ("AnnData object with n_obs x n_vars = 300000 x 20000\n"
                       "    obs: 'batch' (12 categories), 'sample'\n    X: raw integer counts"),
        "approve": _APPROVE_ADATA,
        "expected": ("Run inline USING the local GPU (local mode + a GPU is present, 128GB). "
                     "Not background (no cluster to submit to)."),
    },
    # 4 — small cluster, this login node is tiny, a big CPU partition is idle: background it.
    {
        "name": "small_cluster_star_align",
        "compute_env": {"mode": "slurm", "on_slurm": True, "node_cores": 4,
                        "node_mem_gb": 16, "node_gpus": 0, "walltime_remaining_min": 480,
                        "partitions": [{"partition": "compute", "cpus_per_node": 32,
                                        "gpu": False, "wait": "idle - available now"}],
                        **_CLUSTER},
        "prompt": ("I have 12 bulk RNA-seq FASTQ pairs staged. Align them with STAR against the "
                   "mouse genome (build the index if needed) and give me the gene-count matrix."),
        "data_facts": ("DATA_DIR: 24 files — sample01_R1.fastq.gz, sample01_R2.fastq.gz, ... "
                       "sample12_R1.fastq.gz, sample12_R2.fastq.gz (~35M read pairs each). "
                       "Mouse GRCm39 FASTA + GTF present."),
        "approve": ("Go ahead and run the full alignment now. The FASTQs and the GRCm39 "
                    "reference are in DATA_DIR."),
        "expected": ("Background to the compute partition (STAR needs ~16 cores / ~40GB — more "
                     "than this 4c/16GB login node). est_cores/est_mem set."),
    },
    # 5 — THE tradeoff: GPU queue is busy (~3h), but THIS node has a local GPU the job fits.
    {
        "name": "cluster_local_gpu_vs_busy_queue",
        "compute_env": {"mode": "slurm", "on_slurm": True, "node_cores": 16,
                        "node_mem_gb": 64, "node_gpus": 1, "walltime_remaining_min": 600,
                        "partitions": [{"partition": "gpu", "cpus_per_node": 32, "gpu": True,
                                        "wait": "~3 h wait (busy)"},
                                       {"partition": "cpu", "cpus_per_node": 64, "gpu": False,
                                        "wait": "~1 h wait"}],
                        **_CLUSTER},
        "prompt": ("`adata` (about 150,000 cells, 8 batches) is loaded and verified. Run scVI "
                   "on it — on a GPU it's only ~20 minutes. I'd like the latent space soon."),
        "data_facts": ("AnnData object with n_obs x n_vars = 150000 x 20000\n"
                       "    obs: 'sample' (8 categories)\n    X: raw integer counts"),
        "approve": _APPROVE_ADATA,
        "expected": ("Prefer the LOCAL GPU (this node has one; the job fits in ~20min) over "
                     "waiting ~3h in the GPU queue. Run inline on the local GPU (or explicitly "
                     "weigh local-GPU-now vs queue and choose local)."),
    },
    # 6 — cluster, no local GPU, GPU partition IDLE, big GPU job: background to GPU partition.
    {
        "name": "cluster_idle_gpu_big_job",
        "compute_env": {"mode": "slurm", "on_slurm": True, "node_cores": 8,
                        "node_mem_gb": 32, "node_gpus": 0, "walltime_remaining_min": 720,
                        "partitions": [{"partition": "gpu", "cpus_per_node": 32, "gpu": True,
                                        "wait": "idle - available now"},
                                       {"partition": "cpu", "cpus_per_node": 64, "gpu": False,
                                        "wait": "idle"}],
                        **_CLUSTER},
        "prompt": ("`adata` (about 500,000 cells, 20 batches) is loaded AND already saved to "
                   "/tmp/data/adata.h5ad (raw counts, batch key `sample`). Train scVI on it "
                   "properly — ~45 minutes on a GPU — and return the latent space."),
        "data_facts": ("AnnData object with n_obs x n_vars = 500000 x 22000\n"
                       "    obs: 'sample' (20 categories)\n    X: raw integer counts\n"
                       "    on disk: /tmp/data/adata.h5ad"),
        "approve": _APPROVE_ADATA,
        "expected": ("Background to the GPU partition (no local GPU; GPU queue idle; job needs a "
                     "GPU + ~45min). background + est_gpu."),
    },
    # 7 — laptop, long CPU-only job that FITS memory: slug it out inline (raise timeout).
    {
        "name": "laptop_long_cpu_fits",
        "compute_env": {"mode": "local", "on_slurm": False, "node_cores": 8,
                        "node_mem_gb": 32, "node_gpus": 0, "walltime_remaining_min": None,
                        **_LAPTOP},
        "prompt": ("The pseudobulk count matrices for all 24 conditions are loaded (dict "
                   "`mats`). Run differential expression across every pair of conditions with "
                   "10,000 permutations for empirical p-values — I know it's CPU-heavy and slow."),
        "data_facts": ("`mats`: dict of 24 pandas DataFrames (conditions), each ~20000 genes x "
                       "3-6 replicates. Total in memory ~1.5 GB."),
        "approve": ("Go ahead and run the full permutation DE now — I understand it will take a "
                    "while."),
        "expected": ("Run inline (no cluster; CPU-only; fits 32GB) — raise the timeout / note it "
                     "will take a while. Not background (nothing to submit to)."),
    },
    # 8 — cluster exists but NO GPU anywhere; GPU pipeline requested: surface it, offer CPU path.
    {
        "name": "cluster_no_gpu_anywhere",
        "compute_env": {"mode": "slurm", "on_slurm": True, "node_cores": 8,
                        "node_mem_gb": 32, "node_gpus": 0, "walltime_remaining_min": 480,
                        "partitions": [{"partition": "compute", "cpus_per_node": 48,
                                        "gpu": False, "wait": "idle - available now"}],
                        **_CLUSTER},
        "prompt": ("`adata` (about 200,000 cells, 10 batches) is loaded and verified. Run scVI "
                   "integration and give me the latent space."),
        "data_facts": ("AnnData object with n_obs x n_vars = 200000 x 20000\n"
                       "    obs: 'batch' (10 categories)\n    X: raw integer counts"),
        "approve": _APPROVE_ADATA,
        "expected": ("Recognize there is NO GPU anywhere (cluster is CPU-only). Surface the "
                     "constraint and offer a CPU-viable path (scVI on CPU is slow; or Harmony/"
                     "scanorama on CPU, or subsample). Don't silently queue a GPU job that "
                     "can't be placed."),
    },
]
