"""Download local-only embedding and tokenizer artifacts once."""
import hashlib
import json
import os
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
os.environ["TIKTOKEN_CACHE_DIR"] = str(BASE / ".research/tokenizer")
os.environ["HF_HOME"] = str(BASE / ".research/huggingface")
os.environ["HF_HUB_DISABLE_XET"] = "1"

import tiktoken
from fastembed import TextEmbedding

MODEL = "BAAI/bge-small-en-v1.5"
MODEL_REPOSITORY = "Qdrant/bge-small-en-v1.5-onnx-Q"
MODEL_REVISION = "aa8f8b060edb00e03bfdd08813a2949946c8ba55"


def main():
    tiktoken.get_encoding("cl100k_base").encode("graf research")
    from huggingface_hub import snapshot_download
    model_path = snapshot_download(repo_id=MODEL_REPOSITORY, revision=MODEL_REVISION,
                                   cache_dir=str(BASE / ".research/models"),
                                   allow_patterns=["config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "model_optimized.onnx"])
    model = TextEmbedding(model_name=MODEL, specific_model_path=model_path, threads=2)
    list(model.embed(["repository context retrieval"]))
    from cpu_model import prepare
    prepare()
    record_artifacts()


def record_artifacts():
    artifacts = []
    for directory in ["models", "tokenizer", "cpu-model"]:
        for path in sorted((BASE / ".research" / directory).rglob("*")):
            if path.is_file() and ".lock" not in path.name:
                artifacts.append(dict(path=str(path.relative_to(BASE / ".research")),
                                      sha256=hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
                                      bytes=path.stat().st_size))
    (BASE / "results/model-artifacts.json").write_text(json.dumps(dict(model=MODEL, artifacts=artifacts), indent=2) + "\n")


if __name__ == "__main__":
    main()
