# lento

## Build Docker Image

```sh
docker build -t lento .
```

## Start Server

```sh
docker run -d --name lento-server -p 8000:8000 lento
```

## Convert to markdown

```sh
curl http://127.0.0.1:8000/to_markdown -F "file=@sample.docx"
```

## LLM chat API

```sh
curl -v http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ],
    "stream": true
  }'
```
