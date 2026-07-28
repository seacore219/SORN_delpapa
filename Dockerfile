FROM --platform=linux/amd64 python:2.7-slim

WORKDIR /sorn

ENV MPLBACKEND=Agg

COPY requirements.txt .

RUN echo "deb http://archive.debian.org/debian buster main" > /etc/apt/sources.list \
    && echo "deb http://archive.debian.org/debian-security buster/updates main" >> /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y time \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "cd common && /usr/bin/time -v python -u test_single.py ${PARAM_MODULE:-delpapa.param_FrozenPlasticity}"]