"""Evaluation benchmark for SQLAlchemy backend.

Equivalent to the Jac graph bench_evaluation.py but measures load_feed
latency directly (no index toggle — SQLAlchemy uses SQL queries).

Creates users with varying fan-out (follows) to measure feed query scaling.

Usage:
    python bench_evaluation.py -u http://localhost:8080 -r 5
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'SQLAlchemy feed evaluation — varying fan-out'
parser.add_argument('-r', '--runs', default=5, type=int,
                    help='Timed runs per configuration')
parser.add_argument('--output', default='eval_results',
                    help='Output directory for CSV and charts')
args = parser.parse_args()

api = LittleXAPI(args.url)
PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

# Test configurations: (fan_out, tweets_per_followee)
# Each followee gets N tweets, so total tweets in feed = fan_out * tweets_per_followee
CONFIGS = [
    (50, 3),
    (100, 3),
    (200, 3),
    (300, 3),
    (500, 3),
]

results = []

print(f'Running SQLAlchemy evaluation with {len(CONFIGS)} configurations, {args.runs} runs each')
print(f'Output: {args.output}/')
print()

for idx, (fan_out, tweets_per) in enumerate(CONFIGS):
    total_tweets = fan_out * tweets_per
    print(f'[{idx+1}/{len(CONFIGS)}] fan_out={fan_out}, tweets_per_followee={tweets_per}, '
          f'total_tweets_in_feed~={total_tweets}')

    # Create followee users with tweets
    followee_ids = []
    for i in range(fan_out):
        uid = api.create_user(f'eval{idx}_f{i}@b.com', PASSWORD, f'Eval{idx}_F{i}', 'f')
        for t in range(tweets_per):
            api.create_tweet(f'Eval{idx} followee{i} tweet{t}')
        followee_ids.append(uid)
        if (i + 1) % 100 == 0:
            print(f'  Created {i+1}/{fan_out} followees')

    # Create the benchmark user
    bench_email = f'eval_bench_{idx}@b.com'
    api.create_user(bench_email, PASSWORD, f'EvalBench_{idx}')

    # Follow all followees
    for uid in followee_ids:
        api.follow_user(uid)
    print(f'  Setup complete.')

    # Warm up
    api.load_feed()

    # Timed runs
    times = []
    tweet_counts = []
    for i in range(args.runs):
        start = time.perf_counter()
        r = api.load_feed()
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        feed = r.json()['data']['result']
        tweet_counts.append(len(feed) if isinstance(feed, list) else 0)

    med = statistics.median(times)
    avg_tweets = statistics.mean(tweet_counts) if tweet_counts else 0

    print(f'  Latency: med={med:.1f}ms  mean={statistics.mean(times):.1f}ms  '
          f'min={min(times):.1f}ms  tweets={avg_tweets:.0f}')
    print()

    results.append({
        'fan_out': fan_out,
        'tweets_per_followee': tweets_per,
        'expected_tweets': total_tweets,
        'actual_tweets': round(avg_tweets),
        'median_ms': round(med, 3),
        'mean_ms': round(statistics.mean(times), 3),
        'min_ms': round(min(times), 3),
        'max_ms': round(max(times), 3),
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

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    x = [r['fan_out'] for r in results]
    y = [r['median_ms'] for r in results]

    ax1.plot(x, y, 'o-', color='#3b82f6', linewidth=2, markersize=8)
    ax1.set_xlabel('Fan-out (followed users)')
    ax1.set_ylabel('Median Latency (ms)')
    ax1.set_title('SQLAlchemy load_feed Latency vs Fan-out')
    ax1.grid(True, alpha=0.3)
    for xi, yi in zip(x, y):
        ax1.annotate(f'{yi:.1f}ms', (xi, yi), textcoords="offset points",
                    xytext=(0, 10), ha='center', fontsize=9)

    tweets = [r['actual_tweets'] for r in results]
    ax2.plot(tweets, y, 'o-', color='#22c55e', linewidth=2, markersize=8)
    ax2.set_xlabel('Tweets in Feed')
    ax2.set_ylabel('Median Latency (ms)')
    ax2.set_title('Latency vs Feed Size')
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    path = os.path.join(args.output, 'evaluation.png')
    plt.savefig(path, dpi=150)
    plt.close()
    print(f'Chart saved: {path}')

except ImportError:
    print('matplotlib not installed — CSV saved, skipping charts')
