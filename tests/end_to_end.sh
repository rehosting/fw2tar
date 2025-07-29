#!/bin/bash

failures=0
DEBUG=${DEBUG:-false}

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

    OLD_JSON="$SCRIPT_DIR/results/${FIRMWARE_BASENAME}.json.old"
    NEW_JSON="$SCRIPT_DIR/results/${FIRMWARE_BASENAME}.json.new"

    if ! [ -f "$OLD_JSON" ]; then
        echo "JSON baseline for ${FIRMWARE_NAME} does not exist, bailing!"
        exit 1
    fi

    FIRMWARE_PATH_OUT="${FIRMWARE_PATH}_out"
    OUTPUT_BASE="$(basename "$FIRMWARE_PATH_OUT")"
    ROOTFS="$FIRMWARE_PATH_OUT/${OUTPUT_BASE}.rootfs.tar.gz"

    rm -f "$ROOTFS"

    echo "Extracting ${FIRMWARE_NAME}..."
    echo "Input file: $FIRMWARE_PATH"
    echo "Input file exists: $([ -f "$FIRMWARE_PATH" ] && echo "YES" || echo "NO")"
    echo "Input file size: $([ -f "$FIRMWARE_PATH" ] && ls -lh "$FIRMWARE_PATH" | awk '{print $5}' || echo "N/A")"

    "$SCRIPT_DIR"/../fw2tar --image "${FW2TAR_IMAGE}" --output "$FIRMWARE_PATH_OUT" --extractors "$EXTRACTORS" --timeout 120 --force "$FIRMWARE_PATH"

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

    # Compare JSON files using compare_json.py with exclude patterns for .extracted directories
    if "$SCRIPT_DIR/compare_json.py" "$OLD_JSON" "$NEW_JSON" --exclude '\.extracted($|/)' --verbose; then
        echo -e "${GREEN}Firmware contents match for ${FIRMWARE_NAME}.${END}"
    else
        echo -e "${RED}Contents for ${FIRMWARE_NAME} do not match.${END} To approve changes replace ${OLD_JSON} with ${NEW_JSON}"
        failures=$((failures+1))
    fi
}

# Test function that uses default output naming (no --output flag)
# This tests the file_stem() logic that preserves version numbers
test_default_naming() {
    FIRMWARE_PATH=$1
    FIRMWARE_BASENAME=$2
    FIRMWARE_NAME=$3
    EXTRACTORS=$4

    OLD_JSON="$SCRIPT_DIR/results/${FIRMWARE_BASENAME}_default_naming.json.old"
    NEW_JSON="$SCRIPT_DIR/results/${FIRMWARE_BASENAME}_default_naming.json.new"

    if ! [ -f "$OLD_JSON" ]; then
        echo "JSON baseline for ${FIRMWARE_NAME} (default naming) does not exist, generating..."
        NEW_JSON="$OLD_JSON"
    fi

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
}

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

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

# Download TP-Link AX1800 Firmware
FIRMWARE_PATH="$TMP_DIR/ax1800_firmware.zip"

echo "Downloading AX1800 firmware to: $FIRMWARE_PATH"
download_file "https://static.tp-link.com/upload/firmware/2023/202308/20230818/Archer%20AX1800(US)_V4.6_230725.zip" "$FIRMWARE_PATH"

# Verify the file was downloaded successfully
if [ ! -f "$FIRMWARE_PATH" ]; then
    echo -e "${RED}ERROR: Failed to download AX1800 firmware. File does not exist: $FIRMWARE_PATH${END}"
    echo "Directory contents of $(dirname "$FIRMWARE_PATH"):"
    ls -la "$(dirname "$FIRMWARE_PATH")" || echo "Directory does not exist"
    exit 1
fi

echo -e "${GREEN}✓ AX1800 firmware downloaded successfully: $(ls -lh "$FIRMWARE_PATH")${END}"

test "$FIRMWARE_PATH" "ax1800" "AX1800" "binwalk,unblob"

# Download Mikrotik RB750Gr3 firmware
FIRMWARE_PATH="$TMP_DIR/rb750gr3_firmware.npk"

download_file "https://download.mikrotik.com/routeros/7.14.3/routeros-7.14.3-mmips.npk" "$FIRMWARE_PATH"

test "$FIRMWARE_PATH" "rb750gr3" "RB750Gr3" "unblob"

# Download ASUS RT-AX86U Pro firmware

FIRMWARE_PATH="$TMP_DIR/ax86u_firmware.zip"

download_file "https://dlcdnets.asus.com/pub/ASUS/wireless/RT-AX86U_Pro/FW_RT_AX86U_PRO_300610234312.zip?model=RT-AX86U%20Pro" "$FIRMWARE_PATH"

test "$FIRMWARE_PATH" "ax86u" "RT-AX86U Pro" "binwalk,unblob"

# Download D-Link AC2600 firmware
FIRMWARE_PATH="$TMP_DIR/dlink_ac2600_firmware.zip"
download_file "https://support.dlink.com/resource/PRODUCTS/DIR-882/REVA/DIR-882_REVA_FIRMWARE_v1.30B06.zip" "$FIRMWARE_PATH"

test "$FIRMWARE_PATH" "ac2600" "D-Link AC2600" "binwalk,unblob"

# Download Linksys AX3200
FIRMWARE_PATH="$TMP_DIR/linksys_ax3200.img"

download_file "https://downloads.linksys.com/support/assets/firmware/FW_E8450_1.1.01.272918_PROD_unsigned.img" "$FIRMWARE_PATH"

test "$FIRMWARE_PATH" "linksys_ax3200" "Linksys AX3200" "unblob,binwalk"

# Download Google WiFi Gale
FIRMWARE_PATH="$TMP_DIR/google_wifi.zip"

download_file "https://dl.google.com/dl/edgedl/chromeos/recovery/chromeos_9334.41.3_gale_recovery_stable-channel_mp.bin.zip" "$FIRMWARE_PATH"

test "$FIRMWARE_PATH" "google_wifi" "Google WiFi" "unblob,binwalk"

# Download NETGEAR AX5400 (RAX54S) firmware
FIRMWARE_PATH="$TMP_DIR/RAX54Sv2-V1.1.4.28.zip"

download_file "https://www.downloads.netgear.com/files/GDC/RAX54S/RAX54Sv2-V1.1.4.28.zip" "$FIRMWARE_PATH"

test "$FIRMWARE_PATH" "rax54s" "RAX54S" "binwalk"

if [[ "$failures" -gt 0 ]]; then
    echo "Saw $failures during test"
    echo "Temporary files left in: $TMP_DIR"
    exit 1
fi

# Test default filename behavior (version number preservation)
# Use the RAX54S firmware which has version numbers in the filename
echo "Testing default filename behavior with version numbers..."

# Use the already-downloaded RAX54S firmware: RAX54Sv2-V1.1.4.28.zip
# This should produce: RAX54Sv2-V1.1.4.28.rootfs.tar.gz (preserving version numbers)
test_default_naming "$FIRMWARE_PATH" "rax54s" "RAX54S Default Naming" "binwalk"

# Verify the output filename contains the version numbers
EXPECTED_OUTPUT="$TMP_DIR/RAX54Sv2-V1.1.4.28.rootfs.tar.gz"
if [ -f "$EXPECTED_OUTPUT" ]; then
    echo -e "${GREEN}✓ Version numbers preserved in default naming: $(basename "$EXPECTED_OUTPUT")${END}"
    rm -f "$EXPECTED_OUTPUT"
else
    echo -e "${RED}✗ Version numbers NOT preserved in default naming - expected: $(basename "$EXPECTED_OUTPUT")${END}"
    failures=$((failures+1))
fi

if [[ "$failures" -gt 0 ]]; then
    echo -e "${RED}Found $failures differences during testing${END}"
    echo "Temporary files left in: $TMP_DIR"
    exit 1
fi

echo -e "${GREEN}All tests passed! fw2tar is working correctly.${END}"
