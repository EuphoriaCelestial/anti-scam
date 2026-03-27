FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir flask PyJWT Werkzeug psycopg2-binary pandas

COPY . .

RUN mkdir -p uploads/pdf uploads/video static/assets

EXPOSE 5000

CMD ["python", "app.py"]
