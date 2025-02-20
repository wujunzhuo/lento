import logging
from tempfile import TemporaryDirectory
from fastapi import FastAPI, UploadFile, HTTPException
from markitdown import MarkItDown

app = FastAPI()


@app.post("/to_markdown")
async def to_markdown(file: UploadFile):
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
            logging.error(f"Error converting file {file.filename}: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Error converting file {file.filename}: {e}")
