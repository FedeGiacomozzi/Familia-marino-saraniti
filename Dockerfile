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

# Pre-download Lora and Montserrat TTF fonts so WeasyPrint doesn't need network at PDF time
COPY download_fonts.py /tmp/download_fonts.py
RUN python3 /tmp/download_fonts.py && rm /tmp/download_fonts.py

COPY pipeline/ ./pipeline/

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV FONTS_DIR=/app/fonts

CMD ["uvicorn", "pipeline.main:app", "--host", "0.0.0.0", "--port", "8080"]
