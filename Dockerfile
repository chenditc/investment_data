FROM python:3.9

RUN wget https://github.com/dolthub/dolt/releases/download/v1.30.4/dolt-linux-amd64.tar.gz -O /tmp/dolt-linux-amd64.tar.gz && cd /tmp && tar -zxvf /tmp/dolt-linux-amd64.tar.gz && cp /tmp/dolt-linux-amd64/bin/dolt /usr/bin/ && rm -rf /tmp/* && dolt config --global --add user.email "dockeruser@na.com" && dolt config --global --add user.name "dockeruser"
RUN apt update && apt install -y git psmisc zip gcc g++ jq
RUN mkdir -p /dolt
RUN mkdir -p /investment_data

RUN cd /investment_data && git init && git pull https://github.com/chenditc/investment_data.git
RUN  pip install numpy==1.23.5 && pip install --upgrade cython \
   && cd / && git clone https://github.com/microsoft/qlib.git \
   && cd /qlib/ && pip install . && pip install -r scripts/data_collector/yahoo/requirements.txt 
RUN rm -rf /qlib/
COPY ./requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
COPY . /investment_data/
WORKDIR /investment_data/
