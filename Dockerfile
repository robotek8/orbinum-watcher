FROM python:3.12-alpine

WORKDIR /app

COPY web.py /app/web.py

EXPOSE 8787

CMD ["python3", "/app/web.py"]
