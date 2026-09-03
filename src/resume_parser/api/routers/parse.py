"""Resume parsing and matching routes.

Handlers stay thin on purpose: read the upload, hand it to the service, shape the response.
All error translation happens in the exception handlers, so there is no try/except here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, UploadFile, status

from resume_parser.api.dependencies import CurrentSettings, ParsingService, require_api_key
from resume_parser.api.schemas import (
    BatchItemResult,
    BatchParseResponse,
    MatchRequest,
    MatchResponse,
)
from resume_parser.domain.results import ParseResult
from resume_parser.exceptions import ResumeParserError
from resume_parser.pipeline.parser import BatchItem

__all__ = ["router"]

router = APIRouter(
    prefix="/v1",
    tags=["parsing"],
    dependencies=[Depends(require_api_key)],
)


async def _read_upload(upload: UploadFile, limit: int) -> bytes:
    """Read an upload into memory, stopping as soon as it exceeds ``limit``.

    Reading in chunks and checking as we go means a hostile 2 GB upload is rejected after
    the first oversized chunk instead of being buffered in full first - which is what
    ``await file.read()`` followed by a length check does.
    """
    from resume_parser.exceptions import DocumentTooLargeError

    chunks: list[bytes] = []
    total = 0
    while chunk := await upload.read(1024 * 256):
        total += len(chunk)
        if total > limit:
            raise DocumentTooLargeError(
                f"Document exceeds the {limit}-byte limit.", limit_bytes=limit
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/parse",
    response_model=ParseResult,
    status_code=status.HTTP_200_OK,
    summary="Parse one resume into structured data",
    response_description="The structured resume, plus document provenance and usage metadata.",
)
async def parse_resume(
    service: ParsingService,
    settings: CurrentSettings,
    file: Annotated[UploadFile, File(description="A PDF, DOCX, TXT or Markdown resume.")],
) -> ParseResult:
    """Extract a structured record from an uploaded resume."""
    data = await _read_upload(file, settings.extraction.max_file_size)
    return await service.parse(data, filename=file.filename)


@router.post(
    "/parse/batch",
    response_model=BatchParseResponse,
    summary="Parse many resumes in one request",
    response_description="Per-document outcomes, in request order.",
)
async def parse_batch(
    service: ParsingService,
    settings: CurrentSettings,
    files: Annotated[list[UploadFile], File(description="Up to `server.max_batch_size` files.")],
) -> BatchParseResponse:
    """Parse a set of resumes concurrently.

    Individual failures are reported per document rather than failing the whole request,
    so one corrupt file in a bulk import does not discard the rest.
    """
    from resume_parser.exceptions import ResumeParserError as _Error

    if len(files) > settings.server.max_batch_size:
        raise _Error(
            f"Batch size {len(files)} exceeds the limit of {settings.server.max_batch_size}.",
        )

    items = [
        BatchItem(
            data=await _read_upload(upload, settings.extraction.max_file_size),
            filename=upload.filename,
        )
        for upload in files
    ]
    outcomes = await service.parse_batch(items)

    results: list[BatchItemResult] = []
    for item, outcome in zip(items, outcomes, strict=True):
        if isinstance(outcome, ParseResult):
            results.append(
                BatchItemResult(filename=item.filename, status="success", result=outcome)
            )
        else:
            code = getattr(outcome, "code", "internal_error")
            detail = getattr(outcome, "message", str(outcome))
            results.append(
                BatchItemResult(
                    filename=item.filename,
                    status="error",
                    error_code=code,
                    error_detail=detail,
                )
            )

    succeeded = sum(1 for entry in results if entry.status == "success")
    return BatchParseResponse(
        total=len(results),
        succeeded=succeeded,
        failed=len(results) - succeeded,
        results=results,
    )


@router.post(
    "/match",
    response_model=MatchResponse,
    summary="Score a parsed resume against a job",
    response_description="An explainable fit score with per-dimension sub-scores and gaps.",
)
async def match(service: ParsingService, payload: MatchRequest) -> MatchResponse:
    """Score a resume against a role.

    Pass ``requirements`` to score for free; pass ``job_description`` to have the posting
    structured first with a single, cheap LLM call.
    """
    if payload.resume is None:
        raise ResumeParserError(
            "A 'resume' from a previous /v1/parse response is required.",
        )
    requirements = payload.requirements
    if requirements is None:
        requirements = await service.extract_job_requirements(payload.job_description or "")

    return MatchResponse(
        match=service.match(payload.resume.resume, requirements),
        requirements=requirements,
    )


@router.post(
    "/parse-and-match",
    response_model=MatchResponse,
    summary="Parse a resume and score it against a job in one call",
)
async def parse_and_match(
    service: ParsingService,
    settings: CurrentSettings,
    file: Annotated[UploadFile, File(description="A PDF, DOCX, TXT or Markdown resume.")],
    job_description: Annotated[str, Form(description="The job posting text.")],
) -> MatchResponse:
    """Upload a resume and a job posting, and get a parse plus a score in one round trip."""
    data = await _read_upload(file, settings.extraction.max_file_size)
    parsed = await service.parse(data, filename=file.filename)
    requirements = await service.extract_job_requirements(job_description)
    return MatchResponse(
        match=service.match(parsed.resume, requirements),
        requirements=requirements,
        parse=parsed,
    )
