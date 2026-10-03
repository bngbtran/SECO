#!/usr/bin/env bash
# Rebuild the custom chips (*.chip.c -> *.chip.wasm). The .wasm files are
# committed, so you only need this after editing a .chip.c file.
# Uses wasi-sdk (clang for WebAssembly), downloaded once into .wasi-sdk/.
set -euo pipefail
cd "$(dirname "$0")"

SDK="${WASI_SDK:-.wasi-sdk}"
if [ ! -x "$SDK/bin/clang" ]; then
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) ASSET=arm64-macos ;; Darwin-x86_64) ASSET=x86_64-macos ;;
    Linux-x86_64) ASSET=x86_64-linux ;; Linux-aarch64) ASSET=arm64-linux ;;
    MINGW*-x86_64|MSYS*-x86_64) ASSET=x86_64-windows ;;
    *) echo "Unsupported platform, set WASI_SDK=/path/to/wasi-sdk"; exit 1 ;;
  esac
  echo "Downloading wasi-sdk ($ASSET)..."
  curl -sL "https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-34/wasi-sdk-34.0-$ASSET.tar.gz" | tar xz
  mv "wasi-sdk-34.0-$ASSET" .wasi-sdk
  xattr -dr com.apple.quarantine .wasi-sdk 2>/dev/null || true
  SDK=.wasi-sdk
fi

for src in *.chip.c; do
  out="${src%.c}.wasm"
  # Same flags as Wokwi's docs (docs.wokwi.com/guides/custom-chips-to-wasm):
  # the simulator provides the memory, so it must be imported (--import-memory);
  # Wokwi rejects modules that export their own. -mcpu=mvp keeps it loadable by
  # any WebAssembly runtime; the chips use no libc.
  "$SDK/bin/clang" --target=wasm32-wasip1 -mcpu=mvp --sysroot "$SDK/share/wasi-sysroot" -O2 \
    -nostartfiles -nostdlib -Wl,--import-memory -Wl,--export-table -Wl,--no-entry -Werror \
    -o "$out" "$src"
  # Wokwi for VS Code (2.0.1) sends Buffer.from(file).buffer to the simulator;
  # Node serves buffers < 4 KiB from a shared 8 KiB pool, so a small chip
  # arrives as that whole pool and is rejected as "not a valid WebAssembly
  # module". Pad with an ignored custom section ("pad", 4092 zero bytes) so
  # the file is always >= 4 KiB.
  { printf '\000\200\040\003pad'; head -c 4092 /dev/zero; } >> "$out"
  echo "built $out"
done
