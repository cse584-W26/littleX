"""Run the full bench suite against the pure Flask + Postgres backend.

Clears the DB before each script via /walker/clear_data, then invokes
each bench script as a subprocess so stdout is captured live and CSVs
land in per-script subdirectories under Postgres_results/.
"""
import argparse
import os
import subprocess
import sys
import time
from core import LittleXAPI

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, 'Postgres_results')

# (script, output_subdir, extra_args)
# Run smaller -r counts for the sweep so the whole suite finishes in
# reasonable wall time. Override with --runs.
SCRIPTS = [
    ('bench_evaluation.py',      'eval_results',            ['-r', '3']),
    ('bench_archetypes.py',      'archetype_results',       ['-r', '3']),
    ('bench_concurrent.py',      'concurrent_results',      []),
    ('bench_write_read.py',      'write_read_results',      ['-r', '5']),
    ('bench_graph_ops.py',       'graph_ops_results',       ['-r', '5']),
    ('bench_search.py',          'search_results',          ['-r', '5']),
    ('bench_cache.py',           'cache_results',           ['-r', '5']),
    ('bench_filter_pushdown.py', 'filter_pushdown_results', ['-r', '3']),
]


def reset_db(url):
    api = LittleXAPI(url)
    try:
        api.create_user('reset@b.com', 'pw', 'Reset')
    except Exception:
        pass
    try:
        api.clear_data()
    except Exception as e:
        print(f'  (clear_data warning: {e})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-u', '--url', default='http://localhost:8765')
    ap.add_argument('--only', help='Run only this script (e.g. bench_evaluation.py)')
    args = ap.parse_args()

    os.makedirs(RESULTS, exist_ok=True)
    summary = []
    for script, subdir, extra in SCRIPTS:
        if args.only and script != args.only:
            continue
        out_dir = os.path.join(RESULTS, subdir)
        print(f'\n{"=" * 70}')
        print(f'>>> {script}  ->  {out_dir}')
        print('=' * 70)
        print('Resetting DB...')
        reset_db(args.url)
        cmd = [sys.executable, os.path.join(HERE, script),
               '-u', args.url, '--output', out_dir] + extra
        env = os.environ.copy()
        # Ensure the shim core.py here is imported, not the JacSQL one
        # next to the symlink target.
        env['PYTHONPATH'] = HERE + os.pathsep + env.get('PYTHONPATH', '')
        t0 = time.time()
        rc = subprocess.call(cmd, cwd=HERE, env=env)
        elapsed = time.time() - t0
        summary.append((script, rc, elapsed))
        print(f'\n>>> {script} finished in {elapsed:.1f}s with rc={rc}')

    print(f'\n{"=" * 70}')
    print('SUITE SUMMARY')
    print('=' * 70)
    for script, rc, elapsed in summary:
        status = 'OK' if rc == 0 else f'FAIL(rc={rc})'
        print(f'  {script:32} {status:12} {elapsed:7.1f}s')


if __name__ == '__main__':
    main()
