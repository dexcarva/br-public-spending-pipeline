# Container do painel Streamlit (serviço "streamlit" no docker-compose.yml).
#
# python:3.11-slim (não a imagem cheia): a slim corta ~700MB de ferramentas
# de build e docs que um app que só roda Python puro + wheels nunca usa.
FROM python:3.11-slim

WORKDIR /app

# requirements ANTES do código, em camadas separadas: o Docker cacheia cada
# camada — mudar o app.py (frequente) reaproveita a camada do pip install
# (lenta); só mudar o requirements.txt invalida a instalação. Ordem de
# camadas = ordem de frequência de mudança, do mais estável pro mais volátil.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8501

# --server.address=0.0.0.0: dentro do container, "localhost" significa "só o
# próprio container" — pra porta publicada no compose alcançar o app, ele
# precisa escutar em todas as interfaces. --server.headless evita o prompt
# interativo de primeira execução do Streamlit (container não tem teclado).
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.headless=true"]
