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
RUN python3 - << 'EOF'
import requests, re, os
os.makedirs('/app/fonts', exist_ok=True)
fonts = [
    ('Lora-Regular.ttf',     'https://fonts.googleapis.com/css?family=Lora:400'),
    ('Lora-Italic.ttf',      'https://fonts.googleapis.com/css?family=Lora:400italic'),
    ('Lora-SemiBold.ttf',    'https://fonts.googleapis.com/css?family=Lora:600'),
    ('Montserrat-Light.ttf', 'https://fonts.googleapis.com/css?family=Montserrat:300'),
    ('Montserrat-Regular.ttf','https://fonts.googleapis.com/css?family=Montserrat:400'),
    ('Montserrat-SemiBold.ttf','https://fonts.googleapis.com/css?family=Montserrat:600'),
]
# Older UA causes Google Fonts to return TTF instead of woff2
headers = {'User-Agent': 'Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)'}
for filename, css_url in fonts:
    try:
        css = requests.get(css_url, headers=headers, timeout=15).text
        ttf_url = re.search(r'src:\s*url\(([^)]+)\)', css).group(1)
        data = requests.get(ttf_url, timeout=30).content
        with open(f'/app/fonts/{filename}', 'wb') as f:
            f.write(data)
        print(f'Downloaded {filename} ({len(data)} bytes)')
    except Exception as e:
        print(f'WARNING: could not download {filename}: {e}')
EOF

COPY pipeline/ ./pipeline/

ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV FONTS_DIR=/app/fonts

CMD ["uvicorn", "pipeline.main:app", "--host", "0.0.0.0", "--port", "8080"]
