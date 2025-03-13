import io
import logging
import os
import shutil
from contextlib import asynccontextmanager
from typing import List, Optional
from fastapi import FastAPI, APIRouter, UploadFile, HTTPException
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
router = APIRouter(prefix="/api")


@router.post(
    "/to_markdown",
    summary="Convert a file to markdown")
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


@router.post(
    "/kgb/",
    summary="Create a new knowledge base")
async def create_kgb(
    session: SessionDep,
    name: str,
    description: Optional[str] = None,
):
    logger.info(f"create knowledge base: {name}")

    kgb = KnowledgeBase(
        name=name,
        description=description,
    )
    session.add(kgb)
    session.commit()
    session.refresh(kgb)
    return {"kgb_id": kgb.id}


@router.get(
    "/kgb/",
    summary="Get the knowledge base list",
)
async def get_kgb_list(
    session: SessionDep,
):
    kgb_list = session.exec(
        select(KnowledgeBase).order_by(KnowledgeBase.id)).all()
    return {"kgb_list": kgb_list}


@router.post(
    "/kgb/{kgb_id}/doc/",
    summary="Upload a document to a knowledge base",
)
async def upload_doc(
    session: SessionDep,
    kgb_id: int,
    file: UploadFile,
):
    logger.info(f"upload file {file.filename} to knowledge base {kgb_id}")

    content = await file.read()
    doc_file = DocFile(
        filename=file.filename,
        suffix=file.filename.split(".")[-1],
        content=content,
        kgb_id=kgb_id,
    )
    session.add(doc_file)
    session.commit()
    session.refresh(doc_file)
    return {"doc_id": doc_file.id}


@router.get(
    "/kgb/{kgb_id}/doc/",
    summary="Get the document list of a knowledge base",
)
async def get_doc_list(
    session: SessionDep,
    kgb_id: int,
):
    doc_list = session.exec(
        select(DocFile).options(
            load_only(
                DocFile.id,
                DocFile.filename,
                DocFile.suffix,
                DocFile.created_at,
            ),
        ).order_by(DocFile.id).where(DocFile.kgb_id == kgb_id)).all()
    return {"doc_list": doc_list}


@router.delete(
    "/kgb/{kgb_id}",
    summary="Delete a knowledge base",
)
async def delete_kgb(
    session: SessionDep,
    kgb_id: int,
):
    kgb = session.get(KnowledgeBase, kgb_id)
    if not kgb:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Knowledge base not found")
    session.delete(kgb)
    session.commit()
    return {"kgb_id": kgb_id}


@router.get(
    "/doc/{doc_id}/info",
    summary="Get a document info",
)
async def get_doc_info(
    session: SessionDep,
    doc_id: int,
):
    doc_file = session.exec(
        select(DocFile).options(
            load_only(
                DocFile.id,
                DocFile.filename,
                DocFile.suffix,
                DocFile.created_at,
            ),
        ).where(DocFile.id == doc_id)).first()
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")
    return {"doc_info": doc_file}


@router.get(
    "/doc/{doc_id}/download",
    summary="Download a document",
)
async def download_doc(
    session: SessionDep,
    doc_id: int,
):
    doc_file = session.get(DocFile, doc_id)
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")

    filename = doc_file.filename.encode("utf-8").decode("unicode_escape")
    return StreamingResponse(
        io.BytesIO(doc_file.content),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


@router.post(
    "/doc/{doc_id}/to_markdown",
    summary="Convert a doc to markdown",
)
async def doc_to_markdown(
    session: SessionDep,
    doc_id: int,
    max_lines: Optional[int] = None,
):
    logger.info(f"convert markdown for doc: {doc_id}")

    # get the document content
    doc_file = session.get(DocFile, doc_id)
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")

    # convert the document content to markdown
    with TemporaryDirectory() as tmpdir:
        filepath = f"{tmpdir}/{doc_file.filename}"
        with open(filepath, "wb") as f:
            f.write(doc_file.content)

        try:
            md = MarkItDown()
            md_content = md.convert(filepath).text_content
        except BaseException as e:
            logger.error(f"Error converting to markdown: {e}")
            raise HTTPException(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    md_list = []
    for block in split_by_lines(md_content, max_lines):
        md_file = MarkdownFile(
            doc_id=doc_id,
            content=block,
        )
        session.add(md_file)
        session.commit()
        session.refresh(md_file)
        md_list.append(md_file)
    return {"md_list": md_list}


def split_by_lines(input: str, max_lines: int | None) -> List[str]:
    if not max_lines:
        return [input]

    lines = input.split('\n')
    result = []
    current_block = []

    for line in lines:
        current_block.append(line)
        if len(current_block) >= max_lines:
            result.append('\n'.join(current_block))
            current_block = []

    if current_block:
        result.append('\n'.join(current_block))

    return result


@router.delete(
    "/doc/{doc_id}",
    summary="Delete a document",
)
async def delete_doc(
    session: SessionDep,
    doc_id: int,
):
    doc_file = session.get(DocFile, doc_id)
    if not doc_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Document not found")
    session.delete(doc_file)
    session.commit()
    return {"doc_id": doc_id}


@router.get(
    "/doc/{doc_id}/markdown/",
    summary="Get the markdown file list of a document",
)
async def get_markdown_files(
    session: SessionDep,
    doc_id: int,
):
    md_list = session.exec(
        select(MarkdownFile).options(
            load_only(
                MarkdownFile.id,
                MarkdownFile.doc_id,
                MarkdownFile.summary,
                MarkdownFile.created_at,
            ),
        ).order_by(MarkdownFile.id).where(MarkdownFile.doc_id == doc_id)).all()

    return {"md_list": md_list}


@router.get(
    "/markdown/{md_id}",
    summary="Get a markdown file",
)
async def get_markdown_file(
    session: SessionDep,
    md_id: int,
):
    md_file = session.get(MarkdownFile, md_id)
    if not md_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Markdown file not found")
    return {"md_file": md_file}


@router.post(
    "/markdown/{md_id}/summary",
    summary="Generate summary for a markdown file",
)
async def generate_summary(
    session: SessionDep,
    md_id: int,
):
    logger.info(f"Generating summary for markdown file {md_id}")

    md_file = session.get(MarkdownFile, md_id)
    if not md_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Markdown file not found")

    md_file.summary = generate_markdown_summary(md_file.content)
    session.add(md_file)
    session.commit()
    session.refresh(md_file)
    return {"md_file": md_file}


def generate_markdown_summary(content: str) -> str:
    # todo: implement summary generation logic from LLM
    return content[:100] + "..." if len(content) > 100 else content


@router.delete(
    "/markdown/{md_id}",
    summary="Delete a markdown file",
)
async def delete_markdown(
    session: SessionDep,
    md_id: int,
):
    md_file = session.get(MarkdownFile, md_id)
    if not md_file:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Markdown file not found")
    session.delete(md_file)
    session.commit()
    return {"md_id": md_id}


@router.get(
    "/kgb/{kgb_id}/export",
    summary="Export the knowledge base data",
)
async def export_knowledge_base(
    session: SessionDep,
    kgb_id: int,
):
    kgb = session.get(KnowledgeBase, kgb_id)
    if not kgb:
        raise HTTPException(HTTPStatus.NOT_FOUND, "Knowledge base not found")

    doc_ids = session.exec(
        select(DocFile.id).where(DocFile.kgb_id == kgb_id)).all()
    md_list = session.exec(
        select(MarkdownFile).where(MarkdownFile.doc_id.in_(doc_ids))).all()

    with TemporaryDirectory() as tmpdir:
        data_dir = os.path.join(tmpdir, "data")
        md_dir = os.path.join(data_dir, "markdown")
        os.makedirs(md_dir)

        with open(os.path.join(data_dir, "summary.txt"), "w") as f_summary, \
                open(os.path.join(md_dir, "files.txt"), "w") as f_files:
            for md_file in md_list:
                if not md_file.summary:
                    continue

                with open(os.path.join(md_dir, f"{md_file.id}.md"), "w") as f:
                    f.write(md_file.content)

                summary = md_file.summary.replace("\n", " ")
                f_summary.write(f"{md_file.id}:{summary}\n\n")

                doc_file = session.get(DocFile, md_file.doc_id)
                f_files.write(f"{md_file.id}:{doc_file.filename}\n")

        zip_file = os.path.join(tmpdir, f"{kgb.id}.zip")
        shutil.make_archive(zip_file, "zip", data_dir)

        with open(zip_file + ".zip", "rb") as f:
            zip_content = f.read()

    filename = f"{kgb.name}.zip".encode("utf-8").decode("unicode_escape")
    return StreamingResponse(
        io.BytesIO(zip_content),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


app.include_router(router)
