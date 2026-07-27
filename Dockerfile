FROM --platform=linux/amd64 python:2.7-slim

WORKDIR /sorn

ENV MPLBACKEND=Agg

COPY requirements.txt .

RUN apt-get update && apt-get install -y time && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "cd common && /usr/bin/time -v python test_single.py ${PARAM_MODULE:-delpapa.param_FrozenPlasticity}"]