# lento

## Build Docker Image

```sh
docker build -t lento .
```

## Start Server

```sh
cp lento.example.yaml lento.yaml

# then edit lento.yaml
```

```sh
docker run -d --name lento-server -v $PWD/lento.yaml:/app/lento.yaml -p 8000:8000 lento
```

## Convert to Markdown

```sh
curl http://127.0.0.1:8000/to_markdown -F "file=@sample.docx"
```

## OpenAI Compatible API

```sh
curl -v http://127.0.0.1:8000/v1/models
```

```sh
export TOKEN=****

curl -v http://127.0.0.1:8000/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [
      {
        "role": "user",
        "content": "Hello!"
      }
    ],
    "stream": true
  }'
```
