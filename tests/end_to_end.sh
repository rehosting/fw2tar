#!/bin/bash

test() {
    FIRMWARE_PATH=$1
    FIRMWARE_LISTING=$2
    FIRMWARE_NAME=$3

    OLD_FIRMWARE_LISTING="$FIRMWARE_LISTING.old"
    NEW_FIRMWARE_LISTING="$FIRMWARE_LISTING.new"

    if ! [ -f $OLD_FIRMWARE_LISTING ]; then
        echo "Firmware listing for ${FIRMWARE_NAME} does not exist, generating..."
        NEW_FIRMWARE_LISTING="$OLD_FIRMWARE_LISTING"
    fi

    FIRMWARE_PATH_OUT="${FIRMWARE_PATH}_out"
    ROOTFS="$FIRMWARE_PATH_OUT.rootfs.tar.gz"

    rm -f "$ROOTFS"

    echo "Extracting ${FIRMWARE_NAME}..."

    $SCRIPT_DIR/../fw2tar.sh $FIRMWARE_PATH $FIRMWARE_PATH_OUT --force

    if ! [ -f "$ROOTFS" ]; then
        echo "Failed to extract ${FIRMWARE_NAME}"
        exit 1
    fi

    tar -tvf "$ROOTFS" | awk '{ print $6 " " $2 " " $1 " " $3 " " $4  }' | column -t | sort > $NEW_FIRMWARE_LISTING

    if [[ "$NEW_FIRMWARE_LISTING" == "$OLD_FIRMWARE_LISTING" ]]; then
        echo "Generated new firmware listing for ${FIRMWARE_NAME}. Nothing to diff, skipping."
    else
        if ! diff --color=always "$OLD_FIRMWARE_LISTING" "$NEW_FIRMWARE_LISTING"; then
            echo "Listings for ${FIRMWARE_NAME} do not match. To approve changes replace ${OLD_FIRMWARE_LISTING} with ${NEW_FIRMWARE_LISTING}"
        else
            echo "Firmware listing matches for ${FIRMWARE_NAME}."
        fi
    fi
}

SCRIPT_DIR=$(dirname "$(readlink -f "$0")")

# Download TP-Link AX1800 Firmware
FIRMWARE_PATH="/tmp/ax1800_firmware.zip"

curl "https://static.tp-link.com/upload/firmware/2023/202308/20230818/Archer%20AX1800(US)_V4.6_230725.zip" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/ax1800_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "AX1800"

# Download NETGEAR AX5400 (RAX54S) firmware
FIRMWARE_PATH="/tmp/rax54s_firmware.zip"

curl "https://www.downloads.netgear.com/files/GDC/RAX54S/RAX54Sv2-V1.1.4.28.zip" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/rax54s_listing.txt"
test $FIRMWARE_PATH $FIRMWARE_LISTING "RAX54S"

# Download Mikrotik RB750Gr3 firmware
FIRMWARE_PATH="/tmp/rb750gr3_firmware.zip"

curl "https://download.mikrotik.com/routeros/7.14.3/routeros-7.14.3-mmips.npk" \
    -o "$FIRMWARE_PATH"

FIRMWARE_LISTING="$SCRIPT_DIR/results/rb750gr3_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "RB750Gr3"
