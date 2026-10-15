# Build a partir da RAIZ do repositório:  docker build -f h4-bot-whatsapp/Dockerfile -t bot-hiper .
FROM python:3.12-slim
WORKDIR /app
COPY hiper-sdk ./hiper-sdk
COPY h1-mock-api-hiper ./h1-mock-api-hiper
COPY h4-bot-whatsapp ./h4-bot-whatsapp
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" httpx pydantic pyjwt rapidfuzz && pip install --no-cache-dir -e ./hiper-sdk
WORKDIR /app/h4-bot-whatsapp
ENV BOT_BANCO=/dados/bot.db
VOLUME ["/dados"]
EXPOSE 8300
CMD ["python", "-m", "bot"]
