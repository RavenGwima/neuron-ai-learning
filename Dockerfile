FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY index.html server.py ./

EXPOSE 8000
CMD ["python", "server.py"]
