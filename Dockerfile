FROM python:3.12

WORKDIR /app

# Copy requirements first
COPY requirements.txt .

# Install dependencies from requirements.txt
RUN pip3 install -r requirements.txt

# Copy the rest of the application
COPY iss_tracker.py test_iss_tracker.py ./

RUN chmod +rx iss_tracker.py

ENV PATH="/app:$PATH"

CMD ["python", "iss_tracker.py"]