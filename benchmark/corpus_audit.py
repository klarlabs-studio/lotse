"""Record corpus composition and embedding truncation exposure without an LLM."""
import json
import statistics
from pathlib import Path

from tokenizers import Tokenizer
from retrieval import BASE, chunks_from, load_documents
from prepare_models import MODEL_REVISION


def main():
    tokenizer = Tokenizer.from_file(str(BASE / ".research/models/models--Qdrant--bge-small-en-v1.5-onnx-Q/snapshots" / MODEL_REVISION / "tokenizer.json"))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    result = []
    for repo in ["cobra", "flask", "fastify"]:
        files = load_documents(BASE / ".research/corpora" / repo)
        chunks = [c for p, text in files.items() for c in chunks_from(p, text)]
        lengths = [len(e.ids) for e in tokenizer.encode_batch([c["text"] for c in chunks])]
        result.append(dict(repo=repo, files=len(files), chunks=len(chunks),
                           documentation_files=sum(Path(p).suffix in {".md", ".rst", ".txt", ".mdx"} for p in files),
                           test_files=sum("test" in p.lower() for p in files),
                           median_model_input_tokens=statistics.median(lengths),
                           fraction_chunks_over_128=sum(n > 128 for n in lengths)/len(lengths),
                           fraction_chunks_over_512=sum(n > 512 for n in lengths)/len(lengths)))
    (BASE / "results/corpus-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
