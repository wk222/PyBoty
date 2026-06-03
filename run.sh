#!/usr/bin/env bash
set -e

echo "==================================================="
echo "            PyBot One-Click Launcher               "
echo "==================================================="

# 1. Check for Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed. Please install Python 3.10+."
    exit 1
fi

# 2. Check for uv
if command -v uv &> /dev/null; then
    echo "[INFO] 'uv' detected. Using 'uv' for fast installation."
    USE_UV=1
else
    echo "[INFO] 'uv' not detected. Falling back to standard pip."
    USE_UV=0
fi

# 3. Setup Virtual Environment
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating virtual environment (.venv)..."
    if [ $USE_UV -eq 1 ]; then
        uv venv .venv
    ) else
        python3 -m venv .venv
    fi
fi

# 4. Activate Virtual Environment
source .venv/bin/activate

# 5. Install Dependencies
echo "[INFO] Installing / updating dependencies..."
if [ $USE_UV -eq 1 ]; then
    uv pip install -e .[all-llm]
else
    python3 -m pip install --upgrade pip
    pip install -e .[all-llm]
fi

# Local dev authentication (override in production)
export PYBOT_API_KEYS="${PYBOT_API_KEYS:-dev-key:*}"
export PYBOT_ALLOW_DEV_KEY="${PYBOT_ALLOW_DEV_KEY:-1}"

# 6. Open Web Browser
URL="http://localhost:8000"
echo "[INFO] Opening browser to $URL..."
if command -v xdg-open &> /dev/null; then
    xdg-open "$URL"
elif command -v open &> /dev/null; then
    open "$URL"
else
    echo "[INFO] Please manually navigate to: $URL"
fi

# 7. Start Server
echo "[INFO] Launching PyBot Service..."
python3 service_mode.py
