# Test image for the behavior characterization harness.
#
# Extends the fw2tar image (which already carries every extractor plus mkfs tools
# for ext*/squashfs/cramfs/jffs2/ubifs) with the extra filesystem *builders* needed
# to generate fixtures for the remaining types unblob handles. This keeps the
# harness self-contained: the host only needs docker, not a pile of mkfs tools.
ARG BASE_IMAGE=rehosting/fw2tar:latest
FROM ${BASE_IMAGE}

USER root
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        genromfs \
        genisoimage \
        mtools \
        dosfstools \
        ntfs-3g \
        zip \
    && rm -rf /var/lib/apt/lists/*

# Best-effort: build mkyaffs2image from source so a yaffs fixture can be created.
# Never fail the image build if this can't be fetched/compiled — yaffs simply
# stays a documented gap and the harness skips it.
RUN set -e; \
    ( git clone --depth 1 https://github.com/whinis/yaffs2utils /tmp/yaffs2utils \
      && make -C /tmp/yaffs2utils \
      && install -m 0755 /tmp/yaffs2utils/mkyaffs2image /usr/local/bin/mkyaffs2image \
      && rm -rf /tmp/yaffs2utils ) || echo "WARN: mkyaffs2image build skipped"
