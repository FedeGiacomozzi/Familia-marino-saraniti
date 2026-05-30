FROM python:3.12-slim-bookworm

# System dependencies: ffmpeg for audio, WeasyPrint for PDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    shared-mime-info \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/ ./pipeline/
COPY onboarding.html .
COPY recording.html .
COPY admin.html .

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

CMD ["uvicorn", "pipeline.main:app", "--host", "0.0.0.0", "--port", "8080"]
