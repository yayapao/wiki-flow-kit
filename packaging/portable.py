#!/usr/bin/env python3
"""Portable macOS installer and lifecycle manager for wiki-flow."""

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import platform
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile

APP_REPOSITORY = "nashsu/llm_wiki"
WIKI_FLOW_REPOSITORY = os.environ.get("WIKI_FLOW_REPOSITORY", "yayapao/wiki-flow-kit")
QMD_VERSION = "2.8.3"
QMD_EMBED_MODEL = "hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf"
APP_MCP_RELATIVE = "Contents/Resources/mcp-server/dist/src/index.js"
SUPPORTED_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".doc", ".docx",
    ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp", ".rtf",
    ".csv", ".tsv", ".org", ".epub", ".mobi", ".json", ".yaml", ".yml",
    ".toml", ".xml", ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs",
    ".java", ".kt", ".c", ".h", ".cpp", ".css", ".sql", ".sh", ".swift",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
    ".mp3", ".m4a", ".wav", ".mp4", ".mov",
}
SKIP_DIRS = {".git", ".obsidian", "node_modules", ".venv", "venv", "target", "dist", "__pycache__", ".cache"}
MAX_BUNDLE_BYTES = 10 * 1024 * 1024 * 1024


def app_home():
    return Path.home()


def architecture():
    if sys.platform != "darwin":
        raise RuntimeError("wiki-flow supports macOS only")
    if shutil.which("sysctl"):
        result = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], text=True, capture_output=True)
        if result.returncode == 0 and result.stdout.strip() == "1":
            return "arm64"
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if machine in {"x86_64", "amd64"}:
        return "x86_64"
    raise RuntimeError("Unsupported macOS architecture: " + machine)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path, data, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".wiki-flow-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temp_name, mode)
        elif path.exists():
            os.chmod(temp_name, path.stat().st_mode & 0o777)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def run(command, cwd=None, timeout=None, env=None):
    result = subprocess.run(command, cwd=cwd, timeout=timeout, env=env, text=True, capture_output=True)
    if result.returncode:
        detail = (result.stderr or result.stdout)[-5000:]
        raise RuntimeError("Command failed: {}\n{}".format(" ".join(map(str, command)), detail))
    return result.stdout.strip()


def brew_prefix(formula=None):
    brew = shutil.which("brew")
    if not brew:
        return None
    args = [brew, "--prefix"] + ([formula] if formula else [])
    result = subprocess.run(args, text=True, capture_output=True)
    return Path(result.stdout.strip()) if result.returncode == 0 and result.stdout.strip() else None


def resolve_runtime(name, formula, minimum_major):
    candidate = shutil.which(name)
    if candidate:
        result = subprocess.run([candidate, "--version"], text=True, capture_output=True)
        version = (result.stdout or result.stderr).strip()
        digits = "".join(ch if ch.isdigit() else " " for ch in version).split()
        minimum = (3, 11) if name == "python3" else (minimum_major,)
        parsed = tuple(map(int, digits[:len(minimum)]))
        if digits and parsed >= minimum:
            return str(Path(candidate).resolve())
    prefix = brew_prefix(formula)
    if prefix:
        for path in (prefix / "bin" / name, prefix / "libexec" / "bin" / name):
            if path.exists():
                return str(path)
    raise RuntimeError("{} {}+ was not found. Run packaging/install.sh to install it with Homebrew.".format(name, "3.11" if name == "python3" else minimum_major))


def workspace_paths(root=None):
    registry = Path(os.environ.get("WIKI_FLOW_CONFIG", app_home() / ".config" / "wiki-flow" / "registry.json")).expanduser().absolute()
    config = {}
    if root is None and registry.is_file():
        config = json.loads(registry.read_text())
        root = config.get("workspaceRoot") or (str(Path(config["aiwikiRoot"]).parent) if config.get("aiwikiRoot") else None)
    root = Path(root or (app_home() / "Documents" / "wiki-flow")).expanduser().absolute()
    return {
        "root": root,
        "aiwiki": Path(config.get("aiwikiRoot", root / "aiwiki")).expanduser().absolute(),
        "valut": Path(config.get("general", {}).get("path", root / "valut")).expanduser().absolute(),
        "data": app_home() / ".local" / "share" / "wiki-flow",
        "config": app_home() / ".config" / "wiki-flow",
        "registry": registry,
        "qmd_config": app_home() / ".config" / "qmd" / "wiki-flow.yml",
        "qmd_db": app_home() / ".cache" / "qmd" / "wiki-flow.sqlite",
        "skill": app_home() / ".agents" / "skills" / "wiki-flow",
        "cli": app_home() / ".local" / "bin" / "wiki-flow",
        "app": Path("/Applications/LLM Wiki.app"),
        "app_state": app_home() / "Library" / "Application Support" / "com.llmwiki.app" / "app-state.json",
    }


def project_id(valut):
    identity = Path(valut) / ".llm-wiki" / "project.json"
    if identity.exists():
        try:
            value = json.loads(identity.read_text())
            if isinstance(value.get("id"), str) and value["id"]:
                return value["id"]
        except (OSError, ValueError, AttributeError):
            pass
    if not Path(valut).is_dir():
        return None
    value = {"id": str(uuid.uuid4()), "createdAt": int(time.time() * 1000)}
    write_json(identity, value)
    return value["id"]


def make_scaffold(paths):
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["aiwiki"].mkdir(parents=True, exist_ok=True)
    valut = paths["valut"]
    for relative in ("raw/sources", "raw/assets", "wiki/entities", "wiki/concepts", "wiki/sources", "wiki/queries", "wiki/comparisons", "wiki/synthesis", ".llm-wiki"):
        (valut / relative).mkdir(parents=True, exist_ok=True)
    seed = {
        "purpose.md": "# Purpose\n\nExternal references, research notes, and reusable cross-project knowledge. Keep project-specific facts in aiwiki.\n",
        "schema.md": "# Wiki Schema\n\n- Preserve source files under `raw/sources/`.\n- Put curated Markdown pages under `wiki/`.\n- Add source URLs or relative file links to every source summary.\n- Keep `wiki/index.md` and `wiki/log.md` current.\n",
        "wiki/index.md": "# Wiki Index\n",
        "wiki/log.md": "# Research Log\n",
        "wiki/overview.md": "---\ntype: overview\ntitle: Knowledge Base Overview\ntags: []\nrelated: []\n---\n\n# Overview\n\n",
    }
    for relative, content in seed.items():
        target = valut / relative
        if not target.exists():
            atomic_write(target, content.encode())
    project_id(valut)


def domains_for(aiwiki):
    domains = []
    for child in sorted(Path(aiwiki).iterdir()) if Path(aiwiki).is_dir() else []:
        if child.is_dir() and not child.name.startswith(".") and (child / "README.md").is_file():
            domains.append({"id": child.name, "root": str(child.resolve()), "entry": "README.md", "mode": "files"})
    return domains


def write_profile(paths, python_bin, node_bin, package_root=None):
    package_root = Path(package_root or paths["data"] / "app")
    node_bin = Path(node_bin).expanduser().absolute()
    qmd_root = paths["data"] / "qmd"
    app_mcp = paths["app"] / APP_MCP_RELATIVE
    config = {
        "version": 1,
        "workspaceRoot": str(paths["root"]),
        "apiBase": "http://127.0.0.1:19828/api/v1",
        "appState": str(paths["app_state"]),
        "node": str(node_bin),
        "mcpEntry": str(app_mcp),
        "general": {"id": project_id(paths["valut"]), "name": "通用知识库", "path": str(paths["valut"])},
        "aiwikiRoot": str(paths["aiwiki"]),
        "domains": domains_for(paths["aiwiki"]),
        "qmd": {
            "node": str(node_bin),
            "entry": str(qmd_root / "node_modules" / "@tobilu" / "qmd" / "dist" / "cli" / "qmd.js"),
            "index": "wiki-flow",
            "database": str(paths["qmd_db"]),
            "config": str(paths["qmd_config"]),
            "sources": ["obsidian", "aiwiki"],
        },
        "generation": {"mode": "client-model-and-app-api", "clients": ["Codex"], "localGeneration": False, "appAutoIngest": None},
        "pdfPython": str(paths["data"] / "parser" / "bin" / "python"),
        "retrieval": {"provider": "qmd", "nativeEmbedding": False, "localEmbedding": "Qwen3-Embedding-0.6B-Q8_0.gguf", "service": "on-demand, no Ollama"},
        "updates": {"repository": WIKI_FLOW_REPOSITORY, "channel": "stable"},
        "platform": architecture(),
        "install": {"version": package_version(package_root), "source": str(package_root)},
    }
    write_json(paths["registry"], config)
    yaml = [
        'global_context: "本机知识分为外部通用资料和项目规范；项目当前事实以 aiwiki 为准。"',
        "models:",
        "  embed: " + json.dumps(QMD_EMBED_MODEL),
        "collections:",
        "  obsidian:",
        "    path: " + json.dumps(str(paths["valut"])),
        '    pattern: "**/*.md"',
        "    ignore:",
        "      - raw/**",
        "      - .llm-wiki/**",
        "      - .obsidian/**",
        '      - "**/archive/**"',
        '      - "**/drafts/**"',
        "    context:",
        "      /: 通用资料与跨项目经验；项目规范在 aiwiki。",
        "  aiwiki:",
        "    path: " + json.dumps(str(paths["aiwiki"])),
        '    pattern: "**/*.md"',
        "    ignore:",
        '      - "**/archive/**"',
        '      - "**/drafts/**"',
        '      - "**/.obsidian/**"',
        "    context:",
        "      /: 项目当前设计、接口、约束与验证记录；遵守项目 README 与 AGENTS。",
        "",
    ]
    atomic_write(paths["qmd_config"], ("\n".join(yaml)).encode())
    return config


def package_version(root):
    try:
        return (Path(root) / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def copy_package(source, destination):
    source = Path(source).resolve()
    destination = Path(destination)
    temp = destination.with_name(destination.name + ".installing-" + str(os.getpid()))
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    for name in ("VERSION", "README.md", "bin", "lib", "skills", "packaging"):
        item = source / name
        if not item.exists():
            continue
        target = temp / name
        if item.is_dir():
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        else:
            shutil.copy2(item, target)
    if not (temp / "VERSION").exists() or not (temp / "bin" / "wiki-flow").exists():
        shutil.rmtree(temp, ignore_errors=True)
        raise RuntimeError("Source package is missing VERSION or bin/wiki-flow")
    backup = destination.with_name(destination.name + ".backup-" + dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    moved_old = False
    try:
        if destination.exists():
            os.replace(destination, backup)
            moved_old = True
        os.replace(temp, destination)
    except OSError:
        if moved_old and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def install_launcher(paths, python_bin):
    paths["cli"].parent.mkdir(parents=True, exist_ok=True)
    app = paths["data"] / "app"
    script = "#!/bin/sh\nexec {} {} \"$@\"\n".format(shlex.quote(str(python_bin)), shlex.quote(str(app / "bin" / "wiki-flow")))
    atomic_write(paths["cli"], script.encode(), 0o755)
    manager = paths["data"] / "portable.py"
    source_manager = app / "packaging" / "portable.py"
    atomic_write(manager, source_manager.read_bytes(), 0o755)


def install_skill(paths, source_root):
    source = Path(source_root) / "skills" / "wiki-flow"
    if not source.is_dir():
        raise RuntimeError("wiki-flow Skill directory is missing: " + str(source))
    targets = [paths["skill"]]
    if (app_home() / ".workbuddy").is_dir():
        targets.append(app_home() / ".workbuddy/skills/wiki-flow")
    for target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            resolved = target.resolve(strict=False)
            if "wiki-flow" not in resolved.parts:
                raise RuntimeError("Refusing to replace unrelated Skill symlink: " + str(target))
            target.unlink()
        if target.exists():
            backup = paths["config"] / "backups" / ("skill-" + target.parent.parent.name + "-" + uuid.uuid4().hex)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup, symlinks=False)
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"), symlinks=False)
        write_json(target / ".wiki-flow-managed.json", {"version": package_version(source_root), "source": "wiki-flow-kit"})


def install_qmd(paths, node_bin):
    npm = str(Path(node_bin).parent / "npm")
    if not Path(npm).exists():
        raise RuntimeError("npm was not found beside Node.js")
    qmd_root = paths["data"] / "qmd"
    qmd_root.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": str(Path(node_bin).parent) + os.pathsep + os.environ.get("PATH", "")}
    run([npm, "install", "--prefix", str(qmd_root), "--no-audit", "--no-fund", "@tobilu/qmd@" + QMD_VERSION], timeout=1800, env=env)


def install_pdf_runtime(paths, python_bin):
    venv = paths["data"] / "parser"
    executable = venv / "bin" / "python"
    if not executable.exists():
        run([python_bin, "-m", "venv", str(venv)], timeout=300)
    run([str(executable), "-m", "pip", "install", "--disable-pip-version-check", "pypdf>=5,<7"], timeout=900)


def github_json(url):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "wiki-flow-kit"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def download_asset(url, destination, expected_digest=None):
    request = urllib.request.Request(url, headers={"User-Agent": "wiki-flow-kit"})
    digest = hashlib.sha256()
    destination = Path(destination)
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            output.write(chunk)
    actual = digest.hexdigest()
    if expected_digest and expected_digest.startswith("sha256:") and actual != expected_digest.split(":", 1)[1]:
        destination.unlink(missing_ok=True)
        raise RuntimeError("Downloaded asset SHA-256 does not match GitHub release metadata")
    return actual


def release_dmg_asset(release, arch):
    if arch == "arm64":
        suffixes = ("_aarch64.dmg",)
    elif arch == "x86_64":
        suffixes = ("_x86_64.dmg", "_x64.dmg")
    else:
        raise RuntimeError("Unsupported macOS architecture: " + arch)
    return next((item for item in release.get("assets", []) if item["name"].lower().endswith(suffixes)), None)


def install_dmg_app(paths, asset, arch):
    with tempfile.TemporaryDirectory(prefix="wiki-flow-app-") as temp:
        dmg = Path(temp) / asset["name"]
        download_asset(asset["browser_download_url"], dmg, asset.get("digest"))
        mount = Path(temp) / "mount"
        mount.mkdir()
        run(["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-mountpoint", str(mount), "-quiet"], timeout=180)
        try:
            app_source = next(mount.glob("*.app"), None)
            if app_source is None:
                raise RuntimeError("LLM Wiki DMG did not contain an app bundle")
            run(["codesign", "--verify", "--deep", "--strict", str(app_source)], timeout=120)
            run(["spctl", "--assess", "--type", "execute", "--verbose=2", str(app_source)], timeout=120)
            executable = app_source / "Contents" / "MacOS" / "llm-wiki"
            architectures = run(["lipo", "-archs", str(executable)], timeout=30).split()
            expected = "arm64" if arch == "arm64" else "x86_64"
            if expected not in architectures:
                raise RuntimeError("LLM Wiki DMG does not contain the native {} executable".format(expected))
            if not (app_source / APP_MCP_RELATIVE).is_file():
                raise RuntimeError("LLM Wiki DMG is missing its MCP server resources")
            staging = paths["app"].with_name("LLM Wiki.installing.app")
            if staging.exists():
                shutil.rmtree(staging)
            run(["ditto", str(app_source), str(staging)], timeout=600)
            if paths["app"].exists():
                raise RuntimeError("LLM Wiki.app already exists; use its About screen to update it")
            os.replace(staging, paths["app"])
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], text=True, capture_output=True)


def prepare_intel_pdfium(source):
    source = Path(source)
    pdfium = source / "src-tauri" / "pdfium"
    checksum_file = pdfium / "SHA256SUMS"
    entries = []
    for line in checksum_file.read_text().splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or len(fields[0]) != 64 or any(char not in "0123456789abcdefABCDEF" for char in fields[0]):
            raise RuntimeError("Invalid PDFium checksum entry: " + line)
        relative = fields[1].lstrip(" *")
        if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise RuntimeError("Unsafe PDFium checksum path: " + relative)
        library_path = source / relative
        if not library_path.is_file() or sha256_file(library_path) != fields[0].lower():
            raise RuntimeError("PDFium checksum verification failed: " + relative)
        entries.append(relative)
    if "src-tauri/pdfium/libpdfium-x86_64.dylib" not in entries:
        raise RuntimeError("PDFium checksum list does not cover Intel macOS library")
    library = pdfium / "libpdfium-x86_64.dylib"
    if not library.is_file():
        raise RuntimeError("Pinned LLM Wiki source does not include Intel PDFium")
    shutil.copy2(library, pdfium / "libpdfium.dylib")


def install_intel_app(paths, release, node_bin):
    if not shutil.which("git"):
        raise RuntimeError("Intel LLM Wiki build requires Git")
    if not shutil.which("cargo") or not shutil.which("rustc"):
        raise RuntimeError("Intel LLM Wiki build requires Rust. Install Xcode Command Line Tools and Rust, then rerun install.")
    if not shutil.which("protoc"):
        raise RuntimeError("Intel LLM Wiki build requires protobuf (`brew install protobuf`)")
    tag = release.get("tag_name")
    if not tag:
        raise RuntimeError("LLM Wiki release metadata has no tag")
    with tempfile.TemporaryDirectory(prefix="wiki-flow-llm-wiki-") as temp:
        source = Path(temp) / "llm_wiki"
        run(["git", "clone", "--depth", "1", "--branch", tag, "https://github.com/" + APP_REPOSITORY + ".git", str(source)], timeout=1800)
        version = json.loads((source / "package.json").read_text())["version"]
        if tag.lstrip("v") != version:
            raise RuntimeError("LLM Wiki source tag and package version do not match")
        prepare_intel_pdfium(source)
        run(["npm", "ci", "--no-audit", "--no-fund"], cwd=source, timeout=2400)
        run(["npm", "--prefix", "mcp-server", "ci", "--no-audit", "--no-fund"], cwd=source, timeout=1800)
        run(["npm", "run", "mcp:build"], cwd=source, timeout=900)
        env = {**os.environ, "PATH": str(Path(node_bin).parent) + os.pathsep + os.environ.get("PATH", "")}
        run(["npm", "run", "tauri", "--", "build", "--target", "x86_64-apple-darwin", "--bundles", "app"], cwd=source, timeout=7200, env=env)
        app_candidates = list((source / "src-tauri" / "target" / "x86_64-apple-darwin" / "release" / "bundle" / "macos").glob("*.app"))
        if not app_candidates:
            raise RuntimeError("Intel LLM Wiki build completed without producing an .app bundle")
        app_source = app_candidates[0]
        executable = app_source / "Contents" / "MacOS" / "llm-wiki"
        file_info = run(["file", str(executable)])
        if "x86_64" not in file_info:
            raise RuntimeError("Built LLM Wiki executable is not Intel x86_64")
        if not (app_source / APP_MCP_RELATIVE).is_file() or not (app_source / "Contents/Frameworks/libpdfium.dylib").is_file():
            raise RuntimeError("Built LLM Wiki app is missing its MCP or PDFium resources")
        run(["codesign", "--force", "--deep", "--sign", "-", str(app_source)], timeout=300)
        staging = paths["app"].with_name("LLM Wiki.installing.app")
        run(["ditto", str(app_source), str(staging)], timeout=600)
        if paths["app"].exists():
            raise RuntimeError("LLM Wiki.app already exists; use its About screen to update it")
        os.replace(staging, paths["app"])


def ensure_app(paths, node_bin, no_app=False):
    if no_app:
        return {"installed": False, "skipped": True}
    if paths["app"].exists():
        return {"installed": False, "existing": str(paths["app"])}
    release = github_json("https://api.github.com/repos/{}/releases/latest".format(APP_REPOSITORY))
    arch = architecture()
    asset = release_dmg_asset(release, arch)
    if asset:
        install_dmg_app(paths, asset, arch)
        method = "verified upstream {} DMG".format("Apple Silicon" if arch == "arm64" else "Intel")
    elif arch == "arm64":
        raise RuntimeError("The latest LLM Wiki release has no Apple Silicon DMG")
    else:
        install_intel_app(paths, release, node_bin)
        method = "local build from upstream release tag " + release.get("tag_name", "unknown")
    return {"installed": True, "architecture": arch, "method": method}


def install_codex_mcp(paths):
    codex = shutil.which("codex")
    if not codex:
        return {"client": "codex", "status": "not-installed"}
    result = subprocess.run([codex, "mcp", "add", "llm-wiki", "--", str(paths["cli"]), "mcp"], text=True, capture_output=True)
    if result.returncode:
        message = (result.stderr or result.stdout).strip()[-1500:]
        if "already exists" in message.lower():
            return {"client": "codex", "status": "already-configured"}
        raise RuntimeError("Could not configure Codex MCP: " + message)
    return {"client": "codex", "status": "configured"}


def install_workbuddy_mcp(paths):
    config = app_home() / ".workbuddy" / "mcp.json"
    if not config.parent.exists():
        return {"client": "workbuddy", "status": "not-installed"}
    value = {}
    if config.exists():
        try:
            value = json.loads(config.read_text())
        except ValueError as error:
            raise RuntimeError("Invalid WorkBuddy MCP JSON; preserved without changes: " + str(error))
    servers = value.setdefault("mcpServers", {})
    if "llm-wiki" in servers and servers["llm-wiki"].get("command") not in {None, str(paths["cli"])}:
        raise RuntimeError("WorkBuddy already has a different llm-wiki MCP entry; inspect " + str(config))
    servers["llm-wiki"] = {"command": str(paths["cli"]), "args": ["mcp"], "disabled": False}
    write_json(config, value)
    return {"client": "workbuddy", "status": "configured"}


def install_command(args):
    arch = architecture()
    paths = workspace_paths(args.workspace)
    python_bin = args.python or resolve_runtime("python3", "python@3.12", 3)
    node_bin = args.node or resolve_runtime("node", "node@22", 22)
    source = Path(args.source_root).expanduser().resolve()
    app_root = paths["data"] / "app"
    copy_package(source, app_root)
    paths["data"].mkdir(parents=True, exist_ok=True)
    install_launcher(paths, python_bin)
    paths["root"].mkdir(parents=True, exist_ok=True)
    install_qmd(paths, node_bin)
    install_pdf_runtime(paths, python_bin)
    config = write_profile(paths, python_bin, node_bin, app_root)
    install_skill(paths, app_root)
    app_result = ensure_app(paths, node_bin, args.no_app)
    clients = [install_codex_mcp(paths), install_workbuddy_mcp(paths)]
    receipt = {
        "schemaVersion": 1,
        "installedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "version": package_version(app_root),
        "architecture": arch,
        "workspace": str(paths["root"]),
        "aiwiki": str(paths["aiwiki"]),
        "valut": str(paths["valut"]),
        "llmWiki": app_result,
        "clients": clients,
        "apiCredentials": "not configured; enter credentials in LLM Wiki Settings",
    }
    write_json(paths["data"] / "install-receipt.json", receipt)
    write_profile(paths, python_bin, node_bin, app_root)
    print(json.dumps({"ok": True, "receipt": receipt, "next": ["run wiki-flow init, or restore a migration bundle while the workspace is empty", "open LLM Wiki and the valut folder once to register its project ID", "configure the model provider/API key and local API in LLM Wiki Settings", "run wiki-flow doctor, then preview with wiki-flow sync --dry-run"]}, ensure_ascii=False, indent=2))
    return 0


def apply_package_update(paths, source_root):
    app_root = paths["data"] / "app"
    python_bin = resolve_runtime("python3", "python@3.12", 3)
    registry = paths["registry"]
    config = json.loads(registry.read_text()) if registry.is_file() else None
    paths["data"].mkdir(parents=True, exist_ok=True)
    copy_package(source_root, app_root)
    install_launcher(paths, python_bin)
    install_skill(paths, app_root)

    if config is not None:
        config.setdefault("install", {}).update({"version": package_version(app_root), "source": str(app_root)})
        write_json(registry, config)

    receipt_path = paths["data"] / "install-receipt.json"
    try:
        receipt = json.loads(receipt_path.read_text())
    except (OSError, ValueError):
        receipt = {"schemaVersion": 1}
    receipt.update({"version": package_version(app_root), "updatedAt": dt.datetime.now(dt.timezone.utc).isoformat()})
    write_json(receipt_path, receipt)
    return 0


def safe_walk(root, include_llm_metadata=False):
    root = Path(root)
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not (Path(current) / name).is_symlink() and name not in SKIP_DIRS and (not name.startswith(".") or (include_llm_metadata and name == ".llm-wiki")))
        for name in sorted(files):
            path = Path(current) / name
            if path.is_symlink() or name == ".DS_Store" or (name.startswith(".") and not (include_llm_metadata and name == "project.json")):
                continue
            yield path


def bundle_files(root, bundle_root, label):
    selected = []
    total = 0
    for source in safe_walk(root, include_llm_metadata=(label == "valut")):
        relative = source.relative_to(root)
        if label == "valut" and ".llm-wiki" in relative.parts:
            if relative.as_posix() != ".llm-wiki/project.json" and not relative.as_posix().startswith(".llm-wiki/source-provenance/"):
                continue
        data_size = source.stat().st_size
        total += data_size
        if total > MAX_BUNDLE_BYTES:
            raise RuntimeError("Knowledge bundle exceeds the 10 GiB safety limit")
        target = bundle_root / label / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        selected.append({"path": (Path(label) / relative).as_posix(), "sha256": sha256_file(target), "size": data_size})
    return selected


def create_bundle(args):
    paths = workspace_paths(args.workspace)
    if not paths["aiwiki"].is_dir() or not paths["valut"].is_dir():
        raise RuntimeError("Both aiwiki and valut directories must exist")
    destination = Path(args.output).expanduser().absolute()
    if destination.exists():
        raise RuntimeError("Bundle destination already exists: " + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wiki-flow-bundle-") as temporary:
        stage = Path(temporary)
        files = bundle_files(paths["aiwiki"], stage, "aiwiki") + bundle_files(paths["valut"], stage, "valut")
        manifest = {"schemaVersion": 1, "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(), "architecture": architecture(), "sourceRoots": {"aiwiki": str(paths["aiwiki"]), "valut": str(paths["valut"])}, "files": files, "excluded": ["Git history", "LLM Wiki API credentials", "conversation history", "ingest queues", "qmd database and model cache", "machine runtime configuration"]}
        write_json(stage / "manifest.json", manifest)
        partial = destination.with_name(destination.name + ".partial")
        partial.unlink(missing_ok=True)
        with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file in sorted(stage.rglob("*")):
                if file.is_file():
                    archive.write(file, file.relative_to(stage).as_posix())
        os.replace(partial, destination)
    print(json.dumps({"ok": True, "bundle": str(destination), "files": len(files), "sha256": sha256_file(destination)}, ensure_ascii=False, indent=2))
    return 0


def remap_workspace_instructions(stage, source_roots, paths):
    """Rebase active instructions; keep source snapshots and provenance unchanged."""
    candidates = [file for file in (stage / "aiwiki").rglob("AGENTS.md") if not {"archive", "archives", "snapshots", "raw", "drafts"}.intersection(file.relative_to(stage).parts)]
    candidates += [stage / "valut" / name for name in ("AGENTS.md", "purpose.md", "schema.md")]
    changed = []
    for file in candidates:
        if not file.is_file():
            continue
        before = file.read_text()
        after = before
        for label in ("aiwiki", "valut"):
            original = source_roots.get(label)
            if isinstance(original, str) and Path(original).is_absolute() and original != "/":
                after = after.replace(original.rstrip("/"), str(paths[label]))
        if before != after:
            file.write_text(after)
            changed.append(file.relative_to(stage).as_posix())
    return changed


def restore_bundle(args):
    paths = workspace_paths(args.workspace)
    bundle = Path(args.bundle).expanduser().resolve(strict=True)
    root = paths["root"]
    root.mkdir(parents=True, exist_ok=True)
    entries = list(root.iterdir())
    if entries:
        raise RuntimeError("Restore target must be empty; preserved existing entries: " + ", ".join(item.name for item in entries[:12]))
    with tempfile.TemporaryDirectory(prefix="wiki-flow-restore-") as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            expanded = sum(item.file_size for item in infos)
            if expanded > MAX_BUNDLE_BYTES or len(infos) > 200_000:
                raise RuntimeError("Bundle exceeds restore size or file-count limits")
            for item in infos:
                rel = PurePosixPath(item.filename)
                if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                    raise RuntimeError("Unsafe bundle path: " + item.filename)
                if item.external_attr >> 16 & 0o170000 == 0o120000:
                    raise RuntimeError("Symbolic links are not allowed in migration bundles")
            archive.extractall(stage)
        manifest_path = stage / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        if manifest.get("schemaVersion") != 1 or not isinstance(manifest.get("files"), list):
            raise RuntimeError("Unsupported or malformed bundle manifest")
        expected = {item["path"]: item["sha256"] for item in manifest["files"]}
        actual = {file.relative_to(stage).as_posix() for file in stage.rglob("*") if file.is_file() and file != manifest_path}
        if actual != set(expected):
            raise RuntimeError("Bundle contents do not match manifest file list")
        for relative, checksum in expected.items():
            if sha256_file(stage / relative) != checksum:
                raise RuntimeError("SHA-256 mismatch: " + relative)
        for label in ("aiwiki", "valut"):
            source = stage / label
            if not source.is_dir():
                raise RuntimeError("Bundle is missing " + label)
        python_bin = resolve_runtime("python3", "python@3.12", 3)
        node_bin = resolve_runtime("node", "node@22", 22)
        remapped = remap_workspace_instructions(stage, manifest.get("sourceRoots", {}), paths)
        staging = root / (".restore-staging-" + str(os.getpid()))
        moved = []
        try:
            staging.mkdir()
            for label in ("aiwiki", "valut"):
                shutil.copytree(stage / label, staging / label, symlinks=False)
            for label in ("aiwiki", "valut"):
                os.replace(staging / label, root / label)
                moved.append(root / label)
        except OSError:
            for directory in moved:
                shutil.rmtree(directory)
            raise
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    config = write_profile(paths, python_bin, node_bin)
    result = {"ok": True, "workspace": str(root), "files": len(manifest["files"]), "projectId": config["general"]["id"], "remappedInstructions": remapped, "qmdRebuild": "run wiki-flow refresh --vectors"}
    write_json(paths["config"] / "restore-receipt.json", {**result, "bundleSha256": sha256_file(bundle), "restoredAt": dt.datetime.now(dt.timezone.utc).isoformat()})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def current_app_token(paths):
    try:
        state = json.loads(paths["app_state"].read_text())
        return state.get("apiConfig", {}).get("token")
    except (OSError, ValueError, AttributeError):
        return None


def app_api(paths, suffix, method="GET"):
    token = current_app_token(paths)
    if not token:
        return {"available": False, "reason": "LLM Wiki API token is not configured"}
    config = json.loads(paths["registry"].read_text()) if paths["registry"].is_file() else {}
    request = urllib.request.Request(
        config.get("apiBase", "http://127.0.0.1:19828/api/v1").rstrip("/") + suffix,
        data=b"{}" if method == "POST" else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return {"available": True, "response": json.load(response)}
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
        return {"available": False, "reason": str(error)}


def api_post(paths, project_id, route):
    return app_api(paths, "/projects/{}/{}".format(project_id, route), "POST")


def sync_plan(paths):
    source = paths["aiwiki"]
    target = paths["valut"] / "raw" / "sources" / "aiwiki"
    state_path = paths["valut"] / ".llm-wiki" / "wiki-flow-sync.json"
    try:
        old_state = json.loads(state_path.read_text()).get("files", {})
    except (OSError, ValueError, AttributeError):
        old_state = {}
    current = {}
    plan = []
    for file in safe_walk(source):
        if file.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        relative = file.relative_to(source).as_posix()
        current[relative] = sha256_file(file)
        dest = target / relative
        unsafe = dest.is_symlink() or any(parent.is_symlink() for parent in dest.parents if parent.is_relative_to(paths["valut"]))
        existing = sha256_file(dest) if not unsafe and dest.is_file() else None
        previous = old_state.get(relative)
        if unsafe or (dest.exists() and not dest.is_file()):
            status = "conflict"
        elif existing == current[relative]:
            status = "unchanged"
        elif existing is not None and existing != previous:
            status = "conflict"
        else:
            status = "create" if existing is None else "update"
        plan.append({"path": relative, "status": status, "sha256": current[relative], "destinationSha256": existing})
    stale = sorted(set(old_state) - set(current))
    return target, state_path, current, plan, stale, old_state


def ingest_queue_active(valut):
    for name in ("ingest-queue.json", "file-change-queue.json"):
        queue = Path(valut) / ".llm-wiki" / name
        if not queue.is_file():
            continue
        try:
            value = json.loads(queue.read_text())
            tasks = value if isinstance(value, list) else value.get("tasks", [])
            if any(item.get("status") in {"pending", "processing"} for item in tasks):
                return True
        except (OSError, ValueError, AttributeError):
            return True
    return False


@contextlib.contextmanager
def sync_lock(paths, apply):
    if not apply:
        yield
        return
    lock = paths["config"] / "sync.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def sync_command(args):
    paths = workspace_paths(args.workspace)
    if not paths["aiwiki"].is_dir() or not paths["valut"].is_dir():
        raise RuntimeError("aiwiki and valut must exist; run wiki-flow init or restore a bundle first")
    with sync_lock(paths, args.apply):
        return perform_sync(paths, args.apply)


def perform_sync(paths, apply):
    target, state_path, current, plan, stale, old_state = sync_plan(paths)
    if state_path.is_symlink() or (paths["valut"] / ".llm-wiki").is_symlink():
        raise RuntimeError("Refusing symbolic links in LLM Wiki sync metadata")
    conflicts = [item["path"] for item in plan if item["status"] == "conflict"]
    changed = [item for item in plan if item["status"] in {"create", "update"}]
    result = {"dryRun": not apply, "create": sum(item["status"] == "create" for item in plan), "update": sum(item["status"] == "update" for item in plan), "unchanged": sum(item["status"] == "unchanged" for item in plan), "conflicts": conflicts, "sourceRemoved": stale, "deleted": 0}
    if apply:
        try:
            old_manifest = json.loads(state_path.read_text()) if state_path.exists() else {}
        except (OSError, ValueError):
            old_manifest = {}
        write_json(state_path, {
            "schemaVersion": 1,
            "files": old_state,
            "rescanPending": bool(changed) or old_manifest.get("rescanPending", False),
            "refreshPending": bool(changed) or old_manifest.get("refreshPending", False),
        })
        for item in changed:
            rel = PurePosixPath(item["path"])
            source = paths["aiwiki"].joinpath(*rel.parts)
            dest = target.joinpath(*rel.parts)
            data = source.read_bytes()
            actual = sha256_file(dest) if dest.is_file() and not dest.is_symlink() else None
            unsafe = dest.is_symlink() or any(parent.is_symlink() for parent in dest.parents if parent.is_relative_to(paths["valut"]))
            if unsafe or actual != item["destinationSha256"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise RuntimeError("Source or destination changed during sync; rerun after reviewing: " + item["path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(dest, data)
        previous = dict(old_state)
        for item in plan:
            if item["status"] != "conflict":
                previous[item["path"]] = current[item["path"]]
        try:
            old_manifest = json.loads(state_path.read_text()) if state_path.exists() else {}
        except (OSError, ValueError):
            old_manifest = {}
        state = {
            "schemaVersion": 1,
            "syncedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "files": previous,
            "rescanPending": bool(changed) or old_manifest.get("rescanPending", False),
            "refreshPending": bool(changed) or old_manifest.get("refreshPending", False),
        }
        if state["rescanPending"]:
            config = json.loads(paths["registry"].read_text()) if paths["registry"].exists() else None
            matches_profile = bool(config and Path(config["general"]["path"]).resolve() == paths["valut"].resolve())
            project = config["general"].get("id") if matches_profile else None
            api_result = api_post(paths, project, "sources/rescan") if project else {"available": False, "reason": "LLM Wiki project is not registered yet"}
            result["appRescan"] = api_result
            state["rescanPending"] = not api_result.get("available", False)
        if state["refreshPending"]:
            if state["rescanPending"] or ingest_queue_active(paths["valut"]):
                result["qmdRefresh"] = "deferred: app rescan or ingestion is pending; rerun wiki-flow sync --apply after the app queue is idle"
            else:
                cli = paths["data"] / "app" / "bin" / "wiki-flow"
                try:
                    result["qmdRefresh"] = run([sys.executable, str(cli), "refresh", "--vectors"], timeout=1800) if cli.exists() else "pending: run wiki-flow refresh --vectors"
                    state["refreshPending"] = not cli.exists()
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                    result["qmdRefresh"] = {"ok": False, "error": str(error), "retry": "wiki-flow sync --apply"}
                    state["refreshPending"] = True
        write_json(state_path, state)
    result["files"] = [{"path": item["path"], "status": item["status"]} for item in plan if item["status"] != "unchanged"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if conflicts else 0


def create_launch_agent(paths):
    label = "com.wiki-flow.sync"
    directory = app_home() / "Library" / "LaunchAgents"
    plist = directory / (label + ".plist")
    directory.mkdir(parents=True, exist_ok=True)
    python_bin = resolve_runtime("python3", "python@3.12", 3)
    manager = paths["data"] / "portable.py"
    if not manager.is_file():
        raise RuntimeError("Install wiki-flow-kit before enabling scheduled sync")
    value = {
        "Label": label,
        "ProgramArguments": [python_bin, str(manager), "sync", "--apply"],
        "StartCalendarInterval": {"Hour": 3, "Minute": 0},
        "RunAtLoad": False,
        "EnvironmentVariables": {"WIKI_FLOW_CONFIG": str(paths["registry"])},
        "StandardOutPath": str(paths["data"] / "sync.log"),
        "StandardErrorPath": str(paths["data"] / "sync.error.log"),
    }
    atomic_write(plist, plistlib.dumps(value), 0o600)
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", "gui/{}".format(uid), str(plist)], text=True, capture_output=True)
    run(["launchctl", "bootstrap", "gui/{}".format(uid), str(plist)], timeout=30)
    print(json.dumps({"ok": True, "launchAgent": str(plist), "schedule": "daily at 03:00"}, ensure_ascii=False, indent=2))
    return 0


def disable_launch_agent(paths):
    plist = app_home() / "Library" / "LaunchAgents" / "com.wiki-flow.sync.plist"
    if plist.exists():
        subprocess.run(["launchctl", "bootout", "gui/{}".format(os.getuid()), str(plist)], text=True, capture_output=True)
        plist.unlink()
    print(json.dumps({"ok": True, "scheduledSync": False}, ensure_ascii=False, indent=2))
    return 0


def doctor(args):
    paths = workspace_paths(args.workspace)
    arch = architecture()
    result = {
        "ok": True,
        "platform": "macOS",
        "architecture": arch,
        "supported": arch in {"arm64", "x86_64"},
        "workspace": str(paths["root"]),
        "checks": {
            "python": sys.version.split()[0],
            "node": "missing",
            "qmd": "missing",
            "profile": paths["registry"].is_file(),
            "skill": (paths["skill"] / "SKILL.md").is_file(),
            "skillMode": "source-symlink" if paths["skill"].is_symlink() else "installed-copy",
            "app": paths["app"].is_dir(),
            "aiwiki": paths["aiwiki"].is_dir(),
            "valut": paths["valut"].is_dir(),
            "appApiTokenConfigured": bool(current_app_token(paths)),
        },
    }
    if paths["registry"].is_file():
        try:
            config = json.loads(paths["registry"].read_text())
            node = config["node"]
            result["checks"]["node"] = run([node, "--version"], timeout=10)
            result["checks"]["nodeArchitecture"] = run([node, "-p", "process.arch"], timeout=10)
            result["checks"]["nativeNode"] = result["checks"]["nodeArchitecture"] == ("arm64" if arch == "arm64" else "x64")
            entry = Path(config["qmd"]["entry"])
            result["checks"]["qmd"] = entry.is_file()
            result["checks"]["qmdDatabase"] = Path(config["qmd"]["database"]).is_file()
            result["checks"]["domainCount"] = len(config.get("domains", []))
            if entry.is_file():
                result["checks"]["qmdRuntime"] = bool(run([node, str(entry), "--index", config["qmd"]["index"], "status"], timeout=60))
            api = app_api(paths, "/projects")
            result["api"] = api
            result["checks"]["appApi"] = api["available"]
            projects = api.get("response", {}).get("projects", [])
            result["checks"]["registeredProject"] = any(
                project.get("id") == config["general"]["id"] and Path(project.get("path", "")).resolve() == paths["valut"].resolve()
                for project in projects
            )
            result["checks"]["mcpEntry"] = Path(config["mcpEntry"]).is_file()
            executable = paths["app"] / "Contents/MacOS/llm-wiki"
            app_architectures = run(["lipo", "-archs", str(executable)], timeout=30).split() if executable.is_file() else []
            expected_arch = "arm64" if arch == "arm64" else "x86_64"
            result["checks"]["appArchitecture"] = app_architectures
            result["checks"]["nativeApp"] = expected_arch in app_architectures
        except (KeyError, ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            result["checks"]["profileError"] = str(error)
    result["ok"] = "profileError" not in result["checks"] and all(value is not False and value != "missing" for value in result["checks"].values() if isinstance(value, (bool, str)))
    result["pending"] = []
    if not result["checks"]["appApiTokenConfigured"]:
        result["pending"].append("Open LLM Wiki and enable its local API in Settings")
    if not result["checks"].get("registeredProject"):
        result["pending"].append("Open LLM Wiki and the valut project once to register the local project")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


def update_command(args):
    paths = workspace_paths(args.workspace)
    config = json.loads(paths["registry"].read_text()) if paths["registry"].is_file() else {}
    repository = args.repository or config.get("updates", {}).get("repository") or WIKI_FLOW_REPOSITORY
    script = Path(__file__).resolve()
    current = script.parent.parent if script.parent.name == "packaging" else script.parent / "app"
    local_version = package_version(current)
    try:
        release = github_json("https://api.github.com/repos/{}/releases/latest".format(repository))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
        raise RuntimeError("Could not read wiki-flow releases for {}: {}. The release repository must be published first.".format(repository, error))
    remote = release.get("tag_name", "").removeprefix("v")
    def version_tuple(value):
        parts = value.split(".")
        if len(parts) != 3 or any(not part.isdigit() for part in parts):
            raise RuntimeError("Expected a stable X.Y.Z version: " + value)
        return tuple(map(int, parts))
    if version_tuple(remote) <= version_tuple(local_version):
        print(json.dumps({"ok": True, "status": "up-to-date", "version": local_version, "repository": repository}, ensure_ascii=False, indent=2))
        return 0
    if not args.apply:
        print(json.dumps({"ok": True, "status": "update-available", "current": local_version, "latest": remote, "repository": repository}, ensure_ascii=False, indent=2))
        return 0
    expected_prefix = "wiki-flow-kit-v{}".format(remote)
    asset = next((item for item in release.get("assets", []) if item["name"] == expected_prefix + ".zip"), None)
    if not asset:
        raise RuntimeError("Release does not contain a versioned wiki-flow-kit ZIP asset")
    digest = asset.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise RuntimeError("Release ZIP is missing a verifiable SHA-256 digest")
    with tempfile.TemporaryDirectory(prefix="wiki-flow-update-") as temporary:
        archive_path = Path(temporary) / asset["name"]
        download_asset(asset["browser_download_url"], archive_path, digest)
        stage = Path(temporary) / "source"
        stage.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            names = set()
            for item in archive.infolist():
                relative = PurePosixPath(item.filename)
                if relative.is_absolute() or ".." in relative.parts or item.external_attr >> 16 & 0o170000 == 0o120000 or item.filename in names:
                    raise RuntimeError("Unsafe path in update archive")
                names.add(item.filename)
                if relative.parts and relative.parts[0] != "wiki-flow":
                    raise RuntimeError("Update archive has unexpected top-level paths")
            archive.extractall(stage)
        source = stage / "wiki-flow"
        if not (source / "VERSION").is_file() or (source / "VERSION").read_text().strip() != remote:
            raise RuntimeError("Update version does not match the release metadata")
        apply_package_update(paths, source)
        print(json.dumps({"ok": True, "status": "updated", "previous": local_version, "version": remote, "repository": repository}, ensure_ascii=False, indent=2))
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init", help="create empty aiwiki and valut knowledge roots")
    initialize.add_argument("--workspace")
    initialize.add_argument("--python")
    initialize.add_argument("--node")
    install = sub.add_parser("install", help="install tools, Skill, qmd, and LLM Wiki")
    install.add_argument("--source-root", required=True)
    install.add_argument("--workspace")
    install.add_argument("--python")
    install.add_argument("--node")
    install.add_argument("--no-app", action="store_true")
    bundle = sub.add_parser("bundle", help="create or restore a portable knowledge bundle")
    bundle_sub = bundle.add_subparsers(dest="bundle_action", required=True)
    create = bundle_sub.add_parser("create")
    create.add_argument("--workspace")
    create.add_argument("--output", required=True)
    restore = bundle_sub.add_parser("restore")
    restore.add_argument("--workspace")
    restore.add_argument("--bundle", required=True)
    doctor_parser = sub.add_parser("doctor", help="diagnose local installation and integration")
    doctor_parser.add_argument("--workspace")
    sync = sub.add_parser("sync", help="incrementally mirror aiwiki files into LLM Wiki")
    sync.add_argument("--workspace")
    sync_mode = sync.add_mutually_exclusive_group()
    sync_mode.add_argument("--apply", action="store_true")
    sync_mode.add_argument("--dry-run", action="store_true")
    update = sub.add_parser("update", help="check or apply a wiki-flow release")
    update.add_argument("--workspace")
    update.add_argument("--repository")
    update_mode = update.add_mutually_exclusive_group()
    update_mode.add_argument("--apply", action="store_true")
    update_mode.add_argument("--check", action="store_true")
    schedule = sub.add_parser("schedule", help="enable or disable daily aiwiki synchronization")
    schedule.add_argument("action", choices=["enable", "disable"])
    schedule.add_argument("--workspace")
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            return install_command(args)
        if args.command == "init":
            paths = workspace_paths(args.workspace)
            make_scaffold(paths)
            python_bin = args.python or resolve_runtime("python3", "python@3.12", 3)
            node_bin = args.node or resolve_runtime("node", "node@22", 22)
            write_profile(paths, python_bin, node_bin)
            print(json.dumps({"ok": True, "workspace": str(paths["root"]), "projectId": project_id(paths["valut"]), "next": "run wiki-flow doctor, then open the valut folder in LLM Wiki once"}, ensure_ascii=False, indent=2))
            return 0
        if args.command == "bundle":
            return create_bundle(args) if args.bundle_action == "create" else restore_bundle(args)
        if args.command == "doctor":
            return doctor(args)
        if args.command == "sync":
            return sync_command(args)
        if args.command == "update":
            return update_command(args)
        if args.command == "schedule":
            return create_launch_agent(workspace_paths(args.workspace)) if args.action == "enable" else disable_launch_agent(workspace_paths(args.workspace))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired, urllib.error.URLError, zipfile.BadZipFile) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
