FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS build

ARG VERSION=0.0.0.dev0
COPY . /src
RUN pip install --no-cache-dir --require-hashes -r /src/requirements/build.lock.txt \
    && SETUPTOOLS_SCM_PRETEND_VERSION=$VERSION pip wheel --no-deps --no-build-isolation -w /wheels /src

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --user-group --no-create-home --home-dir /data/home --shell /usr/sbin/nologin cockpit \
    && mkdir -p /data \
    && chown 10001:10001 /data

COPY --from=build /src/requirements/image.lock.txt /requirements/image.lock.txt
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --require-hashes -r /requirements/image.lock.txt \
    && pip install --no-cache-dir --no-deps /wheels/*.whl

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    COCKPIT_DATA_DIR=/data \
    HOME=/data/home \
    TMPDIR=/tmp

USER 10001:10001
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["tini", "--"]
CMD ["cockpit", "serve"]
