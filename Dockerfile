FROM python:3.9.11-slim

WORKDIR /app

COPY requirements.txt /app

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Install system dependencies and clean up
RUN apt update && \
    apt install -y tesseract-ocr tesseract-ocr-spa && \
    rm -rf /var/lib/apt/lists/*

COPY . /app

EXPOSE 7017


ENV FLASK_ENV=production
ENV FLASK_APP=server.py
CMD ["flask", "run", "--host", "0.0.0.0", "--port=5001"]

