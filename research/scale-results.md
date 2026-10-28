# Large-codebase diagnostic

`benchmark/scale_check.py` downloads three pinned large repositories, then measures
`graf index`, `locate`, `units` (cold, then warm), `triage`, and `outline`, plus
Graphify's code-only extract and one query. Each call is one fresh process, timed
with `/usr/bin/time -l` for wall time and peak resident memory. Raw results are
in `results/scale-check.json`.

This is a diagnostic of whether graf works at scale. There is one issue per
repository and one sample per call, on a loaded development machine (load
average recorded). It is not a preregistered comparison and supports no speed
claim against Graphify.

Repositories:
- Django `79502d08`: 4,188 indexed files.
- Kubernetes `2770c7b3`: 22,111 files.
- VS Code `bdce63db`: 18,830 files, including TypeScript units.

## What changed

The first run (`index.json` schema 1, `units.json`) showed that graf did not
scale. On Kubernetes:
- the index was 542 MB;
- `locate` took 3.4 s;
- a warm `triage` took 7.0 s and peaked at 2 GB of memory.

Profiling showed two causes:
- **Index size.** The index stored every excerpt's text, and its postings were
  JSON.
- **Repeated work per query.** Every query re-tokenized every unit's text,
  then rebuilt the BM25 postings and call edges.

Three output-preserving changes followed:
1. **`afa1814`:** the unit cache stores term counts instead of text, in bincode.
2. **`e903f78`:** index schema 2. The index is bincode, with u32 postings, and
   stores no excerpt text; text is read from the hash-checked source when a
   result is delivered.
3. **`ea8824d`:** a query index, `.graf/unit-index.bin`, holds the unit
   postings and call edges. It is reused while a digest of every source hash
   matches.

Outputs are unchanged by all three changes:

| Check | Result |
|---|---|
| `check_equivalence` vs the previous build, index compared in the schema 1 layout | 1631/1631 identical |
| `units` vs the prototype | 830/830 identical |
| `triage` vs the prototype | 767/767 identical |

## Results

Times are seconds; memory is peak resident memory in MB. "Before" is the first
run and "after" is `ea8824d`.

| | Django before | Django after | Kubernetes before | Kubernetes after | VS Code before | VS Code after |
|---|---:|---:|---:|---:|---:|---:|
| `graf index`, cold | 2.0 | 0.7 | 11.7 | 5.7 | 7.4 | 7.0 |
| Index size (MB) | 71.8 | 17.3 | 542 | 132 | 683 | 159 |
| `locate` | 0.28 | 0.05 | 3.36 | 0.16 | 1.00 | 0.17 |
| First `units` (fills caches) | 4.2 | 2.1 | 35.0 | 16.3 | 23.3 | 20.9 |
| Warm `units` | 1.20 | 0.11 | 6.90 | 0.29 | 5.87 | 0.30 |
| Warm `triage` | 1.23 | 0.14 | 7.04 | 0.42 | 6.09 | 0.48 |
| Warm `triage` memory | 297 | 161 | 2,050 | 789 | 2,283 | 903 |
| `outline` | 0.17 | 0.02 | 1.43 | 0.10 | 0.94 | 0.17 |

Graphify, from the first run with the same snapshots and issues:

| | Django | Kubernetes | VS Code |
|---|---:|---:|---:|
| Extract, code-only, 2 workers (s) | 64.6 | 1,001 | 411 |
| Extract peak memory (MB) | 1,574 | 1,965 | 5,189 |
| `graph.json` (MB) | 52 | 545 | 490 |
| One query (s) | 4.3 | failed | 17.8 |

## Remaining limits

- **In this historical diagnostic, first-time function indexing took 16–21 s.**
  Later commit `ad53f4d` moved function-index construction into `graf index`,
  so the recorded cold-query boundary does not describe the current build.
- **Warm memory is still 0.8–0.9 GB on the largest repositories,** because the
  whole query index is deserialized on each call. A memory-mapped or
  persistent-process design can avoid repeated deserialization. The later MCP
  server keeps indexes loaded, but its latency and memory have not been
  comparatively measured; it now scans source hashes before every tool call.
