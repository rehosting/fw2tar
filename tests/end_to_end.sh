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

echo "$SCRIPT_DIR"

# Download AX1800 Firmware
curl "https://static.tp-link.com/upload/firmware/2023/202308/20230818/Archer%20AX1800(US)_V4.6_230725.zip" \
    -o /tmp/ax1800_firmware.zip

# Extract AX1800 zip to get the firmware binary blob
unzip -o /tmp/ax1800_firmware.zip -d /tmp/ax1800_firmware

FIRMWARE_PATH="/tmp/ax1800_firmware/AX23_us_ca_tw_sg-up-ver1-1-0-P1[20230725-rel55602]_2023-07-25_15.39.38.bin"
FIRMWARE_LISTING="$SCRIPT_DIR/results/ax1800_listing.txt"

test $FIRMWARE_PATH $FIRMWARE_LISTING "AX1800"
