FROM golang:1.22.3 AS restic

# renovate: datasource=github-releases depName=restic/restic
ARG RESTIC_VERSION=v0.16.4

RUN apt update && \
    apt -y install git && \
    git clone https://github.com/restic/restic /restic && \
    git -C /restic checkout ${RESTIC_VERSION}

WORKDIR /restic
RUN CGO_ENABLED=0 go run helpers/build-release-binaries/main.go -p linux/amd64 --skip-compress

FROM alpine:3.19.1

# renovate: datasource=pip depName=requests
ARG REQUESTS_VERSION=2.31.0
ARG USERNAME=restic
ARG USER_UID=16523
ARG USER_GID=$USER_UID

RUN apk add --no-cache python3 py-pip gzip mariadb-client postgresql16-client
RUN pip3 install --break-system-packages requests==${REQUESTS_VERSION}

RUN addgroup --gid "$USER_GID" "$USERNAME" && \
    adduser --disabled-password --ingroup "$USERNAME" --uid "$USER_UID" $USERNAME

COPY --from=restic /output/restic_linux_amd64 /usr/local/bin/restic
COPY restic* /usr/local/bin/

USER ${USERNAME}
ENTRYPOINT ["/usr/local/bin/restic_backup.py"]
