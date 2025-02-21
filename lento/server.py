import logging
import os
from fastapi import FastAPI, UploadFile, HTTPException
from fastapi.responses import StreamingResponse
from http import HTTPStatus
from tempfile import TemporaryDirectory
from markitdown import MarkItDown
from openai import OpenAI, APIStatusError
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
    ChatCompletionToolChoiceOptionParam,
)
from pydantic import BaseModel
from typing import Optional, List


QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "http://127.0.0.1:8001/v1")
QWEN_TOKEN = os.getenv("QWEN_TOKEN", "****")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen2.5:32b")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "http://127.0.0.1:8002/v1")
DEEPSEEK_TOKEN = os.getenv("DEEPSEEK_TOKEN", "****")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-r1:32b")


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


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = "auto"
    messages: List[ChatCompletionMessageParam]
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    tools: List[ChatCompletionToolParam] = None
    tool_choice: Optional[ChatCompletionToolChoiceOptionParam] = None


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest):
    logger.info(f"model: {req.model}, stream: {req.stream}")
    logger.info(f"messages: {req.messages}")

    model = req.model
    if not model or model == "auto":
        if req.tool_choice:
            model = QWEN_MODEL
        else:
            model = DEEPSEEK_MODEL

    if model == QWEN_MODEL:
        base_url = QWEN_BASE_URL
        api_key = QWEN_TOKEN
    elif model == DEEPSEEK_MODEL:
        base_url = DEEPSEEK_BASE_URL
        api_key = DEEPSEEK_TOKEN
    else:
        raise HTTPException(HTTPStatus.NOT_FOUND, f"model [{model}] not found")

    client = OpenAI(base_url=base_url, api_key=api_key, max_retries=0)

    params = dict(
        model=model,
        messages=req.messages,
        stream=req.stream,
        temperature=req.temperature,
        top_p=req.top_p,
        max_tokens=req.max_tokens,
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
