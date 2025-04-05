FROM ghcr.io/watsona4/python-pvlib:latest

RUN apk update && apk add --no-cache mosquitto-clients

ENV HDF5_DISABLE_VERSION_CHECK=1

COPY chicken_lights.py colour_system.py cie-cmf.txt healthcheck.sh ./

HEALTHCHECK --interval=15m CMD ./healthcheck.sh

LABEL org.opencontainers.image.source=https://github.com/watsona4/chicken-lights

ENTRYPOINT ["python", "chicken_lights.py"]
