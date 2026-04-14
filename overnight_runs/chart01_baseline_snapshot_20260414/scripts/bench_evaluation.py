"""Comprehensive topology index evaluation across selectivity levels.

Creates multiple users with varying fan-out and selectivity:
  - Tweets (Post edges -> Tweet nodes) = target nodes
  - Channels (Member edges -> Channel nodes) = noise nodes (same root)

Measures cold-start [profile-->(?:Tweet)] with INDEX OFF vs ON.
Outputs CSV + generates matplotlib charts.

Usage:
    python bench_evaluation.py -u http://localhost:8080 -r 5
    python bench_evaluation.py -u http://localhost:8080 -r 10 --output results
"""

from core import parser, LittleXAPI
import json
import statistics
import csv
import os
import time

parser.description = 'Comprehensive topology index evaluation'
parser.add_argument('-r', '--runs', default=5, type=int,
                    help='Timed runs per configuration')
parser.add_argument('--output', default='eval_results',
                    help='Output directory for CSV and charts')
args = parser.parse_args()

api = LittleXAPI(args.url)
PASSWORD = 'testte'

os.makedirs(args.output, exist_ok=True)

# Test configurations: (fan_out, n_tweets, n_channels)
# selectivity = n_tweets / fan_out
CONFIGS = [
    # Varying selectivity at fixed fan-out=200
    (200, 100, 100),   # 50%
    (200, 40, 160),    # 20%
    (200, 20, 180),    # 10%
    (200, 10, 190),    # 5%
    (200, 4, 196),     # 2%
    # Varying fan-out at fixed selectivity ~5%
    (50, 3, 47),       # 6%, fan=50
    (100, 5, 95),      # 5%, fan=100
    (200, 10, 190),    # 5%, fan=200
    (400, 20, 380),    # 5%, fan=400
    # High fan-out low selectivity
    (500, 5, 495),     # 1%, fan=500
    (500, 25, 475),    # 5%, fan=500
    (500, 50, 450),    # 10%, fan=500
]

# Deduplicate configs
seen = set()
unique_configs = []
for c in CONFIGS:
    key = (c[0], c[1], c[2])
    if key not in seen:
        seen.add(key)
        unique_configs.append(c)
CONFIGS = unique_configs

results = []


def setup_user(user_idx, n_tweets, n_channels):
    """Create a user with n_tweets tweets and n_channels channels."""
    email = f'eval_{user_idx}@bench.com'
    username = f'EvalUser_{user_idx}'
    api.create_user(email, PASSWORD, username)

    for i in range(n_tweets):
        api.create_tweet(f'Eval tweet {user_idx}_{i}')

    for i in range(n_channels):
        api.session.post('/walker/create_channel', data=json.dumps({
            'name': f'eval_ch_{user_idx}_{i}',
            'description': f'Eval channel'
        }))

    return email


def cold_run(index_enabled):
    """Clear cache then run bench_feed."""
    api.session.post('/walker/clear_cache', data=json.dumps({}))
    r = api.session.post('/walker/bench_feed', data=json.dumps({
        'clear_before': False,
        'index_enabled': index_enabled
    }))
    return r.json()['data']['reports'][0]


print(f'Running evaluation with {len(CONFIGS)} configurations, {args.runs} runs each')
print(f'Output: {args.output}/')
print()

for idx, (fan_out, n_tweets, n_channels) in enumerate(CONFIGS):
    selectivity = n_tweets / fan_out * 100

    print(f'[{idx+1}/{len(CONFIGS)}] fan_out={fan_out}, tweets={n_tweets}, '
          f'channels={n_channels}, selectivity={selectivity:.1f}%')

    # Setup user
    email = setup_user(idx, n_tweets, n_channels)
    print(f'  Setup complete.')

    # Warm up
    cold_run('false')

    # INDEX OFF cold runs
    off_my_tweets = []
    off_total = []
    off_l1 = 0
    for i in range(args.runs):
        r = cold_run('false')
        off_my_tweets.append(r['ms_my_tweets'])
        off_total.append(r['ms_total_traversal'])
        off_l1 = r['l1_after']

    # INDEX ON cold runs
    on_my_tweets = []
    on_total = []
    on_l1 = 0
    for i in range(args.runs):
        r = cold_run('true')
        on_my_tweets.append(r['ms_my_tweets'])
        on_total.append(r['ms_total_traversal'])
        on_l1 = r['l1_after']

    off_med = statistics.median(off_my_tweets)
    on_med = statistics.median(on_my_tweets)
    speedup = off_med / on_med if on_med > 0 else float('inf')

    print(f'  OFF: {off_med:.3f}ms (l1={off_l1})  ON: {on_med:.3f}ms (l1={on_l1})  '
          f'Speedup: {speedup:.1f}x')
    print()

    results.append({
        'fan_out': fan_out,
        'n_tweets': n_tweets,
        'n_channels': n_channels,
        'selectivity_pct': round(selectivity, 1),
        'off_median_ms': round(off_med, 3),
        'off_mean_ms': round(statistics.mean(off_my_tweets), 3),
        'off_min_ms': round(min(off_my_tweets), 3),
        'off_l1': off_l1,
        'on_median_ms': round(on_med, 3),
        'on_mean_ms': round(statistics.mean(on_my_tweets), 3),
        'on_min_ms': round(min(on_my_tweets), 3),
        'on_l1': on_l1,
        'speedup': round(speedup, 2),
        'off_total_median_ms': round(statistics.median(off_total), 3),
        'on_total_median_ms': round(statistics.median(on_total), 3),
    })

# Write CSV
csv_path = os.path.join(args.output, 'results.csv')
with open(csv_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
print(f'CSV saved to {csv_path}')

# Generate charts
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    # ---- Chart 1: Speedup vs Selectivity (fixed fan-out=200) ----
    sel_data = [r for r in results if r['fan_out'] == 200]
    if sel_data:
        sel_data.sort(key=lambda r: r['selectivity_pct'])
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

        x = [r['selectivity_pct'] for r in sel_data]
        off_y = [r['off_median_ms'] for r in sel_data]
        on_y = [r['on_median_ms'] for r in sel_data]
        speedups = [r['speedup'] for r in sel_data]

        ax1.bar([i - 0.2 for i in range(len(x))], off_y, 0.4, label='INDEX OFF', color='#ef4444')
        ax1.bar([i + 0.2 for i in range(len(x))], on_y, 0.4, label='INDEX ON', color='#22c55e')
        ax1.set_xticks(range(len(x)))
        ax1.set_xticklabels([f'{v}%' for v in x])
        ax1.set_xlabel('Selectivity')
        ax1.set_ylabel('Cold Start Latency (ms)')
        ax1.set_title('Cold Start: INDEX OFF vs ON\n(fan-out=200)')
        ax1.legend()
        ax1.set_yscale('log')

        ax2.plot(x, speedups, 'o-', color='#3b82f6', linewidth=2, markersize=8)
        ax2.set_xlabel('Selectivity (%)')
        ax2.set_ylabel('Speedup (x)')
        ax2.set_title('Topology Index Speedup vs Selectivity\n(fan-out=200)')
        ax2.grid(True, alpha=0.3)
        for i, (xi, yi) in enumerate(zip(x, speedups)):
            ax2.annotate(f'{yi:.1f}x', (xi, yi), textcoords="offset points",
                        xytext=(0, 10), ha='center', fontsize=9)

        plt.tight_layout()
        path1 = os.path.join(args.output, 'selectivity_vs_speedup.png')
        plt.savefig(path1, dpi=150)
        plt.close()
        print(f'Chart saved: {path1}')

    # ---- Chart 2: Speedup vs Fan-out (fixed ~5% selectivity) ----
    fan_data = [r for r in results if 4 <= r['selectivity_pct'] <= 6]
    if fan_data:
        fan_data.sort(key=lambda r: r['fan_out'])
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

        x = [r['fan_out'] for r in fan_data]
        off_y = [r['off_median_ms'] for r in fan_data]
        on_y = [r['on_median_ms'] for r in fan_data]
        speedups = [r['speedup'] for r in fan_data]

        ax1.plot(x, off_y, 'o-', label='INDEX OFF', color='#ef4444', linewidth=2, markersize=8)
        ax1.plot(x, on_y, 's-', label='INDEX ON', color='#22c55e', linewidth=2, markersize=8)
        ax1.set_xlabel('Fan-out (total edges)')
        ax1.set_ylabel('Cold Start Latency (ms)')
        ax1.set_title('Cold Start Latency vs Fan-out\n(~5% selectivity)')
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        ax2.bar(range(len(x)), speedups, color='#3b82f6')
        ax2.set_xticks(range(len(x)))
        ax2.set_xticklabels([str(v) for v in x])
        ax2.set_xlabel('Fan-out (total edges)')
        ax2.set_ylabel('Speedup (x)')
        ax2.set_title('Topology Index Speedup vs Fan-out\n(~5% selectivity)')
        for i, v in enumerate(speedups):
            ax2.text(i, v + 0.5, f'{v:.1f}x', ha='center', fontsize=10, fontweight='bold')

        plt.tight_layout()
        path2 = os.path.join(args.output, 'fanout_vs_speedup.png')
        plt.savefig(path2, dpi=150)
        plt.close()
        print(f'Chart saved: {path2}')

    # ---- Chart 3: L1 nodes loaded ----
    fig, ax = plt.subplots(figsize=(10, 5))
    labels = [f'f={r["fan_out"]}\ns={r["selectivity_pct"]}%' for r in results]
    off_l1s = [r['off_l1'] for r in results]
    on_l1s = [r['on_l1'] for r in results]

    x_pos = np.arange(len(results))
    ax.bar(x_pos - 0.2, off_l1s, 0.4, label='INDEX OFF', color='#ef4444', alpha=0.8)
    ax.bar(x_pos + 0.2, on_l1s, 0.4, label='INDEX ON', color='#22c55e', alpha=0.8)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(labels, fontsize=7, rotation=45, ha='right')
    ax.set_ylabel('Nodes Loaded into L1')
    ax.set_title('Memory Selectivity: Nodes Loaded per Query (Cold Start)')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()
    path3 = os.path.join(args.output, 'l1_nodes_loaded.png')
    plt.savefig(path3, dpi=150)
    plt.close()
    print(f'Chart saved: {path3}')

    # ---- Chart 4: Comprehensive heatmap-style summary ----
    fig, ax = plt.subplots(figsize=(12, 5))
    labels = [f'f={r["fan_out"]} s={r["selectivity_pct"]}%' for r in results]
    speedups = [r['speedup'] for r in results]

    colors = ['#22c55e' if s > 1.5 else '#f59e0b' if s > 1.0 else '#ef4444' for s in speedups]
    bars = ax.barh(range(len(results)), speedups, color=colors)
    ax.set_yticks(range(len(results)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel('Speedup (x)')
    ax.set_title('Topology Index Speedup Across All Configurations')
    ax.axvline(x=1.0, color='black', linestyle='--', alpha=0.5, label='Breakeven')
    ax.legend()

    for i, (bar, v) in enumerate(zip(bars, speedups)):
        ax.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2,
                f'{v:.1f}x', va='center', fontsize=9, fontweight='bold')

    plt.tight_layout()
    path4 = os.path.join(args.output, 'speedup_summary.png')
    plt.savefig(path4, dpi=150)
    plt.close()
    print(f'Chart saved: {path4}')

    print(f'\nAll charts saved to {args.output}/')

except ImportError:
    print('matplotlib not installed — CSV saved, skipping charts')
    print('Install with: pip install matplotlib')
