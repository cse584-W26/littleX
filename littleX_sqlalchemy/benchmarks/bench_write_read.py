"""Write-then-read latency benchmark for SQLAlchemy backend.

Measures how quickly new tweets appear in feeds after posting.
No cache clearing (SQLAlchemy has no graph cache).

Usage:
    python bench_write_read.py -u http://localhost:8080 -r 10
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'Write-then-read latency (SQLAlchemy)'
parser.add_argument('-r', '--runs', default=10, type=int)
parser.add_argument('--output', default='write_read_results')
args = parser.parse_args()

PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

def measure_ms(fn):
    start = time.perf_counter()
    result = fn()
    return (time.perf_counter() - start) * 1000, result

print('Setting up users...')
writer_api = LittleXAPI(args.url)
writer_uid = writer_api.create_user('writer@bench.com', PASSWORD, 'WriterUser')
for i in range(5):
    writer_api.create_tweet(f'Initial tweet {i}')

reader_api = LittleXAPI(args.url)
reader_uid = reader_api.create_user('reader@bench.com', PASSWORD, 'ReaderUser')
reader_api.follow_user(writer_uid)

unfollowed_api = LittleXAPI(args.url)
unfollowed_uid = unfollowed_api.create_user('unfollowed@bench.com', PASSWORD, 'UnfollowedUser')
for i in range(10):
    unfollowed_api.create_tweet(f'Unfollowed tweet {i}')

print('Setup complete.\n')
results = []

# S1: Self write-read
print(f'=== S1: Self write-then-read ({args.runs} runs) ===')
s1_write, s1_read, s1_found = [], [], []
for i in range(args.runs):
    writer_api.load_feed()
    w_ms, _ = measure_ms(lambda: writer_api.create_tweet(f'S1 tweet {i} {time.time():.0f}'))
    r_ms, resp = measure_ms(lambda: writer_api.load_feed())
    feed = resp.json()['data']['result']
    found = any(f'S1 tweet {i}' in t['content'] for t in feed) if isinstance(feed, list) else False
    s1_write.append(w_ms); s1_read.append(r_ms); s1_found.append(found)
    print(f'  Run {i+1}: write={w_ms:.1f}ms  read={r_ms:.1f}ms  found={found}')

# S2: Cross-user write-read
print(f'\n=== S2: Cross-user write-then-read ({args.runs} runs) ===')
s2_write, s2_read, s2_found = [], [], []
for i in range(args.runs):
    writer_api.load_feed(); reader_api.load_feed()
    w_ms, _ = measure_ms(lambda: writer_api.create_tweet(f'S2 tweet {i} {time.time():.0f}'))
    r_ms, resp = measure_ms(lambda: reader_api.load_feed())
    feed = resp.json()['data']['result']
    found = any(f'S2 tweet {i}' in t['content'] for t in feed) if isinstance(feed, list) else False
    s2_write.append(w_ms); s2_read.append(r_ms); s2_found.append(found)
    print(f'  Run {i+1}: write={w_ms:.1f}ms  read={r_ms:.1f}ms  found={found}')

# S3: Follow-then-read
print(f'\n=== S3: Follow-then-read ({args.runs} runs) ===')
s3_follow, s3_read, s3_new = [], [], []
follow_targets = []
for i in range(args.runs):
    ft_api = LittleXAPI(args.url)
    ft_uid = ft_api.create_user(f'ft_{i}@b.com', PASSWORD, f'FT_{i}')
    for t in range(3):
        ft_api.create_tweet(f'FT {i} tweet {t}')
    follow_targets.append(ft_uid)

for i in range(args.runs):
    resp_before = reader_api.load_feed()
    count_before = len(resp_before.json()['data']['result'])
    f_ms, _ = measure_ms(lambda: reader_api.follow_user(follow_targets[i]))
    r_ms, resp = measure_ms(lambda: reader_api.load_feed())
    count_after = len(resp.json()['data']['result'])
    s3_follow.append(f_ms); s3_read.append(r_ms); s3_new.append(count_after - count_before)
    print(f'  Run {i+1}: follow={f_ms:.1f}ms  read={r_ms:.1f}ms  +{count_after - count_before} tweets')

print('\n' + '=' * 60)
print('RESULTS')
print('=' * 60)

def fmt(times):
    return f'mean={statistics.mean(times):.1f}ms  med={statistics.median(times):.1f}ms  min={min(times):.1f}ms'

scenarios = [
    ('S1: Self write-read', s1_write, s1_read, s1_found),
    ('S2: Cross-user write-read', s2_write, s2_read, s2_found),
]
for name, writes, reads, found in scenarios:
    vis = sum(found) / len(found) * 100
    print(f'\n{name}:')
    print(f'  Write: {fmt(writes)}')
    print(f'  Read:  {fmt(reads)}')
    print(f'  Visibility: {vis:.0f}%')

print(f'\nS3: Follow-then-read:')
print(f'  Follow: {fmt(s3_follow)}')
print(f'  Read:   {fmt(s3_read)}')
print(f'  Avg new tweets: {statistics.mean(s3_new):.1f}')

csv_data = []
for name, writes, reads, found in scenarios:
    csv_data.append({
        'scenario': name,
        'write_median_ms': round(statistics.median(writes), 1),
        'read_median_ms': round(statistics.median(reads), 1),
        'visibility_pct': round(sum(found) / len(found) * 100, 0),
    })
csv_data.append({
    'scenario': 'S3: Follow-then-read',
    'write_median_ms': round(statistics.median(s3_follow), 1),
    'read_median_ms': round(statistics.median(s3_read), 1),
    'visibility_pct': 100 if statistics.mean(s3_new) > 0 else 0,
})

csv_path = os.path.join(args.output, 'results.csv')
with open(csv_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=csv_data[0].keys())
    writer.writeheader()
    writer.writerows(csv_data)
print(f'\nCSV saved to {csv_path}')
