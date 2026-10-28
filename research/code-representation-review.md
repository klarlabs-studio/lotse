# Code representation and retrieval for localization and coding agents: a literature review

**Date:** 2026-10-20
**Scope:** How to represent and retrieve source code for (a) code analytics and understanding and (b) LLM agents doing repository-level work (fault localization, issue resolution, code QA). The review focuses on efficiency (tokens, turns, cost) as well as accuracy.
**Method:** Web search and primary-source checks (arXiv abstract or HTML pages, proceedings pages, and the PDFs of SWE-agent and SWE-bench). Each entry gives title, authors, year, venue or arXiv ID, and URL. "PR" marks peer-reviewed work and "preprint" marks arXiv-only work. A venue that only secondary pages confirm is marked *(venue unverified)*. Numbers are the authors' own unless noted. Several papers report different numbers in different arXiv versions; those differences are flagged.

---

## 1. Executive summary

1. **Lexical retrieval is a real baseline, but a weak one at function level when it stands alone.** On SWE-bench Lite, standalone BM25 reaches 61.7% file Acc@5 and 31.8% function Acc@5. A 137M code embedder reaches 84.7% and 51.8% on the same metrics, and agents or retrieve-and-rerank systems reach 90–96% at file level (LocAgent and SweRank tables). Inside agents, a BM25 index still matters: removing it from LocAgent cuts file Acc@5 from 88.3% to 75.2%. On RepoEval-style completion, BM25 is as good as most dense models (CodeRAG-Bench). Classic IR bug-localization work (BLUiR, BRTracer, BLIZZARD) improves results by changing **what is indexed and how the query is built**, not the ranking function.
2. **Structural (AST) chunking gives small gains, and only when sizes are bounded.** cAST, which splits and merges AST nodes under a size limit, reports +4.3 Recall@5 on RepoEval. A 2026 controlled study of 864 settings found that **pure function-level chunking was the worst strategy** for retrieval-augmented completion: it trailed sliding windows, declaration chunks, and cAST by 3.6–5.6 points, and was never Pareto-optimal. The authors attribute this to high variance in unit size. Neither study covers issue-text fault localization.
3. **Call and dependency graphs help most at finer granularity and when the agent controls the traversal. Fixed-hop expansion can hurt.** RepoGraph adds about 2 points of resolve rate across four frameworks, with gains concentrated at file level. Its 2-hop expansion fell *below* baseline (26.0% vs 27.3%), and it raised tokens and cost in every framework. In CoSIL, removing the module call graph cost 5 points of function Top-1; removing iterative search cost 11. In LocAgent, fixing traversal to 1 hop lowered function-level accuracy more than file-level accuracy.
4. **The cheapest localizers are pipelines, not agents.** SweRank (retrieve and rerank) costs about $0.011–0.015 per instance and matches or beats Claude-3.5 agents (about $0.46–0.83) on localization. Agentless's skeleton step was both **cheaper and more accurate than passing full files** (58.3% vs 53.7% element retention, at $0.02 vs $0.15).
5. **Agent cost is driven by accumulated context, not retrieval payload. The literature agrees with our measurements.** Input tokens dominate agent cost (Bai et al. 2026). In SWE-Pruner's traces, read operations (cat, grep, head) are 67–76% of tokens. Simple observation masking halves cost at equal solve rate (Complexity Trap), and trajectory reduction cuts input tokens by 40–60% (AgentDiet). SWE-agent's ACI ablations show that **interface format** moves resolve rate more than search capability does. A badly designed search tool (12.0%) did worse than no search tool (15.7%).
6. **Evidence quality is mixed.** Most results come from SWE-bench Lite or Verified (Python only, 12 repositories) and use the authors' own baselines. Many report different numbers across arXiv versions. Contamination is documented: o3 names the buggy file path from issue text alone 76% of the time on SWE-bench Verified, versus at most 53% on other repositories. **Very few papers report matched-budget efficiency.** No peer-reviewed study we found isolates an LSP or graph tool and measures its effect on agent turns and tokens against a grep-plus-read baseline.

---

## 2. Taxonomy of code representations for retrieval

| # | Family | Unit or structure | Typical retrieval | Representative work |
|---|---|---|---|---|
| R1 | **Lexical, flat** | file, fixed line window, fixed token chunk | BM25, VSM, TF-IDF | BugLocator, Rao & Kak, SWE-bench BM25, RepoCoder windows, graf (24-line windows) |
| R2 | **Lexical, fielded or structured** | file or method with separate fields (class names, method names, identifiers, comments, path) | BM25F, structured IR, query reformulation | BLUiR, BRTracer (segments + stack traces), BLIZZARD, LLM query reformulation over BM25 |
| R3 | **Syntactic units** | AST function, class, or declaration; size-bounded AST chunks | any retriever over units | cAST, FinerBench4BL (method-level IR), SweRank and CoRNStack (function candidates) |
| R4 | **Summaries, skeletons, repo maps** | tree listing, signatures and docstrings, ranked definitions | LLM reads the summary and selects | Agentless (tree + skeleton), Aider repo map (tree-sitter + graph rank) |
| R5 | **Dependency, call, and knowledge graphs** | nodes for dir, file, class, function (or lines); edges for contain, import, invoke, inherit; optionally issues and PRs | k-hop expansion, agent traversal, graph query language, GNN-integrated LLM | RepoGraph, LocAgent, CoSIL, OrcaLoca, CodexGraph, KGCompass, CGM, CodeRAG |
| R6 | **Program-analysis graphs** | AST + CFG + PDG (code property graph); dataflow graph | graph traversals or queries | Yamaguchi et al. CPG; DraCo (dataflow) |
| R7 | **Learned dense representations** | embeddings of functions or chunks; cross-encoder or LLM rerankers | ANN + rerank | CodeBERT, UniXcoder, CodeSage, CodeRankEmbed (CoRNStack), SweRank |
| R8 | **Compiler-grade symbol indexes** | definitions and references and types via LSP or SCIP | jump-to-definition, find-references | MGD (static analysis at decode time), RepoNavigator (one jump-to-definition tool), Cody context engine, Lanser-CLI (position) |
| R9 | **Interaction-time representations (ACI)** | how results are *shown*: summarized hit lists, windowed file viewer, folded or preview entities, history collapsing | agent tool calls | SWE-agent ACI, LocAgent fold/preview/full, OpenHands, Moatless |
| R10 | **Compressed or pruned context** | line-level pruning of tool outputs, function-level plus perplexity pruning, observation masking, trajectory reduction | budgeted selection | SWE-Pruner, LongCodeZip, Complexity Trap, AgentDiet, ACON |

R1–R8 decide *what can be retrieved*. R9–R10 decide *what the agent pays for, and how often*. Our trials suggest R9–R10 dominate cost.

---

## 3. Key papers

Legend: **Eff?** means efficiency (tokens, cost, turns, latency) was measured. "y*" means it was measured but not under a matched budget or matched configuration. Evidence quality is rated **H** (peer-reviewed, multiple settings or replications, matched baselines), **M** (peer-reviewed or careful preprint on a single benchmark family, authors' own baselines), or **L** (preprint, small sample, version-inconsistent numbers, or vendor-authored).

### 3.1 IR-based bug localization (classic foundations)

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Rao & Kak, "Retrieval from software libraries for bug localization: a comparative study of generic and composite text models," MSR 2011, pp. 43–52. [dblp](https://dblp1.uni-trier.de/rec/conf/msr/RaoK11.html) | R1 (unigram, VSM, LSA, LDA, cluster models) | file-level bug localization (iBUGS, 291 bugs) | Simpler models (unigram, VSM) beat LDA and other complex models | n | M (PR; one small dataset) |
| Zhou, Zhang, Lo, "Where should the bugs be fixed? More accurate IR-based bug localization based on bug reports" (BugLocator), ICSE 2012, pp. 14–24. [SMU record](https://ink.library.smu.edu.sg/sis_research/1531) | R1, revised VSM with length prior + similar past bugs | file-level, about 3,000 bugs in 4 projects | Eclipse 3.1: buggy file in top 10 for 62.6% of bugs | n | M (PR) |
| Saha, Lease, Khurshid, Perry, "Improving bug localization using structured information retrieval" (BLUiR), ASE 2013. [PDF](https://ischool.utexas.edu/ml/papers/saha-ase13.pdf) | R2: fields for class, method, identifier, comment; report summary and description searched separately | file-level, about 3,400 bugs | Structure alone matched or beat BugLocator *without* its bug-history data. ASE Most Influential Paper 2024 | n | M (PR; implementation unreleased) |
| Wong et al., "Boosting bug-report-oriented fault localization with segmentation and stack-trace analysis" (BRTracer), ICSME 2014, pp. 181–190. [DOI](https://doi.org/10.1109/ICSME.2014.40) | R1+R2: files split into segments, each file scored by its **best segment**; stack-trace files boosted | file-level (Eclipse, AspectJ, SWT) | Significantly beats BugLocator; segmentation and stack traces are complementary | n | M (PR) |
| Lee, Kim, Bissyandé, Jung, Le Traon, "Bench4BL: reproducibility study on the performance of IR-based bug localization," ISSTA 2018. [DOI](https://doi.org/10.1145/3213846.3213856) | reproduces 6 IRBL techniques | 46 new subjects, 9,459 reports | Results are sensitive to version matching and to including test files; reproducibility problems | n | H (PR; reproducibility study) |
| Rahman & Roy, "Improving IR-based bug localization with context-aware query reformulation" (BLIZZARD), ESEC/FSE 2018. [arXiv 1808.00594](https://arxiv.org/abs/1808.00594) | R2, query side: classifies reports as noisy (stack traces), rich (code entities), or poor, and reformulates the query to match | 5,139 reports | Query reformulation improves IRBL over baselines | n | M (PR) |
| Tsumita, Hayashi, Amasaki, "Large-scale evaluation of method-level bug localization with FinerBench4BL," arXiv 2302.14293 (2023) *(venue unverified)*. [arXiv](https://arxiv.org/abs/2302.14293) | R3: method-level IR | Bench4BL converted to methods | Method-level techniques **lower accuracy** but **improve debugging efficiency** relative to file level | y* (inspection effort) | M |
| Niu, Li, Liu, Xia, Lo, "When deep learning meets IR-based bug localization: a survey," arXiv 2505.00144 (2025); listed in ACM CSUR *(venue per institutional repository)*. [arXiv](https://arxiv.org/abs/2505.00144) | survey of 61 DL-IRBL studies | — | DL helps with lexical gaps and code structure; method-level and cross-language remain open | n | survey |
| Chakraborty, Alfadel, Nagappan, "BLAZE: cross-language and cross-project bug localization via dynamic chunking and hard example learning," arXiv 2407.17631 *(venue unverified)*. [arXiv](https://arxiv.org/abs/2407.17631) | R3/R7: dynamic chunking + fine-tuned GPT-style embeddings | BeetleBox (29 projects, 5 languages), SWE-bench | Up to +120% Top-1 vs 6 baselines (relative); dataset size differs between v1 and v3 | n | L–M |
| Caumartin & Melo, "Reformulate, retrieve, localize: agents for repository-level bug localization," arXiv 2512.07022, BoatSE 2026 (workshop). [arXiv](https://arxiv.org/abs/2512.07022) | R2: an open LLM extracts identifiers and snippets from the report, then BM25 | file-level | Best reformulation: 35% better first-file ranking than raw-query BM25; up to +22% file retrieval vs SWE-agent | not stated | L (workshop) |

### 3.2 Code retrieval benchmarks and learned representations

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Husain et al., "CodeSearchNet challenge," arXiv 1909.09436 (2019). [arXiv](https://arxiv.org/abs/1909.09436) | function-level corpus (6M functions, 6 languages) | NL→code search, 99 queries | Established function-as-unit retrieval benchmark | n | M (preprint, widely used) |
| Feng et al., "CodeBERT," Findings of EMNLP 2020. [ACL](https://aclanthology.org/2020.findings-emnlp.139) / [arXiv 2002.08155](https://arxiv.org/abs/2002.08155) | R7 bimodal encoder | code search, documentation | Best reported results on code search at the time | n | M (PR) |
| Guo et al., "UniXcoder," ACL 2022. [ACL](https://aclanthology.org/2022.acl-long.499) | R7 + flattened AST in pre-training | 5 tasks, 9 datasets | Ablations show AST and comments each help the representation | n | M (PR) |
| Zhang et al., "Code representation learning at scale" (CodeSage), ICLR 2024. [arXiv 2402.01935](https://arxiv.org/abs/2402.01935) | R7 contrastive encoder | code retrieval | Large-scale contrastive pre-training improves code embeddings | n | M (PR) |
| Li et al., "CoIR: a comprehensive benchmark for code information retrieval models," arXiv 2407.02883 *(venue unverified)*. [arXiv](https://arxiv.org/abs/2407.02883) | benchmark (10 datasets, 8 tasks) | code IR | Code retrieval is hard even for state-of-the-art embedders; BM25 is the sparse baseline | n | M |
| Wang, Asai et al., "CodeRAG-Bench: can retrieval augment code generation?" Findings of NAACL 2025. [arXiv 2406.14497](https://arxiv.org/abs/2406.14497) | R1 vs R7 | retrieval-augmented generation incl. RepoEval and SWE-bench | NDCG@10: **BM25 93.2 on RepoEval**, beating most dense models; on SWE-bench-Lite BM25 scores 43.0 vs up to 62.7 dense. BM25 indexes in seconds; the best dense model has about 100x encoding latency | y (index and latency) | M–H (PR, many retrievers) |
| Suresh et al., "CoRNStack: high-quality contrastive data for better code retrieval and reranking," ICLR 2025. [arXiv 2412.01007](https://arxiv.org/abs/2412.01007) | R7: CodeRankEmbed (137M) + LLM reranker | code retrieval; function localization for GitHub issues | Better than prior embedders; improves function localization | n | M (PR) |
| Reddy et al., "SweRank: software issue localization with code ranking," ICLR 2026. [arXiv 2505.07849](https://arxiv.org/abs/2505.07849) | R3 function candidates + R7 retrieve and rerank | SWE-bench Lite and LocBench localization | SweRankEmbed-Small (137M): 90.9 file and 63.1 function on SWE-bench Lite (first column, about Acc@1–5). LLM reranker beats Claude-3.5 agents. **$0.011–0.015 vs $0.46–0.83 per instance** | y* (list-price $; agents not matched) | M (PR; training data from GitHub, so contamination risk) |

### 3.3 Chunking

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Zhang, Zhao, Wang, Yang, Wei, Wu, "cAST: enhancing code retrieval-augmented generation with structural chunking via abstract syntax tree," Findings of EMNLP 2025. [arXiv 2506.15655](https://arxiv.org/abs/2506.15655) | R3, size-bounded: recursive split of large AST nodes, merge of small siblings | RepoEval retrieval; SWE-bench generation | +4.3 Recall@5 (RepoEval), +2.67 Pass@1 (SWE-bench) vs line-based chunking | n | M (PR) |
| Wu, Gong, Jahangirova, Zhang, "How does chunking affect retrieval-augmented code completion? A controlled empirical study," arXiv 2605.04763 (2026). [arXiv](https://arxiv.org/abs/2605.04763) | R1 vs R3: function, declaration, sliding window, cAST | completion; 4 retrievers × 5 generators × 9 configurations (864 settings) | **Function chunking is worst** (−3.57 to −5.64 pp on RepoEval, Cliff's δ = −1.0) and never Pareto-optimal. Sliding window and cAST dominate the cost-quality front. Context length (2K→8K: up to +4.2 pp) matters more than chunk size | y (cost-quality Pareto) | M (preprint, but controlled and factorial) |
| Zhang et al., "RepoCoder," EMNLP 2023. [arXiv 2303.12570](https://arxiv.org/abs/2303.12570) | R1 sliding windows, iterative retrieve-generate | completion | Over 10% over in-file baseline | n | M (PR) |

### 3.4 Graph representations

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Yamaguchi, Golde, Arp, Rieck, "Modeling and discovering vulnerabilities with code property graphs," IEEE S&P 2014. [PDF](https://mlsec.tu-berlin.de/docs/2014-ieeesp.pdf) | R6: AST + CFG + PDG in one graph database | vulnerability discovery | 18 new Linux kernel vulnerabilities; Test-of-Time award | n | H (PR; analytics, not LLM) |
| Cheng, Wu, Hu, "Dataflow-guided retrieval augmentation for repository-level code completion" (DraCo), ACL 2024. [arXiv 2405.19782](https://arxiv.org/abs/2405.19782) | R6 dataflow context graph | completion | +3.43 EM over state of the art; **about 100x faster prompt construction than RepoCoder** | y (latency) | M (PR) |
| Ouyang et al., "RepoGraph: enhancing AI software engineering with repository-level code graph," ICLR 2025. [arXiv 2410.14684](https://arxiv.org/abs/2410.14684) | R5: tree-sitter def/ref **line** nodes, invoke and contain edges; k-hop ego-graph | SWE-bench Lite | +2.0 to +2.7 resolve rate across RAG, Agentless, AutoCodeRover, SWE-agent. File localization +4.4 to +6.7, line-level +1.6 to +2.7. **k=2 fell below baseline (26.0 vs 27.3)**. **Tokens and cost rose in every framework** (for example Agentless 42.4k→47.3k tokens) | y* | M (PR; text and table disagree on cost) |
| Liu et al., "CodexGraph: bridging LLMs and code repositories via code graph databases," NAACL 2025. [arXiv 2408.03910](https://arxiv.org/abs/2408.03910) | R5 in a graph DB; the agent writes graph queries | CrossCodeEval, SWE-bench, EvoCodeBench | Competitive, better with stronger LLMs; argues similarity retrieval has low recall | n | M (PR) |
| Chen et al., "LocAgent: graph-guided LLM agents for code localization," ACL 2025. [arXiv 2503.09089](https://arxiv.org/abs/2503.09089) | R5 heterogeneous graph (dir, file, class, function; contain, import, invoke, inherit) + BM25 entity index + R9 fold, preview, full views | SWE-bench Lite, Loc-Bench | Claude-3.5: 94.2 file Acc@5, 73.4 function Acc@5. Fine-tuned Qwen-32B: 92.7 file Acc@5 at about $0.09 vs $0.66. **Removing the BM25 index: file Acc@5 88.3→75.2.** Removing SearchEntity is the largest drop | y* (rounds and $; Table 5) | M (PR; strict Acc@k) |
| Jiang et al., "CoSIL: issue localization via LLM-driven code graph searching," ASE 2025. [arXiv 2503.22424](https://arxiv.org/abs/2503.22424) | R5 call graph **built on the fly by the LLM**, no index; file-level module graph, then function call graph | SWE-bench Lite and Verified | Qwen2.5-Coder-32B: function Top-1 43.3 (Lite) and 44.6 (Verified). Ablations: no module call graph −5.0, no iterative search −11.3. Cost per instance on GPT-4o (50-instance subset): CoSIL $0.34, LocAgent $4.00, Agentless-FL $0.026 | y* (50-instance subsets) | M (PR; LocAgent rerun gives very different numbers from its own paper; v1 and v2 numbers differ) |
| Yu et al., "OrcaLoca: an LLM agent framework for software issue localization," ICML 2025 (PMLR 267). [arXiv 2502.00350](https://arxiv.org/abs/2502.00350) | R5/R9: priority action scheduling, relevance scoring, **distance-aware context pruning** | SWE-bench Lite | 65.33% function match; +6.33 pp resolve when plugged into an open framework | partial | M (PR) |
| Yang et al., "KGCompass: enhancing repository-level software repair via repository-aware knowledge graphs," arXiv 2503.21710 (preprint). [arXiv](https://arxiv.org/abs/2503.21710) | R5 + issues and PRs linked to code entities; narrows to about 20 functions | SWE-bench Lite | v2: 58.3% repair, 56.0% function localization; says 89.7% of localized bugs lacked an explicit location hint. v1: 45.67% at about $0.20 per repair | y* (v1) | L–M (numbers change across versions) |
| Tao et al., "Code Graph Model (CGM)," arXiv 2505.16901 *(venue unverified)*. [arXiv](https://arxiv.org/abs/2505.16901) | R5 graph fed into an LLM through adapters; Graph-RAG pipeline (rewriter, retriever, reranker, reader) | SWE-bench Lite and Verified | 43.0% Lite with Qwen2.5-72B, agentless | n | L–M |
| "CodeRAG: supportive code retrieval on bigraph for real-world code generation," arXiv 2504.10046 (preprint). [arXiv](https://arxiv.org/abs/2504.10046) | R5 requirement graph ↔ code graph + agentic retrieval | DevEval generation | Large Pass@1 gains over no-RAG (+40.9 for GPT-4o) | n | L (vs no-RAG baseline) |

### 3.5 Repo maps, skeletons, pipelines

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Xia, Deng, Dunn, Zhang, "Agentless: demystifying LLM-based software engineering agents," FSE 2025. [arXiv 2407.01489](https://arxiv.org/abs/2407.01489) | R4 tree + **skeleton** (signatures, fields, comments) + R7 (512-token chunks, text-embedding-3-small) | SWE-bench Lite | File step: LLM 78.7%, embedding 70.3%, combined 81.7%. **Skeleton 58.3% at $0.02 vs full file 53.7% at $0.15.** Hierarchical beats jumping from file directly to edit lines (50.7% vs 47.0%). About $0.70 per issue overall | y (per-step $ and LOC) | M–H (PR; careful ablations) |
| Aider, "Building a better repository map with tree sitter," blog, 2023-10-22. [aider.chat](https://aider.chat/2023/10/22/repomap.html) | R4 tree-sitter definitions and references, graph-ranked, default 1k-token budget | coding assistant | Design description only; **no evaluation** | n | L (practitioner) |

### 3.6 Agent-computer interfaces and agent frameworks

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Jimenez et al., "SWE-bench: can language models resolve real-world GitHub issues?" ICLR 2024. [arXiv 2310.06770](https://arxiv.org/abs/2310.06770) | R1 BM25 over files under 13k/27k/50k context | issue resolution | BM25 oracle-file recall rises 29.6→44.4→51.1% from 13k to 50k, but Claude 2's resolve rate *falls* 1.96→1.87→1.22. In about half of 27k cases BM25 retrieves *none* of the oracle files | y (context length) | H (PR; foundational) |
| Yang et al., "SWE-agent: agent-computer interfaces enable automated software engineering," NeurIPS 2024. [arXiv 2405.15793](https://arxiv.org/abs/2405.15793) | R9 | SWE-bench Lite (GPT-4 Turbo, $4 per instance cap) | Table 3: summarized search 18.0, **iterative search 12.0, no search 15.7**; viewer 100 lines 18.0, 30 lines 14.3, full file 12.7; last-5-observations history 18.0 vs full history 15.0. Successful runs: median $1.21 and 12 steps; failures: mean $2.52 and 21 steps | y | H (PR; single model ablation) |
| Zhang et al., "AutoCodeRover: autonomous program improvement," ISSTA 2024. [arXiv 2404.05427](https://arxiv.org/abs/2404.05427) | R3/R8-lite: AST-backed search_class, search_method, search_code APIs + SBFL when tests exist | SWE-bench Lite | 19% at $0.43 per issue | y* | M (PR) |
| Wang et al., "OpenHands: an open platform for AI software developers as generalist agents," ICLR 2025. [arXiv 2407.16741](https://arxiv.org/abs/2407.16741) | general shell + editor ACI | 13 benchmarks | Platform paper; in the LocAgent and SweRank tables it localizes well but uses the most rounds (13–15) and the highest cost | y* (third-party) | M (PR) |
| Antoniades et al., "SWE-Search" (Moatless Tools + MCTS), ICLR 2025. [arXiv 2410.20285](https://arxiv.org/abs/2410.20285) | Moatless: semantic + structured search tools | SWE-bench | +23% relative with tree search; performance scales with inference compute (cost rises) | y* | M (PR) |
| Zhang et al., "One tool is enough: RL for repository-level LLM agents" (RepoNavigator), arXiv 2512.20957 (preprint). [arXiv](https://arxiv.org/abs/2512.20957) | R8: a single **jump-to-definition** tool | localization | RL-trained 7B beats 14B baselines; 32B beats GPT-5 on most metrics | n (not in abstract) | L–M |
| Agrawal et al., "Monitor-guided decoding of code LMs with static analysis of repository context," NeurIPS 2023. [arXiv 2306.10763](https://arxiv.org/abs/2306.10763) | R8 static analysis (via LSP, multilspy) at decode time | Java method completion | SantaCoder-1.1B + MGD beats text-davinci-003 on compilation rate | n | M (PR; completion, not agents) |
| Hartman et al., "AI-assisted coding with Cody: lessons from context retrieval and evaluation," arXiv 2408.05344 (industry preprint). [arXiv](https://arxiv.org/abs/2408.05344) | R1+R7+R8 candidate generation then ranking | assistant context | Recall-oriented retrieval then ranking; latency pushes autocomplete toward classical and compiler-based retrieval | qualitative | L (experience report) |
| Lanser-CLI, arXiv 2510.22907 (position paper). [arXiv](https://arxiv.org/abs/2510.22907) | R8 LSP via CLI | — | Argues LSP diagnostics can serve as process rewards; **no experiments** | n | L |

### 3.7 Context pruning, compression, and agent efficiency studies

| Citation | Representation | Task | Main result | Eff? | Quality |
|---|---|---|---|---|---|
| Wang, Shi, Yang, et al., "SWE-Pruner: self-adaptive context pruning for coding agents," arXiv 2601.16746 (preprint). [arXiv](https://arxiv.org/abs/2601.16746) | R10: 0.6B skimmer prunes cat and grep outputs given an agent-stated goal | SWE-bench Verified (mini-SWE-agent), SWE-QA (OpenHands) | **Reads are 67.5–76.1% of tokens.** Tokens −23% (Claude Sonnet 4.5) and −38% (GLM-4.6); rounds −18% and −26%; solve rate +1.2–1.4 pp | y | M (preprint; baseline comparison on 50 instances) |
| Shi et al., "LongCodeZip: compress long context for code language models," ASE 2025. [arXiv 2510.00446](https://arxiv.org/abs/2510.00446) | R10: rank **function-level** chunks by conditional perplexity, then block-level pruning | completion, summarization, QA | Up to 5.6x compression without degradation | y | M (PR) |
| Lindenbauer et al., "The complexity trap: simple observation masking is as efficient as LLM summarization for agent context management," arXiv 2508.21433; listed at NeurIPS 2025 *(track unverified)*. [arXiv](https://arxiv.org/abs/2508.21433) | R10: mask old observations vs LLM summaries | SWE-agent on SWE-bench Verified, 5 model configurations | Masking **about halves cost** vs the raw agent at equal or better solve rate (Qwen3-Coder: 53.8→54.8). A hybrid saves a further 7–11% | y | M–H (matched scaffold) |
| Xiao, Gao, Peng, Xiong, "Reducing cost of LLM agents with trajectory reduction" (AgentDiet), arXiv 2509.23586; FSE 2026 *(per DOI 10.1145/3797084)*. [arXiv](https://arxiv.org/abs/2509.23586) | R10: reflection module removes useless, redundant, or expired steps | Trae Agent on SWE-bench Verified, Multi-SWE-bench Flash | Input tokens −39.9 to −59.7%; total cost −21.1 to −35.9%; pass rate −1.0 to +2.0 | y | M |
| Kang et al., "ACON: optimizing context compression for long-horizon LLM agents," arXiv 2510.00615 *(venue unverified)*. [arXiv](https://arxiv.org/abs/2510.00615) | R10 learned compression guidelines | AppWorld, OfficeBench (not code) | Peak tokens −26 to −54% | y | M |
| Fan et al., "An empirical study of harness design for coding agents," arXiv 2609.20804 (preprint). [arXiv](https://arxiv.org/abs/2609.20804) | R9/R10 factorial: planning × action space × context management | SWE-bench Verified, Terminal-Bench 2.1, 176 matched settings | Context management matters most under tight budgets (it prevents overflow). Rule-based elision before LLM summarization is most efficient. **Strong-bash models do well with bash-only tools at much lower cost** | y | M (preprint, matched) |
| Bai et al., "How do AI agents spend your money? Analyzing and predicting token consumption in agentic coding tasks," arXiv 2604.22750 (preprint). [arXiv](https://arxiv.org/abs/2604.22750) | — | 8 LLMs on SWE-bench Verified | About 1000x more tokens than code chat; **input tokens drive cost**; up to 30x run-to-run variance on the same task; accuracy peaks at intermediate cost | y | M |
| Fan et al., "SWE-Effi: re-evaluating software AI agent system effectiveness under resource constraints," arXiv 2509.09853 (preprint). [arXiv](https://arxiv.org/abs/2509.09853) | — | SWE-bench subset | Effectiveness depends on scaffold–model fit; describes a "token snowball" and "expensive failures" | y | M |
| Majgaonkar et al., "Understanding code agent behaviour: an empirical study of success and failure trajectories," arXiv 2511.00197 *(venue unverified)*. [arXiv](https://arxiv.org/abs/2511.00197) | — | OpenHands, SWE-agent, Prometheus trajectories | **Failed runs still find the right files 72–81% of the time (Lite)**; failures are longer and more variable; function-level precision about 30% | y* (lengths) | M |
| Kapoor, Stroebl, Siegel, Nadgir, Narayanan, "AI agents that matter," TMLR 2025. [arXiv 2407.01502](https://arxiv.org/abs/2407.01502) | methodology | — | Agent evaluations must be cost-controlled (accuracy–cost Pareto); simple baselines often match complex agents | y | H (methodology) |
| Sen et al., "Is grep all you need? How agent harnesses reshape agentic search," arXiv 2605.15184 (preprint). [arXiv](https://arxiv.org/abs/2605.15184) | R1 grep vs R7 vector *inside agent harnesses* | LongMemEval (116 questions; **not code**) | Grep generally beats vector search, but the harness and result-delivery format change the ranking | partial | L–M (not code) |
| Zhang et al., "SWE-Explore: benchmarking how coding agents explore repositories," arXiv 2606.07297 (preprint). [arXiv](https://arxiv.org/abs/2606.07297) | ranked code regions under a **fixed line budget** | 848 issues, 10 languages, 203 repositories | Agentic explorers beat classical retrieval; **file-level is largely solved**; line-level coverage and efficient ranking separate the best | y (context efficiency) | M |
| Liu et al., "Lost in the middle," TACL 2024. [arXiv 2307.03172](https://arxiv.org/abs/2307.03172) | — | long-context QA | Accuracy drops when relevant text sits mid-context | n | H |
| Liang, Garg, Zilouchian Moghaddam, "The SWE-Bench illusion: when state-of-the-art LLMs remember instead of reason," arXiv 2506.12286; NeurIPS 2025 listing. [arXiv](https://arxiv.org/abs/2506.12286) | — | contamination diagnostics | From issue text + repo name only, o3 names the buggy file **76%** of the time on SWE-bench Verified vs **at most 53%** on other repositories | — | M–H |


Provider fact used below: Anthropic prompt caching bills cache reads at 0.1x the base input price for most models (5-minute writes at 1.25x), with a model-specific minimum cacheable length (for example 4,096 tokens for Haiku 4.5). [docs](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching)

---

## 4. What the evidence says

### 4.1 Structure vs lexical for **localization**

- **What gets indexed matters more than the ranking function.** Across fifteen years, gains in IR bug localization came from fields and structure (BLUiR), best-segment scoring (BRTracer), stack-trace and identifier extraction (BRTracer, BLIZZARD, Caumartin & Melo), and history. Simple ranking models were not beaten by complex topic models (Rao & Kak). graf's "best window per file" already resembles BRTracer's best-segment idea.
- **Granularity trade-off.** Moving to method or function units lowers ranked accuracy but cuts inspection effort (FinerBench4BL). This matches graf's goal: fewer, more precise tokens. File-level localization from issue text is now largely solved by competent systems (SWE-Explore; failed agent runs find the file 72–81% of the time). The headroom is at function and line level.
- **AST chunking: small, conditional gains.** cAST gains come from *size-bounded* structural chunks (split and merge). Pure function chunks lose to sliding windows for completion (Wu et al. 2026); the authors' suspected cause is size variance under similarity scoring. For BM25 this interacts with length normalization (b): a 500-line handler and a 5-line getter are hard to score on one scale. **No study we found tests AST units vs fixed windows for issue-text localization with BM25 at a matched token budget.** That is exactly graf's `ast_units` arm, so our screen would add new evidence.
- **Graph propagation: helpful in a controlled form; noisy when expanded mechanically.** RepoGraph 1-hop helps modestly; 2-hop hurts. LocAgent's hop ablation shows multi-hop matters mainly at function level. CoSIL's call graph adds about 5 pp function Top-1, but iterative LLM-guided search adds more. KGCompass claims most of its localized bugs need multi-hop links (89.7%), but this is a version-variable preprint. No paper we found evaluates *static score propagation* (score + λ·neighbour) without an LLM in the loop, which is what our `ast_graph` arm is. The closest analogue in classic fault localization is propagation over dependence graphs, and those methods are test-based.
- **Learned retrievers beat BM25 at function level on SWE-bench Lite by a wide margin** (BM25 function Acc@5 31.8 vs CodeRankEmbed 51.8 vs SweRankEmbed-Small about 63). They need GPU or CPU embedding at index time, carry contamination risk (trained on GitHub issue-fix pairs), and are evaluated almost only on Python. On RepoEval-style completion, BM25 is on par with dense models (CodeRAG-Bench).

### 4.2 Structure vs lexical for **agent cost**

- **Cost scales with turns × accumulated context.** Bai et al. (input tokens dominate), SWE-Pruner (reads are 67–76% of tokens), the Complexity Trap (masking about halves cost), AgentDiet (40–60% fewer input tokens), and SWE-agent (history collapsing *raised* resolve rate from 15.0 to 18.0) all point the same way. This matches our measurement that about 85% of tokens are re-sent context.
- **Graph tools have not been shown to cut agent cost.** RepoGraph *increased* tokens in all four frameworks. LocAgent with Claude-3.5 costs about as much as SWE-agent ($0.66 vs $0.67). Under CoSIL's rerun, LocAgent cost $4.00 per instance on GPT-4o. Cheap localization comes from **non-agentic pipelines** (SweRank about $0.01; Agentless-FL about $0.03) or small fine-tuned models, not from giving a frontier agent a graph tool.
- **Interface format beats capability.** SWE-agent: a search tool that pages through hits one at a time (12.0) did worse than no search tool (15.7). Too-small (30 lines) and too-large (full file) viewers both hurt. Agentless: skeletons beat full files on accuracy and cost. The harness study found strong-bash models need no special tools and run cheaper with bash only. This is consistent with our observation that plain regex search plus reads was not beaten.
- **Grep vs embeddings in agents.** There is no peer-reviewed code study. One preprint (not on code) finds grep generally better but sensitive to the harness. Vendor and blog claims point both ways and are not evidence.
- **Contamination confounds localization claims.** Models can often name the file from memory (SWE-Bench Illusion). Cobra, Flask, and Fastify are popular and probably in training data, so a retrieval arm must beat a no-retrieval "guess the file" prior to count as useful.

### 4.3 Program-analysis representations for agents (LSP, SCIP, types, dataflow)

The evidence is thin. MGD (NeurIPS 2023) shows static analysis helps *completion*. DraCo shows dataflow-guided retrieval is both more accurate and about 100x faster to build prompts than RepoCoder. RepoNavigator (preprint) shows one jump-to-definition tool suffices for an RL-trained localizer. We found **no controlled study measuring whether LSP or SCIP navigation reduces agent turns or tokens** versus grep plus read with the same model and budget. Claims on practitioner blogs (for example 16–22% fewer tokens) are single-codebase anecdotes.

---

## 5. Gaps

1. **Matched-budget efficiency.** Few papers compare tools at equal delivered tokens or equal agent spend. Most report list-price $ without caching. None of the localization papers count cached vs uncached input separately.
2. **Static AST-unit vs window BM25 for issue-text localization.** Untested at matched budget. Completion results (cAST, Wu et al.) conflict in direction.
3. **Non-LLM call-graph propagation.** Untested on issue localization; all graph wins involve an LLM choosing what to expand.
4. **Languages and repositories.** Evidence is mostly Python on 12 SWE-bench repositories. Go and JavaScript (Cobra, Fastify) are barely covered (LocBench, SWE-Explore, and BLAZE are partial exceptions).
5. **Retrieval tools inside agents.** No peer-reviewed ablation isolates grep, BM25, embeddings, graph, and LSP tools inside the *same* agent while counting turns and tokens.
6. **Contamination controls.** Rarely applied to localization; no-retrieval priors are seldom reported.
7. **Version instability.** CoSIL, KGCompass, Agentless, and SWE-Fixer report different numbers across arXiv versions. Cross-paper reruns disagree sharply (LocAgent under CoSIL's setup: 10.3 vs 73.4 function-level, with different models and metrics).

---

## 6. Testable hypotheses for graf

Two settings: **(O)** the offline localization screen (`benchmark/localization.py`, preregistered `localization_screen`: issue text → 2,000-token payload, unit_hit, file_hit, gold_line_shown); **(A)** agent trials (Haiku solver, Cobra, Flask, and Fastify repair tasks). Each hypothesis states a direction, the paper that motivates it, and what would falsify it. Hypotheses outside the current preregistration need a new recorded version before new data is seen (AGENTS.md).

### Offline screen (O)

**O1. Unbounded AST units do not beat 24-line windows on unit_hit. Size-bounded AST chunks may.**
*Motivation:* Wu et al. 2026 (function chunking worst; size variance); cAST 2025 (size-bounded gains); FinerBench4BL (method-level lowers ranked accuracy).
*Prediction:* `ast_units` vs `graf_floor` has no significant McNemar win. Losses concentrate where gold units are long (over 2x median) or very short. A new arm `ast_bounded` (cAST rule: split units over N non-whitespace characters, merge adjacent small siblings, N fixed in advance) does at least as well as `ast_units`.
*Falsified if:* `ast_units` wins significantly, or wins and losses show no relation to unit length.

**O2. Call-graph propagation gives a small function-level gain, and only with 1-hop and a hub cap.**
*Motivation:* RepoGraph (1-hop helps, 2-hop hurts); LocAgent hop ablation; CoSIL module graph ablation (−5 pp).
*Prediction:* `ast_graph` ≥ `ast_units` on unit_hit, with gains mainly on tasks whose gold unit has no lexical overlap with the issue but sits next to one that does. Payload tokens are unchanged by construction. A follow-up arm with no hub cap, or with 2 hops, does worse.
*Falsified if:* propagation hurts unit_hit, or gains are unrelated to the neighbour-overlap condition.

**O3. Query reformulation beats representation changes.**
*Motivation:* BLIZZARD; Caumartin & Melo (+35% first-file); BRTracer (stack traces); BLUiR (identifier fields).
*Prediction:* A deterministic issue-text rewriter yields a larger unit_hit gain over `graf_floor` than either `ast_units` or `ast_graph`. It would keep code spans, backticked tokens, CamelCase and snake_case identifiers, file paths, and stack-frame symbols, and down-weight prose. Gains concentrate in "rich" and "noisy" reports, in BLIZZARD's classes.
*Falsified if:* the rewriter gains less than the structural arms, or gains only on reports with explicit file paths.

**O4. A no-retrieval prior is a strong competitor on these popular repositories.**
*Motivation:* SWE-Bench Illusion (76% file paths from issue text alone, at most 53% elsewhere).
*Prediction:* Haiku asked to name the files and functions from issue text plus repo name, with no code, reaches a non-trivial file_hit. Any retrieval arm's *added* value is the gain over this prior. That gain is largest on tasks where the prior fails.
*Falsified if:* the prior's file_hit is near the random baseline.

**O5. Exposing structure as BM25F fields helps more than changing the unit.**
*Motivation:* BLUiR; Robertson & Zaragoza BM25F (already planned as arm B2 for path weighting).
*Prediction:* On the same 24-line windows, adding fields (enclosing function or class name, path) with weight above 1 improves unit_hit more than replacing windows with AST units, at equal payload.
*Falsified if:* fielding gains nothing or less than `ast_units`.

**O6. Learned retrieval beats BM25 at function granularity on these tasks, but the gap is smaller than on SWE-bench Lite.**
*Motivation:* SweRank and CoRNStack (large gaps on SWE-bench Lite); CodeRAG-Bench (BM25 on par on RepoEval); contamination concerns.
*Prediction:* A diagnostic arm using CodeRankEmbed (137M) over the same units shows a positive unit_hit difference smaller than the about 20 pp gap seen on SWE-bench Lite. If adopted, it would be diagnostic only, since it adds an embedding dependency.
*Falsified if:* the gap equals or exceeds the SWE-bench Lite gap, which would suggest the BM25 gap generalizes, or is zero.

### Agent trials (A)

**A1. Context management, not retrieval, is the main lever on cost.**
*Motivation:* Complexity Trap (masking about halves cost); AgentDiet (−40 to −60% input tokens); SWE-Pruner (reads 67–76%); SWE-agent (last-5-observations beat full history); our 85% re-sent finding.
*Prediction:* Masking tool outputs older than the last k turns (k fixed in advance, for example 5), with the same tools, cuts total input tokens by at least 30% and total cost by at least 20% (counting cache reads at their billed rate), with resolve rate non-inferior. The effect is larger than any retrieval-tool arm's.
*Falsified if:* savings are below 15%, or resolve rate is inferior.

**A2. Front-loading localization reduces turns only when it is precise. A noisy tool costs turns.**
*Motivation:* Agentless and SweRank (pipelines cost about 1/50 of agents); SWE-agent (iterative search 12.0 vs no search 15.7); Majgaonkar et al. (failures are longer).
*Prediction:* Injecting the best offline arm's payload (from O1–O3) into the first prompt reduces exploration turns before the first edit on tasks where it hits (unit_hit = 1). It *increases* turns when it misses. The net effect follows the arm's hit rate.
*Falsified if:* turn reduction does not depend on hit or miss.

**A3. Skeleton or outline views cut read tokens at equal success.**
*Motivation:* Agentless (skeleton beats full file at about 1/7 the cost); LocAgent (fold, preview, and full detail levels); SWE-agent (100-line viewer beats full file).
*Prediction:* An `outline <file>` tool (signatures and docstrings only, built from the tree-sitter units already in the screen) lowers read-tool tokens per task by at least 25%, with no rise in turns.
*Falsified if:* the agent reads full files anyway, so turns rise, or read tokens fall less than 10%.

**A4. Structural navigation (jump-to-definition or callers) does not beat grep plus read for a capable model on small repositories.**
*Motivation:* Harness study (strong-bash models do well with bash only); RepoNavigator (one definition tool suffices, but only after RL training); no controlled LSP-for-agents evidence.
*Prediction:* Adding a `callers` or `definition` tool, backed by the `ast_graph` edges, to the grep-plus-read baseline does not significantly reduce turns or tokens on Cobra, Flask, or Fastify.
*Falsified if:* the tool significantly reduces turns at non-inferior success.

**A5. Report cost with and without caching. Retrieval payload size barely matters once the prefix is cached.**
*Motivation:* Prompt caching price (0.1x reads); Bai et al. (input tokens dominate); Kapoor et al. (cost-controlled evaluation).
*Prediction:* Under cached accounting, the cost difference between a 2K and an 8K retrieval payload delivered in turn 1 is within run-to-run noise, because Bai et al. found up to 30x variance. The difference in turn count is not.
*Falsified if:* payload size explains more cost variance than turn count under cached accounting.

---

## 7. Top five hypotheses

1. **O3 (query reformulation beats representation changes).** Cheapest to build, and has the strongest lineage (BLIZZARD, BRTracer, Caumartin & Melo).
2. **O1 (unbounded AST units do not beat windows; size-bounded ones may).** Directly tests the current preregistered arm against conflicting completion-task evidence (cAST vs Wu et al. 2026).
3. **O2 (1-hop call-graph propagation gives a small function-level gain).** RepoGraph and LocAgent hop results predict both the sign and where the gain should appear.
4. **A1 (context management dominates cost).** Complexity Trap, AgentDiet, and SWE-Pruner all point here, and our 85% re-sent finding agrees.
5. **O4 (no-retrieval memorization prior as a control).** SWE-Bench Illusion shows that without it, any localization gain on popular repositories can be overstated.

All claims above are limited to the cited workloads. None of these papers establish that a given representation will win on Cobra, Flask, or Fastify issue text, or with a Haiku solver.
