FROM python:3.12-slim

# Shared logging library
COPY shared/ /tmp/shared/
RUN pip install --no-cache-dir /tmp/shared/ && rm -rf /tmp/shared/

WORKDIR /app

COPY telegram-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY telegram-bot/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

COPY telegram-bot/ .

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python3", "-u", "bot.py"]
