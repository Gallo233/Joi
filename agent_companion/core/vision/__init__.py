from agent_companion.core.vision.observer import VisionObserver
from agent_companion.core.vision.ocr import OcrExtractor, OcrResult, OcrTextBlock, PytesseractOcrExtractor, UnavailableOcrExtractor
from agent_companion.core.vision.regions import OcrRegion, group_ocr_regions, regions_to_agent_state, summarize_ocr_regions
from agent_companion.core.vision.schemas import CaptureRect, VisionObservation
from agent_companion.core.vision.summarizer import MockSummarizer, OpenAIVisionSummarizer, VisionSummarizer, VisionSummary
from agent_companion.core.vision.targeting import TargetCandidate, resolve_target_candidates
from agent_companion.core.vision.windows import WindowsScreenObserver

__all__ = [
    "MockSummarizer",
    "OcrExtractor",
    "OcrRegion",
    "OcrResult",
    "OcrTextBlock",
    "OpenAIVisionSummarizer",
    "PytesseractOcrExtractor",
    "CaptureRect",
    "TargetCandidate",
    "UnavailableOcrExtractor",
    "VisionObservation",
    "VisionObserver",
    "VisionSummarizer",
    "VisionSummary",
    "WindowsScreenObserver",
    "group_ocr_regions",
    "regions_to_agent_state",
    "resolve_target_candidates",
    "summarize_ocr_regions",
]
