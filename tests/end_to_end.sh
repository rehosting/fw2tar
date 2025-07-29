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
    FIRMWARE_LISTING=$2
    FIRMWARE_NAME=$3
    EXTRACTORS=$4

    OLD_FIRMWARE_LISTING="$FIRMWARE_LISTING.old"
    NEW_FIRMWARE_LISTING="$FIRMWARE_LISTING.new"

    if ! [ -f $OLD_FIRMWARE_LISTING ]; then
        echo "Firmware listing for ${FIRMWARE_NAME} does not exist, generating..."
        NEW_FIRMWARE_LISTING="$OLD_FIRMWARE_LISTING"
    fi

    FIRMWARE_PATH_OUT="${FIRMWARE_PATH}_out"
    OUTPUT_BASE="$(basename $FIRMWARE_PATH_OUT)"
    ROOTFS="$FIRMWARE_PATH_OUT/${OUTPUT_BASE}.rootfs.tar.gz"

    rm -f "$ROOTFS"

    echo "Extracting ${FIRMWARE_NAME}..."
    echo "Input file: $FIRMWARE_PATH"
    echo "Input file exists: $([ -f "$FIRMWARE_PATH" ] && echo "YES" || echo "NO")"
    echo "Input file size: $([ -f "$FIRMWARE_PATH" ] && ls -lh "$FIRMWARE_PATH" | awk '{print $5}' || echo "N/A")"

    $SCRIPT_DIR/../fw2tar --image "${FW2TAR_IMAGE}" --output $FIRMWARE_PATH_OUT --extractors $EXTRACTORS --timeout 120 --force $FIRMWARE_PATH

    if ! [ -f "$ROOTFS" ]; then
        echo -e "${RED}Failed to extract ${FIRMWARE_NAME}${END}"
        exit 1
    fi

    tar --utc -tvf "$ROOTFS" | awk '{ print $6 " " $2 " " $1 " " $3 " " $4  }' | column -t | LC_ALL=C LANG=C sort > $NEW_FIRMWARE_LISTING

    if [[ "$NEW_FIRMWARE_LISTING" == "$OLD_FIRMWARE_LISTING" ]]; then
        echo -e "Generated new firmware listing for ${FIRMWARE_NAME}. ${YELLOW}Nothing to diff, skipping.${END}"
    else
        if ! diff --color=always "$OLD_FIRMWARE_LISTING" "$NEW_FIRMWARE_LISTING"; then
            echo -e "${RED}Listings for ${FIRMWARE_NAME} do not match.${END} To approve changes replace ${OLD_FIRMWARE_LISTING} with ${NEW_FIRMWARE_LISTING}"
	    failures=$((failures+1))
        else
            echo -e "${GREEN}Firmware listing matches for ${FIRMWARE_NAME}.${END}"
        fi
    fi
}

# Test function that uses default output naming (no --output flag)
# This tests the file_stem() logic that preserves version numbers
test_default_naming() {
    FIRMWARE_PATH=$1
    FIRMWARE_LISTING=$2
    FIRMWARE_NAME=$3
    EXTRACTORS=$4

    OLD_FIRMWARE_LISTING="$FIRMWARE_LISTING.old"
    NEW_FIRMWARE_LISTING="$FIRMWARE_LISTING.new"

    if ! [ -f $OLD_FIRMWARE_LISTING ]; then
        echo "Firmware listing for ${FIRMWARE_NAME} does not exist, generating..."
        NEW_FIRMWARE_LISTING="$OLD_FIRMWARE_LISTING"
    fi

    # Calculate expected output filename using file_stem logic (like our Rust code)
    FIRMWARE_BASENAME="$(basename "$FIRMWARE_PATH")"
    FIRMWARE_STEM="${FIRMWARE_BASENAME%.*}"  # Remove last extension (like file_stem())
    EXPECTED_ROOTFS="$(dirname "$FIRMWARE_PATH")/${FIRMWARE_STEM}.rootfs.tar.gz"

    rm -f "$EXPECTED_ROOTFS"

    echo "Extracting ${FIRMWARE_NAME} (default naming)..."
    echo "Input file: $FIRMWARE_PATH"
    echo "Input file exists: $([ -f "$FIRMWARE_PATH" ] && echo "YES" || echo "NO")"
    echo "Expected output: $EXPECTED_ROOTFS"

    # Run fw2tar WITHOUT --output to test default filename logic
    $SCRIPT_DIR/../fw2tar --image "${FW2TAR_IMAGE}" --extractors $EXTRACTORS --timeout 120 --force $FIRMWARE_PATH

    if ! [ -f "$EXPECTED_ROOTFS" ]; then
        echo -e "${RED}Failed to extract ${FIRMWARE_NAME} - expected output: ${EXPECTED_ROOTFS}${END}"
        exit 1
    fi

    tar --utc -tvf "$EXPECTED_ROOTFS" | awk '{ print $6 " " $2 " " $1 " " $3 " " $4  }' | column -t | LC_ALL=C LANG=C sort > $NEW_FIRMWARE_LISTING

    if [[ "$NEW_FIRMWARE_LISTING" == "$OLD_FIRMWARE_LISTING" ]]; then
        echo -e "Generated new firmware listing for ${FIRMWARE_NAME}. ${YELLOW}Nothing to diff, skipping.${END}"
    else
        if ! diff --color=always "$OLD_FIRMWARE_LISTING" "$NEW_FIRMWARE_LISTING"; then
            echo -e "${RED}Listings for ${FIRMWARE_NAME} do not match.${END} To approve changes replace ${OLD_FIRMWARE_LISTING} with ${NEW_FIRMWARE_LISTING}"
	    failures=$((failures+1))
        else
            echo -e "${GREEN}Firmware listing matches for ${FIRMWARE_NAME}.${END}"
        fi
    fi
}

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

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

    for attempt in $(seq 1 $max_retries); do
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

FIRMWARE_LISTING="$SCRIPT_DIR/results/ax1800_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "AX1800" "binwalk,unblob"

# Download Mikrotik RB750Gr3 firmware
FIRMWARE_PATH="$TMP_DIR/rb750gr3_firmware.npk"

download_file "https://download.mikrotik.com/routeros/7.14.3/routeros-7.14.3-mmips.npk" "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/rb750gr3_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "RB750Gr3" "unblob"

# Download ASUS RT-AX86U Pro firmware

FIRMWARE_PATH="$TMP_DIR/ax86u_firmware.zip"

download_file "https://dlcdnets.asus.com/pub/ASUS/wireless/RT-AX86U_Pro/FW_RT_AX86U_PRO_300610234312.zip?model=RT-AX86U%20Pro" "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/ax86u_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "RT-AX86U Pro" "binwalk,unblob"

# Download D-Link AC2600 firmware
FIRMWARE_PATH="$TMP_DIR/dlink_ac2600_firmware.zip"
download_file "https://support.dlink.com/resource/PRODUCTS/DIR-882/REVA/DIR-882_REVA_FIRMWARE_v1.30B06.zip" "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/ac2600_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "D-Link AC2600" "binwalk,unblob"

# Download Linksys AX3200
FIRMWARE_PATH="$TMP_DIR/linksys_ax3200.img"

download_file "https://downloads.linksys.com/support/assets/firmware/FW_E8450_1.1.01.272918_PROD_unsigned.img" "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/linksys_ax3200_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "Linksys AX3200" "unblob,binwalk"

# Download Google WiFi Gale
FIRMWARE_PATH="$TMP_DIR/google_wifi.zip"

download_file "https://dl.google.com/dl/edgedl/chromeos/recovery/chromeos_9334.41.3_gale_recovery_stable-channel_mp.bin.zip" "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/google_wifi_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "Google WiFi" "unblob,binwalk"

# Download NETGEAR AX5400 (RAX54S) firmware
FIRMWARE_PATH="$TMP_DIR/RAX54Sv2-V1.1.4.28.zip"

download_file "https://www.downloads.netgear.com/files/GDC/RAX54S/RAX54Sv2-V1.1.4.28.zip" "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/rax54s_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "RAX54S" "binwalk"

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
FIRMWARE_LISTING="$SCRIPT_DIR/results/rax54s_default_naming_listing.txt"
test_default_naming "$FIRMWARE_PATH" "$FIRMWARE_LISTING" "RAX54S Default Naming" "binwalk"

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
    echo "Saw $failures during test"
    echo "Temporary files left in: $TMP_DIR"
    exit 1
fi

# Optional cleanup - only if CLEANUP_DOWNLOADS is set
if [[ "${CLEANUP_DOWNLOADS:-false}" == "true" ]]; then
    echo "Cleaning up temporary downloads (CLEANUP_DOWNLOADS=true)..."
    rm -rf "$TMP_DIR"
else
    echo "Downloads cached in: $TMP_DIR (set CLEANUP_DOWNLOADS=true to clean up)"
fi
