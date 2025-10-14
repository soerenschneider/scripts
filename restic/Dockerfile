FROM golang:1.25.3 AS restic

# renovate: datasource=github-releases depName=restic/restic
ARG RESTIC_VERSION=v0.18.1

RUN apt update && \
    apt -y install git && \
    git clone https://github.com/restic/restic /restic && \
    git -C /restic checkout ${RESTIC_VERSION}

WORKDIR /restic
RUN CGO_ENABLED=0 go run helpers/build-release-binaries/main.go -p linux/amd64 --skip-compress

FROM rust:1.90.0-alpine3.21 AS build-taskwarrior

# Install dependencies needed to build Taskwarrior
RUN apk add --no-cache \
    build-base \
    git \
    cmake \
    libtool \
    autoconf \
    pkgconfig \
    sqlite-dev \
    ncurses-dev \
    libunwind-dev \
    util-linux-dev \
    zlib-dev

# Set the working directory
WORKDIR /taskwarrior

# renovate: datasource=github-releases depName=GothenburgBitFactory/taskwarrior
ARG TASKWARRIOR_VERSION=v3.4.1
RUN git clone https://github.com/GothenburgBitFactory/taskwarrior.git . && \
    git -C /taskwarrior checkout ${TASKWARRIOR_VERSION}

# Build Taskwarrior using cmake
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release . && \
    cmake --build build -j 8 && \
    cmake --install build

FROM alpine:3.22.2

# renovate: datasource=pypi depName=requests
ARG REQUESTS_VERSION=2.31.0
ARG USERNAME=restic
ARG USER_UID=16523
ARG USER_GID=$USER_UID
ARG POSTGRES_MAJOR=17

RUN apk add --no-cache python3 py-pip gzip mariadb-client postgresql${POSTGRES_MAJOR}-client sqlite

# runtime dependencies for taskwarrior
RUN apk add --no-cache \
    tzdata \
    sqlite-dev \
    libunwind-dev \
    util-linux-dev \
    libstdc++

RUN pip3 install --break-system-packages requests==${REQUESTS_VERSION}

RUN addgroup --gid "$USER_GID" "$USERNAME" && \
    adduser --disabled-password --ingroup "$USERNAME" --uid "$USER_UID" $USERNAME

COPY --from=restic /output/restic_linux_amd64 /usr/local/bin/restic
COPY --from=build-taskwarrior /usr/local/bin/task /usr/bin/task
COPY restic* /usr/local/bin/

USER ${USERNAME}
ENTRYPOINT ["/usr/local/bin/restic_backup.py"]
