FROM golang:1.21.5 AS restic-builder

# renovate: datasource=github-releases depName=restic/restic
ARG restic_version=v0.16.2
RUN go install github.com/restic/restic/cmd/restic@${restic_version}

FROM postgres:16.2

RUN apt update && apt -y install python3 python3-pip && rm -rf /var/lib/apt/lists/*

# renovate: datasource=pip depName=requests
ARG requests_version=2.31.0
RUN pip3 install --break-system-packages requests==${requests_version}

COPY --from=restic-builder /go/bin/restic /usr/bin/restic

COPY restic_backup.py \
     restic_prune.py  \
    /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/restic_backup.py"]