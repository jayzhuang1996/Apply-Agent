import requests
import json

url = 'http://localhost:8000/run'
files = {
    'resume': open('/Users/jayzhuang/Desktop/Jay Zhuang_Resume  .docx', 'rb'),
    'cover_letter': open('/Users/jayzhuang/Desktop/Cover_Letter_Agent.docx', 'rb')
}
data = {
    'job_url': 'https://ats.rippling.com/en-CA/opendoor/jobs/f572e889-0644-4590-8a5a-64f73d7db17d/apply?step=application'
}

print("Submitting files to the agent backend...")
response = requests.post(url, files=files, data=data)
res_data = response.json()
print("Response:", res_data)

if 'session_id' in res_data:
    session_id = res_data['session_id']
    print(f"Listening to SSE for session {session_id}...")
    stream_url = f'http://localhost:8000/stream/{session_id}'
    with requests.get(stream_url, stream=True) as response:
        for line in response.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                if decoded_line.startswith('data: '):
                    data_str = decoded_line[6:]
                    print("Data:", data_str)
                    try:
                        msg = json.loads(data_str)
                        if msg.get("type") == "done":
                            print("\n\n=== DONE ===")
                            print(json.dumps(msg, indent=2))
                            break
                        elif msg.get("type") == "error":
                            print("ERROR:", msg)
                            break
                    except Exception as e:
                        pass
