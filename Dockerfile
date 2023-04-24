FROM continuumio/anaconda3

RUN wget https://github.com/dolthub/dolt/releases/download/v0.75.12/dolt-linux-amd64.tar.gz -O /tmp/dolt-linux-amd64.tar.gz && cd /tmp && tar -zxvf /tmp/dolt-linux-amd64.tar.gz && cp /tmp/dolt-linux-amd64/bin/dolt /usr/bin/ && rm -rf /tmp/*
RUN apt update && apt install -y git psmisc zip gcc g++
RUN cd / && dolt clone chenditc/investment_data
RUN cd /investment_data && git init && git pull https://github.com/chenditc/investment_data.git
RUN  pip install numpy && pip install --upgrade cython \
   && cd / && git clone https://github.com/microsoft/qlib.git \
   && cd /qlib/ && pip install . && pip install -r scripts/data_collector/yahoo/requirements.txt
COPY ./requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY . /investment_data/
WORKDIR /investment_data/
