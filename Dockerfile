FROM python:3.12-slim
WORKDIR /app
COPY pattern_bot.py /app/pattern_bot.py
ENV PYTHONUNBUFFERED=1
CMD ["python", "-u", "pattern_bot.py", "--monitor"]
