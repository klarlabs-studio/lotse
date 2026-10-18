"""Repeat the upstream/CPU artifact numerical sanity check on fixed source chunks."""
import json
import time
import numpy as np

from retrieval import BASE, chunks_from, load_documents
from prepare_models import MODEL, MODEL_REVISION


def main():
    from fastembed import TextEmbedding
    texts = [c["text"] for p, text in load_documents(BASE / ".research/corpora/cobra").items() for c in chunks_from(p, text)][:16]
    paths = [BASE / ".research/models/models--Qdrant--bge-small-en-v1.5-onnx-Q/snapshots" / MODEL_REVISION,
             BASE / ".research/cpu-model"]
    vectors, times = [], []
    for path in paths:
        model = TextEmbedding(MODEL, specific_model_path=str(path), threads=4, local_files_only=True)
        model.model.tokenizer.enable_truncation(max_length=128)
        start = time.perf_counter()
        vectors.append(np.asarray(list(model.embed(texts, batch_size=8))))
        times.append(time.perf_counter()-start)
    old, new = vectors
    cos = np.sum(old*new, axis=1) / (np.linalg.norm(old, axis=1)*np.linalg.norm(new, axis=1))
    result = dict(samples=len(texts), min_cosine=float(min(cos)), mean_cosine=float(np.mean(cos)),
                  upstream_batch_s=times[0], derivative_batch_s=times[1], input_cap=128, finite=bool(np.isfinite(new).all()))
    (BASE / "results/cpu-model-validation.json").write_text(json.dumps(result, indent=2) + "\n")
    if not result["finite"] or result["min_cosine"] < .999:
        raise RuntimeError("CPU model sanity check failed")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
