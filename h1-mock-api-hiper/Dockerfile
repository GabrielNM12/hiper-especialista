FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mock_hiper ./mock_hiper
COPY contrato ./contrato
ENV PORT=8000 MOCK_HIPER_URL_PUBLICA=http://localhost:8000
EXPOSE 8000
CMD ["python", "-m", "mock_hiper"]
