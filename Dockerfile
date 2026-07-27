FROM --platform=linux/amd64 python:2.7-slim

WORKDIR /sorn

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "cd common && python test_single.py ${PARAM_MODULE:-delpapa.param_FrozenPlasticity}"]