---
name: wiki-flow
description: 跨 Agent 本地 Wiki 助手。处理安装诊断、知识迁移、aiwiki 同步、导入整理、检索和索引刷新；用户明确发起 mylife 记录、项目经历访谈、个人思考整理或旁观者评价时，进入事件驱动的个人知识流程。
---

# wiki-flow

把用户的话路由到下列操作，直接执行并返回结果。仅当用户问“怎么做”、索取教程或命令时才提供操作说明；已提出执行请求时，不把命令或长 Prompt 交还给用户执行。用户在修改 Skill 时给出的示例不是立即运行所有示例的请求。

工具入口：运行 `wiki-flow`，PATH 中找不到时用 `~/.local/bin/wiki-flow`。安装器将 Skill 复制到 `~/.agents/skills/wiki-flow`，检测到 WorkBuddy 时也复制到其 Skill 目录；不依赖某台机器的仓库绝对路径或符号链接。工作目录、项目 UUID、域和检索配置从 `~/.config/wiki-flow/registry.json` 读取；需要路由时运行 `wiki-flow domains`。介绍和帮助不先运行 `domains`。首次安装使用 checkout 内的 `packaging/install.sh`，初始化工作区另运行 `wiki-flow init`，恢复迁移包则保留目标工作区为空。

## 自然语言入口

| 用户说法（可用同义表达） | 执行动作 |
| --- | --- |
| `介绍你自己`、`你能做什么`、`帮助`、仅提及 `$wiki-flow` | 按“介绍”回答，不查询服务、不改文件 |
| `当前状态`、`status`、`看看知识库情况` | 运行 `status`，按“状态”报告实时数据 |
| `mylife`、`这段经历值得留下，采访我`、`这个项目不错，聊聊写进 mylife`、`整理我的这个想法` | 读 [mylife 流程](references/mylife.md)；首次进入运行 `mylife interview` 并阅读 Vault 约定，依据用户意图开展事件访谈或协作整理 |
| `以旁观者看看我`、`mylife 客观评价我的选择` | 读 [mylife 流程](references/mylife.md)，运行 `mylife observe`，按独立旁观者人设基于具体材料观察 |
| `mylife 当前状态`、`检查 mylife` | 分别运行 `mylife` 或 `check --domain mylife`；只读本地 Vault |
| `导入 <文件/网址/目录>`、`把 aiwiki 喂给 LLM Wiki`、`收录这篇文章` | 读 [导入与整理](references/ingestion.md)，保存原文后检查应用自动摄入结果，完成回读与刷新；应用未接管或用户明确要求时才由当前模型整理 |
| `整理已导入资料`、`整理 amg`、`继续整理` | 读 [导入与整理](references/ingestion.md)，先检查现有原文与处理进度，复用快照并完成指定范围 |
| `查询 <问题>`、`搜索 <关键词>` 或直接提出知识问题 | 检索并阅读证据后回答；默认不写页 |
| `更新 <项目/页面>`、`根据本次变化维护 Wiki`、`保存这次问答` | 读取当前正文和来源，按“更新”增量保存、回读与刷新 |
| `刷新索引`、`更新向量` | 运行 `refresh --vectors`，分别报告全文和向量；明确“只刷新全文”则运行 `refresh` |
| `检查知识库`、`检查断链`、`巡检` | 运行 `check`；应用可用时补充 `graph`、`reviews`，报告问题，修复按用户给定范围进行 |
| `安装 wiki-flow`、`在这台 Mac 配置 Wiki` | 从用户指定的 checkout 或 release 运行安装器；只支持 macOS Apple Silicon / Intel，诊断并报告需要人工完成的系统依赖或模型凭证 |
| `检查本机安装`、`doctor` | 运行 `wiki-flow doctor`，区分已通过项和仍待完成的配置 |
| `创建迁移包 <路径>`、`备份并迁移到新 Mac` | 运行 `wiki-flow bundle create --output <路径>`；不要上传或发送知识包 |
| `恢复迁移包 <zip>` | 仅在目标工作目录为空时运行 `wiki-flow bundle restore --bundle <zip>`，完成后重建 qmd 索引 |
| `预览知识同步` | 运行 `wiki-flow sync --dry-run`，列出新增、更新、冲突和源文件删除；不写入 |
| `同步知识库`、`同步 aiwiki` | 运行 `wiki-flow sync --apply`；保留冲突和源删除项，队列空闲后重跑以完成 qmd 刷新 |
| `开启每日同步`、`停止每日同步` | 按明确请求运行 `wiki-flow schedule enable` / `disable` |
| `更新工具` | 先运行 `wiki-flow update --check`；用户明确要求执行更新时运行 `wiki-flow update --apply`，失败时保留当前安装 |

缺少必需的导入来源、问题或更新目标时，只询问缺失项。`aiwiki`（也含用户常写的 `aikiwi`）对应注册表中的 `aiwikiRoot`；`valut` 对应 `general.path`；`mylife` 对应 `personal.path`。更具体的 mylife 意图优先于普通知识问答和更新路由。在目录中找到唯一对应的项目可直接使用；多个项目无法区分时再澄清。介绍、状态、查询和检查不隐含清理、导入或全库改写。

## 介绍

以“我是 wiki-flow，你的跨 Agent 本地 Wiki 助手”开头，简短说明可以在 macOS 上安装/诊断、跨 aiwiki 与 valut 检索、导入整理资料、维护文档、迁移知识和同步索引，也能通过事件访谈协助沉淀 mylife。给出 6 个可直接使用的短例子：`$wiki-flow 当前状态`、`$wiki-flow 导入 <网址或文件>`、`$wiki-flow 查询 <问题>`、`$wiki-flow 预览知识同步`、`$wiki-flow 更新工具`、`$wiki-flow mylife，这个项目值得留下，采访我`。

说明客户端与应用各自使用配置的模型，LLM Wiki 与 Obsidian 共读 valut 的本地 Markdown，mylife 是 Obsidian 独立管理的个人 Vault；项目事实以 aiwiki 为准。应用自动摄入可用时由应用生成知识页，客户端负责检索、维护及补充整理。使用上游对应架构的签名 DMG；Intel 没有官方 DMG 时从选定 Release tag 在本机编译，需 Xcode Command Line Tools、Rust 和 protobuf。不要在未执行状态检查时声称 API 正常、资料为空或任务已完成。

## 状态

每次实际运行 `status`，不沿用记忆或历史收据。用简短表格报告：

- API 是否可用、固定项目 UUID / 路径，以及应用当前项目是否匹配；保留失败原因。
- `inventory` 的原始资料数量、顶层文件夹、Wiki 页数与来源页数。
- `qmdStatus` 的数据库可读状态、各 collection 的有效文档数与向量块数。`freshness=not-checked` 表示未比较正文与索引，不得据此说索引完全同步；数据库存在也不等于可用。
- 模型分工、队列中的进行中 / 失败任务，以及明确的阻塞项。

状态是只读操作，不自动打开应用、刷新向量、导入、生成或清理。API 不可用时仍报告可读取的本机数据，并说明 qmd / 文件读取与应用 API 的边界。原文与页面不是一一对应，不用数量相减冒充精确的待整理清单；来源页为 0 时可说明尚无来源摘要。

## 查询

- mylife 内的检索使用 `find --domain mylife <关键词>`、`read --domain mylife <相对路径>`；当前是否纳入 qmd 以注册表为准，不把 `search-all` 的两来源结果当作已搜索个人库。
- 两来源为注册表中的 valut（通用资料）与 aiwiki（项目规范）。先用 `search-all <关键词>`；需要中文语义召回用 `semantic <问题>`。后者使用 qmd 结构化 vec 查询并关闭重排，不运行本地生成式查询扩展；问题改写由当前会话模型完成。
- 检索结果只是候选，回答前读相关正文与来源。通用页用 `read <wiki/相对路径>`；项目文档使用 `find/read --domain <域>`，先读 aiwiki 的 `AGENTS.md`、目标域 README 和相关专项规范。遵守项目维护约定；缺失的 ViewAI Wiki 静默跳过。
- `search` 是 llm_wiki 当前项目内关键词 / 图谱检索。LLM Wiki 没有直接接入 qmd；aiwiki 在 qmd 可检索，不等于已经导入应用。
- 回答引用真实文件或原始来源，区分项目当前事实、外部建议、推断和待验证项。默认只回答，不为每个问题写新页。

## 更新

个人经历、思考、想象和自我观察按 [mylife 流程](references/mylife.md) 写入 `--domain mylife`，使用本地文件、基准校验与历史收据；不走通用库 API、自动摄入、index/log 或默认 qmd 刷新。普通项目资料整理不会自动启动个人访谈、生成自我画像或整库回流；由用户明确发起相关意图。

1. 读取 schema、目标原页与来源；`read` 返回正文及 SHA-256。项目事实原位维护在 aiwiki；通用经验进入 valut。已授权任务的确定增量直接处理。
2. 同一页面串行写入。用 UTF-8 临时文件准备完整正文，运行 `write <相对路径> --file <临时文件> --expected <读取时sha256>`，项目文档加 `--domain <域>`，新页用 `--expected new`。基准变化时重新读、合并，不绕过检查。
3. 通用页遵守 schema，补来源、related，增量维护 index / log，必要时更新 overview。只写本次任务文件，保留其他改动；不把外部建议直接改成项目事实。
4. 批量完成后 `refresh --vectors`，分别报告正文、全文与向量状态；索引失败只重试刷新，不重复写页。旧 `embed <路径>` 兼容为两来源增量刷新，优先使用 `refresh --vectors`。
5. 回读正文和引用，以 `check` 验证链接。语义矛盾、过时结论由当前模型基于证据判断；`reviews` 不代表已经全面审核。用户要求保存问答时记录问题、证据、结论和范围。

已导入的 aiwiki 摘要需要更新时，比较原文散列，保留新旧快照并合并原有摘要，流程见 [导入与整理](references/ingestion.md)。qmd 刷新不会自动重写摘要。

## 工具与边界

- 客户端操作使用当前会话模型；LLM Wiki 自动摄入和应用内对话使用应用独立配置的 API 模型。先读取该机器的 `sourceWatchConfig.autoIngest`、队列和生成结果，不能把另一台机器的开关或模型当作当前配置；安装器不会配置模型凭证或开启自动摄入。避免客户端同时处理同一来源，不转发客户端会话或提取客户端登录凭证。
- 本套件不需要 Ollama 或本地生成模型，也不卸载用户已有工具。qmd 按需加载约 610 MiB embedding GGUF，索引使用本机 SQLite；不依赖 PostgreSQL 或常驻模型服务。CLI 使用 qmd 时不调用原生 MCP 的 `embed_page`。
- API / MCP 和通用页 API 写入需要 LLM Wiki 运行；本机文件与 qmd 可独立读取。执行导入/写入因应用未启动受阻时，可正常打开 `/Applications/LLM Wiki.app` 后重试一次并核对固定项目；纯状态请求不自动启动应用。失败保留已保存内容并报告实际阻塞。
- MCP 服务器名 `llm-wiki`；用 `llm_wiki_set_project` 绑定注册表固定 UUID，后续调用仍明确传 UUID。Token 由工具运行时读取，不在输出、配置示例或参数中打印。
- 优先用 CLI 写页；原生 MCP `write_page` 没有读取基准校验。CLI 锁只协调本工具，不能锁住 Obsidian、应用或其他外部写入者。
