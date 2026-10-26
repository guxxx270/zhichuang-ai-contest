FROM ghcr.io/astral-sh/uv:0.11.6-python3.12-trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=3
ARG DEBIAN_MIRROR=http://deb.debian.org
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY app/requirements.txt /tmp/requirements.txt
ARG PYPI_INDEX=https://pypi.org/simple
RUN uv pip install --system --no-cache --index-url "$PYPI_INDEX" -r /tmp/requirements.txt
RUN groupadd -g 10001 xuzhi && useradd -u 10001 -g 10001 -m xuzhi
WORKDIR /app
COPY app/ /app/
RUN chmod -R a+rX /app
USER 10001:10001
RUN python -c "import streamlit, xuzhi.pipeline, xuzhi.ledger"
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true", "--server.fileWatcherType=none", "--browser.gatherUsageStats=false"]
