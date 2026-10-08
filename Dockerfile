FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY imagechanger ./imagechanger
RUN pip install --no-cache-dir .
EXPOSE 8765
# LAN only: the server has no login. Never publish this port to the internet.
CMD ["imagechanger", "serve", "--host", "0.0.0.0", "--port", "8765"]
