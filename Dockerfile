FROM ubuntu:latest
RUN apt-get update && apt-get install -y python2.7 python-pip python-dev build-essential
RUN pip install tornado
ADD bin/influxdb_common.py bin/tornado_webserver.py /
EXPOSE 8086
CMD python /tornado_webserver.py