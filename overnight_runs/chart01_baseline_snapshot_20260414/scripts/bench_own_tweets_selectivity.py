"""Own-tweets selectivity sweep at fan_out=200, client-side timed.

Sweeps selectivity (% of the user's fan-out edges that are tweets vs
channel memberships) and measures end-to-end GET latency for
`load_own_tweets`. Unlike the older bench_evaluation.py (which reads
server-side `ms_my_tweets`), this driver times the full network round
trip from the client, so port-forward + Flask/Jaseci dispatch +
serialization are all included.

Works against any backend that exposes the endpoints below. Pass
`--endpoint-prefix /walker` for Jac graph / sqlalchemy_jac, or
`--endpoint-prefix ""` for pure Flask (PG handtuned, SQLAlchemy pure).

Usage:
    python bench_own_tweets_selectivity.py -u http://localhost:8000 -r 5
    python bench_own_tweets_selectivity.py -u http://localhost:8090 -r 5 --endpoint-prefix ""
"""

import argparse
import csv
import json
import os
import statistics
import time

import requests


CONFIGS = [
    # (fan_out, n_tweets, n_channels, selectivity_pct)
    # Mirrors the 11-row CONFIGS in littleX-benchmarks/bench_evaluation.py
    # (the Jac variant) so the SQL rerun aligns 1:1 with Naive_SAM,
    # New_SAM, Filter_Pushdown CSVs.
    # Selectivity sweep at fan_out=200
    (200, 100, 100, 50.0),
    (200, 40,  160, 20.0),
    (200, 20,  180, 10.0),
    (200, 10,  190, 5.0),
    (200, 4,   196, 2.0),
    # Fan-out sweep at ~5%
    (50,  3,   47,  6.0),
    (100, 5,   95,  5.0),
    (400, 20,  380, 5.0),
    # High fan-out
    (500, 5,   495, 1.0),
    (500, 25,  475, 5.0),
    (500, 50,  450, 10.0),
]

PASSWORD = "testte"


def mkargs():
    p = argparse.ArgumentParser(description="own-tweets selectivity sweep")
    p.add_argument("-u", "--url", default="http://localhost:8000")
    p.add_argument("-r", "--runs", type=int, default=5)
    p.add_argument("--endpoint-prefix", default="/walker",
                   help="'/walker' for Jac graph / sqlalchemy_jac; '' for PG/SQLAlchemy Flask")
    p.add_argument("--output", default="own_tweets_results",
                   help="output dir for results.csv")
    p.add_argument("--auth-scheme", default="jwt",
                   choices=["jwt", "bearer-username"],
                   help="jwt = POST /user/login and use returned token; "
                        "bearer-username = Authorization: Bearer <username> (PG handtuned)")
    p.add_argument("--warmup", type=int, default=1,
                   help="Number of warmup load_own_tweets calls before timed runs "
                        "(default 1). Use 20+ to isolate cold-start floor vs steady-state.")
    return p.parse_args()


class Client:
    def __init__(self, base_url, endpoint_prefix, auth_scheme):
        self.base = base_url.rstrip("/")
        self.prefix = endpoint_prefix
        self.auth_scheme = auth_scheme
        self.session = requests.Session()

    def _url(self, path):
        return self.base + path

    def register_and_login(self, username, password):
        if self.auth_scheme == "jwt":
            self.session.post(self._url("/user/register"),
                              json={"username": username, "password": password})
            r = self.session.post(self._url("/user/login"),
                                  json={"username": username, "password": password})
            body = r.json()
            # Jac jaseci returns {"data": {"token": "..."}}; Flask baselines
            # return {"data": {"result": {"token": "..."}}} or {"token": "..."}
            tok = (body.get("token")
                   or body.get("data", {}).get("token")
                   or body.get("data", {}).get("result", {}).get("token"))
            if not tok:
                raise RuntimeError(f"no token in /user/login response: {body}")
            self.session.headers["Authorization"] = f"Bearer {tok}"
        else:
            self.session.post(self._url("/user/register"),
                              json={"username": username, "password": password})
            self.session.headers["Authorization"] = f"Bearer {username}"

    def setup_profile(self, handle, bio=""):
        return self.session.post(self._url(f"{self.prefix}/setup_profile"),
                                 json={"username": handle, "bio": bio})

    def create_tweet(self, content):
        return self.session.post(self._url(f"{self.prefix}/create_tweet"),
                                 json={"content": content})

    def create_channel(self, name, description=""):
        return self.session.post(self._url(f"{self.prefix}/create_channel"),
                                 json={"name": name, "description": description})

    def load_own_tweets(self):
        return self.session.post(self._url(f"{self.prefix}/load_own_tweets"),
                                 json={})


def run_one_config(base_url, endpoint_prefix, auth_scheme, idx, fan_out,
                   n_tweets, n_channels, runs, warmup):
    email = f"owntweets_{idx}_{int(time.time())}@b.com"
    handle = f"OwnTweets_{idx}"

    c = Client(base_url, endpoint_prefix, auth_scheme)
    c.register_and_login(email, PASSWORD)
    c.setup_profile(handle)

    print(f"  Creating {n_tweets} tweets + {n_channels} channels...")
    for i in range(n_tweets):
        c.create_tweet(f"Own-tweets bench {idx} tweet {i}")
    for i in range(n_channels):
        c.create_channel(f"own_{idx}_ch_{i}", "noise")

    # Warm-up (cold L1 plus any first-call JITs)
    for _ in range(max(1, warmup)):
        c.load_own_tweets()

    times = []
    ms_traversal_samples = []
    ms_build_samples = []
    correctness_failures = 0
    for _ in range(runs):
        t0 = time.perf_counter()
        r = c.load_own_tweets()
        elapsed_ms = (time.perf_counter() - t0) * 1000
        r.raise_for_status()
        times.append(elapsed_ms)
        # Correctness check: the response must contain exactly n_tweets
        # tweets (the user created no more, no less). If any baseline
        # silently under- or over-fetches we want to see it, not a
        # misleadingly fast timing. Scan the usual payload locations.
        try:
            body_c = r.json()
            result_c = body_c.get("data", {}).get("result")
            reports_c = body_c.get("data", {}).get("reports") or []
            tweets_list = None
            for cand in (result_c, reports_c[0] if reports_c else None):
                if isinstance(cand, dict) and "tweets" in cand:
                    tweets_list = cand["tweets"]; break
                if isinstance(cand, list):
                    tweets_list = cand; break
            if tweets_list is not None and len(tweets_list) != n_tweets:
                correctness_failures += 1
                if correctness_failures <= 1:
                    print(f"  [CORRECTNESS] expected {n_tweets} tweets, "
                          f"got {len(tweets_list)}")
        except Exception as e:
            print(f"  [correctness check error] {e}")
        # Server-timed fields are optional (only the Jac walker reports
        # them). SQL/Neo4j endpoints return a plain list — skip gracefully.
        try:
            body = r.json()
            result = body.get("data", {}).get("result")
            reports = body.get("data", {}).get("reports") or []
            # Prefer the dict that actually contains ms_traversal. Jac
            # returns walker metadata in `result` and user reports in
            # `reports[0]`; Flask/FastAPI baselines put the payload in
            # `result`. Scan both in priority order.
            payload = None
            candidates = []
            if isinstance(result, dict):
                candidates.append(result)
            if reports and isinstance(reports[0], dict):
                candidates.append(reports[0])
            for cand in candidates:
                if "ms_traversal" in cand:
                    payload = cand
                    break
            if payload:
                ms_traversal_samples.append(payload["ms_traversal"])
                ms_build_samples.append(payload.get("ms_build_payload", 0))
            elif not ms_traversal_samples:
                # Debug: if the first sample has no ms_traversal, dump
                # the response keys so we can see the actual shape.
                import json as _json
                print("  [debug] response keys:",
                      list((body.get("data") or {}).keys()),
                      "| result type:", type(result).__name__,
                      "| reports[0] type:",
                      type(reports[0]).__name__ if reports else None)
                print("  [debug] first 400 chars:",
                      _json.dumps(body)[:400])
        except Exception as e:
            print(f"  [debug] extraction error: {e}")

    row = {
        "fan_out": fan_out,
        "n_tweets": n_tweets,
        "n_channels": n_channels,
        "selectivity_pct": round(n_tweets / fan_out * 100, 1),
        "median_ms": round(statistics.median(times), 3),
        "mean_ms": round(statistics.mean(times), 3),
        "min_ms": round(min(times), 3),
        "max_ms": round(max(times), 3),
    }
    if ms_traversal_samples:
        row["server_traversal_median_ms"] = round(statistics.median(ms_traversal_samples), 4)
        row["server_build_median_ms"] = round(statistics.median(ms_build_samples), 4)
    row["correctness_failures"] = correctness_failures
    return row


def main():
    args = mkargs()
    os.makedirs(args.output, exist_ok=True)
    print(f"Own-tweets selectivity sweep  —  url={args.url}  runs={args.runs}")
    print(f"  endpoint prefix: {args.endpoint_prefix!r}, auth: {args.auth_scheme}")
    print()

    rows = []
    for idx, (fan_out, n_tweets, n_channels, sel) in enumerate(CONFIGS):
        print(f"[{idx+1}/{len(CONFIGS)}] fan_out={fan_out} tweets={n_tweets} "
              f"channels={n_channels} selectivity={sel}%")
        row = run_one_config(args.url, args.endpoint_prefix, args.auth_scheme,
                             idx, fan_out, n_tweets, n_channels, args.runs,
                             args.warmup)
        rows.append(row)
        print(f"  median={row['median_ms']:.3f}ms  "
              f"mean={row['mean_ms']:.3f}ms  "
              f"min={row['min_ms']:.3f}ms")
        print()

    csv_path = os.path.join(args.output, "results.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
