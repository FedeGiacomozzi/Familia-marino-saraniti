import requests
import re
import os

os.makedirs("/app/fonts", exist_ok=True)

fonts = [
    ("Lora-Regular.ttf",      "https://fonts.googleapis.com/css?family=Lora:400"),
    ("Lora-Italic.ttf",       "https://fonts.googleapis.com/css?family=Lora:400italic"),
    ("Lora-SemiBold.ttf",     "https://fonts.googleapis.com/css?family=Lora:600"),
    ("Montserrat-Light.ttf",  "https://fonts.googleapis.com/css?family=Montserrat:300"),
    ("Montserrat-Regular.ttf","https://fonts.googleapis.com/css?family=Montserrat:400"),
    ("Montserrat-SemiBold.ttf","https://fonts.googleapis.com/css?family=Montserrat:600"),
]

# Older UA causes Google Fonts to return TTF instead of woff2
headers = {"User-Agent": "Mozilla/4.0 (compatible; MSIE 6.0; Windows NT 5.1)"}

for filename, css_url in fonts:
    try:
        css = requests.get(css_url, headers=headers, timeout=15).text
        match = re.search(r"src:\s*url\(([^)]+)\)", css)
        if not match:
            print(f"WARNING: no TTF URL found for {filename}")
            continue
        ttf_url = match.group(1)
        data = requests.get(ttf_url, timeout=30).content
        with open(f"/app/fonts/{filename}", "wb") as f:
            f.write(data)
        print(f"Downloaded {filename} ({len(data):,} bytes)")
    except Exception as e:
        print(f"WARNING: could not download {filename}: {e}")
