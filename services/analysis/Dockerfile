FROM python:3.12-slim

WORKDIR /app

COPY shared /app/shared
COPY services/analysis/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir /app/shared -r /app/requirements.txt \
    && python -m nltk.downloader vader_lexicon

COPY services/analysis/analysis_app /app/analysis_app

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "analysis_app", "--watch"]
