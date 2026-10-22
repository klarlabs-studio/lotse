//! `graf mcp`: a Model Context Protocol server over stdio (newline-delimited JSON-RPC 2.0).
//!
//! The server keeps the index and the function index in memory between calls. It builds
//! the index when none exists, reloads it when `graf index` rewrites it, and when a
//! call fails because sources changed since indexing, it updates the index and retries
//! once, so an agent editing the repository never has to reindex by hand.
use crate::index::index_path;
use crate::triage::{IndexedUnits, indexed_units, outline, ranked_from, triage_with};
use crate::{Index, canonical_root};
use anyhow::{Context, Result, bail};
use serde_json::{Value, json};
use std::fs;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::time::SystemTime;
use tiktoken_rs::CoreBPE;

const PROTOCOL_VERSION: &str = "2025-06-18";
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
        json!({"type": "integer", "minimum": 1, "default": default,
               "description": "Maximum cl100k tokens of returned context"})
    };
    json!([
        {"name": "triage",
         "description": "Start here. Given an issue or task description, list the likely files and \
                         show the likely functions in full, within the budget.",
         "inputSchema": {"type": "object", "required": ["issue"], "properties": {
             "issue": text("issue", "the task, bug report, or question, verbatim"),
             "budget": budget(1500)}}},
        {"name": "outline",
         "description": "List a source file's functions with line ranges and signatures, \
                         so you can read one function instead of the whole file.",
         "inputSchema": {"type": "object", "required": ["path"], "properties": {
             "path": text("path", "repository-relative file path, as triage lists it")}}},
        {"name": "units",
         "description": "Whole functions ranked for a follow-up query, within the budget.",
         "inputSchema": {"type": "object", "required": ["query"], "properties": {
             "query": text("query", "identifiers or words to look for"),
             "budget": budget(2000)}}},
        {"name": "locate",
         "description": "One source anchor per matching file (path, line, preview, id), within the budget.",
         "inputSchema": {"type": "object", "required": ["query"], "properties": {
             "query": text("query", "identifiers or words to look for"),
             "budget": budget(1000)}}},
        {"name": "search",
         "description": "Ranked source excerpts (code, docs, tests) with citations, within the budget.",
         "inputSchema": {"type": "object", "required": ["query"], "properties": {
             "query": text("query", "identifiers or words to look for"),
             "budget": budget(2000)}}},
        {"name": "expand",
         "description": "Return an excerpt by the id that locate or search printed, widened by lines.",
         "inputSchema": {"type": "object", "required": ["id"], "properties": {
             "id": text("id", "excerpt id or a unique prefix of at least 12 characters"),
             "before": {"type": "integer", "minimum": 0, "default": 0},
             "after": {"type": "integer", "minimum": 0, "default": 0},
             "budget": budget(2000)}}},
        {"name": "index",
         "description": "Update the index now (normally automatic). rebuild=true starts from scratch.",
         "inputSchema": {"type": "object", "properties": {
             "rebuild": {"type": "boolean", "default": false}}}}
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
            .map(|n| n as usize)
            .with_context(|| format!("`{key}` must be a non-negative integer")),
    }
}

impl Server {
    pub fn new(root: &Path) -> Result<Self> {
        Ok(Self {
            root: canonical_root(root)?,
            bpe: tiktoken_rs::cl100k_base()?,
            loaded: None,
        })
    }

    /// The loaded index, built if missing and reloaded if rewritten since it was loaded.
    fn index(&mut self) -> Result<&mut Loaded> {
        let current = stamp(&self.root);
        if current.is_none() {
            Index::build(&self.root, false)?;
        }
        let current = stamp(&self.root);
        if self.loaded.as_ref().is_none_or(|l| l.stamp != current) {
            self.loaded = Some(Loaded {
                index: Index::load(&self.root)?,
                units: None,
                stamp: current,
            });
        }
        Ok(self.loaded.as_mut().expect("loaded above"))
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
            "outline" => Ok(outline(index, &string(args, "path")?)?.context),
            "locate" => {
                let query = string(args, "query")?;
                Ok(index
                    .locate(&query, number(args, "budget", 1000)?, 1000, None, None)?
                    .context)
            }
            "search" => {
                let query = string(args, "query")?;
                Ok(index
                    .search(&query, number(args, "budget", 2000)?, 1000, None, None)?
                    .context)
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
        let id = message.get("id").cloned()?;
        let method = message.get("method").and_then(Value::as_str).unwrap_or("");
        let params = message.get("params").cloned().unwrap_or(json!({}));
        let result = match method {
            "initialize" => json!({
                "protocolVersion": params.get("protocolVersion").and_then(Value::as_str)
                    .unwrap_or(PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "graf", "version": env!("CARGO_PKG_VERSION")},
                "instructions": INSTRUCTIONS,
            }),
            "ping" => json!({}),
            "tools/list" => json!({"tools": tools()}),
            "tools/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or("");
                let args = params.get("arguments").cloned().unwrap_or(json!({}));
                match self.call(name, &args) {
                    Ok(text) => {
                        let text = if text.is_empty() {
                            "No matches.".to_string()
                        } else {
                            text
                        };
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

/// Serve requests from `input` until it closes, one JSON message per line.
pub fn serve(root: &Path, input: impl BufRead, mut output: impl Write) -> Result<()> {
    let mut server = Server::new(root)?;
    for line in input.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Value>(&line) {
            Ok(message) => server.handle(&message),
            Err(error) => Some(json!({"jsonrpc": "2.0", "id": null,
                                      "error": {"code": -32700, "message": format!("Parse error: {error}")}})),
        };
        if let Some(response) = response {
            writeln!(output, "{response}")?;
            output.flush()?;
        }
    }
    Ok(())
}
