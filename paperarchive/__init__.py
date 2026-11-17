"""paper-archive: paper medical documents -> reviewed transcription -> FHIR R4 -> Medplum."""

MAPPING_VERSION = "0.4.0"
EXTRACTION_FORMAT = "paper-archive/extraction/1"
DEFAULT_BASE = "https://github.com/jollyjoker992/paper-archive/fhir"
DOCUMENT_TYPES = ("lab-report", "ecg", "ultrasound", "xray", "endoscopy", "visit", "procedure", "vaccination")
