FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# GitHub's web upload can't take folders, so the web UI files were uploaded flat: put them back into web/
RUN mkdir -p web && mv app.css app.js botsui.js favicon.png icon-192.png icon-512.png icon-maskable.png icon.svg index.html logo.png manifest.webmanifest mic-worklet.js room.js stories.js sw.js  web/
ENV PYTHONUNBUFFERED=1 \
    WINDROIDA_TRUST_PROXY=1
CMD ["sh", "-c", "python server.py --host 0.0.0.0 --port 8765 --http --web --web-port ${PORT:-8087}"]
