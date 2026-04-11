"""User archetype benchmark for SQLAlchemy backend.

Creates different user shapes and measures load_feed latency.

Usage:
    python bench_archetypes.py -u http://localhost:8080 -r 3 --scales 0.1,0.5
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'User archetype benchmark (SQLAlchemy)'
parser.add_argument('-r', '--runs', default=5, type=int)
parser.add_argument('--output', default='archetype_results')
parser.add_argument('--scales', default='0.1,0.5')
args = parser.parse_args()

api = LittleXAPI(args.url)
PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

scales = [float(x) for x in args.scales.split(',')]

BASE_ARCHETYPES = [
    {'name': 'Lurker',       'follows': 500, 'tweets': 0},
    {'name': 'Celebrity',    'follows': 20,  'tweets': 500},
    {'name': 'Power User',   'follows': 300, 'tweets': 200},
    {'name': 'Bot/Spammer',  'follows': 1000,'tweets': 500},
    {'name': 'Channel Admin','follows': 50,  'tweets': 100},
]

user_counter = [0]

def setup_archetype(arch, follows, tweets):
    idx = user_counter[0]
    user_counter[0] += 1

    followee_ids = []
    if follows > 0:
        print(f'    Creating {follows} followee users...')
        for i in range(follows):
            uid = api.create_user(f'a{idx}_f{i}@b.com', PASSWORD, f'F{idx}_{i}', 'f')
            for t in range(3):
                api.create_tweet(f'F{idx}_{i} t{t}')
            followee_ids.append(uid)
            if (i + 1) % 100 == 0:
                print(f'      {i+1}/{follows}')

    api.create_user(f'arch_{idx}@b.com', PASSWORD, f'{arch["name"]}_{idx}')
    if tweets > 0:
        for i in range(tweets):
            api.create_tweet(f'{arch["name"]} tweet #{i}')

    if followee_ids:
        print(f'    Following {len(followee_ids)} users...')
        for uid in followee_ids:
            api.follow_user(uid)

results = []

print(f'Archetype benchmark: {len(BASE_ARCHETYPES)} archetypes x {len(scales)} scales, {args.runs} runs each\n')

for arch in BASE_ARCHETYPES:
    for scale in scales:
        follows = int(arch['follows'] * scale)
        tweets = int(arch['tweets'] * scale)
        total_edges = follows + tweets
        selectivity = (tweets / total_edges * 100) if total_edges > 0 else 0

        print(f'[{arch["name"]} @ {scale}x] follows={follows}, tweets={tweets}')
        setup_archetype(arch, follows, tweets)

        # Warm up
        api.load_feed()

        times = []
        for i in range(args.runs):
            start = time.perf_counter()
            api.load_feed()
            times.append((time.perf_counter() - start) * 1000)

        med = statistics.median(times)
        print(f'  Latency: med={med:.1f}ms  mean={statistics.mean(times):.1f}ms\n')

        results.append({
            'archetype': arch['name'],
            'scale': scale,
            'follows': follows,
            'tweets': tweets,
            'total_edges': total_edges,
            'selectivity_pct': round(selectivity, 1),
            'median_ms': round(med, 3),
            'mean_ms': round(statistics.mean(times), 3),
            'min_ms': round(min(times), 3),
        })

csv_path = os.path.join(args.output, 'results.csv')
with open(csv_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
print(f'CSV saved to {csv_path}')

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    archetype_names = list(dict.fromkeys(r['archetype'] for r in results))
    n_scales = len(scales)
    x = np.arange(len(archetype_names))
    width = 0.8 / n_scales

    fig, ax = plt.subplots(figsize=(12, 6))
    for i, scale in enumerate(scales):
        latencies = [r['median_ms'] for r in results if r['scale'] == scale]
        offset = (i - n_scales / 2 + 0.5) * width
        bars = ax.bar(x + offset, latencies, width, label=f'{scale}x scale')
        for bar, v in zip(bars, latencies):
            ax.text(bar.get_x() + bar.get_width()/2, v + 1, f'{v:.0f}', ha='center', fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels(archetype_names, rotation=20, ha='right')
    ax.set_ylabel('Median Latency (ms)')
    ax.set_title('SQLAlchemy load_feed Latency by Archetype and Scale')
    ax.legend()

    plt.tight_layout()
    plt.savefig(os.path.join(args.output, 'archetype_benchmark.png'), dpi=150)
    plt.close()
    print(f'Chart saved.')
except ImportError:
    pass
