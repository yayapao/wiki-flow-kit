# wiki-flow-kit

`wiki-flow-kit` packages the local cross-agent Wiki workflow for macOS. An Agent can install it, create or restore a knowledge workspace, register the shared Skill with Codex, diagnose the setup, sync `aiwiki` into LLM Wiki, and refresh qmd.

Supported machines are Apple Silicon (`arm64`) and Intel (`x86_64`) Macs. The kit requires native Homebrew under `/opt/homebrew` on Apple Silicon and `/usr/local` on Intel, plus Python 3.12, Node.js 22, qmd 2.8.3, and the open-source LLM Wiki app. It installs an architecture-matched upstream DMG when available. Intel otherwise builds the selected upstream release tag from source using Xcode Command Line Tools, Rust, protobuf, and the included x86_64 PDFium library.

## Agent Setup

After `yayapao/wiki-flow-kit` has its first GitHub Release, give the target Mac Agent this request:

```text
Set up wiki-flow on this Mac using the latest release from yayapao/wiki-flow-kit.
Download the fixed wiki-flow-kit.zip asset through the GitHub Release API and verify its SHA-256 digest before extracting it. Support this Mac's native Apple Silicon or Intel architecture. Install the kit and LLM Wiki, configure the shared Skill and Codex MCP if Codex is installed, then run wiki-flow doctor.
Use ~/Documents/wiki-flow as the workspace. If I provide a .wiki-flow.zip migration bundle, verify its manifest and SHA-256 before restoring it. Otherwise run wiki-flow init.
Do not create compatibility symlinks, upload my knowledge bundle, or put API keys in files. Ask me to enter the model API key in LLM Wiki Settings, then verify the local API, MCP, qmd search, and a small source sync. Report versions, paths, checks, and anything still pending.
```

For a local checkout or extracted kit, run `packaging/install.sh`. It installs prerequisites and uses the architecture-specific LLM Wiki path above. Installation does not import personal knowledge or configure model credentials.

The local kit can already be copied to another Mac without a remote release:

```text
Install the attached wiki-flow-kit-v0.1.0.zip on this Mac. Verify it against the attached SHA256SUMS.txt, extract it, read wiki-flow/README.md, and run wiki-flow/packaging/install.sh --workspace ~/Documents/wiki-flow.
Then run ~/.local/bin/wiki-flow init, or restore my migration bundle into the empty workspace. Open LLM Wiki with the valut folder and let me enter the model credentials in its Settings. Finish by running ~/.local/bin/wiki-flow doctor and report checks that remain pending.
```

The remote repository and first release have not been published yet. Until then, the installer, updater, and release workflow can be tested from this checkout, but a second Mac cannot fetch a release from GitHub.

## Agent Commands

```text
$wiki-flow 当前状态
$wiki-flow 导入 <文件、网址或目录>
$wiki-flow 查询 <问题>
$wiki-flow 更新工具
$wiki-flow 创建知识迁移包 <输出路径>
$wiki-flow 恢复知识迁移包 <zip 路径>
$wiki-flow 预览知识同步
$wiki-flow 同步知识库
$wiki-flow 每日同步知识库
```

The equivalent management commands are `wiki-flow init`, `doctor`, `bundle create`, `bundle restore`, `sync --dry-run`, `sync --apply`, `schedule enable`, `schedule disable`, and `update --check` / `update --apply`. The normal Wiki CLI remains available through the same `wiki-flow` command.

## Portability Boundaries

- `wiki-flow bundle create` exports `aiwiki` and `valut` with SHA-256 entries. It omits Git metadata, API credentials, chat history, queues, runtime configuration, qmd SQLite files, and model caches. The bundle may contain personal documents; keep it private.
- `bundle restore` requires an empty workspace, validates archive paths and hashes, and never follows symbolic links. qmd indexes are rebuilt on the target Mac.
- Restore rebases knowledge-root references in active `AGENTS.md`, `purpose.md`, and `schema.md` instruction files. Historical source snapshots and provenance retain their original text. The restore receipt lists rewritten instruction files; other machine-specific paths in documents may still need review.
- `sync --apply` copies supported source files from `aiwiki` into `valut/raw/sources/aiwiki/`. Modified destination files become conflicts; source deletions are reported and never propagated as deletes.
- If LLM Wiki has queued ingestion work, qmd refresh is deferred and marked for retry. Run `wiki-flow sync --apply` again after the queue is idle.
- Daily sync uses a per-user macOS LaunchAgent. It is opt-in and can be removed with `wiki-flow schedule disable`.
- `update --apply` accepts a versioned GitHub Release archive only when the API supplies a SHA-256 digest; it validates archive paths and the embedded version before replacing the kit. It updates the shared Skill and local install version while preserving machine settings, data, dependencies, and LLM Wiki app settings.

## Development Checks

```bash
bash -n packaging/install.sh
python3 -m py_compile packaging/portable.py bin/wiki-flow
python3 -m unittest discover -s tests -v
python3 packaging/build_release.py --output dist
```

The release workflow runs these checks on native Apple Silicon and Intel GitHub runners, then publishes a versioned archive, a stable `wiki-flow-kit.zip` download, and a checksum list. `aiwiki/wiki-flow/` remains the canonical home for detailed design and operational facts. The source currently has no configured Git remote or published Release, so remote installation and self-update become available after the repository and first Release are published.
