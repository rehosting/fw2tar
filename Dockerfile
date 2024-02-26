FROM ubuntu:22.04

# Install unblob dependencies, curl, and fakeroot
ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=America/New_York
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

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
      git+https://github.com/AndrewFasano/binwalk.git \
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

# Clone unblob fork then install with poetry
RUN git clone --depth=1 https://github.com/AndrewFasano/unblob.git /unblob
RUN cd /unblob && poetry install --no-dev

# Explicitly install unblob deps - mostly captured above, but some of the .debs get updated and installed via curl
RUN sh -c /unblob/unblob/install-deps.sh

# Patch to fix unblob #767 that hasn't yet been upstreamed. Pip install didn't work. I don't understand poetry
#RUN pip install git+https://github.com/qkaiser/arpy.git
RUN curl "https://raw.githubusercontent.com/qkaiser/arpy/23faf88a88488c41fc4348ea2b70996803f84f40/arpy.py" -o /usr/local/lib/python3.10/dist-packages/arpy.py

COPY fw2tar.py /
