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
