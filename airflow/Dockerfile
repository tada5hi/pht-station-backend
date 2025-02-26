FROM ubuntu
MAINTAINER michael.graf@uni-tuebingen.de

EXPOSE 8080 5555 8793

ENV DEBIAN_FRONTEND=noninteractive

# install python3.8 and os dependencies
RUN apt -y update && apt-get -y install software-properties-common && \
    apt-get install -yqq --no-install-recommends --no-install-suggests \
        git \
        libffi-dev \
        libpq-dev \
        build-essential \
        apt-utils \
        netcat \
        locales \
        curl && \
    # install python
    add-apt-repository ppa:deadsnakes/ppa && \
    apt-get install -y python3.8 && apt install python-is-python3 && apt install -y python3-pip && \
    rm -rf /var/lib/apt/lists


# Install container diff library
RUN curl -LO https://storage.googleapis.com/container-diff/latest/container-diff-linux-amd64 && \
    install container-diff-linux-amd64 /usr/local/bin/container-diff



# install airflow
RUN pip install "apache-airflow[celery,crypto,hashicorp,password,postgres,redis]==2.4.3" --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.4.3/constraints-3.8.txt"

RUN mkdir /opt/train_data
COPY ./airflow/requirements.txt /tmp/requirements.txt


RUN pip install -r /tmp/requirements.txt && \
    pip install git+https://github.com/PHT-Medic/train-container-library.git

COPY . /opt/station
RUN pip install /opt/station


COPY ./airflow/ /root/airflow/
RUN chmod +x /root/airflow/entrypoint.sh
ENTRYPOINT ["/root/airflow/entrypoint.sh"]
CMD ["webserver"]

