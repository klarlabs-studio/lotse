"""Exploratory retrievers. No gold annotations or task-specific query rewrites."""
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

import numpy as np
import tiktoken

BASE = Path(__file__).resolve().parents[1]
os.environ["TIKTOKEN_CACHE_DIR"] = str(BASE / ".research/tokenizer")
os.environ["HF_HOME"] = str(BASE / ".research/huggingface")
os.environ["HF_HUB_OFFLINE"] = "1"
ENCODING = tiktoken.get_encoding("cl100k_base")
EXTENSIONS = {".go", ".py", ".js", ".mjs", ".cjs", ".ts", ".md", ".mdx", ".rst", ".txt"}
EXTENSIONS_V2 = EXTENSIONS | {".tsx", ".jsx"}  # TypeScript screens (preregistration v25); earlier corpora keep V1.
STOP = set("a an the is are be to of for and or in on with its it this that which what where how why can as by from if should does do need find locate explain check claim against changes change implementation test tests".split())


def tokens(text):
    return ENCODING.encode(text, disallowed_special=())


def terms(text):
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [w for w in re.findall(r"[a-zA-Z0-9]+", split.lower()) if len(w) > 1 and w not in STOP]


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def declaration(line):
    # Syntax heuristics, deliberately not called compiler-accurate symbol resolution.
    patterns = [r"^\s*(?:async\s+)?(?:def|class|function)\s+(\w+)",
                r"^func\s+(?:\([^)]*\)\s*)?(\w+)", r"^type\s+(\w+)"]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1)
    return None


def chunks_from(path, text):
    lines = text.splitlines()
    result = []
    for offset in range(0, len(lines), 24):
        body = "\n".join(lines[offset:offset + 24])
        if not body.strip():
            continue
        result.append(dict(path=path, start=offset + 1, end=min(offset + 24, len(lines)), text=body))
    return result


def graf_artifact(root):
    """The index file graf wrote under `root`: schema 2 `.graf/index.bin`, else schema 1 JSON."""
    compact = Path(root) / ".graf/index.bin"
    return compact if compact.is_file() else Path(root) / ".graf/index.json"


def graf_index(binary, root):
    """The index under `root` in the schema 1 JSON layout, whichever schema the build wrote."""
    artifact = graf_artifact(root)
    if artifact.suffix == ".json":
        return json.loads(artifact.read_text())
    return json.loads(subprocess.run([str(binary), "--root", str(root), "export-index"], check=True,
                                     capture_output=True, text=True).stdout)


def load_documents(root, extensions=EXTENSIONS):
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_symlink() or not path.is_file() or path.suffix not in extensions:
            continue
        if any(p.startswith(".") or p in {"node_modules", "vendor", "graphify-out"} for p in rel.parts):
            continue
        if path.stat().st_size > 1_000_000:
            continue
        try:
            files[rel.as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeError:
            continue
    return files


class BM25:
    def __init__(self, texts):
        self.postings = defaultdict(list)
        self.lengths = []
        for i, text in enumerate(texts):
            counts = Counter(terms(text))
            self.lengths.append(sum(counts.values()))
            for term, count in counts.items():
                self.postings[term].append((i, count))
        self.average = sum(self.lengths) / max(len(self.lengths), 1)

    def score(self, query):
        scores = np.zeros(len(self.lengths), dtype=np.float64)
        n = len(self.lengths)
        for term in set(terms(query)):
            hits = self.postings.get(term, [])
            idf = math.log(1 + (n - len(hits) + .5) / (len(hits) + .5))
            for i, count in hits:
                norm = 1.2 * (.25 + .75 * self.lengths[i] / max(self.average, 1))
                scores[i] += idf * count * 2.2 / (count + norm)
        return scores


def ranked(scores):
    return sorted((i for i, value in enumerate(scores) if value > 0), key=lambda i: (-scores[i], i))


def fuse(rankings):
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, i in enumerate(ranking[:100], 1):
            scores[i] += 1 / (60 + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))


class Index:
    def __init__(self, files, cache, model):
        self.files, self.cache, self.model = files, cache, model
        self.chunks = []
        self.paths = sorted(files)
        self.by_path = defaultdict(list)
        self.definitions = defaultdict(set)
        self.projections = []
        for path in self.paths:
            text = files[path]
            skeleton = [path]
            for number, line in enumerate(text.splitlines(), 1):
                name = declaration(line)
                if name:
                    self.definitions[name].add(path)
                if name or line.startswith("#") or (path.endswith(".rst") and line.startswith(".. ")):
                    skeleton.append(line)
            self.projections.append("\n".join(skeleton))
            for chunk in chunks_from(path, text):
                self.by_path[path].append(len(self.chunks))
                self.chunks.append(chunk)
        start = time.perf_counter()
        self.lexical = BM25([c["path"] + "\n" + c["text"] for c in self.chunks])
        self.hierarchy = BM25(self.projections)
        self.lexical_build_s = time.perf_counter() - start
        start = time.perf_counter()
        self.edges = defaultdict(set)
        for i, chunk in enumerate(self.chunks):
            for name in set(re.findall(r"\b[A-Za-z_]\w*\b", chunk["text"])):
                owners = self.definitions.get(name, set())
                # Only globally unambiguous names; relations remain heuristic.
                if len(owners) == 1 and len(name) >= 4:
                    owner = next(iter(owners))
                    if owner != chunk["path"]:
                        self.edges[chunk["path"]].add(owner)
                        self.edges[owner].add(chunk["path"])
        self.graph_build_s = time.perf_counter() - start
        start = time.perf_counter()
        self.vectors, self.embedding_misses = self.embed([c["text"] for c in self.chunks])
        self.embedding_build_s = time.perf_counter() - start

    def embed(self, texts):
        self.cache.mkdir(parents=True, exist_ok=True)
        values = [None] * len(texts)
        missing = {}
        for i, text in enumerate(texts):
            key = digest(text)
            path = self.cache / (key + ".npy")
            if path.exists():
                values[i] = np.load(path)
            else:
                missing.setdefault(key, (text, []))[1].append(i)
        if missing:
            # Group similar lengths to avoid padding many tiny snippets to 512 tokens.
            pending = sorted(missing.items(), key=lambda item: (len(item[1][0]), item[0]))
            generated = self.model.embed([v[0] for _, v in pending], batch_size=8)
            for (key, (_, positions)), vector in zip(pending, generated):
                vector = np.asarray(vector, dtype=np.float32)
                np.save(self.cache / (key + ".npy"), vector)
                for i in positions:
                    values[i] = vector
        array = np.asarray(values, dtype=np.float32)
        array /= np.maximum(np.linalg.norm(array, axis=1, keepdims=True), 1e-12)
        return array, len(missing)

    def rank(self, query, method):
        scores = self.lexical.score(query)
        lexical = ranked(scores)
        if method == "bm25":
            return lexical
        if method == "grep":
            # A transparent OR-term occurrence baseline, not a simulated agent.
            qt = set(terms(query))
            counts = [len(qt.intersection(terms(c["text"]))) for c in self.chunks]
            return ranked(counts)
        if method == "hierarchy":
            paths = {self.paths[i] for i in ranked(self.hierarchy.score(query))[:6]}
            return [i for i in lexical if self.chunks[i]["path"] in paths]
        if method in {"graph", "hybrid"}:
            expanded = set()
            for i in lexical[:3]:
                path = self.chunks[i]["path"]
                expanded.add(path)
                expanded.update(sorted(self.edges[path])[:32])
            graph = [i for i in lexical if self.chunks[i]["path"] in expanded]
            if method == "graph":
                return graph
        # Dense cosine ranking includes all chunks; no uncalibrated abstention.
        vector = np.asarray(next(iter(self.model.query_embed(query))), dtype=np.float32)
        vector /= max(np.linalg.norm(vector), 1e-12)
        dense = ranked(self.vectors @ vector)
        return dense if method == "semantic" else fuse([lexical, dense, graph])


def render(chunk):
    return f"{chunk['path']}:{chunk['start']}-{chunk['end']}\n{chunk['text']}\n"


def pack(chunks, order, budget, excluded=()):
    selected = []
    excluded = set(excluded)
    text = ""
    used = 0
    for i in order[:100]:
        if i in excluded:
            continue
        candidate = render(chunks[i])
        cost = len(tokens(candidate))
        if used + cost <= budget and len(tokens(text + candidate)) <= budget:
            selected.append(i)
            text += candidate
            used = len(tokens(text))
    return selected, text


def score_evidence(chunks, selected, evidence):
    if not evidence:
        return dict(anchor_recall=None, annotated_chunk_precision=None, file_recall=None,
                    all_anchors=None, returned_context=bool(selected))
    def matches(c, e):
        return c["path"] == e["path"] and c["start"] <= e["start"] and c["end"] >= e["end"]
    hit = sum(any(matches(chunks[i], e) for i in selected) for e in evidence)
    paths = {e["path"] for e in evidence}
    found = {chunks[i]["path"] for i in selected}
    return dict(anchor_recall=hit / len(evidence), all_anchors=hit == len(evidence),
                annotated_chunk_precision=sum(any(matches(chunks[i], e) for e in evidence) for i in selected) / max(len(selected), 1),
                file_recall=len(paths & found) / len(paths), returned_context=bool(selected))
