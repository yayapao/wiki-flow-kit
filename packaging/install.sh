#!/bin/bash
set -euo pipefail

source_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "wiki-flow supports macOS only." >&2
  exit 2
fi

native_arch="$(uname -m)"
if [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" == "1" ]]; then
  native_arch=arm64
  if [[ "$(uname -m)" != "arm64" ]]; then
    exec /usr/bin/arch -arm64 /bin/bash "$source_root/packaging/install.sh" "$@"
  fi
fi
if [[ "$native_arch" != "arm64" && "$native_arch" != "x86_64" ]]; then
  echo "wiki-flow supports Apple Silicon and Intel Macs only." >&2
  exit 2
fi
if [[ "$native_arch" == "arm64" && -x /opt/homebrew/bin/brew ]]; then
  export PATH="/opt/homebrew/bin:$PATH"
elif [[ "$native_arch" == "x86_64" && -x /usr/local/bin/brew ]]; then
  export PATH="/usr/local/bin:$PATH"
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh, then rerun this installer." >&2
  exit 2
fi
brew_root="$(brew --prefix)"
if [[ "$native_arch" == "arm64" && "$brew_root" != /opt/homebrew* ]]; then
  echo "A native Apple Silicon Homebrew installation under /opt/homebrew is required." >&2
  exit 2
fi
if [[ "$native_arch" == "x86_64" && "$brew_root" != /usr/local* ]]; then
  echo "A native Intel Homebrew installation under /usr/local is required." >&2
  exit 2
fi

brew install python@3.12 node@22
if [[ "$native_arch" == "x86_64" ]]; then
  brew install protobuf rust
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install || true
    echo "Finish the Xcode Command Line Tools installation, then rerun this installer." >&2
    exit 2
  fi
fi
brew_prefix="$(brew --prefix)"
export PATH="$(brew --prefix node@22)/bin:$(brew --prefix python@3.12)/libexec/bin:$(brew --prefix python@3.12)/bin:$brew_prefix/bin:$PATH"

python_bin="$(brew --prefix python@3.12)/bin/python3.12"
node_bin="$(brew --prefix node@22)/bin/node"
python_minor="$($python_bin -c 'import sys; print(sys.version_info.minor if sys.version_info.major == 3 else 0)')"
node_major="$("$node_bin" -p 'Number(process.versions.node.split(".")[0])')"
if (( python_minor < 11 )); then
  echo "Python 3.11 or newer is required; found $($python_bin --version)." >&2
  exit 2
fi
if (( node_major < 22 )); then
  echo "Node.js 22 or newer is required; found $("$node_bin" --version)." >&2
  exit 2
fi

exec "$python_bin" "$source_root/packaging/portable.py" install \
  --source-root "$source_root" \
  --python "$python_bin" \
  --node "$node_bin" "$@"
