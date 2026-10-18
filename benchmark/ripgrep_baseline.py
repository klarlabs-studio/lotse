"""Native ripgrep OR-term search plus the shared bounded source-window packer."""
from collections import Counter
import json
import re
import statistics
import subprocess
import time

from retrieval import BASE, chunks_from, load_documents, pack, score_evidence, terms, tokens


def main():
    rows = []
    cases = json.loads((BASE / "benchmark/queries.json").read_text())
    for repo in ["cobra", "flask", "fastify"]:
        root = BASE / ".research/corpora" / repo
        files = load_documents(root)
        chunks = [c for path, text in files.items() for c in chunks_from(path, text)]
        lookup = {(c["path"], (c["start"] - 1) // 24): i for i, c in enumerate(chunks)}
        for case in [q for q in cases if q["repo"] == repo]:
            pattern = "|".join(re.escape(t) for t in sorted(set(terms(case["query"]))))
            for budget in [2000, 8000]:
                timings = []
                for _ in range(3):
                    start = time.perf_counter()
                    run = subprocess.run(["rg", "--no-config", "--no-heading", "--with-filename", "--line-number", "--color", "never", "-i", "-e", pattern, "--", *files], cwd=root, capture_output=True, text=True)
                    if run.returncode not in [0, 1]:
                        raise RuntimeError(run.stderr)
                    counts = Counter()
                    for line in run.stdout.splitlines():
                        path, number, _ = line.split(":", 2)
                        key = (path, (int(number) - 1) // 24)
                        if key in lookup:
                            counts[lookup[key]] += 1
                    order = sorted(counts, key=lambda i: (-counts[i], i))
                    selected, context = pack(chunks, order, budget)
                    timings.append((time.perf_counter()-start)*1000)
                rows.append(dict(repo=repo, query_id=case["id"], category=case["category"], budget=budget,
                                 method="ripgrep", latency_ms=statistics.median(timings), latency_samples_ms=timings,
                                 output_tokens=len(tokens(context)), raw_match_bytes=len(run.stdout.encode()),
                                 chunks=len(selected), **score_evidence(chunks, selected, case["evidence"])))
    version = subprocess.run(["rg", "--version"], capture_output=True, text=True, check=True).stdout
    (BASE / "results/ripgrep.json").write_text(json.dumps(dict(version=version, queries=rows), indent=2) + "\n")


if __name__ == "__main__":
    main()
