"""Promote float16 ONNX operations to float32 for a CPU-only pilot.

Keep the downloaded artifact intact; save a separate hashed derivative. Integer
quantized tensors/operators remain unchanged. This is runtime adaptation, not training.
"""
import json
from pathlib import Path
import shutil
from collections import Counter

import numpy as np
import onnx
from onnx import TensorProto, numpy_helper

BASE = Path(__file__).resolve().parents[1]


def promote_tensor(tensor):
    if tensor.data_type == TensorProto.FLOAT16:
        tensor.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(tensor).astype(np.float32), name=tensor.name))


def promote_graph(graph):
    for tensor in graph.initializer:
        promote_tensor(tensor)
    for info in list(graph.input) + list(graph.output) + list(graph.value_info):
        if info.type.HasField("tensor_type") and info.type.tensor_type.elem_type == TensorProto.FLOAT16:
            info.type.tensor_type.elem_type = TensorProto.FLOAT
    for node in graph.node:
        for attr in node.attribute:
            if node.op_type == "Cast" and attr.name == "to" and attr.i == TensorProto.FLOAT16:
                attr.i = TensorProto.FLOAT
            if attr.type == onnx.AttributeProto.TENSOR:
                promote_tensor(attr.t)
            elif attr.type == onnx.AttributeProto.GRAPH:
                promote_graph(attr.g)


def prepare():
    from prepare_models import MODEL_REVISION
    source = BASE / ".research/models/models--Qdrant--bge-small-en-v1.5-onnx-Q/snapshots" / MODEL_REVISION
    dest = BASE / ".research/cpu-model"
    dest.mkdir(parents=True, exist_ok=True)
    for path in source.iterdir():
        if path.is_file() and path.suffix != ".onnx":
            shutil.copy2(path, dest / path.name)
    model = onnx.load(str(source / "model_optimized.onnx"))
    before = Counter(onnx.TensorProto.DataType.Name(t.data_type) for t in model.graph.initializer)
    promote_graph(model.graph)
    # The upstream ORT-optimized graph contains LayerNormalization at opset 11,
    # which the generic ONNX checker rejects even before conversion. Validate
    # the derivative with the same ORT runtime that executes the upstream graph.
    onnx.save(model, str(dest / "model_optimized.onnx"))
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = 4
    onnxruntime.InferenceSession(str(dest / "model_optimized.onnx"), sess_options=options, providers=["CPUExecutionProvider"])
    after = Counter(onnx.TensorProto.DataType.Name(t.data_type) for t in model.graph.initializer)
    print(json.dumps(dict(before=before, after=after, output=str(dest)), indent=2))


if __name__ == "__main__":
    prepare()
