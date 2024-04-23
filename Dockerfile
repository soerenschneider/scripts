FROM hashicorp/vault:1.16.2 AS donor

FROM python:3.12.2-slim AS final

COPY --from=donor /bin/vault /usr/bin/vault

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

COPY . /scripts/

WORKDIR /scripts/bin