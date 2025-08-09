FROM ghcr.io/watsona4/python-pvlib:latest

RUN apk update && apk add --no-cache mosquitto-clients

ENV HDF5_DISABLE_VERSION_CHECK=1

COPY chicken_lights.py colour_system.py cie-cmf.txt healthcheck.py ./

RUN chmod +x healthcheck.py

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python3 healthcheck.py

LABEL org.opencontainers.image.source=https://github.com/watsona4/chicken-lights

ENTRYPOINT ["python", "chicken_lights.py"]
