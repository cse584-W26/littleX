"""Cache/repeat query benchmark for SQLAlchemy backend.

SQLAlchemy has no explicit cache, but we can measure:
1. First query vs repeated queries (DB connection pool warmth)
2. Temporal locality (query after idle)

Usage:
    python bench_cache.py -u http://localhost:8080 -r 5
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'Repeat query / temporal locality (SQLAlchemy)'
parser.add_argument('-r', '--runs', default=5, type=int)
parser.add_argument('--follows', default=40, type=int)
parser.add_argument('--wait-times', default='0,1,5,10')
parser.add_argument('--output', default='cache_results')
args = parser.parse_args()

PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

def measure_ms(fn, retries=3):
    for attempt in range(retries):
        try:
            start = time.perf_counter()
            result = fn()
            return (time.perf_counter() - start) * 1000, result
        except Exception as e:
            if attempt < retries - 1:
                print(f'    Retry {attempt+1}/{retries} after error: {type(e).__name__}')
                time.sleep(2)
            else:
                raise

# Setup users
print(f'Setting up {args.follows} followed users...')
followee_ids = []
for i in range(args.follows):
    fapi = LittleXAPI(args.url)
    uid = fapi.create_user(f'cache_{i}@b.com', PASSWORD, f'CacheUser_{i}')
    for t in range(3):
        fapi.create_tweet(f'Cache user {i} tweet {t}')
    followee_ids.append(uid)
    if (i + 1) % 20 == 0:
        print(f'  {i+1}/{args.follows}')

user_api = LittleXAPI(args.url)
user_api.create_user('cachebench@b.com', PASSWORD, 'CacheBencher')
for uid in followee_ids:
    user_api.follow_user(uid)

print('Setup complete.\n')

# ============================================================
# 1. First vs repeat queries
# ============================================================
print('=' * 60)
print('1. FIRST QUERY vs REPEATED QUERIES')
print('=' * 60)

# First query (cold connection pool)
first_ms, _ = measure_ms(lambda: user_api.load_feed())
print(f'  First query: {first_ms:.1f}ms')

# Repeated queries
repeat_times = []
for i in range(args.runs):
    ms, _ = measure_ms(lambda: user_api.load_feed())
    repeat_times.append(ms)

repeat_med = statistics.median(repeat_times)
print(f'  Repeated: med={repeat_med:.1f}ms  mean={statistics.mean(repeat_times):.1f}ms')

repeat_results = [
    {'scenario': 'First query', 'median_ms': round(first_ms, 1), 'mean_ms': round(first_ms, 1)},
    {'scenario': 'Repeated queries', 'median_ms': round(repeat_med, 1), 'mean_ms': round(statistics.mean(repeat_times), 1)},
]

# ============================================================
# 2. Temporal locality
# ============================================================
print('\n' + '=' * 60)
print('2. TEMPORAL LOCALITY')
print('=' * 60)

wait_times = [int(x) for x in args.wait_times.split(',')]
temporal_results = []

for wait_sec in wait_times:
    print(f'\n  Wait time: {wait_sec}s...')
    times = []
    for i in range(args.runs):
        # Warm up
        user_api.load_feed()
        if wait_sec > 0:
            time.sleep(wait_sec)
        ms, _ = measure_ms(lambda: user_api.load_feed())
        times.append(ms)

    med = statistics.median(times)
    print(f'    Latency after {wait_sec}s: med={med:.1f}ms  mean={statistics.mean(times):.1f}ms')
    temporal_results.append({
        'wait_seconds': wait_sec,
        'median_ms': round(med, 1),
        'mean_ms': round(statistics.mean(times), 1),
        'min_ms': round(min(times), 1),
        'max_ms': round(max(times), 1),
    })

csv_path1 = os.path.join(args.output, 'repeat_query.csv')
with open(csv_path1, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=repeat_results[0].keys())
    writer.writeheader()
    writer.writerows(repeat_results)
print(f'\nCSV saved to {csv_path1}')

csv_path2 = os.path.join(args.output, 'temporal_locality.csv')
with open(csv_path2, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=temporal_results[0].keys())
    writer.writeheader()
    writer.writerows(temporal_results)
print(f'CSV saved to {csv_path2}')
