use graf::mcp::serve;
use graf::{Analyzer, Index, Posting, Stopwords, index_path};
use serde_json::Value;
use std::fs;
use std::io::Cursor;

fn run(input: Vec<u8>, root: &std::path::Path) -> Vec<Value> {
    let mut output = Vec::new();
    serve(root, Cursor::new(input), &mut output).unwrap();
    String::from_utf8(output)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

#[test]
fn oversized_frame_is_drained_and_next_request_is_served() {
    let dir = tempfile::tempdir().unwrap();
    let mut input = vec![b' '; 1_048_577];
    input.extend_from_slice(b"\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"ping\"}\n");
    let output = run(input, dir.path());
    assert_eq!(output.len(), 2);
    assert_eq!(output[0]["error"]["code"], -32700);
    assert_eq!(output[1]["id"], 2);
    assert_eq!(output[1]["result"], serde_json::json!({}));
}

#[test]
fn malformed_and_invalid_utf8_frames_do_not_stop_later_requests() {
    let dir = tempfile::tempdir().unwrap();
    let mut input = b"{bad json}\n".to_vec();
    input.extend_from_slice(b"\xff\n{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"ping\"}\n");
    let output = run(input, dir.path());
    assert_eq!(output.len(), 3);
    assert_eq!(output[0]["error"]["code"], -32700);
    assert_eq!(output[1]["error"]["code"], -32700);
    assert_eq!(output[2]["id"], 3);
}

#[test]
fn invalid_tool_arguments_are_rejected_before_indexing_and_normal_requests_continue() {
    let dir = tempfile::tempdir().unwrap();
    let input = concat!(
        "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"search\",\"arguments\":{\"query\":\"x\",\"budget\":32001}}}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"index\",\"arguments\":{\"rebuild\":\"yes\"}}}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/list\"}\n",
        "{\"jsonrpc\":\"2.0\",\"id\":4,\"method\":\"ping\"}\n",
        "{\"jsonrpc\":\"2.0\",\"method\":\"tools/list\"}\n"
    );
    let output = run(input.as_bytes().to_vec(), dir.path());
    assert_eq!(output.len(), 4); // valid notification has no response
    assert_eq!(output[0]["result"]["isError"], true);
    assert_eq!(output[1]["result"]["isError"], true);
    assert!(!dir.path().join(".graf").exists());
    assert_eq!(output[2]["result"]["tools"].as_array().unwrap().len(), 7);
    assert_eq!(output[3]["result"], serde_json::json!({}));
}

#[test]
fn invalid_rpc_envelope_returns_error_then_continues() {
    let dir = tempfile::tempdir().unwrap();
    let input = b"{\"jsonrpc\":\"1.0\",\"id\":1,\"method\":\"ping\"}\n{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"ping\",\"params\":[]}\n{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"ping\"}\n";
    let output = run(input.to_vec(), dir.path());
    assert_eq!(output.len(), 3);
    assert_eq!(output[0]["error"]["code"], -32600);
    assert_eq!(output[1]["error"]["code"], -32600);
    assert_eq!(output[2]["result"], serde_json::json!({}));
}

#[test]
fn mcp_recovers_corrupt_snapshots_before_and_between_calls_preserving_analyzer() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("src")).unwrap();
    fs::write(
        dir.path().join("src/sample.js"),
        "function first() { return 1; }\nfunction second() { return 2; }\n",
    )
    .unwrap();
    fs::create_dir_all(dir.path().join(".graf")).unwrap();
    let root = dir.path().canonicalize().unwrap();
    fs::write(index_path(&root), b"broken before first MCP call").unwrap();

    let mut server = graf::mcp::Server::new(dir.path()).unwrap();
    let first = server
        .call("outline", &serde_json::json!({"path":"src/sample.js"}))
        .unwrap();
    assert!(first.contains("first"));
    assert!(Index::load(&root).is_ok());

    let analyzer = Analyzer {
        stopwords: Stopwords::English,
        full_identifiers: true,
        path_weight: 3,
    };
    Index::build_with(&root, true, analyzer).unwrap();
    let path = index_path(&root);
    let mut index: Index = bincode::deserialize(&fs::read(&path).unwrap()).unwrap();
    index.postings.insert(
        "deliberately invalid term".into(),
        vec![Posting { chunk: 0, count: 1 }],
    );
    fs::write(&path, bincode::serialize(&index).unwrap()).unwrap();

    let second = server
        .call("outline", &serde_json::json!({"path":"src/sample.js"}))
        .unwrap();
    assert!(second.contains("second"));
    assert_eq!(Index::load(&root).unwrap().analyzer, analyzer);
}

#[test]
fn outline_pages_are_budgeted_and_protocol_negotiation_is_fixed() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("src")).unwrap();
    fs::write(
        dir.path().join("src/sample.ts"),
        "function alpha() { return 1; }\nfunction beta() { return 2; }\nfunction gamma() { return 3; }\n",
    )
    .unwrap();
    let mut server = graf::mcp::Server::new(dir.path()).unwrap();
    let first = server
        .call(
            "outline",
            &serde_json::json!({"path":"src/sample.ts", "budget":100, "offset":0, "limit":1}),
        )
        .unwrap();
    assert!(first.contains("next_offset=1"), "{first}");
    let bpe = tiktoken_rs::cl100k_base().unwrap();
    assert!(bpe.encode_ordinary(&first).len() <= 100);
    let second = server
        .call(
            "outline",
            &serde_json::json!({"path":"src/sample.ts", "budget":100, "offset":1, "limit":1}),
        )
        .unwrap();
    assert!(second.contains("beta"), "{second}");

    let init = server
        .handle(&serde_json::json!({
            "jsonrpc":"2.0", "id":1, "method":"initialize",
            "params":{"protocolVersion":"arbitrary-version"}
        }))
        .unwrap();
    assert_eq!(init["result"]["protocolVersion"], "2025-06-18");
    let invalid_id = server
        .handle(&serde_json::json!({"jsonrpc":"2.0", "id":true, "method":"ping"}))
        .unwrap();
    assert_eq!(invalid_id["id"], Value::Null);
}

#[test]
fn rpc_success_text_obeys_context_budget_including_empty_and_tiny_results() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("src")).unwrap();
    fs::write(
        dir.path().join("src/example.js"),
        "function unique_graf_resilience_marker() { return 'useful result'; }\n",
    )
    .unwrap();
    let mut server = graf::mcp::Server::new(dir.path()).unwrap();
    let bpe = tiktoken_rs::cl100k_base().unwrap();

    let no_match = server
        .handle(&serde_json::json!({
            "jsonrpc":"2.0", "id":1, "method":"tools/call",
            "params":{"name":"search", "arguments":{"query":"never_present_zebra_term", "budget":1}}
        }))
        .unwrap();
    let no_match_result = &no_match["result"];
    assert_eq!(no_match_result["isError"], false);
    let no_match_text = no_match_result["content"][0]["text"].as_str().unwrap();
    assert_eq!(no_match_text, "");
    assert!(bpe.encode_ordinary(no_match_text).len() <= 1);

    let tiny = server
        .handle(&serde_json::json!({
            "jsonrpc":"2.0", "id":2, "method":"tools/call",
            "params":{"name":"search", "arguments":{"query":"unique_graf_resilience_marker", "budget":1}}
        }))
        .unwrap();
    let tiny_result = &tiny["result"];
    assert_eq!(tiny_result["isError"], false);
    let tiny_text = tiny_result["content"][0]["text"].as_str().unwrap();
    assert!(bpe.encode_ordinary(tiny_text).len() <= 1, "{tiny_text:?}");

    let normal = server
        .handle(&serde_json::json!({
            "jsonrpc":"2.0", "id":3, "method":"tools/call",
            "params":{"name":"search", "arguments":{"query":"unique_graf_resilience_marker", "budget":300}}
        }))
        .unwrap();
    let normal_result = &normal["result"];
    assert_eq!(normal_result["isError"], false);
    let normal_text = normal_result["content"][0]["text"].as_str().unwrap();
    assert!(!normal_text.is_empty());
    assert!(bpe.encode_ordinary(normal_text).len() <= 300);

    let invalid = server
        .handle(&serde_json::json!({
            "jsonrpc":"2.0", "id":4, "method":"tools/call",
            "params":{"name":"search", "arguments":{"query":"marker", "budget":0}}
        }))
        .unwrap();
    assert_eq!(invalid["result"]["isError"], true);
    assert!(
        invalid["result"]["content"][0]["text"]
            .as_str()
            .unwrap()
            .contains("budget")
    );
}
