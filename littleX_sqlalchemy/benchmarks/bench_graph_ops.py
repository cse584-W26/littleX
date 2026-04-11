"""Graph operations equivalent for SQLAlchemy backend.

Tests get_profile with varying followers and delete_tweet performance.
(No channels/trending in SQLAlchemy version.)

Usage:
    python bench_graph_ops.py -u http://localhost:8080 -r 5
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'Graph ops equivalent (SQLAlchemy)'
parser.add_argument('-r', '--runs', default=5, type=int)
parser.add_argument('--output', default='graph_ops_results')
parser.add_argument('--follower-counts', default='50,200,500')
args = parser.parse_args()

PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

api = LittleXAPI(args.url)

def measure_ms(fn):
    start = time.perf_counter()
    result = fn()
    return (time.perf_counter() - start) * 1000, result

# ============================================================
# 1. get_profile — varying follower counts
# ============================================================
print('=' * 60)
print('1. GET_PROFILE — varying follower counts')
print('=' * 60)

follower_counts = [int(x) for x in args.follower_counts.split(',')]
results = []

for n_followers in follower_counts:
    print(f'\n  Setting up celebrity with {n_followers} followers...')
    celeb_api = LittleXAPI(args.url)
    celeb_uid = celeb_api.create_user(f'celeb_{n_followers}@b.com', PASSWORD, f'Celebrity_{n_followers}')
    for i in range(20):
        celeb_api.create_tweet(f'Celebrity tweet {i}')

    for i in range(n_followers):
        follower_api = LittleXAPI(args.url)
        follower_api.create_user(f'fan_{n_followers}_{i}@b.com', PASSWORD, f'Fan_{n_followers}_{i}')
        follower_api.follow_user(celeb_uid)
        if (i + 1) % 100 == 0:
            print(f'    {i+1}/{n_followers} followers')

    # Warm up
    celeb_api.get_profile()

    times = []
    for i in range(args.runs):
        ms, _ = measure_ms(lambda: celeb_api.get_profile())
        times.append(ms)

    med = statistics.median(times)
    print(f'  Results ({n_followers} followers): med={med:.1f}ms  mean={statistics.mean(times):.1f}ms')

    results.append({
        'operation': 'get_profile',
        'parameter': f'{n_followers} followers',
        'n': n_followers,
        'median_ms': round(med, 1),
        'mean_ms': round(statistics.mean(times), 1),
    })

# ============================================================
# 2. delete_tweet
# ============================================================
print('\n' + '=' * 60)
print('2. DELETE_TWEET')
print('=' * 60)

del_api = LittleXAPI(args.url)
del_api.create_user('delbench@b.com', PASSWORD, 'DeleteBencher')
tweet_ids = []
for t in range(50):
    tid = del_api.create_tweet(f'Delete tweet {t}')
    tweet_ids.append(tid)

del_times = []
for i in range(min(args.runs, len(tweet_ids))):
    ms, _ = measure_ms(lambda: del_api.delete_tweet(tweet_ids[i]))
    del_times.append(ms)

print(f'  delete_tweet: med={statistics.median(del_times):.1f}ms')

results.append({
    'operation': 'delete_tweet',
    'parameter': f'{len(tweet_ids)} tweets',
    'n': len(tweet_ids),
    'median_ms': round(statistics.median(del_times), 1),
    'mean_ms': round(statistics.mean(del_times), 1),
})

csv_path = os.path.join(args.output, 'results.csv')
with open(csv_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
print(f'\nCSV saved to {csv_path}')
