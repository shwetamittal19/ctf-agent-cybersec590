# Solver sandbox image. Contains tools only: no flags, no challenge sources, no docker client.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PIP_BREAK_SYSTEM_PACKAGES=1
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-dev build-essential gdb binutils file xxd bsdmainutils \
      netcat-openbsd curl wget ca-certificates socat sqlite3 jq unzip p7zip-full \
      ltrace strace patchelf libc6-dbg \
    && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir pwntools pycryptodome requests z3-solver sympy gmpy2 ROPgadget
RUN useradd -m -u 1000 ctf && mkdir -p /work && chown ctf:ctf /work
USER ctf
WORKDIR /work
CMD ["sleep", "infinity"]
