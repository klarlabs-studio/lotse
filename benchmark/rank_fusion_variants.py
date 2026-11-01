"""Development only: score native-fusion variants offline from the full rankings rank_fusion_check.py --native saved.

    python benchmark/rank_fusion_variants.py t12 t13

A variant cuts graph-files' ranking to its first N files before fusing it with graf's triage files (reciprocal rank,
k = 60), as Graphify's own output is cut by its token budget. Prints, per batch and pooled, how often each variant
ranks a faulted file first, and its gains and losses against graf alone and against Graphify's fusion. Never run on
a test batch: the variant is chosen here, then frozen before the test.
"""
import json
import sys

from rank_fusion_check import BASE, first, fused
from triage_replay import mcnemar_one_sided

CAPS = (10, 15, 20, 30, None)


def score(rows):
    out = {}
    for cap in CAPS:
        hits = []
        for row in rows:
            lists = row["lists"]
            faulted = set(lists["faulted"])
            hits.append((first(fused(lists["graf"], lists["native"][:cap]), faulted),
                         row["graf"]["first"], row["fused"]["first"]))
        gained = sum(h and not g for h, g, _ in hits)
        lost = sum(g and not h for h, g, _ in hits)
        out[str(cap)] = {"first": sum(h for h, _, _ in hits), "vs_graf": [gained, lost, mcnemar_one_sided(gained, lost)],
                         "vs_graphify_fused": [sum(h and not f for h, _, f in hits), sum(f and not h for h, _, f in hits)]}
    return out


def main(batches):
    pooled = []
    for batch in batches:
        rows = json.loads((BASE / f"results/rank-fusion-native-{batch}.json").read_text())["rows"]
        pooled += rows
        print(batch, len(rows), "graf", sum(r["graf"]["first"] for r in rows),
              "graphify-fused", sum(r["fused"]["first"] for r in rows), json.dumps(score(rows)))
    print("pooled", len(pooled), json.dumps(score(pooled), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
