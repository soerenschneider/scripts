FROM hashicorp/vault:1.15.0 AS donor

FROM python:3.11.5-slim AS final

COPY --from=donor /bin/vault /usr/bin/vault

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

COPY . /scripts/

WORKDIR /scripts/bin