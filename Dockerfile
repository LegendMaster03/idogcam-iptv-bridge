FROM python:3.12-slim
WORKDIR /opt/bridge
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir . && groupadd --gid 568 bridge && useradd --uid 568 --gid 568 --system --no-create-home bridge
USER 568:568
EXPOSE 8080
CMD ["python", "-m", "idogcam_bridge"]
