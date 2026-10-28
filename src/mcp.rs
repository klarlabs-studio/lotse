//! `graf mcp`: a Model Context Protocol server over stdio (newline-delimited JSON-RPC 2.0).
//!
//! The server keeps the index and the function index in memory between calls. It builds
//! the index when none exists, reloads it when `graf index` rewrites it, and when a
//! checks the complete eligible source set before each tool call. A stale-source
//! error during delivery triggers one additional refresh and retry.
use crate::index::index_path;
use crate::triage::{IndexedUnits, indexed_units, outline_budgeted, ranked_from, triage_with};
use crate::{Index, canonical_root};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::fs;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use tiktoken_rs::CoreBPE;

const PROTOCOL_VERSION: &str = "2025-06-18";
const MAX_FRAME_BYTES: usize = 1024 * 1024;
const MAX_STRING_BYTES: usize = 64_000;
const MAX_CONTEXT_TOKENS: usize = 32_000;
const MAX_EXPANSION_LINES: usize = 10_000;
const MAX_SAFE_INTEGER: u64 = 9_007_199_254_740_991;
type ToolArgSpec = (
    &'static [&'static str],
    &'static [(&'static str, u64, u64)],
    &'static [&'static str],
);
const INSTRUCTIONS: &str = "graf finds where to work in this repository without reading it all. \
Start every task with `triage` on the issue or task text: it returns likely files, then the \
likely functions in full, within a token budget. Use `outline` to see a file's functions \
with line ranges before reading it, then read only the lines you need. Use `units` for a \
follow-up question, `locate` for one source anchor per file, and `expand` to widen an \
excerpt by its id. The index lives in .graf/ and updates itself when files change.";

/// The tool catalog: name, description, and JSON Schema of the arguments.
pub fn tools() -> Value {
    let text = |name: &str, about: &str| json!({"type": "string", "description": format!("{name}: {about}")});
    let budget = |default: usize| {
        json!({"type": "integer", "minimum": 1, "maximum": MAX_CONTEXT_TOKENS, "default": default,
               "description": "Maximum cl100k tokens of returned context"})
    };
    json!([
        {"name": "triage",
         "description": "Start here. Given an issue or task description, list the likely files and \
                         show the likely functions in full, within the budget.",
         "inputSchema": {"type": "object", "required": ["issue"], "properties": {
             "issue": text("issue", "the task, bug report, or question, verbatim"),
             "budget": budget(1500)}, "additionalProperties": false}},
        {"name": "outline",
         "description": "List a source file's functions with line ranges and signatures, \
                         for Go, Python, JavaScript, TypeScript, and Rust, so you can read \
                         one function instead of the whole file. Use offset from next_offset \
                         to request another page.",
         "inputSchema": {"type": "object", "required": ["path"], "properties": {
             "path": text("path", "repository-relative file path, as triage lists it"),
             "budget": {"type": "integer", "minimum": 64, "maximum": MAX_CONTEXT_TOKENS, "default": 1500,
                        "description": "Maximum cl100k tokens of returned outline context"},
             "offset": {"type": "integer", "minimum": 0, "maximum": MAX_SAFE_INTEGER, "default": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}}, "additionalProperties": false}},
        {"name": "units",
         "description": "Whole functions ranked for a follow-up query, within the budget.",
         "inputSchema": {"type": "object", "required": ["query"], "properties": {
             "query": text("query", "identifiers or words to look for"),
             "budget": budget(2000)}, "additionalProperties": false}},
        {"name": "locate",
         "description": "One source anchor per matching file (path, line, preview, id), within the budget.",
         "inputSchema": {"type": "object", "required": ["query"], "properties": {
             "query": text("query", "identifiers or words to look for"),
             "budget": budget(1000)}, "additionalProperties": false}},
        {"name": "search",
         "description": "Ranked source excerpts (code, docs, tests) with citations, within the budget.",
         "inputSchema": {"type": "object", "required": ["query"], "properties": {
             "query": text("query", "identifiers or words to look for"),
             "budget": budget(2000)}, "additionalProperties": false}},
        {"name": "expand",
         "description": "Return an excerpt by the id that locate or search printed, widened by lines.",
         "inputSchema": {"type": "object", "required": ["id"], "properties": {
             "id": text("id", "excerpt id or a unique prefix of at least 12 characters"),
             "before": {"type": "integer", "minimum": 0, "maximum": MAX_EXPANSION_LINES, "default": 0},
             "after": {"type": "integer", "minimum": 0, "maximum": MAX_EXPANSION_LINES, "default": 0},
             "budget": budget(2000)}, "additionalProperties": false}},
        {"name": "index",
         "description": "Update the index now (normally automatic). rebuild=true starts from scratch.",
         "inputSchema": {"type": "object", "properties": {
             "rebuild": {"type": "boolean", "default": false}}, "additionalProperties": false}}
    ])
}

struct Loaded {
    index: Index,
    units: Option<IndexedUnits>,
    stamp: Option<(SystemTime, u64)>,
}

pub struct Server {
    root: PathBuf,
    bpe: CoreBPE,
    loaded: Option<Loaded>,
}

fn stamp(root: &Path) -> Option<(SystemTime, u64)> {
    let meta = fs::metadata(index_path(root)).ok()?;
    Some((meta.modified().ok()?, meta.len()))
}

fn needs_reindex(error: &anyhow::Error) -> bool {
    let message = format!("{error:#}");
    message.contains("run `graf index`") || message.contains("search again after indexing")
}

fn string(args: &Value, key: &str) -> Result<String> {
    args.get(key)
        .and_then(Value::as_str)
        .map(str::to_string)
        .with_context(|| format!("missing string argument `{key}`"))
}

fn number(args: &Value, key: &str, default: usize) -> Result<usize> {
    match args.get(key) {
        None | Some(Value::Null) => Ok(default),
        Some(value) => value
            .as_u64()
            .filter(|n| *n <= MAX_SAFE_INTEGER)
            .and_then(|n| usize::try_from(n).ok())
            .with_context(|| format!("`{key}` must be a non-negative integer")),
    }
}

fn validate_tool_args(name: &str, args: &Value) -> Result<()> {
    let obj = args
        .as_object()
        .context("tool arguments must be an object")?;
    let (strings, numbers, booleans): ToolArgSpec = match name {
        "triage" => (&["issue"], &[("budget", 1, MAX_CONTEXT_TOKENS as u64)], &[]),
        "units" | "search" => (&["query"], &[("budget", 1, MAX_CONTEXT_TOKENS as u64)], &[]),
        "locate" => (&["query"], &[("budget", 1, MAX_CONTEXT_TOKENS as u64)], &[]),
        "outline" => (
            &["path"],
            &[
                ("budget", 64, MAX_CONTEXT_TOKENS as u64),
                ("offset", 0, MAX_SAFE_INTEGER),
                ("limit", 1, 1000),
            ],
            &[],
        ),
        "expand" => (
            &["id"],
            &[
                ("before", 0, MAX_EXPANSION_LINES as u64),
                ("after", 0, MAX_EXPANSION_LINES as u64),
                ("budget", 1, MAX_CONTEXT_TOKENS as u64),
            ],
            &[],
        ),
        "index" => (&[], &[], &["rebuild"]),
        other => bail!("unknown tool `{other}`"),
    };
    let allowed: Vec<&str> = strings
        .iter()
        .copied()
        .chain(numbers.iter().map(|x| x.0))
        .chain(booleans.iter().copied())
        .collect();
    for key in obj.keys() {
        if !allowed.contains(&key.as_str()) {
            bail!("unknown argument `{key}`");
        }
    }
    for key in strings {
        let value = obj
            .get(*key)
            .and_then(Value::as_str)
            .with_context(|| format!("`{key}` must be a string"))?;
        if value.is_empty() {
            bail!("`{key}` must not be empty");
        }
        if value.len() > MAX_STRING_BYTES {
            bail!("`{key}` exceeds {MAX_STRING_BYTES} UTF-8 bytes");
        }
    }
    for (key, min, max) in numbers {
        if let Some(value) = obj.get(*key) {
            let n = value
                .as_u64()
                .filter(|n| *n <= MAX_SAFE_INTEGER)
                .context(format!("`{key}` must be a non-negative safe integer"))?;
            let n = usize::try_from(n).context(format!("`{key}` is out of range"))?;
            if (n as u64) < *min || (n as u64) > *max {
                bail!("`{key}` must be between {min} and {max}");
            }
        }
    }
    for key in booleans {
        if let Some(value) = obj.get(*key)
            && !value.is_boolean()
        {
            bail!("`{key}` must be a boolean");
        }
    }
    Ok(())
}

impl Server {
    pub fn new(root: &Path) -> Result<Self> {
        Ok(Self {
            root: canonical_root(root)?,
            bpe: tiktoken_rs::cl100k_base()?,
            loaded: None,
        })
    }

    /// Load external index changes, then check source hashes and file membership.
    fn index(&mut self) -> Result<&mut Loaded> {
        let current = stamp(&self.root);
        if self.loaded.as_ref().is_none_or(|l| l.stamp != current) {
            self.loaded = Some(Loaded {
                index: Index::load_or_rebuild_for_mcp(&self.root)?,
                units: None,
                stamp: stamp(&self.root),
            });
        }
        let loaded = self.loaded.as_mut().expect("loaded above");
        if loaded.index.refresh()? {
            loaded.units = None;
            loaded.stamp = stamp(&self.root);
        }
        Ok(loaded)
    }

    fn reindex(&mut self, rebuild: bool) -> Result<String> {
        let report = Index::build(&self.root, rebuild)?;
        self.loaded = None;
        let loaded = self.index()?;
        loaded.units = Some(indexed_units(&loaded.index)?);
        Ok(serde_json::to_string(&report)?)
    }

    /// Run one tool; on a stale-index error, update the index and run it once more.
    pub fn call(&mut self, name: &str, args: &Value) -> Result<String> {
        validate_tool_args(name, args)?;
        match self.run(name, args) {
            Err(error) if name != "index" && needs_reindex(&error) => {
                self.reindex(false)?;
                self.run(name, args)
            }
            result => result,
        }
    }

    fn run(&mut self, name: &str, args: &Value) -> Result<String> {
        if name == "index" {
            let rebuild = args
                .get("rebuild")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            return self.reindex(rebuild);
        }
        let bpe = self.bpe.clone();
        let loaded = self.index()?;
        let index = &loaded.index;
        match name {
            "triage" | "units" => {
                if loaded.units.is_none() {
                    loaded.units = Some(indexed_units(index)?);
                }
                let units = loaded.units.as_ref().expect("loaded above");
                if name == "triage" {
                    let issue = string(args, "issue")?;
                    let result =
                        triage_with(&bpe, index, units, &issue, number(args, "budget", 1500)?)?;
                    Ok(result.context)
                } else {
                    let query = string(args, "query")?;
                    Ok(ranked_from(&bpe, index, units, &query, number(args, "budget", 2000)?)?.1)
                }
            }
            "outline" => Ok(outline_budgeted(
                &bpe,
                index,
                &string(args, "path")?,
                number(args, "budget", 1500)?,
                number(args, "offset", 0)?,
                number(args, "limit", 100)?,
            )?
            .context),
            "locate" => {
                let query = string(args, "query")?;
                Ok(index
                    .locate(&query, number(args, "budget", 1000)?, 1000, None, None)?
                    .context)
            }
            "search" => {
                let query = string(args, "query")?;
                index.search_for_agent(&query, number(args, "budget", 2000)?)
            }
            "expand" => {
                let id = string(args, "id")?;
                let response = index.expand(
                    &id,
                    number(args, "before", 0)?,
                    number(args, "after", 0)?,
                    number(args, "budget", 2000)?,
                )?;
                Ok(response.context)
            }
            other => bail!("unknown tool `{other}`"),
        }
    }

    /// The response to one JSON-RPC message, or `None` for a notification.
    pub fn handle(&mut self, message: &Value) -> Option<Value> {
        let Some(object) = message.as_object() else {
            return Some(rpc_error(Value::Null, -32600, "Invalid Request"));
        };
        let id = object.get("id");
        let notification = id.is_none();
        let valid_id = id.is_none_or(|value| {
            value.is_null()
                || value.is_string()
                || value.as_i64().is_some()
                || value.as_u64().is_some()
        });
        let response_id = if valid_id {
            id.cloned().unwrap_or(Value::Null)
        } else {
            Value::Null
        };
        let method = object.get("method").and_then(Value::as_str);
        if object.get("jsonrpc").and_then(Value::as_str) != Some("2.0")
            || !valid_id
            || method.is_none()
        {
            return Some(rpc_error(response_id, -32600, "Invalid Request"));
        }
        let method = method.unwrap();
        let params = match object.get("params") {
            None => json!({}),
            Some(Value::Object(_)) => object.get("params").unwrap().clone(),
            Some(_) => {
                return Some(rpc_error(
                    response_id,
                    -32600,
                    "Invalid Request: params must be an object",
                ));
            }
        };
        if notification {
            return None;
        }
        let id = response_id;
        let result = match method {
            "initialize" => json!({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "graf", "version": env!("CARGO_PKG_VERSION")},
                "instructions": INSTRUCTIONS,
            }),
            "ping" => json!({}),
            "tools/list" => json!({"tools": tools()}),
            "tools/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or("");
                let args = params.get("arguments").cloned().unwrap_or(json!({}));
                if params.get("name").and_then(Value::as_str).is_none() || !args.is_object() {
                    return Some(rpc_error(
                        id,
                        -32602,
                        "Invalid params: tools/call requires a string name and object arguments",
                    ));
                }
                match self.call(name, &args) {
                    Ok(text) => {
                        json!({"content": [{"type": "text", "text": text}], "isError": false})
                    }
                    Err(error) => {
                        json!({"content": [{"type": "text", "text": format!("{error:#}")}],
                                         "isError": true})
                    }
                }
            }
            _ => {
                return Some(json!({"jsonrpc": "2.0", "id": id,
                                   "error": {"code": -32601, "message": format!("Method not found: {method}")}}));
            }
        };
        Some(json!({"jsonrpc": "2.0", "id": id, "result": result}))
    }
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
}

/// Serve requests from `input` until it closes, one JSON message per line.
pub fn serve(root: &Path, input: impl BufRead, mut output: impl Write) -> Result<()> {
    let mut server = Server::new(root)?;
    let mut input = input;
    loop {
        let mut frame = Vec::with_capacity(4096);
        let mut oversized = false;
        let mut saw_bytes = false;
        loop {
            let available = input.fill_buf()?;
            if available.is_empty() {
                break;
            }
            let newline = available.iter().position(|b| *b == b'\n');
            let count = newline.map_or(available.len(), |i| i + 1);
            saw_bytes = true;
            if !oversized {
                let payload_len = count - usize::from(newline.is_some());
                if frame.len().saturating_add(payload_len) > MAX_FRAME_BYTES {
                    oversized = true;
                    frame.clear();
                } else {
                    frame.extend_from_slice(&available[..payload_len]);
                }
            }
            input.consume(count);
            if newline.is_some() {
                break;
            }
        }
        if !saw_bytes {
            break;
        }
        let response = if oversized {
            Some(rpc_error(
                Value::Null,
                -32700,
                "Parse error: frame exceeds 1048576 bytes",
            ))
        } else if frame.iter().all(u8::is_ascii_whitespace) {
            None
        } else {
            match std::str::from_utf8(&frame) {
                Err(error) => Some(rpc_error(
                    Value::Null,
                    -32700,
                    &format!("Parse error: invalid UTF-8: {error}"),
                )),
                Ok(line) => match serde_json::from_str::<Value>(line) {
                    Ok(message) => server.handle(&message),
                    Err(error) => Some(rpc_error(
                        Value::Null,
                        -32700,
                        &format!("Parse error: {error}"),
                    )),
                },
            }
        };
        if let Some(response) = response {
            writeln!(output, "{response}")?;
            output.flush()?;
        }
        if !saw_bytes || !frame.ends_with(b"\n") && input.fill_buf()?.is_empty() {
            break;
        }
    }
    Ok(())
}
