"""Search query benchmark for SQLAlchemy backend.

Tests load_feed with varying search_query selectivity.

Usage:
    python bench_search.py -u http://localhost:8080 -r 5 --tweets 300 --follows 30
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time

parser.description = 'Search query selectivity (SQLAlchemy)'
parser.add_argument('-r', '--runs', default=10, type=int)
parser.add_argument('--tweets', default=500, type=int)
parser.add_argument('--follows', default=50, type=int)
parser.add_argument('--output', default='search_results')
args = parser.parse_args()

PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

api = LittleXAPI(args.url)

hashtags = {'#common': 0.50, '#moderate': 0.10, '#uncommon': 0.03, '#rare': 0.005}

print(f'Setting up: {args.follows} followed users, ~{args.tweets} total tweets')

followee_ids = []
tweets_per_user = max(1, args.tweets // args.follows)
tweet_idx = 0

for i in range(args.follows):
    fapi = LittleXAPI(args.url)
    uid = fapi.create_user(f'search_{i}@b.com', PASSWORD, f'SearchUser_{i}')
    followee_ids.append(uid)
    for t in range(tweets_per_user):
        content = f'Tweet number {tweet_idx} by user {i}'
        for tag, prob in hashtags.items():
            if (tweet_idx * 7 + hash(tag)) % int(1 / prob) == 0:
                content += f' {tag}'
        fapi.create_tweet(content)
        tweet_idx += 1
    if (i + 1) % 20 == 0:
        print(f'  Created {i+1}/{args.follows} users')

total_tweets = tweet_idx

bench_api = LittleXAPI(args.url)
bench_api.create_user('searchbench@b.com', PASSWORD, 'SearchBencher')
for uid in followee_ids:
    bench_api.follow_user(uid)
for i in range(10):
    bench_api.create_tweet(f'My tweet {i} #common #moderate')

print(f'Setup complete. {total_tweets} tweets.\n')

search_terms = [
    ('', 'No filter (baseline)'),
    ('tweet', 'Common ("tweet")'),
    ('#common', 'Common hashtag (~50%)'),
    ('#moderate', 'Moderate hashtag (~10%)'),
    ('#uncommon', 'Uncommon hashtag (~3%)'),
    ('#rare', 'Rare hashtag (~0.5%)'),
    ('#nonexistent', 'Nonexistent (0 matches)'),
]

results = []
for query, description in search_terms:
    print(f'--- {description} ---')
    bench_api.load_feed_with_search(query)  # warm up
    times, counts = [], []
    for i in range(args.runs):
        start = time.perf_counter()
        resp = bench_api.load_feed_with_search(query)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
        feed = resp.json()['data']['result']
        counts.append(len(feed) if isinstance(feed, list) else 0)

    med = statistics.median(times)
    avg_count = statistics.mean(counts)
    print(f'  Query: "{query}"  Latency: med={med:.1f}ms  Results: {avg_count:.0f}\n')
    results.append({
        'query': query if query else '(none)',
        'description': description,
        'median_ms': round(med, 1),
        'mean_ms': round(statistics.mean(times), 1),
        'avg_result_count': round(avg_count, 0),
        'total_tweets': total_tweets,
    })

csv_path = os.path.join(args.output, 'results.csv')
with open(csv_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=results[0].keys())
    writer.writeheader()
    writer.writerows(results)
print(f'CSV saved to {csv_path}')
