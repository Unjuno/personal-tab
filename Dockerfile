FROM python:3.13.5-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src CUDA_VISIBLE_DEVICES="" NVIDIA_VISIBLE_DEVICES=void OMP_NUM_THREADS=1
WORKDIR /app
RUN groupadd --gid 10001 tab && useradd --uid 10001 --gid 10001 --no-create-home tab
COPY --chown=10001:10001 src/ ./src/
COPY --chown=10001:10001 serve_demo.py LICENSE README.md pyproject.toml ./
USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=2).read()"]
CMD ["python", "serve_demo.py", "--host", "0.0.0.0", "--container-mode", "--port", "8765"]
