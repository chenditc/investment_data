FROM python:3.9

ARG DOLT_VERSION=1.88.0
ARG INVESTMENT_DATA_REVISION
ARG QLIB_REVISION=b87a2c294d364a33fb739359886acffe8ec907d1

LABEL org.opencontainers.image.revision="${INVESTMENT_DATA_REVISION}"
LABEL io.chenditc.investment-data.qlib-revision="${QLIB_REVISION}"

RUN case "${INVESTMENT_DATA_REVISION}" in \
      [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;; \
      *) echo "INVESTMENT_DATA_REVISION must be a lowercase 40-hex commit" >&2; exit 1 ;; \
    esac \
    && test "${QLIB_REVISION}" = "b87a2c294d364a33fb739359886acffe8ec907d1"

RUN wget "https://github.com/dolthub/dolt/releases/download/v${DOLT_VERSION}/dolt-linux-amd64.tar.gz" -O /tmp/dolt-linux-amd64.tar.gz \
    && cd /tmp \
    && tar -zxvf /tmp/dolt-linux-amd64.tar.gz \
    && cp /tmp/dolt-linux-amd64/bin/dolt /usr/bin/ \
    && rm -rf /tmp/* \
    && dolt config --global --add user.email "dockeruser@na.com" \
    && dolt config --global --add user.name "dockeruser"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git psmisc zip gcc g++ jq util-linux \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /dolt /investment_data /opt/investment-data

RUN pip install numpy==1.23.5 \
    && pip install --upgrade cython \
    && git clone https://github.com/microsoft/qlib.git /qlib \
    && git -C /qlib checkout --detach "${QLIB_REVISION}" \
    && test "$(git -C /qlib rev-parse HEAD)" = "${QLIB_REVISION}" \
    && pip install -e '/qlib[dev]' \
    && pip install -r /qlib/scripts/data_collector/yahoo/requirements.txt

COPY ./requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY . /investment_data/

RUN test "$(git -C /investment_data rev-parse HEAD)" = "${INVESTMENT_DATA_REVISION}" \
    && printf '%s\n' "${INVESTMENT_DATA_REVISION}" > /opt/investment-data/REVISION \
    && printf '%s\n' "${QLIB_REVISION}" > /opt/investment-data/QLIB_REVISION \
    && chmod 0444 /opt/investment-data/REVISION /opt/investment-data/QLIB_REVISION \
    && PYTHONPATH=/qlib:/qlib/scripts python3 -m unittest discover -s /investment_data/tests -p test_qlib_normalize.py

# Add a global sitecustomize.py so every Python process defaults to "spawn".
RUN printf '%s\n' \
    'import multiprocessing as mp' \
    'try:' \
    '    mp.set_start_method("spawn")' \
    'except RuntimeError:' \
    '    pass' \
    > /usr/local/lib/python3.9/site-packages/sitecustomize.py

WORKDIR /investment_data/
