FROM python:3.9
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Permission fix
RUN chmod +x main.py
EXPOSE 8080
CMD ["python", "main.py"]
