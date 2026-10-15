FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY analisador ./analisador
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "analisador/dashboard.py", "--server.address=0.0.0.0", "--server.headless=true"]
