FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
RUN useradd --create-home --uid 10001 bot
WORKDIR /app
COPY pyproject.toml ./
COPY watchbot ./watchbot
RUN pip install --no-cache-dir .
COPY config.json news.json ./
RUN mkdir runtime && chown bot:bot runtime
USER bot
CMD ["python", "-m", "watchbot", "run"]
