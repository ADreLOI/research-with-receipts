# Sandbox image: headless Chromium (Playwright) + a small Python toolbox.
# Every research run gets its own container from this image; it is
# destroyed when the run ends. No credentials are ever baked in or passed.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

RUN pip install --no-cache-dir \
        playwright==1.49.1 \
        httpx==0.27.2 \
        beautifulsoup4==4.12.3 \
        pandas==2.2.3 \
        matplotlib==3.9.2 \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox

COPY worker.py /opt/worker/worker.py
RUN chmod 0555 /opt/worker/worker.py

ENV HOME=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

USER 10001
WORKDIR /tmp
# Idle until the control plane execs tools into it.
CMD ["sleep", "infinity"]
