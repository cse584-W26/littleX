"""Concurrent load benchmark for SQLAlchemy backend.

Adapted from littleX-benchmarks/bench_concurrent.py for /function/ endpoints.

Usage:
    python bench_concurrent.py -u http://localhost:8080 --users 20 --requests 50
"""

from core import parser, LittleXAPI
import statistics
import csv
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

parser.description = 'Concurrent load benchmark (SQLAlchemy)'
parser.add_argument('--users', default=20, type=int)
parser.add_argument('--requests', default=50, type=int)
parser.add_argument('--concurrency-levels', default='1,5,10,20')
parser.add_argument('--output', default='concurrent_results')
args = parser.parse_args()

PASSWORD = 'testte'
os.makedirs(args.output, exist_ok=True)

concurrency_levels = [int(x) for x in args.concurrency_levels.split(',')]
concurrency_levels = [c for c in concurrency_levels if c <= args.users]

print(f'Setting up {args.users} users...')
user_apis = []
for i in range(args.users):
    user_api = LittleXAPI(args.url)
    uid = user_api.create_user(f'conc_{i}@b.com', PASSWORD, f'ConcUser_{i}')
    for t in range(5):
        user_api.create_tweet(f'Conc user {i} tweet {t}')
    user_apis.append(user_api)
    if (i + 1) % 10 == 0:
        print(f'  Created {i+1}/{args.users}')

print('Setting up follow relationships...')
for i, user_api in enumerate(user_apis):
    for j in range(min(5, len(user_apis) - 1)):
        target_idx = (i + j + 1) % len(user_apis)
        target_api = user_apis[target_idx]
        r = target_api.get_profile()
        target_id = r.json()['data']['result']['id']
        user_api.follow_user(target_id)

print(f'Setup complete.\n')

results_lock = threading.Lock()

def do_read(user_api, results_list):
    start = time.perf_counter()
    try:
        user_api.load_feed()
        elapsed = (time.perf_counter() - start) * 1000
        with results_lock:
            results_list.append({'op': 'read', 'ms': elapsed, 'ok': True})
    except Exception:
        elapsed = (time.perf_counter() - start) * 1000
        with results_lock:
            results_list.append({'op': 'read', 'ms': elapsed, 'ok': False})

def do_write(user_api, results_list):
    start = time.perf_counter()
    try:
        user_api.create_tweet(f'Write at {time.time():.0f}')
        elapsed = (time.perf_counter() - start) * 1000
        with results_lock:
            results_list.append({'op': 'write', 'ms': elapsed, 'ok': True})
    except Exception:
        elapsed = (time.perf_counter() - start) * 1000
        with results_lock:
            results_list.append({'op': 'write', 'ms': elapsed, 'ok': False})

all_results = []
modes = [('read-only', 0), ('mixed-80/20', 20)]

for mode_name, write_pct in modes:
    print(f'=== Mode: {mode_name} ===\n')
    for concurrency in concurrency_levels:
        print(f'  Concurrency={concurrency}, {args.requests} requests...')
        for ua in user_apis[:5]:
            ua.load_feed()

        latencies = []
        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = []
            for i in range(args.requests):
                ua = user_apis[i % len(user_apis)]
                if write_pct > 0 and (i % int(100 / write_pct)) == 0:
                    futures.append(executor.submit(do_write, ua, latencies))
                else:
                    futures.append(executor.submit(do_read, ua, latencies))
            for f in as_completed(futures):
                f.result()
        wall_time = (time.perf_counter() - start) * 1000

        ok_times = sorted([l['ms'] for l in latencies if l['ok']])
        errors = sum(1 for l in latencies if not l['ok'])
        if ok_times:
            p50 = ok_times[len(ok_times) // 2]
            p95 = ok_times[int(len(ok_times) * 0.95)]
            p99 = ok_times[int(len(ok_times) * 0.99)]
            throughput = len(ok_times) / (wall_time / 1000)
            print(f'    Throughput: {throughput:.1f} req/s  p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms')
            if errors:
                print(f'    Errors: {errors}/{len(latencies)}')
            print()
            all_results.append({
                'mode': mode_name, 'concurrency': concurrency,
                'throughput_rps': round(throughput, 1),
                'p50_ms': round(p50, 1), 'p95_ms': round(p95, 1), 'p99_ms': round(p99, 1),
                'mean_ms': round(statistics.mean(ok_times), 1),
                'min_ms': round(min(ok_times), 1), 'max_ms': round(max(ok_times), 1),
                'errors': errors,
            })

if all_results:
    csv_path = os.path.join(args.output, 'results.csv')
    all_keys = list(dict.fromkeys(k for r in all_results for k in r.keys()))
    with open(csv_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(all_results)
    print(f'CSV saved to {csv_path}')

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(18, 5))
    for mode_name, _ in modes:
        data = [r for r in all_results if r['mode'] == mode_name]
        if not data: continue
        cs = [r['concurrency'] for r in data]
        axes[0].plot(cs, [r['throughput_rps'] for r in data], 'o-', label=mode_name, linewidth=2)
        axes[1].plot(cs, [r['p50_ms'] for r in data], 'o-', label=f'{mode_name} p50', linewidth=2)
        axes[1].plot(cs, [r['p95_ms'] for r in data], 's--', label=f'{mode_name} p95', linewidth=1.5, alpha=0.7)
        axes[2].plot(cs, [r['mean_ms'] for r in data], 'o-', label=mode_name, linewidth=2)

    axes[0].set_xlabel('Concurrency'); axes[0].set_ylabel('Throughput (req/s)'); axes[0].set_title('Throughput'); axes[0].legend(); axes[0].grid(True, alpha=0.3)
    axes[1].set_xlabel('Concurrency'); axes[1].set_ylabel('Latency (ms)'); axes[1].set_title('Latency Percentiles'); axes[1].legend(fontsize=7); axes[1].grid(True, alpha=0.3)
    axes[2].set_xlabel('Concurrency'); axes[2].set_ylabel('Latency (ms)'); axes[2].set_title('Mean Latency'); axes[2].legend(); axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(os.path.join(args.output, 'concurrent_benchmark.png'), dpi=150)
    plt.close()
    print(f'Chart saved.')
except ImportError:
    pass
