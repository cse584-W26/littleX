# Chart 01 baseline snapshot — 2026-04-14

Snapshot of every CSV + producing script that feeds the Chart 01
("Selectivity Sweep @ fan_out=200") comparison in
`EVALUATION_ANALYSIS.md`, plus today's SQL-baseline rerun.

## Results CSVs

| Path | Original location | Data date | Producer | Timer boundary |
|---|---|---|---|---|
| `jac_naive_sam/results.csv` | `littleX-benchmarks/Naive_SAM/eval_results_run1/results.csv` | 2026-03-31 17:00 | `scripts/bench_evaluation.py` | server-only (`ms_my_tweets`) |
| `jac_new_sam/results.csv` | `littleX-benchmarks/New_SAM/eval_results_run1/results.csv` | 2026-03-31 21:05 | `scripts/bench_evaluation.py` | server-only |
| `jac_filter_pushdown/results.csv` | `littleX-benchmarks/Filter_Pushdown/eval_results_run1/results.csv` | 2026-04-02 13:44 | `scripts/bench_evaluation.py` | server-only |
| `neo4j/neo4j_results.csv` | `littleX-benchmarks/Neo4j/neo4j_eval_results/neo4j_results.csv` | 2026-04-09 01:13 | `scripts/neo4j_evaluation.py` + `neo4j_littlex_schema.py` | bolt client round-trip (port-forwarded) |
| `pg_handtuned/results.csv` | `overnight_runs/sql_vs_jac_full_sweep_20260414/pg_handtuned/results.csv` | 2026-04-14 | `scripts/bench_own_tweets_selectivity.py` + `pg_handtuned_walker.py` | end-to-end + `server_traversal_median_ms` column |
| `sqlalchemy_pure/results.csv` | `overnight_runs/sql_vs_jac_full_sweep_20260414/sqlalchemy_pure/results.csv` | 2026-04-14 | `scripts/bench_own_tweets_selectivity.py` + `sqlalchemy_walker.py` | end-to-end + `server_traversal_median_ms` column |

All six CSVs share the `(fan_out, n_tweets, n_channels)` key so they join cleanly.
11-row sweep: 5 selectivity rows at fan_out=200, 3 fan-out rows at ~5%, 3 high-fan-out rows.

## Scripts

Every script that produced a CSV in this snapshot:

- `scripts/bench_evaluation.py` — Jac driver; hits `/walker/bench_feed` with INDEX ON/OFF toggle + `clear_cache` between runs; reads `ms_my_tweets` from walker report.
- `scripts/neo4j_evaluation.py` — direct bolt driver; runs `MATCH (p:Profile)-[:POST]->(t:Tweet) RETURN count(t)` with `PROFILE` wrapped in `perf_counter()` ([line 145-148](scripts/neo4j_evaluation.py)). Depends on `neo4j_littlex_schema.py`.
- `scripts/bench_own_tweets_selectivity.py` — HTTP client driver used for both SQL baselines; hits `/walker/load_own_tweets`.
- `scripts/pg_handtuned_walker.py` + `scripts/pg_handtuned_schema.sql` — PG backend route + schema as deployed 2026-04-14.
- `scripts/sqlalchemy_walker.py` + `scripts/sqlalchemy_models.py` — SQLAlchemy backend route + ORM models as deployed 2026-04-14.

## Comparison artifacts

- `comparison/compare.py` — joins all six CSVs on key, prints Markdown table, writes two-panel plot.
- `comparison/sql_vs_jac_comparison.png` — plot using `median_ms` for SQL (end-to-end) vs `on_median_ms` for Jac (server-only). **Not apples-to-apples.**
- `comparison/sql_vs_jac_comparison_server_timed.png` — plot using `server_traversal_median_ms` for SQL instead. Fairer for PG + SQLA, but Neo4j still includes bolt round-trip and Jac is still server-only.

## Known fairness caveats

1. Jac CSVs are 12-14 days older than the SQL rerun (different minikube session).
2. Jac `on_median_ms` excludes HTTP/JSON/port-forward overhead; Neo4j includes bolt round-trip through port-forward; SQL `server_traversal_median_ms` excludes only the HTTP layer (pod-to-pod SQL call is still in the number). Three different timer boundaries.
3. SQL bench runs the `/walker/load_own_tweets` endpoint, which returns tweets WHERE author_id=me. Jac `bench_feed` with INDEX ON measures the same logical workload via the topology index on the `[profile-->(?:Tweet)]` path.
