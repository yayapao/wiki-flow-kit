# wiki-flow-kit

`wiki-flow-kit` 把本地 Wiki 流程打包为可通过 Agent 对话安装和维护的能力，支持 macOS Apple Silicon（M 系列）与 Intel。它将项目规范 `aiwiki`、通用资料 `valut`、LLM Wiki 应用和 qmd 检索连起来，支持导入整理、跨库问答、知识同步和工具更新。

Supported machines are Apple Silicon (`arm64`) and Intel (`x86_64`) Macs. The kit requires native Homebrew under `/opt/homebrew` on Apple Silicon and `/usr/local` on Intel, plus Python 3.12, Node.js 22, qmd 2.8.3, and the open-source LLM Wiki app. It installs an architecture-matched upstream DMG when available. Intel otherwise builds the selected upstream release tag from source using Xcode Command Line Tools, Rust, protobuf, and the included x86_64 PDFium library.

## 让 Agent 安装

把下面这段话发给新 Mac 上可以执行本机命令的 Agent：

```text
请从 https://github.com/yayapao/wiki-flow-kit 的最新 Release 为这台 Mac 安装 wiki-flow。
先读取仓库 README，再通过 GitHub Release API 下载 wiki-flow-kit.zip，按 asset digest 校验 SHA-256。解压后运行 packaging/install.sh，使用本机原生 arm64 或 x86_64 架构，工作区设为 ~/Documents/wiki-flow。缺少依赖时处理安装；系统安装窗口或管理员密码由我操作。
没有迁移包就初始化空知识库；如我另行提供迁移包，先校验清单和文件散列，再恢复到空工作区。打开 LLM Wiki 和 valut 项目，让我在 App 设置里输入模型凭证并启用本地 API。最后运行 doctor，验证 API、MCP、qmd 和少量资料同步，并报告完成项和待完成项。
不要创建兼容软链，不要上传个人知识包，也不要读取或转移其他客户端的登录凭证。
```

[最新版 Release](https://github.com/yayapao/wiki-flow-kit/releases/latest) · [直接下载安装包](https://github.com/yayapao/wiki-flow-kit/releases/latest/download/wiki-flow-kit.zip) · [SHA-256 清单](https://github.com/yayapao/wiki-flow-kit/releases/latest/download/SHA256SUMS.txt)

安装包包含工具和共享 Skill，安装时联网获取依赖及 LLM Wiki 应用。个人知识和模型凭证由各机器分别管理。Intel 在上游缺少原生 DMG 时从 Release tag 本机编译，完整 App 构建仍需 Intel 实机验收。

后续直接说 `$wiki-flow 更新工具`，Agent 会检查 GitHub 最新版并执行更新，保留本机知识与配置。

For a local checkout or extracted kit, run `packaging/install.sh`. It installs prerequisites and uses the architecture-specific LLM Wiki path above. Installation does not import personal knowledge or configure model credentials.

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
$wiki-flow mylife，这个项目值得留下，采访我
$wiki-flow mylife，以旁观者视角看看我的这次选择
```

The equivalent management commands are `wiki-flow init`, `doctor`, `bundle create`, `bundle restore`, `sync --dry-run`, `sync --apply`, `schedule enable`, `schedule disable`, and `update --check` / `update --apply`. The normal Wiki CLI remains available through the same `wiki-flow` command.

An independent personal Obsidian vault can be registered as `personal.path` in `~/.config/wiki-flow/registry.json`. `wiki-flow mylife interview` and `wiki-flow mylife observe` load role instructions for the current conversation agent; local page operations use `--domain mylife`. This optional vault is separate from the LLM Wiki project, qmd sources, and the existing aiwiki/valut migration bundle.

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

The release workflow runs these checks on native Apple Silicon and Intel GitHub runners, then publishes a versioned archive, a stable `wiki-flow-kit.zip` download, and a checksum list. Push a `vX.Y.Z` tag matching `VERSION` to publish the next release after both test jobs pass. `aiwiki/wiki-flow/` remains the canonical home for detailed design and operational facts.
