#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source .venv/bin/activate

python -m pip install "cmake>=3.30,<5"

tool_dir="data/tools/whisper.cpp"
model_dir="data/models"
model_path="$model_dir/ggml-small.bin"

mkdir -p "$(dirname "$tool_dir")" "$model_dir"

if [ ! -d "$tool_dir/.git" ]; then
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp "$tool_dir"
else
    git -C "$tool_dir" pull --ff-only
fi

cmake -S "$tool_dir" -B "$tool_dir/build" \
    -DCMAKE_BUILD_TYPE=Release \
    -DGGML_METAL=ON
cmake --build "$tool_dir/build" --config Release --parallel 4

if [ ! -f "$model_path" ]; then
    curl --fail --location --progress-bar \
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin" \
        --output "$model_path"
fi

echo "Local OCR, document extraction and voice transcription are ready."
