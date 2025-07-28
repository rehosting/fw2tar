#!/bin/bash
set -euo pipefail

# Simple download script with GitHub token support
# Usage: download_github_asset.sh <url> [output_file]

URL="$1"
OUTPUT_FILE="${2:-}"

# If GITHUB_TOKEN is set and URL is from GitHub, use it
CURL_OPTS=(-L --connect-timeout 30 --max-time 300)
if [[ -n "${GITHUB_TOKEN:-}" ]] && [[ "$URL" == *"github.com"* ]]; then
    CURL_OPTS+=(-H "Authorization: token $GITHUB_TOKEN")
    echo "Using GitHub token for authenticated request"
fi

# Add output file if specified
if [[ -n "$OUTPUT_FILE" ]]; then
    CURL_OPTS+=(-o "$OUTPUT_FILE")
fi

echo "Downloading: $URL"
curl "${CURL_OPTS[@]}" "$URL"
