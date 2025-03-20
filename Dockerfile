FROM python-pvlib:latest

ENV TZ="America/New_York"
RUN cp /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

COPY chicken_lights.py colour_system.py cie-cmf.txt .

ENV HDF5_DISABLE_VERSION_CHECK=1

ENTRYPOINT ["python", "chicken_lights.py"]
