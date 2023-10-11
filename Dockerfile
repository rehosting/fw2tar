FROM python:3.8-slim

# Install unblob dependencies, curl, and fakeroot
RUN apt-get update && \
  apt-get install -y \
    android-sdk-libsparse-utils \
    curl \
    e2fsprogs \
    fakeroot \
    liblzo2-dev \
    libmagic1 \
    p7zip-full \
    unar \
    zlib1g-dev

# Install rust (with curl), upgrade pip
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
RUN pip install --upgrade pip
#RUN PATH="$PATH:$HOME/.cargo/bin" pip install unblob
# Install sasquatch
RUN curl -L -o sasquatch_1.0_amd64.deb https://github.com/onekey-sec/sasquatch/releases/download/sasquatch-v4.5.1-4/sasquatch_1.0_amd64.deb && dpkg -i sasquatch_1.0_amd64.deb && rm sasquatch_1.0_amd64.deb

RUN apt-get update && \
  apt-get install -y \
    git \
    lz4 \
    lziprecover \
    lzop \
    zstd

# Install poetry but disable venv
RUN python3 -m pip install poetry && poetry config virtualenvs.create false

# Clone unblob then install with poetry
RUN git clone https://github.com/onekey-sec/unblob.git /unblob
RUN cd /unblob && poetry install --no-dev
RUN unblob --help

COPY run.sh run_inner.sh /unblob/

# Input/ouput directories
RUN mkdir -p /data/input /data/output

# Set the working directory to '/data/output'
WORKDIR /data/output

# Set the entry point to our unblob wrapper
ENTRYPOINT ["/unblob/run.sh"]
