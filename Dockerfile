FROM hashicorp/vault:2.1.0 AS donor

FROM python:3.13.7-slim AS final

COPY --from=donor /bin/vault /usr/bin/vault

COPY requirements.txt /requirements.txt
RUN pip install -r /requirements.txt

COPY . /scripts/

WORKDIR /scripts/bin