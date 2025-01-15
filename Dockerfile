FROM ubuntu:22.04

# Install unblob dependencies, curl, and fakeroot
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/New_York
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV HOME=/root

RUN apt-get update && \
  apt-get install -q -y \
    android-sdk-libsparse-utils \
    arj \
    automake \
    build-essential \
    bzip2 \
    cabextract \
    cpio \
    cramfsswap \
    curl \
    default-jdk \
    e2fsprogs \
    fakeroot \
    gcc \
    git \
    gzip \
    lhasa \
    libarchive-dev \
    liblzma-dev \
    liblzo2-dev \
    libmagic1 \
    locales \
    lz4 \
    lziprecover \
    lzop \
    mtd-utils \
    openssh-client \
    p7zip \
    p7zip-full \
    python3 \
    python3-pip \
    qtbase5-dev \
    sleuthkit \
    squashfs-tools \
    srecord \
    tar \
    unar \
    unrar-free \
    unzip \
    xz-utils \
    zlib1g-dev \
    zstd

# Install dependencies
RUN pip install --upgrade pip && \
    python3 -m pip install \
      git+http://github.com/jrspruitt/ubi_reader.git@v0.8.5-master \
      git+https://github.com/rehosting/binwalk.git \
      git+https://github.com/ahupp/python-magic \
      git+https://github.com/devttys0/yaffshiv.git \
      git+https://github.com/marin-m/vmlinux-to-elf \
      jefferson \
      gnupg \
      poetry \
      psycopg2-binary \
      pycryptodome \
      pylzma \
      pyyaml \
      setuptools \
      sqlalchemy \
      telnetlib3 \
      tk \
      lz4 \
      zstandard \
      pyelftools \
      lief && \
    python3 -m pip install python-lzo==1.14 && \
    poetry config virtualenvs.create false

# CramFS no longer in apt - needed by binwalk
RUN git clone --depth=1 https://github.com/davidribyrne/cramfs.git /cramfs && \
   cd /cramfs && make && make install

# Clone unblob fork then install with poetry
RUN git clone --depth=1 https://github.com/rehosting/unblob.git /unblob
RUN cd /unblob && poetry install --only main

# Explicitly install unblob deps - mostly captured above, but some of the .debs get updated and installed via curl
RUN sh -c /unblob/unblob/install-deps.sh

# We will run as other users (matching uid/gid to host), but binwalk has config files in /root/.config
# that need to be created and read at runtime.
RUN chmod -R 777 /root/

# Try to install custom fakeroot. This is optional - we have regular fakeroot. If we're building
# with host SSH keys, we can do this, otherwise we'll just skip it
# Setup ssh keys for github.com
RUN mkdir -p -m 0600 ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts
ARG SSH
RUN --mount=type=ssh git clone git@github.com:rehosting/fakeroot.git /fakeroot && \
    sed -i 's/^# deb-src/deb-src/' /etc/apt/sources.list && \
    apt-get update && apt-get build-dep -y fakeroot && \
    cd /fakeroot && ./bootstrap && ./configure && make && make install -k || true

# Patch to fix unblob #767 that hasn't yet been upstreamed. Pip install didn't work. I don't understand poetry
#RUN pip install git+https://github.com/qkaiser/arpy.git
RUN curl "https://raw.githubusercontent.com/qkaiser/arpy/23faf88a88488c41fc4348ea2b70996803f84f40/arpy.py" -o /usr/local/lib/python3.10/dist-packages/arpy.py

# Copy wrapper script into container so we can copy out - note we don't put it on guest path
COPY ./fw2tar /usr/local/src/fw2tar_wrapper
# And add install helpers which generate shell commands to install it on host
COPY ./src/resources/banner.sh ./src/resources/fw2tar_install ./src/resources/fw2tar_install.local /usr/local/bin/
# Warn on interactive shell sessions and provide instructions for install
RUN echo '[ ! -z "$TERM" ] && [ -z "$NOBANNER" ] && /usr/local/bin/banner.sh' >> /etc/bash.bashrc

COPY src/fw2tar /usr/local/bin/

# Symlink for backwards compatibility
RUN ln -s /usr/local/bin/fw2tar /usr/local/bin/fakeroot_fw2tar

CMD ["/usr/local/bin/banner.sh"]
