# Frozen retrieval backends

This directory holds the exact source files an agent batch's Python backend ran with
when the batch started, so later edits to the benchmark code cannot change the
backend (or its preflight hash) mid-batch.

- This directory: the `ast_graph` backend of batch t4 (`ast_retrieve.py`, `localization.py`,
  `retrieval.py` from commit d18957e; preflight hash `fc85e659…`). Do not edit.
