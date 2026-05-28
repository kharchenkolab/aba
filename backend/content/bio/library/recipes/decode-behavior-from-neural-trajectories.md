---
name: decode-behavior-from-neural-trajectories
description: Reduce population neural activity to low-dimensional trajectories via PCA then decode behavioral variables using a Kalman filter
when_to_use: When relating high-dimensional neural spiking or fluorescence recordings to continuous behavioral variables (position, velocity, limb angle, etc.)
requires_tools: [run_python]
capabilities_needed: [numpy, pandas, scikit-learn, pykalman, matplotlib]
keywords: [neural decoding, Kalman filter, PCA, dimensionality reduction, population dynamics, neural trajectories, behavioral variables, brain-machine interface]
produces: [pca_explained_variance.png, decoded_trajectories.png, neural_decoding_results.pkl, decoding_results_sample.csv, neural_decoding_log.txt]
domain: bioengineering
source: biomni:tool/bioengineering.py::decode_behavior_from_neural_trajectories
---
# Decode Behavior from Neural Trajectories

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Accept neural data (T × N_neurons) and behavioral data (T × N_vars) as numpy arrays. Replace NaN with 0.
2. Split 80/20 into train/test sets (`sklearn.model_selection.train_test_split`, random_state=42).
3. Fit PCA (`sklearn.decomposition.PCA`, n_components) on training neural data; transform both splits. Report total variance explained.
4. Initialize a `pykalman.KalmanFilter` with initial_state_mean = zeros(N_vars), n_dim_obs = n_components. Train with `.em(X_train_pca, y_train)`.
5. Decode test set: `kf.filter(X_test_pca)` → predicted behavioral state.
6. Evaluate with `sklearn.metrics.mean_squared_error(y_test, y_pred)`.
7. Save outputs: PCA scree bar chart, true-vs-predicted time traces for first two behavioral variables, full results as pickle (PCA model, KF model, arrays, MSE), sample CSV of first 100 time points.

## Key decisions
- PCA is applied to neural data only; behavioral data is used as the observation target for the Kalman filter.
- `n_components` controls the trade-off between noise reduction and information retention; default 10 is conservative for large populations.
- Kalman EM fitting assumes Gaussian noise; for point-process (spike) data, consider GPFA or Poisson-based decoders.

## Caveats
- `pykalman` EM can be slow for large datasets; reduce `n_components` or subsample time series if needed.
- KF `.em()` API passes observations (PCA-reduced neural) but the state is the behavioral variable — verify dimension alignment before calling.
- Results pickle includes the fitted models; reload for inference on new data without retraining.

## In ABA
Implement with `run_python`; `ensure_capability("scikit-learn", "pykalman", "matplotlib", "pandas")`. Original impl: `source` -> lift to lakeFS later.
