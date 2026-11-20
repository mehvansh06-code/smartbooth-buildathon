import os
from google import genai

client = genai.Client(vertexai=True, project='project-de57b711-daa3-47db-9c0', location='us-central1')

for m in ['gemini-1.5-flash', 'gemini-1.5-flash-002', 'gemini-2.0-flash-exp', 'gemini-2.5-flash']:
    try:
        response = client.models.generate_content(
            model=m,
            contents='Hello in Hindi'
        )
        print(f"Model {m} Success: {response.text.strip()}")
        break
    except Exception as e:
        print(f"Model {m} Error: {e}")
