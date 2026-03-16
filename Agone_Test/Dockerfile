# Usa un'immagine base con Python e Java
FROM python:3.9-slim

# Installa le dipendenze Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install mvn
RUN apt update && apt install -y maven

# Copia il codice sorgente nel container
COPY . /app

# Imposta la directory di lavoro
WORKDIR /app

# Comando di default per eseguire lo script
CMD ["python", "agone_test.py"]