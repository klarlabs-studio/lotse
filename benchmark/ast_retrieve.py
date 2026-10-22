"""Retrieval backend for the ast_graph agent arm (prototype of the localization-screen arm).

    python benchmark/ast_retrieve.py index INPUT_DIR OUTPUT_DIR
    python benchmark/ast_retrieve.py query OUTPUT_DIR QUERY BUDGET

index stores tree-sitter units and call edges for the files load_documents admits; query
scores units with BM25, adds one-hop call propagation, and prints whole units in score order.
Parameters are those frozen in localization_screen (floor 0.5, propagation weight 0.5). TypeScript, TSX,
and JSX are parsed as in `graf units` (v25); the t4 agent backend is the frozen copy in frozen-t4/.
"""
import json
from pathlib import Path
import sys

from localization import FLOOR, WEIGHT, call_edges, code_units, pack_units, propagate
from retrieval import BM25, load_documents

INDEX = "ast-index.json"


def index(input_dir, output_dir):
    files = load_documents(input_dir)
    units = [u for path in sorted(files) for u in code_units(path, files[path], typescript=True)]
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / INDEX).write_text(json.dumps({"files": sorted(files), "units": units,
                                                "edges": call_edges(units)}))


def query(output_dir, text, budget):
    stored = json.loads((output_dir / INDEX).read_text())
    units = stored["units"]
    scores = propagate(list(BM25([u["text"] for u in units]).score(text)),
                       [tuple(e) for e in stored["edges"]], WEIGHT)
    _, payload = pack_units(units, scores, budget, FLOOR)
    return payload


def main(argv):
    if argv[:1] == ["index"] and len(argv) == 3:
        index(Path(argv[1]), Path(argv[2]))
        return 0
    if argv[:1] == ["query"] and len(argv) == 4:
        sys.stdout.write(query(Path(argv[1]), argv[2], int(argv[3])))
        return 0
    sys.stderr.write(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
