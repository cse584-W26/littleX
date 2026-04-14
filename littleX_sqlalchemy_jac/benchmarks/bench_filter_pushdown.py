"""Filter pushdown equivalent benchmark for SQLAlchemy backend.

Tests the same needle-in-a-haystack scenarios as the Jac graph version:
  1. follow_user at scale — SQL: SELECT ... WHERE id = X
  2. like_tweet at scale — SQL: SELECT ... WHERE id = X
  3. delete_tweet at scale — SQL: DELETE ... WHERE id = X AND author_id = Y

These are simple primary-key lookups in SQL — always O(1) with an index.
The comparison shows how SQL handles what graph needs filter pushdown for.

Usage:
    python bench_filter_pushdown.py -u http://localhost:8080 -r 10
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'Filter pushdown equivalent (SQLAlchemy)'
parser.add_argument('-r', '--runs', default=10, type=int)
parser.add_argument('--output', default='filter_pushdown_results')
parser.add_argument('--user-counts', default='50,200,500')
parser.add_argument('--tweet-counts', default='50,200,500')
args = parser.parse_args()

PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

api = LittleXAPI(args.url)


def measure_ms(fn):
    start = time.perf_counter()
    result = fn()
    return (time.perf_counter() - start) * 1000, result


# ============================================================
# 1. follow_user at scale — SQL primary key lookup
# ============================================================
print('=' * 60)
print('1. FOLLOW_USER — profile lookup by ID')
print('   SQL: SELECT * FROM user WHERE id = X')
print('=' * 60)

user_counts = [int(x) for x in args.user_counts.split(',')]
follow_results = []

for n_users in user_counts:
    print(f'\n  Creating {n_users} users...')
    user_ids = []
    for i in range(n_users):
        uid = api.create_user(f'fp_f_{n_users}_{i}@b.com', PASSWORD, f'FP_F_{n_users}_{i}')
        user_ids.append(uid)
        if (i + 1) % 100 == 0:
            print(f'    {i+1}/{n_users}')

    bench_api = LittleXAPI(args.url)
    bench_api.create_user(f'fp_bench_f_{n_users}@b.com', PASSWORD, f'FPBenchF_{n_users}')

    # Warm up
    bench_api.follow_user(user_ids[0])

    times = []
    for i in range(min(args.runs, len(user_ids) - 1)):
        target = user_ids[i + 1]
        ms, _ = measure_ms(lambda: bench_api.follow_user(target))
        times.append(ms)

    med = statistics.median(times)
    print(f'  follow_user ({n_users} users): med={med:.1f}ms  mean={statistics.mean(times):.1f}ms')

    follow_results.append({
        'operation': 'follow_user',
        'scale': f'{n_users} users',
        'n': n_users,
        'median_ms': round(med, 1),
        'mean_ms': round(statistics.mean(times), 1),
        'min_ms': round(min(times), 1),
        'max_ms': round(max(times), 1),
    })


# ============================================================
# 2. like_tweet at scale — tweet lookup by ID
# ============================================================
print('\n' + '=' * 60)
print('2. LIKE_TWEET — tweet lookup by ID')
print('   SQL: SELECT * FROM tweet WHERE id = X')
print('=' * 60)

like_results = []

for n_users in user_counts:
    print(f'\n  Setting up {n_users} users with tweets...')
    tweet_ids_all = []
    for i in range(n_users):
        like_api = LittleXAPI(args.url)
        like_api.create_user(f'fp_l_{n_users}_{i}@b.com', PASSWORD, f'FP_L_{n_users}_{i}')
        for t in range(5):
            tid = like_api.create_tweet(f'Like test {n_users}_{i}_{t}')
            tweet_ids_all.append(tid)
        if (i + 1) % 100 == 0:
            print(f'    {i+1}/{n_users}')

    total_tweets = len(tweet_ids_all)
    print(f'  {total_tweets} tweets across {n_users} users.')

    liker_api = LittleXAPI(args.url)
    liker_api.create_user(f'fp_liker_{n_users}@b.com', PASSWORD, f'FPLiker_{n_users}')

    # Warm up
    liker_api.like_tweet(tweet_ids_all[0])

    times = []
    for i in range(min(args.runs, len(tweet_ids_all) - 1)):
        tid = tweet_ids_all[i + 1]
        ms, _ = measure_ms(lambda: liker_api.like_tweet(tid))
        times.append(ms)

    med = statistics.median(times)
    print(f'  like_tweet ({n_users} users, {total_tweets} tweets): med={med:.1f}ms  mean={statistics.mean(times):.1f}ms')

    like_results.append({
        'operation': 'like_tweet',
        'scale': f'{n_users} users ({total_tweets} tweets)',
        'n': total_tweets,
        'median_ms': round(med, 1),
        'mean_ms': round(statistics.mean(times), 1),
        'min_ms': round(min(times), 1),
        'max_ms': round(max(times), 1),
    })


# ============================================================
# 3. delete_tweet at scale — targeted delete by ID
# ============================================================
print('\n' + '=' * 60)
print('3. DELETE_TWEET — targeted delete by ID')
print('   SQL: DELETE FROM tweet WHERE id = X AND author_id = Y')
print('=' * 60)

tweet_counts = [int(x) for x in args.tweet_counts.split(',')]
delete_results = []

for n_tweets in tweet_counts:
    print(f'\n  Creating user with {n_tweets} tweets...')
    del_api = LittleXAPI(args.url)
    del_api.create_user(f'fp_del_{n_tweets}@b.com', PASSWORD, f'FPDel_{n_tweets}')

    tweet_ids = []
    for i in range(n_tweets):
        tid = del_api.create_tweet(f'Delete test {n_tweets}_{i}')
        tweet_ids.append(tid)
        if (i + 1) % 100 == 0:
            print(f'    {i+1}/{n_tweets}')

    times = []
    for i in range(min(args.runs, len(tweet_ids))):
        tid = tweet_ids[i]
        ms, _ = measure_ms(lambda: del_api.delete_tweet(tid))
        times.append(ms)

    med = statistics.median(times)
    print(f'  delete_tweet ({n_tweets} tweets): med={med:.1f}ms  mean={statistics.mean(times):.1f}ms')

    delete_results.append({
        'operation': 'delete_tweet',
        'scale': f'{n_tweets} tweets',
        'n': n_tweets,
        'median_ms': round(med, 1),
        'mean_ms': round(statistics.mean(times), 1),
        'min_ms': round(min(times), 1),
        'max_ms': round(max(times), 1),
    })

# Write CSV
all_results = follow_results + like_results + delete_results
csv_path = os.path.join(args.output, 'results.csv')
with open(csv_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=all_results[0].keys())
    writer.writeheader()
    writer.writerows(all_results)
print(f'\nCSV saved to {csv_path}')

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for ax, data, title in [
        (axes[0], follow_results, 'follow_user\n(profile lookup by ID)'),
        (axes[1], like_results, 'like_tweet\n(tweet lookup by ID)'),
        (axes[2], delete_results, 'delete_tweet\n(targeted delete by ID)'),
    ]:
        x = [r['n'] for r in data]
        y = [r['median_ms'] for r in data]
        ax.plot(x, y, 'o-', color='#3b82f6', linewidth=2, markersize=8)
        ax.set_xlabel('Scale (items)')
        ax.set_ylabel('Median Latency (ms)')
        ax.set_title(title)
        ax.grid(True, alpha=0.3)
        for xi, yi in zip(x, y):
            ax.annotate(f'{yi:.1f}ms', (xi, yi), textcoords="offset points",
                       xytext=(0, 10), ha='center', fontsize=9)

    plt.tight_layout()
    plt.savefig(os.path.join(args.output, 'filter_pushdown.png'), dpi=150)
    plt.close()
    print(f'Chart saved.')
except ImportError:
    pass
