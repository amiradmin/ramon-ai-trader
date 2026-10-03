# Local XGBoost reconstruction and tick inspection

This experiment builds native CPU XGBoost models locally. It is inspired by the tree-based approach in [SMC-v2](https://huggingface.co/JonusNattapong/xauusd-trading-ai-smc-v2), but **does not reproduce or load its published pretrained classifier**. The objective here is BUY/SELL net-trade-R regression, whereas the published model predicts price direction. A result here is evidence about this local protocol only, not a direct verdict on that publisher's weights.

The publisher's [training script](https://huggingface.co/JonusNattapong/xauusd-trading-ai-smc-v2/blob/main/train_multi_timeframe.py) assigns an FVG feature to a row using the next candle and splits samples randomly. Our existing local SMC features receive only completed bars through the signal; inner/outer chronological training labels are purged before each validation boundary. No remote pickle, joblib or custom model code is executed.

## Fixed experiment

Reuse the cost-aware selector's 24 features, H4, signal ATR stop 1.5 / target 3, next-open Bid/Ask execution, stop-first intrabar ordering, timestamp gap guard, assumed missing spread 42 points and additional 0.1R cost. Training stride is 16; held-out evaluation stride is 4. Five expanding evaluation blocks cover the last 80% of the archive. All baseline policies retain identical execution assumptions.

For each outer fold, reserve the final 25% of prior history as inner validation, with strict label-end purging. Test only these three tree configurations:

| Maximum depth | Boosting rounds |
|---|---:|
| 2 | 100 |
| 4 | 150 |
| 6 | 200 |

Other parameters are fixed: learning rate 0.05, histogram tree method, minimum child weight 20, L2 penalty 10, max bins 128, no row/column subsampling and seed 42. Test net-R entry thresholds 0, 0.05 and 0.1 in inner validation only. A candidate needs at least 30 trades, PF > 1 and MeanR > 0 there. If none qualifies, the outer policy is WAIT rather than a model chosen from outer outcomes.

Native boosters are saved as separate BUY/SELL JSON files with feature names, model order, parameters, library versions and purged training bounds. Batch inference is cached to avoid per-row matrix overhead. XGBoost and BLAS thread limits are 2. The native implementation and JSON format follow [XGBoost's documentation](https://xgboost.readthedocs.io/en/stable/python/python_intro.html).

This protocol uses the already-researched external archive; chronological algorithmic OOS does not create a pristine project-level holdout. Independent broker validation remains required if a future candidate passes. No installed EA, live service, dashboard or order path was modified.

## Reproduce in an isolated environment

The actual run used Python 3.13.7, XGBoost CPU 3.0.5, NumPy 2.2.6 and PyArrow 20.0.0. The original live Python environment was left untouched. PyArrow is available for subsequent Parquet work; the initial bounded tick preview uses only Python's standard library.

```bash
git pull --ff-only
uv venv .venv-research
uv pip install --python .venv-research/bin/python \
  xgboost-cpu==3.0.5 numpy==2.2.6 pyarrow==20.0.0 'pytest>=8,<10'
PYTHONPATH=src PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv-research/bin/python -m pytest \
  tests/test_xgboost_lab.py tests/test_tick_sample_audit.py \
  tests/test_trade_selector_lab.py tests/test_smc_local_lab.py \
  tests/test_historical_benchmark.py tests/test_research_shadow.py tests/test_progress.py -q
PYTHONPATH=src OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  .venv-research/bin/python -m ramon.xgboost_lab \
  --db data/ramon_kaggle_m15.sqlite3 --symbol XAUUSD_KAGGLE \
  --cpu-workers 2 --output-dir data/xgboost_lab
PYTHONPATH=src .venv-research/bin/python -m ramon.tick_sample_audit \
  --output-dir data/tick_sample_audit
```

The main PyPI file host timed out during this session; dependency installation succeeded via the public Aliyun PyPI mirror with the same pinned versions. If that connectivity problem recurs, append `--index-url https://mirrors.aliyun.com/pypi/simple` to the installation command. This is a dependency download fallback, not a model source.

## Tick preview scope

The tick inspector requests at most 100 rows at each of offsets 0, 1,000,000 and 100,000,000 from the public viewer of [CarlosSilva1/xauusd-ticks](https://huggingface.co/datasets/CarlosSilva1/xauusd-ticks). It does not download the roughly 287-million-row archive. Raw bounded responses, hashes, the observed repository revision and per-window diagnostics are saved separately under `data/`.

The [dataset card](https://huggingface.co/datasets/CarlosSilva1/xauusd-ticks/blob/main/README.md) declares UTC and describes a reconstructed broker feed. Viewer timestamps are naive; we interpret them using that declaration, not an independently verified clock. Viewer responses are not cryptographically pinned to the repository revision; the inspector checks that the observed revision stays unchanged during retrieval and saves response hashes. The publisher's exact broker and equivalence to LiteFinance have not been verified.

Quote checks cover finite positive Bid, Ask >= Bid, backward/duplicate timestamps, zero spreads and spread statistics. Converting spread to points uses **reference point 0.01**, not a verified source tick-size contract. Small disjoint windows cannot certify the full feed, infer spread distributions across sessions, establish TP/SL execution ordering for complete trades, or calibrate slippage. No price series were merged with the Kaggle or broker history and no spread assumption was changed.

Attribution: Silva, C. (2026), XAU/USD Tick Data (May 2021–May 2026), Hugging Face, CC-BY-4.0.
