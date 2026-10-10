# Common-input pretrained model comparison

This is offline research. It never changes trading models, places orders, or writes to the broker history. Each model runs in a disposable container with two CPUs and a 4 GB memory limit. Existing running model services are not restarted.

## Models

- autogluon/chronos-2-small (current family)
- amazon/chronos-2
- NeoQuasar/Kronos-small
- NeoQuasar/Kronos-base
- google/timesfm-2.5-200m-pytorch
- google/timesfm-3.0-pytorch (research only; self-hosted weights are restricted)
- FinText/Chronos_Tiny_2023_Global
- FinText/Chronos_Small_2023_Global

These are representative checkpoints of the discussed families, not every FinText yearly or regional variant. Kaggle's official TimesFM checkpoint is an older member of the same family, not a separate forecasting architecture. Educational LSTM notebooks without a selected pretrained checkpoint are not included as ready models.

Official sources: https://github.com/shiyu-coder/Kronos ; https://github.com/amazon-science/chronos-forecasting ; https://github.com/google-research/timesfm ; https://github.com/DeepIntoStreams/TSFM_Finance .

## Frozen inputs and metrics

The manifest selects 120 evenly spaced eligible broker-history contexts. Each has 256 completed M15 bars and five contiguous future bars. Sample origins are at least five bars apart, so the evaluated future targets do not overlap. Context may include ordinary daily/weekend market closures as defined by the existing Kronos helper; the last 64 bars and future horizon must be contiguous. All models receive the same price history; Kronos additionally uses OHLC fields. No actual future prices are passed to inference. Kronos receives scheduled next timestamps only. Future actuals are exclusively for scoring.

Predictions are five-step price paths, scored separately at 1, 3 and 5 candles. Direction is down/neutral/up relative to the same last closed price. Neutral band is max(0.2 ATR14, signal-time spread plus friction). Default point 0.01, fallback spread 42 points and round-trip additional friction 0.10 price units. Metrics include balanced accuracy, raw accuracy, class counts, mean absolute price error normalized by ATR, and a fixed unfiltered directional policy's cost-adjusted result. Policy buys/sells on directional predictions, enters at next open and exits at the horizon close. Longs pay entry spread; shorts pay exit spread. Samples do not overlap, but this is a sparse policy, not continuous production trading. Net ATR is not account return. No latency, swap or variable-slippage model is included.

Persistence (unchanged price) and extrapolation of the last close-to-close return provide naive baselines. Kronos uses one sampled path and fixed seed 17 for this CPU screening run; leading candidates need a multi-seed/multi-path repeat on new samples. FinText uses 32 sampled return paths and reconstructs cumulative prices before taking the median. FinText's fractional intraday simple returns (e.g. 0.01 means 1%) approximate its native daily excess-return domain; zero risk-free return is assumed at this intraday horizon, and transfer remains unvalidated. Other models ingest close levels. Checkpoint revision and inference timings are saved per model. Models that fail are listed separately and excluded from ranking; partial runs cannot be compared with complete runs. Manifest hashes and exact timestamps must match before scoring.

## Interpretation

This is an exploratory head-to-head comparison. Broker history was already inspected in previous experiments and is not a new untouched final holdout. Foundation-model pretraining overlap is generally unknown. No hyperparameter tuning or winner promotion follows from these results. A leading model should next undergo paired forward comparison and testing on new unseen periods, with actual trading costs. Better point-price error alone does not establish profitable directional trading.

## Files and commands

Results are stored under data/research/foundation_v1/: manifest.json, one checkpoint result per model, and summary.json. Freeze once with `python -m ramon.foundation_benchmark prepare`; run each model with `python -m ramon.foundation_benchmark run --model NAME`; score with `python -m ramon.foundation_benchmark summarize`. Model runs require the advisors runtime; preparing/scoring only needs the local project environment. Preserve the manifest rather than regenerating it during a comparison. Downloaded official weights are cached separately from live model activation.
