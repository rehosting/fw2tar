#!/bin/bash

failures=0
DEBUG=${DEBUG:-false}
UPDATE_MODE=false
SELECTED=()

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --update)
            UPDATE_MODE=true
            shift
            ;;
        --debug)
            DEBUG=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--update] [--debug] [--help] [firmware ...]"
            echo "  --update    Update baseline JSON files instead of comparing"
            echo "  --debug     Enable debug output"
            echo "  --help      Show this help message"
            echo "  firmware    Names to run (default: all). Known names:"
            echo "              ax1800 rb750gr3 ax86u ac2600 linksys_ax3200"
            echo "              google_wifi tl_wr841n openwrt_x86_64 rax54s"
            exit 0
            ;;
        -*)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
        *)
            SELECTED+=("$1")
            shift
            ;;
    esac
done

# Color definitions
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
END='\033[0m'

test() {
    FIRMWARE_PATH=$1
    FIRMWARE_BASENAME=$2
    FIRMWARE_NAME=$3
    EXTRACTORS=$4
    TIMEOUT=${5:-120}   # per-extractor timeout (s); large images need more

    OLD_JSON="$SCRIPT_DIR/results/${FIRMWARE_BASENAME}.json.old"
    NEW_JSON="$SCRIPT_DIR/results/${FIRMWARE_BASENAME}.json.new"

    if [[ "$UPDATE_MODE" == "true" ]]; then
        echo "Update mode: Will update baseline JSON for ${FIRMWARE_NAME}"
    else
        if ! [ -f "$OLD_JSON" ]; then
            echo "JSON baseline for ${FIRMWARE_NAME} does not exist, bailing!"
            exit 1
        fi
    fi

    FIRMWARE_PATH_OUT="${FIRMWARE_PATH}_out"
    OUTPUT_BASE="$(basename "$FIRMWARE_PATH_OUT")"
    ROOTFS="$FIRMWARE_PATH_OUT/${OUTPUT_BASE}.rootfs.tar.gz"

    rm -f "$ROOTFS"

    echo "Extracting ${FIRMWARE_NAME}..."
    echo "Input file: $FIRMWARE_PATH"
    echo "Input file exists: $([ -f "$FIRMWARE_PATH" ] && echo "YES" || echo "NO")"
    echo "Input file size: $([ -f "$FIRMWARE_PATH" ] && ls -lh "$FIRMWARE_PATH" | awk '{print $5}' || echo "N/A")"

    "$SCRIPT_DIR"/../fw2tar --image "${FW2TAR_IMAGE}" --output "$FIRMWARE_PATH_OUT" --extractors "$EXTRACTORS" --timeout "$TIMEOUT" --force "$FIRMWARE_PATH"

    # Debug: List what files were actually created
    if $DEBUG; then
        echo "Debug: Looking for output files..."
        echo "Expected path: $ROOTFS"
        echo "Contents of output directory:"
        ls -la "$(dirname "$ROOTFS")" 2>/dev/null || echo "Output directory doesn't exist"
        echo "Searching for .rootfs.tar.gz files:"
        find "$(dirname "$FIRMWARE_PATH")" -name "*.rootfs.tar.gz" 2>/dev/null || echo "No rootfs files found"
    fi

    # Check if the expected file exists, and if not, try to find what was actually created
    if ! [ -f "$ROOTFS" ]; then
        echo "Expected file not found: $ROOTFS"
        echo "Looking for alternative rootfs files in output directory:"
        find "$FIRMWARE_PATH_OUT" -name "*.rootfs.tar.gz" 2>/dev/null | head -5

        # Try to use the first rootfs file we find
        ACTUAL_ROOTFS=$(find "$FIRMWARE_PATH_OUT" -name "*.rootfs.tar.gz" 2>/dev/null | head -1)
        if [ -n "$ACTUAL_ROOTFS" ] && [ -f "$ACTUAL_ROOTFS" ]; then
            echo "Using found rootfs file: $ACTUAL_ROOTFS"
            ROOTFS="$ACTUAL_ROOTFS"
        fi
    fi

    if ! [ -f "$ROOTFS" ]; then
        echo -e "${RED}Failed to extract ${FIRMWARE_NAME}${END}"
        exit 1
    fi

    # Convert tar file to JSON format
    "$SCRIPT_DIR/tar_to_json.py" "$ROOTFS" > "$NEW_JSON"

    if [[ "$UPDATE_MODE" == "true" ]]; then
        # Update mode: copy new JSON to old JSON (baseline)
        mkdir -p "$(dirname "$OLD_JSON")"
        cp "$NEW_JSON" "$OLD_JSON"
        echo -e "${GREEN}✓ Updated baseline JSON for ${FIRMWARE_NAME}: $OLD_JSON${END}"
    else
        # Compare JSON files using compare_json.py with exclude patterns for .extracted directories
        if "$SCRIPT_DIR/compare_json.py" "$OLD_JSON" "$NEW_JSON" --exclude '\.extracted($|/)' --verbose; then
            echo -e "${GREEN}Firmware contents match for ${FIRMWARE_NAME}.${END}"
        else
            echo -e "${RED}Contents for ${FIRMWARE_NAME} do not match.${END} To approve changes replace ${OLD_JSON} with ${NEW_JSON}"
            failures=$((failures+1))
        fi
    fi
}

# Test function that uses default output naming (no --output flag)
# This tests the file_stem() logic that preserves version numbers
test_default_naming() {
    FIRMWARE_PATH=$1
    FIRMWARE_BASENAME=$2
    FIRMWARE_NAME=$3
    EXTRACTORS=$4

    # This test only checks filename generation, not content comparison

    # Calculate expected output filename using file_stem logic (like our Rust code)
    FIRMWARE_BASENAME_FILE="$(basename "$FIRMWARE_PATH")"
    FIRMWARE_STEM="${FIRMWARE_BASENAME_FILE%.*}"  # Remove last extension (like file_stem())
    EXPECTED_ROOTFS="$(dirname "$FIRMWARE_PATH")/${FIRMWARE_STEM}.rootfs.tar.gz"

    rm -f "$EXPECTED_ROOTFS"

    echo "Extracting ${FIRMWARE_NAME} (default naming)..."
    echo "Input file: $FIRMWARE_PATH"
    echo "Input file exists: $([ -f "$FIRMWARE_PATH" ] && echo "YES" || echo "NO")"
    echo "Expected output: $EXPECTED_ROOTFS"

    # Run fw2tar WITHOUT --output to test default filename logic
    "$SCRIPT_DIR"/../fw2tar --image "${FW2TAR_IMAGE}" --extractors "$EXTRACTORS" --timeout 120 --force "$FIRMWARE_PATH"

    if ! [ -f "$EXPECTED_ROOTFS" ]; then
        echo -e "${RED}Failed to extract ${FIRMWARE_NAME} - expected output: ${EXPECTED_ROOTFS}${END}"
        exit 1
    fi

    echo -e "${GREEN}✓ Default naming test passed for ${FIRMWARE_NAME}: $(basename "$EXPECTED_ROOTFS")${END}"
}

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

if [[ "$UPDATE_MODE" == "true" ]]; then
    echo -e "${YELLOW}Running in UPDATE MODE - will update baseline JSON files${END}"
fi

# Use a subdirectory relative to the script for downloads to ensure Docker volume mounting works
TMP_DIR="$SCRIPT_DIR/tmp_downloads"
mkdir -p "$TMP_DIR"

echo "Using temp directory: $TMP_DIR"
echo "Directory exists: $([ -d "$TMP_DIR" ] && echo "YES" || echo "NO")"
echo "Directory is writable: $([ -w "$TMP_DIR" ] && echo "YES" || echo "NO")"

# Check for GitHub token and warn if unavailable
if [ -z "$GITHUB_TOKEN" ]; then
    echo -e "${YELLOW}Warning: GITHUB_TOKEN not set. Downloads from GitHub may be rate-limited.${END}"
    echo -e "${YELLOW}If running in CI, consider setting GITHUB_TOKEN to avoid rate limits.${END}"
fi

# Set default fw2tar image if not provided
FW2TAR_IMAGE="${FW2TAR_IMAGE:-rehosting/fw2tar}"
echo "Using fw2tar Docker image: $FW2TAR_IMAGE"

download_file() {
    local url="$1"
    local output_path="$2"
    local max_retries=3
    local retry_delay=10

    # Check if file already exists and is non-empty (for caching)
    if [ -f "$output_path" ] && [ -s "$output_path" ]; then
        echo "✓ $(basename "$output_path") already exists (cached): $(ls -lh "$output_path" | awk '{print $5}')"
        return 0
    fi

    echo "Downloading $(basename "$output_path") from $url"
    if $DEBUG; then
        echo "Output path: $output_path"
    fi

    # Ensure the directory exists
    mkdir -p "$(dirname "$output_path")"

    attempt=1
    while [ $attempt -le $max_retries ]; do
        if [[ "$url" == *"github.com"* ]] && [ -n "$GITHUB_TOKEN" ]; then
            # Use GitHub token for GitHub URLs
            if curl -L -H "Authorization: token $GITHUB_TOKEN" -o "$output_path" "$url"; then
                echo "✓ Download successful (attempt $attempt)"
                # Verify the file was actually created
                if [ -f "$output_path" ] && [ -s "$output_path" ]; then
                    echo "✓ File verified: $(ls -lh "$output_path")"
                    return 0
                else
                    echo "✗ File was not created or is empty"
                    rm -f "$output_path"
                fi
            fi
        else
            # Regular curl for non-GitHub URLs or when no token available
            if curl -L -o "$output_path" "$url"; then
                echo "✓ Download successful (attempt $attempt)"
                # Verify the file was actually created
                if [ -f "$output_path" ] && [ -s "$output_path" ]; then
                    echo "✓ File verified: $(ls -lh "$output_path")"
                    return 0
                else
                    echo "✗ File was not created or is empty"
                    rm -f "$output_path"
                fi
            fi
        fi

        if [ $attempt -lt $max_retries ]; then
            echo "✗ Download failed (attempt $attempt/$max_retries), retrying in ${retry_delay}s..."
            sleep $retry_delay
        else
            echo "✗ Download failed after $max_retries attempts"
            return 1
        fi
        attempt=$((attempt + 1))
    done
}

# Firmware table: name|filename|display name|extractors|timeout(optional)|url
# Notes on individual entries:
#   - google_wifi: large (~68 MB) ChromeOS image; unblob can need well over the
#     default 120s, so it gets extra headroom to avoid a flaky "no extractor
#     succeeded" on a slow runner.
#   - tl_wr841n: regression guard for issue #35 (stopped extracting after
#     f44361d4; the unblob 26.6.4 rebase restored it — a real busybox rootfs
#     with all key dirs/critical files).
#   - openwrt_x86_64: regression guard for #52 (ext permissions). An
#     MBR-partitioned disk image whose rootfs is a real ext4 filesystem,
#     extracted via unblob's debugfs `rdump` handler; before the fix that path
#     reported every file/dir as 0700, so a regression shows as a `mode` diff.
#     Pinned to a release so the baseline stays stable.
FIRMWARE_TABLE=(
    "ax1800|ax1800_firmware.zip|AX1800|binwalk,unblob||https://static.tp-link.com/upload/firmware/2023/202308/20230818/Archer%20AX1800(US)_V4.6_230725.zip"
    "rb750gr3|rb750gr3_firmware.npk|RB750Gr3|unblob||https://download.mikrotik.com/routeros/7.14.3/routeros-7.14.3-mmips.npk"
    "ax86u|ax86u_firmware.zip|RT-AX86U Pro|binwalk,unblob||https://dlcdnets.asus.com/pub/ASUS/wireless/RT-AX86U_Pro/FW_RT_AX86U_PRO_300610234312.zip?model=RT-AX86U%20Pro"
    "ac2600|dlink_ac2600_firmware.zip|D-Link AC2600|binwalk,unblob||https://support.dlink.com/resource/PRODUCTS/DIR-882/REVA/DIR-882_REVA_FIRMWARE_v1.30B06.zip"
    "linksys_ax3200|linksys_ax3200.img|Linksys AX3200|unblob,binwalk||https://downloads.linksys.com/support/assets/firmware/FW_E8450_1.1.01.272918_PROD_unsigned.img"
    "google_wifi|google_wifi.zip|Google WiFi|unblob,binwalk|360|https://dl.google.com/dl/edgedl/chromeos/recovery/chromeos_9334.41.3_gale_recovery_stable-channel_mp.bin.zip"
    "tl_wr841n|tl_wr841n.zip|TL-WR841N|unblob,binwalk||https://static.tp-link.com/2018/201804/20180403/TL-WR841N(EU)_V14_180319.zip"
    "openwrt_x86_64|openwrt_x86_64_ext4.img.gz|OpenWrt x86-64 ext4|unblob||https://downloads.openwrt.org/releases/23.05.5/targets/x86/64/openwrt-23.05.5-x86-64-generic-ext4-combined.img.gz"
    "rax54s|RAX54Sv2-V1.1.4.28.zip|RAX54S|binwalk||https://www.downloads.netgear.com/files/GDC/RAX54S/RAX54Sv2-V1.1.4.28.zip"
)

table_row() {  # <name> -> echoes the row, or fails
    local row
    for row in "${FIRMWARE_TABLE[@]}"; do
        [ "${row%%|*}" = "$1" ] && { echo "$row"; return 0; }
    done
    return 1
}

is_selected() {  # <name> -> true if no filter given or name was requested
    [ ${#SELECTED[@]} -eq 0 ] && return 0
    local n
    for n in "${SELECTED[@]}"; do [ "$n" = "$1" ] && return 0; done
    return 1
}

# Validate requested names up front so a typo fails fast, not after downloads.
for name in ${SELECTED[@]+"${SELECTED[@]}"}; do
    table_row "$name" >/dev/null || {
        echo -e "${RED}Unknown firmware name: $name${END} (see --help)"; exit 1; }
done

run_firmware() {  # <table row>
    local name filename display extractors timeout url
    IFS='|' read -r name filename display extractors timeout url <<<"$1"

    FIRMWARE_PATH="$TMP_DIR/$filename"
    echo "Downloading $display firmware to: $FIRMWARE_PATH"
    download_file "$url" "$FIRMWARE_PATH"

    if [ ! -f "$FIRMWARE_PATH" ]; then
        echo -e "${RED}ERROR: Failed to download $display firmware. File does not exist: $FIRMWARE_PATH${END}"
        echo "Directory contents of $(dirname "$FIRMWARE_PATH"):"
        ls -la "$(dirname "$FIRMWARE_PATH")" || echo "Directory does not exist"
        exit 1
    fi

    test "$FIRMWARE_PATH" "$name" "$display" "$extractors" ${timeout:+"$timeout"}
}

for row in "${FIRMWARE_TABLE[@]}"; do
    is_selected "${row%%|*}" && run_firmware "$row"
done

if [[ "$failures" -gt 0 ]]; then
    echo "Saw $failures during test"
    echo "Temporary files left in: $TMP_DIR"
    exit 1
fi

# Test default filename behavior (version number preservation) using the RAX54S
# firmware, which has version numbers in its filename: RAX54Sv2-V1.1.4.28.zip
# should produce RAX54Sv2-V1.1.4.28.rootfs.tar.gz. Runs only when rax54s is in
# the selection (it needs that download).
if is_selected "rax54s"; then
    echo "Testing default filename behavior with version numbers..."
    FIRMWARE_PATH="$TMP_DIR/RAX54Sv2-V1.1.4.28.zip"
    test_default_naming "$FIRMWARE_PATH" "rax54s" "RAX54S Default Naming" "binwalk"

    EXPECTED_OUTPUT="$TMP_DIR/RAX54Sv2-V1.1.4.28.rootfs.tar.gz"
    if [ -f "$EXPECTED_OUTPUT" ]; then
        echo -e "${GREEN}✓ Version numbers preserved in default naming: $(basename "$EXPECTED_OUTPUT")${END}"
        rm -f "$EXPECTED_OUTPUT"
    else
        echo -e "${RED}✗ Version numbers NOT preserved in default naming - expected: $(basename "$EXPECTED_OUTPUT")${END}"
        failures=$((failures+1))
    fi
fi

if [[ "$failures" -gt 0 ]]; then
    echo -e "${RED}Found $failures differences during testing${END}"
    echo "Temporary files left in: $TMP_DIR"
    exit 1
fi

if [[ "$UPDATE_MODE" == "true" ]]; then
    echo -e "${GREEN}All baseline JSON files updated successfully!${END}"
else
    echo -e "${GREEN}All tests passed! fw2tar is working correctly.${END}"
fi
