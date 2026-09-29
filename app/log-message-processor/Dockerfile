FROM python:3.11-alpine

WORKDIR /usr/src/app
RUN apk add --no-cache build-base python3-dev
COPY requirements.txt .
RUN pip3 install -r requirements.txt

COPY main.py .

CMD ["python3","-u","main.py"]

