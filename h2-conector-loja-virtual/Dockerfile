# Build a partir da RAIZ do repositório:  docker build -f h2-conector-loja-virtual/Dockerfile -t conector-hiper .
FROM python:3.12-slim
WORKDIR /app
COPY hiper-sdk ./hiper-sdk
COPY h1-mock-api-hiper ./h1-mock-api-hiper
COPY h2-conector-loja-virtual ./h2-conector-loja-virtual
RUN pip install --no-cache-dir fastapi "uvicorn[standard]" httpx pydantic pyjwt && pip install --no-cache-dir -e ./hiper-sdk
WORKDIR /app/h2-conector-loja-virtual
ENV CONECTOR_BANCO=/dados/conector.db
VOLUME ["/dados"]
EXPOSE 8200
CMD ["python", "-m", "conector", "painel"]
