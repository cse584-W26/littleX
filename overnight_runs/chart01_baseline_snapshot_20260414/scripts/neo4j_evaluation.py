"""Topology traversal baseline for Neo4j (single-curve database baseline).

Mirrors littleX-benchmarks/bench_evaluation.py one-to-one. Sweeps the same
fan-out / selectivity grid and times the same one-hop type-filtered query
that the Jac and SQLAlchemy benches measure:

    [profile-->(?:Tweet)]   (Jac)
    SELECT ... FROM tweet WHERE author_id = ? AND ...   (SQLAlchemy)
    MATCH (p:Profile {jac_id: $uid})-[:POST]->(t:Tweet) RETURN count(t)  (this)

Each Profile node is created with `n_tweets` outgoing :POST -> :Tweet edges
(targets) and `n_channels` outgoing :MEMBER -> :Channel edges (noise). The
fan-out parameter equals n_tweets + n_channels.

Neo4j is run in its NATURAL configuration — there is no artificial "INDEX OFF"
mode. The previous version of this script forced a label-filter-after-scan
query as a fake OFF baseline, which was misleading: no Neo4j developer would
ever write that, and Neo4j has no analogue of Jac's "load all nodes into
Python and filter client-side" path. As a database baseline, what we want
from Neo4j is the floor it sets when used correctly.

CSV columns:
    fan_out, n_tweets, n_channels, selectivity_pct,
    neo4j_median_ms, neo4j_mean_ms, neo4j_min_ms, neo4j_db_hits

Usage:
    python neo4j_evaluation.py -r 5
    python neo4j_evaluation.py -r 10 --output neo4j_eval_results
"""

import argparse
import csv
import os
import statistics
import time

import neo4j_littlex_schema as schema


parser = argparse.ArgumentParser(description="Neo4j topology traversal baseline")
parser.add_argument("-u", "--uri", default=schema.DEFAULT_URI)
parser.add_argument("--user", default=schema.DEFAULT_USER)
parser.add_argument("--password", default=schema.DEFAULT_PASSWORD)
parser.add_argument("-r", "--runs", default=5, type=int,
                    help="Timed runs per configuration")
parser.add_argument("--output", default="neo4j_eval_results",
                    help="Output directory for CSV and charts")
args = parser.parse_args()

os.makedirs(args.output, exist_ok=True)


# Identical sweep to bench_evaluation.py — keep these in sync.
CONFIGS = [
    # Varying selectivity at fixed fan-out=200
    (200, 100, 100),  # 50%
    (200, 40, 160),   # 20%
    (200, 20, 180),   # 10%
    (200, 10, 190),   # 5%
    (200, 4, 196),    # 2%
    # Varying fan-out at fixed selectivity ~5%
    (50, 3, 47),      # 6%, fan=50
    (100, 5, 95),     # 5%, fan=100
    (200, 10, 190),   # 5%, fan=200 (dup with above; deduped below)
    (400, 20, 380),   # 5%, fan=400
    # High fan-out, low selectivity
    (500, 5, 495),    # 1%, fan=500
    (500, 25, 475),   # 5%, fan=500
    (500, 50, 450),   # 10%, fan=500
]
seen = set()
unique = []
for cfg in CONFIGS:
    if cfg not in seen:
        seen.add(cfg)
        unique.append(cfg)
CONFIGS = unique


def extract_db_hits(profile_tree):
    """Recursively sum dbHits from a Neo4j PROFILE execution tree."""
    if not profile_tree:
        return 0
    hits = profile_tree.get("dbHits", 0)
    for child in profile_tree.get("children", []):
        hits += extract_db_hits(child)
    return hits


def setup_user(session, idx, n_tweets, n_channels):
    """Build one Profile with n_tweets :POST->:Tweet and n_channels :MEMBER->:Channel.

    Uses batched UNWIND so setup time isn't dominated by per-row round-trips.
    Setup time is NOT measured — only the query time below is.
    """
    uid = schema.create_user(session, f"EvalUser_{idx}")

    if n_tweets > 0:
        session.run(
            """
            MATCH (p:Profile {jac_id: $uid})
            UNWIND range(1, $n) AS i
            CREATE (p)-[:POST]->(:Tweet {
                jac_id: $uid + '_t_' + toString(i),
                content: 'Eval tweet ' + toString(i),
                author_username: p.username,
                created_at: '',
                likes: [],
                comments: []
            })
            """,
            uid=uid, n=n_tweets,
        )

    if n_channels > 0:
        session.run(
            """
            MATCH (p:Profile {jac_id: $uid})
            UNWIND range(1, $n) AS i
            CREATE (p)-[:MEMBER]->(:Channel {
                jac_id: $uid + '_c_' + toString(i),
                name: 'eval_ch_' + toString(i),
                description: '',
                creator_username: p.username,
                created_at: ''
            })
            """,
            uid=uid, n=n_channels,
        )

    return uid


def run_query(session, uid):
    """The natural Neo4j way to express [profile-->(?:Tweet)].

    Indexed Profile lookup, typed [:POST] traversal, label-constrained Tweet
    target. The planner pushes everything down — no client-side filtering.
    """
    query = """
    PROFILE
    MATCH (p:Profile {jac_id: $uid})-[:POST]->(t:Tweet)
    RETURN count(t) AS c
    """
    t0 = time.perf_counter()
    result = session.run(query, uid=uid)
    summary = result.consume()
    latency_ms = (time.perf_counter() - t0) * 1000
    db_hits = extract_db_hits(summary.profile) if summary.profile else 0
    return latency_ms, db_hits


def main():
    print(f"Connecting to Neo4j at {args.uri}")
    print(f"Sweeping {len(CONFIGS)} configurations, {args.runs} runs each.")
    print(f"Output directory: {args.output}/\n")

    driver = schema.connect(args.uri, args.user, args.password)
    results = []

    try:
        with driver.session() as session:
            print("[Setup] Wiping DB and rebuilding constraints ...")
            schema.reset_database(session)

            for idx, (fan_out, n_tweets, n_channels) in enumerate(CONFIGS):
                selectivity = n_tweets / fan_out * 100
                print(f"[{idx + 1}/{len(CONFIGS)}] fan_out={fan_out}  "
                      f"tweets={n_tweets}  channels={n_channels}  "
                      f"sel={selectivity:.1f}%")

                uid = setup_user(session, idx, n_tweets, n_channels)

                # Warm up — first query pays planner cache cost.
                run_query(session, uid)

                latencies = []
                db_hits = 0
                for _ in range(args.runs):
                    lat, hits = run_query(session, uid)
                    latencies.append(lat)
                    db_hits = hits

                med = statistics.median(latencies)
                print(f"   median={med:.3f}ms  mean={statistics.mean(latencies):.3f}ms  "
                      f"dbHits={db_hits}\n")

                results.append({
                    "fan_out": fan_out,
                    "n_tweets": n_tweets,
                    "n_channels": n_channels,
                    "selectivity_pct": round(selectivity, 1),
                    "neo4j_median_ms": round(med, 3),
                    "neo4j_mean_ms": round(statistics.mean(latencies), 3),
                    "neo4j_min_ms": round(min(latencies), 3),
                    "neo4j_db_hits": db_hits,
                })
    finally:
        driver.close()

    # ----- CSV -----
    csv_path = os.path.join(args.output, "neo4j_results.csv")
    with open(csv_path, "w", newline="") as f:
        f.write("# Neo4j topology baseline (natural mode, indexed Profile + typed [:POST] traversal).\n")
        f.write("# CSV is mergeable with bench_evaluation.py output via (fan_out,n_tweets,n_channels) keys.\n")
        f.write("# dbHits is logical I/O reported by Neo4j PROFILE; latency is wall clock.\n\n")
        writer = csv.DictWriter(f, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"CSV: {csv_path}")

    # ----- Chart (single curve, two panels) -----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not available; skipping chart.")
        return

    # Panel 1: Latency vs Selectivity at fan_out=200
    sel = sorted(
        [r for r in results if r["fan_out"] == 200],
        key=lambda r: r["selectivity_pct"],
    )
    # Panel 2: Latency vs Fan-out at ~5% selectivity
    fan = sorted(
        [r for r in results if 4 <= r["selectivity_pct"] <= 6],
        key=lambda r: r["fan_out"],
    )

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    if sel:
        x = [r["selectivity_pct"] for r in sel]
        y = [r["neo4j_median_ms"] for r in sel]
        ax1.plot(x, y, "o-", color="#9333ea", linewidth=2, markersize=8, label="Neo4j")
        ax1.set_xlabel("Selectivity (%)")
        ax1.set_ylabel("Median Latency (ms)")
        ax1.set_title("Latency vs Selectivity (fan-out = 200)")
        ax1.grid(True, alpha=0.3)
        ax1.legend()

    if fan:
        x = [r["fan_out"] for r in fan]
        y = [r["neo4j_median_ms"] for r in fan]
        ax2.plot(x, y, "o-", color="#9333ea", linewidth=2, markersize=8, label="Neo4j")
        ax2.set_xlabel("Fan-out (total outgoing edges)")
        ax2.set_ylabel("Median Latency (ms)")
        ax2.set_title("Latency vs Fan-out (~5% selectivity)")
        ax2.grid(True, alpha=0.3)
        ax2.legend()

    plt.tight_layout()
    chart_path = os.path.join(args.output, "neo4j_baseline.png")
    plt.savefig(chart_path, dpi=150)
    plt.close()
    print(f"Chart: {chart_path}")


if __name__ == "__main__":
    main()
