class DocumentError(Exception):
    """Permanent failure: retrying will not help."""
    code = "document_error"


class UnsupportedFile(DocumentError):
    code = "unsupported_file"


class CorruptFile(DocumentError):
    code = "corrupt_file"


class EncryptedFile(DocumentError):
    code = "encrypted_file"


class NoContent(DocumentError):
    code = "no_extractable_content"


class OcrUnavailable(DocumentError):
    """The OCR engine (Tesseract) cannot be run. An environment problem, not a problem with the file:
    install Tesseract / set TESSERACT_CMD, then reprocess."""
    code = "ocr_unavailable"
