# Document-to-plan contract

GISdo injects this skill only after the fixed system/tool cache prefix.

## `document_corpus`

- `documents[].path`: absolute source path.
- `documents[].kind`: detected format.
- `documents[].sha256`: source identity.
- `documents[].content_markdown`: locally extracted content.
- `documents[].truncated`: content was capped before model context.
- `documents[].warnings`: extraction limitations, including OCR requirements.
- `total_characters`: characters included in context.
- `truncated`: at least one source or the whole corpus was capped.

## Requirement mapping

For each requested artifact, determine:

1. The source clause or document.
2. Required GIS inputs and their inspected metadata.
3. Missing user-owned business parameters.
4. Processing and cartographic steps.
5. Output path and format.
6. Automated validation and visual checks.

Use document filenames in the plan goal or step identifiers when traceability would otherwise be ambiguous. Do not place raw document text in ArcPy parameters.
