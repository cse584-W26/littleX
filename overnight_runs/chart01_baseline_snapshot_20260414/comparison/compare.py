"""Compare SQL-baseline /walker/load_own_tweets numbers (collected today,
2026-04-14) against the saved Jac bench_evaluation.py CSVs that feed
Chart 01 of EVALUATION_ANALYSIS.md.

Workload: user with N own tweets + M channel memberships (fan_out=N+M),
client-timed end-to-end latency of one call that returns only the user's
own tweets.

Jac CSVs (Naive/New SAM, Filter Pushdown) expose that measurement under
`on_median_ms`  — INDEX ON column — because their bench toggles the Jac
topology index. The SQL equivalent has no toggle: Postgres always uses
the index, so we read its single `median_ms` column.

Neo4j's eval CSV is keyed on the same 11 configs and exposes
`neo4j_median_ms`.
"""

import csv
import os

ROOT = "/home/savini98/repos/littleX"
NEW_ROOT = os.path.join(ROOT, "overnight_runs/sql_vs_jac_full_sweep_20260414")
BENCH = os.path.join(ROOT, "littleX-benchmarks")

SOURCES = {
    "Naive SAM (ON)":       (os.path.join(BENCH, "Naive_SAM/eval_results_run1/results.csv"),    "on_median_ms"),
    "New SAM (ON)":         (os.path.join(BENCH, "New_SAM/eval_results_run1/results.csv"),      "on_median_ms"),
    "Filter Pushdown (ON)": (os.path.join(BENCH, "Filter_Pushdown/eval_results_run1/results.csv"), "on_median_ms"),
    "Neo4j":                (os.path.join(BENCH, "Neo4j/neo4j_eval_results/neo4j_results.csv"), "neo4j_median_ms"),
    "PG hand-tuned":        (os.path.join(NEW_ROOT, "pg_handtuned/results.csv"),                 "server_traversal_median_ms"),
    "SQLAlchemy pure":      (os.path.join(NEW_ROOT, "sqlalchemy_pure/results.csv"),              "server_traversal_median_ms"),
}

CONFIGS = [
    (200, 100, 100, 50.0),
    (200, 40,  160, 20.0),
    (200, 20,  180, 10.0),
    (200, 10,  190, 5.0),
    (200, 4,   196, 2.0),
    (50,  3,   47,  6.0),
    (100, 5,   95,  5.0),
    (400, 20,  380, 5.0),
    (500, 5,   495, 1.0),
    (500, 25,  475, 5.0),
    (500, 50,  450, 10.0),
]


def read(path):
    with open(path) as f:
        # skip comment lines AND blank lines; keep header + data
        rows = [l for l in f if l.strip() and not l.lstrip().startswith("#")]
    return list(csv.DictReader(rows))


data = {}
for label, (path, col) in SOURCES.items():
    rows = read(path)
    data[label] = {
        (int(r["fan_out"]), int(r["n_tweets"]), int(r["n_channels"])): float(r[col])
        for r in rows
    }


# ---- Print table (Markdown-ready) ----
def cell(lbl, k):
    v = data[lbl].get(k)
    return f"{v:.2f}" if v is not None else "  —  "


labels = list(SOURCES.keys())
hdr = "| fan_out | sel% | " + " | ".join(labels) + " |"
sep = "|---:|---:|" + "|".join(["---:"] * len(labels)) + "|"
print(hdr)
print(sep)
for fo, nt, nc, sel in CONFIGS:
    k = (fo, nt, nc)
    cells = " | ".join(cell(lbl, k) for lbl in labels)
    print(f"| {fo} | {sel} | {cells} |")


# ---- Plot ----
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {
        "Naive SAM (ON)":       "#f59e0b",
        "New SAM (ON)":         "#10b981",
        "Filter Pushdown (ON)": "#22c55e",
        "Neo4j":                "#ef4444",
        "PG hand-tuned":        "#3b82f6",
        "SQLAlchemy pure":      "#8b5cf6",
    }
    markers = {"Naive SAM (ON)": "x", "New SAM (ON)": "*",
               "Filter Pushdown (ON)": "o", "Neo4j": "^",
               "PG hand-tuned": "s", "SQLAlchemy pure": "D"}

    # Panel A — selectivity sweep at fan_out=200
    fig, (axA, axB) = plt.subplots(1, 2, figsize=(16, 6))
    sel_configs = [(200, nt, nc, sel) for (fo, nt, nc, sel) in CONFIGS if fo == 200]
    sel_configs.sort(key=lambda c: c[3])
    xs = [c[3] for c in sel_configs]
    for lbl in labels:
        ys = [data[lbl].get((fo, nt, nc))
              for (fo, nt, nc, sel) in sel_configs for k in [None]]
        ys = [data[lbl].get((200, nt, nc)) for (fo, nt, nc, sel) in sel_configs]
        axA.plot(xs, ys, markers[lbl] + "-", color=colors[lbl], ms=8, lw=2, label=lbl)
    axA.set_xlabel("Selectivity (%)")
    axA.set_ylabel("median_ms (log)")
    axA.set_yscale("log")
    axA.set_xticks([2, 5, 10, 20, 50])
    axA.set_title("Selectivity sweep @ fan_out=200\n(server-timed: Jac ms_my_tweets / SQL server_traversal_median_ms / Neo4j bolt)")
    axA.grid(True, which="both", alpha=0.3)
    axA.legend(fontsize=9)

    # Panel B — fan-out sweep at ~5% selectivity
    fan_configs = [(fo, nt, nc, sel) for (fo, nt, nc, sel) in CONFIGS
                   if 4.0 <= sel <= 6.0]
    fan_configs.sort(key=lambda c: c[0])
    xs = [c[0] for c in fan_configs]
    for lbl in labels:
        ys = [data[lbl].get((fo, nt, nc)) for (fo, nt, nc, sel) in fan_configs]
        axB.plot(xs, ys, markers[lbl] + "-", color=colors[lbl], ms=8, lw=2, label=lbl)
    axB.set_xlabel("Fan-out")
    axB.set_ylabel("median_ms (log)")
    axB.set_yscale("log")
    axB.set_title("Fan-out sweep @ ~5% selectivity (server-timed)")
    axB.grid(True, which="both", alpha=0.3)
    axB.legend(fontsize=9)

    footer = ("Server-timed: Jac on_median_ms (ms_my_tweets from bench_feed), "
              "Neo4j tx.run() wall-clock, SQL server_traversal_median_ms "
              "(wrap around the SQL execute() inside the Flask handler). "
              "Excludes HTTP/port-forward/JSON serialization overhead.")
    fig.text(0.5, 0.005, footer, ha="center", fontsize=8, style="italic", color="#444")
    plt.tight_layout(rect=[0, 0.04, 1, 1])
    out = os.path.join(NEW_ROOT, "sql_vs_jac_comparison_server_timed.png")
    plt.savefig(out, dpi=150)
    print(f"\nwrote {out}")
except ImportError:
    pass
