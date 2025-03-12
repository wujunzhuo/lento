import io
import logging
import os
import shutil
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, HTTPException
from http import HTTPStatus
from tempfile import TemporaryDirectory
from fastapi.responses import StreamingResponse
from markitdown import MarkItDown
from sqlmodel import select
from sqlalchemy.orm import load_only
from .models import (
    SessionDep, KnowledgeBase, DocFile, MarkdownFile, create_db_and_tables
)


logger = logging.getLogger("uvicorn")


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)


# convert a file to markdown
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


# create a new knowledge-base
@app.post("/kb/")
async def create_kb(
    name: str,
    description: str,
    session: SessionDep,
):
    logger.info(f"create knowledge base: {name}")

    kb = KnowledgeBase(
        name=name,
        description=description,
    )
    session.add(kb)
    session.commit()
    session.refresh(kb)
    return {"kb_id": kb.id}


# get the knowledge-base list
@app.get("/kb/")
async def get_kb_list(
    session: SessionDep,
):
    kb_list = session.exec(select(KnowledgeBase)).all()
    return {"kb_list": kb_list}


# upload a file to a knowledge-base
@app.post("/kb/{kb_id}/doc/")
async def upload_file(
    kb_id: int,
    file: UploadFile,
    session: SessionDep,
):
    logger.info(f"upload file {file.filename} to knowledge base {kb_id}")
    content = await file.read()
    doc_file = DocFile(
        filename=file.filename,
        suffix=file.filename.split(".")[-1],
        content=content,
        kb_id=kb_id,
    )
    session.add(doc_file)
    session.commit()
    session.refresh(doc_file)
    return {"doc_id": doc_file.id}


# get the document list of a knowledge-base
@app.get("/kb/{kb_id}/doc/")
async def get_doc_list(
    kb_id: int,
    session: SessionDep,
):
    doc_list = session.exec(
        select(DocFile).options(
            load_only(
                DocFile.id,
                DocFile.filename,
                DocFile.suffix,
                DocFile.created_at,
            ),
        ).where(DocFile.kb_id == kb_id)).all()
    return {"doc_list": doc_list}


# download a document
@app.get("/kb/{kb_id}/doc/{doc_id}/download")
async def download_doc(
    kb_id: int,
    doc_id: int,
    session: SessionDep,
):
    doc_file = session.get(DocFile, doc_id)
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")
    if doc_file.kb_id != kb_id:
        raise HTTPException(HTTPStatus.FORBIDDEN,
                            "Document does not belong to the knowledge base")

    return StreamingResponse(
        io.BytesIO(doc_file.content),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={doc_file.filename}",
        },
    )


# convert a doc to markdown
@app.post("/kb/{kb_id}/doc/{doc_id}/to_markdown")
async def doc_to_markdown(
    kb_id: int,
    doc_id: int,
    session: SessionDep,
):
    logger.info(f"convert markdown for doc: {doc_id}")

    # get the document content
    doc = session.get(DocFile, doc_id)
    if not doc:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")
    if doc.kb_id != kb_id:
        raise HTTPException(HTTPStatus.FORBIDDEN,
                            "Document does not belong to the knowledge base")

    # convert the document content to markdown
    with TemporaryDirectory() as tmpdir:
        filepath = f"{tmpdir}/{doc.filename}"
        with open(filepath, "wb") as f:
            f.write(doc.content)

        try:
            md = MarkItDown()
            md_content = md.convert(filepath).text_content
        except BaseException as e:
            logger.error(f"Error converting file {doc.filename}: {e}")
            raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    md_file = MarkdownFile(doc_id=doc_id, content=md_content)
    session.add(md_file)
    session.commit()
    session.refresh(md_file)
    return {"id": md_file.id}


# get the markdown file list of a document
@app.get("/kb/{kb_id}/doc/{doc_id}/markdown")
async def get_markdown_files(
    kb_id: int,
    doc_id: int,
    session: SessionDep,
):
    doc_file = session.get(DocFile, doc_id)
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")
    if doc_file.kb_id != kb_id:
        raise HTTPException(HTTPStatus.FORBIDDEN,
                            "Document does not belong to the knowledge base")

    md_files = session.exec(
        select(MarkdownFile).where(MarkdownFile.doc_id == doc_id)).all()

    return {"md_list": md_files}


# generate summary for a markdown file
@app.post("/kb/{kb_id}/markdown/{md_id}/summary")
async def generate_summary(
    kb_id: int,
    md_id: int,
    session: SessionDep,
):
    md_file = session.get(MarkdownFile, md_id)
    if not md_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Markdown file not found")

    doc_file = session.get(DocFile, md_file.doc_id)
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")
    if doc_file.kb_id != kb_id:
        raise HTTPException(HTTPStatus.FORBIDDEN,
                            "Document does not belong to the knowledge base")

    md_file.summary = generate_markdown_summary(md_file.content)
    session.add(md_file)
    session.commit()
    session.refresh(md_file)

    return {"markdown": md_file}


def generate_markdown_summary(content: str) -> str:
    # todo: implement summary generation logic from LLM
    return content[:100] + "..." if len(content) > 100 else content


# export the knowledge base data
@app.get("/kb/{kb_id}/export")
async def export_knowledge_base(
    kb_id: int,
    session: SessionDep,
):
    kb = session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Knowledge base not found")

    doc_list = session.exec(
        select(DocFile).options(
            load_only(
                DocFile.id,
                DocFile.filename,
                DocFile.suffix,
                DocFile.created_at,
            ),
        ).where(DocFile.kb_id == kb_id)).all()
    md_files = session.exec(
        select(MarkdownFile).where(
            MarkdownFile.doc_id.in_([doc.id for doc in doc_list]))).all()

    with TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "data")
        md_dir = os.path.join(data_dir, "markdown")
        os.makedirs(md_dir)

        with open(os.path.join(data_dir, "summary.txt"), "w") as f_summary:
            for md_file in md_files:
                with open(os.path.join(md_dir, f"{md_file.id}.md"), "w") as f:
                    f.write(md_file.content)

            summary = md_file.summary.replace("\n", " ")
            f_summary.write(f"{md_file.id}:{summary}\n\n")

        zip_file = os.path.join(tmpdir, f"{kb.id}.zip")
        shutil.make_archive(zip_file, "zip", data_dir)

        with open(zip_file + ".zip", "rb") as f:
            zip_content = f.read()

    return StreamingResponse(
        io.BytesIO(zip_content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={kb.id}.zip",
        },
    )
