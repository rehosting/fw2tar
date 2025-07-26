#!/bin/bash

failures=0

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

    $SCRIPT_DIR/../fw2tar --output $FIRMWARE_PATH_OUT --extractors $EXTRACTORS --timeout 120 --force $FIRMWARE_PATH

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

    # Run fw2tar WITHOUT --output to test default filename logic
    $SCRIPT_DIR/../fw2tar --extractors $EXTRACTORS --timeout 120 --force $FIRMWARE_PATH

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

# Download TP-Link AX1800 Firmware
FIRMWARE_PATH="/tmp/ax1800_firmware.zip"

curl "https://static.tp-link.com/upload/firmware/2023/202308/20230818/Archer%20AX1800(US)_V4.6_230725.zip" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/ax1800_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "AX1800" "binwalk,unblob"

# Download Mikrotik RB750Gr3 firmware
FIRMWARE_PATH="/tmp/rb750gr3_firmware.npk"

curl "https://download.mikrotik.com/routeros/7.14.3/routeros-7.14.3-mmips.npk" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/rb750gr3_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "RB750Gr3" "unblob"

# Download ASUS RT-AX86U Pro firmware

FIRMWARE_PATH="/tmp/ax86u_firmware.zip"

curl "https://dlcdnets.asus.com/pub/ASUS/wireless/RT-AX86U_Pro/FW_RT_AX86U_PRO_300610234312.zip?model=RT-AX86U%20Pro" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/ax86u_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "RT-AX86U Pro" "binwalk,unblob"

# Download D-Link AC2600 firmware
FIRMWARE_PATH="/tmp/dlink_ac2600_firmware.zip"
curl "https://support.dlink.com/resource/PRODUCTS/DIR-882/REVA/DIR-882_REVA_FIRMWARE_v1.30B06.zip" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/ac2600_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "D-Link AC2600" "binwalk,unblob"

# Download Linksys AX3200
FIRMWARE_PATH="/tmp/linksys_ax3200.img"

curl "https://downloads.linksys.com/support/assets/firmware/FW_E8450_1.1.01.272918_PROD_unsigned.img" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/linksys_ax3200_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "Linksys AX3200" "unblob,binwalk"

# Download Google WiFi Gale
FIRMWARE_PATH="/tmp/google_wifi.zip"

curl "https://dl.google.com/dl/edgedl/chromeos/recovery/chromeos_9334.41.3_gale_recovery_stable-channel_mp.bin.zip" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/google_wifi_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "Google WiFi" "unblob,binwalk"

# Download NETGEAR AX5400 (RAX54S) firmware
FIRMWARE_PATH="/tmp/rax54s_firmware.zip"

curl "https://www.downloads.netgear.com/files/GDC/RAX54S/RAX54Sv2-V1.1.4.28.zip" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/rax54s_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "RAX54S" "binwalk"

if [[ "$failures" -gt 0 ]]; then
    echo "Saw $failures during test"
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
EXPECTED_OUTPUT="/tmp/RAX54Sv2-V1.1.4.28.rootfs.tar.gz"
if [ -f "$EXPECTED_OUTPUT" ]; then
    echo -e "${GREEN}✓ Version numbers preserved in default naming: $(basename "$EXPECTED_OUTPUT")${END}"
    rm -f "$EXPECTED_OUTPUT"
else
    echo -e "${RED}✗ Version numbers NOT preserved in default naming - expected: $(basename "$EXPECTED_OUTPUT")${END}"
    failures=$((failures+1))
fi

if [[ "$failures" -gt 0 ]]; then
    echo "Saw $failures during test"
    exit 1
fi
