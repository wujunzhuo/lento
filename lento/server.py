import logging
import yaml
from datetime import datetime
from fastapi import FastAPI, Depends, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from http import HTTPStatus
from tempfile import TemporaryDirectory
from markitdown import MarkItDown
from openai import OpenAI, APIStatusError
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
    ChatCompletionToolChoiceOptionParam,
)
from openai.types.model import Model
from pydantic import BaseModel
from typing import Annotated, Dict, Optional, List


with open("./lento.yaml", 'r') as file:
    config = yaml.safe_load(file)
    MODELS: Dict[str, str] = config.get("models", [])
    DEFAULT_MODEL = config.get("default_model", "")


logger = logging.getLogger("uvicorn")


app = FastAPI()


@app.post("/to_markdown")
async def to_markdown(file: UploadFile):
    logger.info(f"convert markdown: {file.filename}")

    content = await file.read()

    with TemporaryDirectory() as tmpdir:
        filepath = f"{tmpdir}/{file.filename}"
        with open(filepath, "wb") as f:
            f.write(content)

        try:
            md = MarkItDown()
            md_content = md.convert(filepath).text_content
            return {"markdown": md_content}
        except BaseException as e:
            logger.error(f"Error converting file {file.filename}: {e}")
            raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            Model(
                id=model,
                created=int(datetime.now().timestamp()),
                object="model",
                owned_by="system",
            )
            for model in MODELS
        ]
    }


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatCompletionMessageParam]
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    tools: List[ChatCompletionToolParam] = None
    tool_choice: Optional[ChatCompletionToolChoiceOptionParam] = None


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


@app.post("/v1/chat/completions", )
async def chat_completions(
    req: ChatCompletionRequest,
    token: Annotated[str, Depends(oauth2_scheme)],
):
    logger.info("new request")

    if not req.model:
        req.model = DEFAULT_MODEL

    base_url = MODELS.get(req.model)
    if not base_url:
        raise HTTPException(HTTPStatus.NOT_FOUND, "model not found")

    logger.info(f"model: {req.model}, stream: {req.stream}")
    logger.info(f"messages: {req.messages}")

    client = OpenAI(base_url=base_url, api_key=token, max_retries=0)

    params = dict(
        model=req.model,
        messages=req.messages,
        stream=req.stream,
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_tokens,
        max_completion_tokens=req.max_completion_tokens,
        presence_penalty=req.presence_penalty,
        frequency_penalty=req.frequency_penalty,
    )
    if req.tool_choice:
        logger.info(f"tools: {req.tools}")
        params["tools"] = req.tools
        params["tool_choice"] = req.tool_choice

    try:
        completion = client.chat.completions.create(**params)

        if req.stream:
            async def event_generator():
                for chunk in completion:
                    if chunk.choices:
                        if content := chunk.choices[0].delta.content:
                            logger.info(f"stream: {content}")
                    yield f"data: {chunk.model_dump_json()}\n\n"
                logger.info("stream: [DONE]")
                yield "data: [DONE]\n\n"
            return StreamingResponse(
                event_generator(), media_type="text/event-stream")

        if completion.choices:
            choice = completion.choices[0]
            if choice.finish_reason == "tool_calls":
                for tool_call in choice.message.tool_calls:
                    f = tool_call.function
                    logger.info(f"invoked tool_call: {f.name}, {f.arguments}")
            else:
                logger.info(f"response: {choice.message.content}")
        return completion

    except APIStatusError as e:
        logger.error(f"APIStatusError: {e}")
        raise HTTPException(e.status_code, e.message)
    except Exception as e:
        logger.error(f"Error: {e}")
        raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
