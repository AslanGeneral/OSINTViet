"""S2 classifier: deterministic lexicon plus existing embedding prototypes.

The classifier only recommends a mode.  ``requested_mode`` always wins when it
is explicit.  The decision threshold is loaded from the calibration file
produced by ``scripts/calibrate_pipeline_v2.py``; there is no hard-coded
threshold in this module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.pipeline_v2.config import (
    Calibration,
    ClassifierConfig,
    load_calibration,
    load_pipeline_config,
)
from app.pipeline_v2.contracts import (
    ClassifierResult,
    PersonalContext,
    RawInput,
)
from app.pipeline_v2.errors import ClassifierError


@dataclass
class ClassifierScores:
    macro_score: float = 0.0
    micro_score: float = 0.0
    macro_lexicon: float = 0.0
    micro_lexicon: float = 0.0
    macro_embedding: Optional[float] = None
    micro_embedding: Optional[float] = None
    macro_hits: List[str] = field(default_factory=list)
    micro_hits: List[str] = field(default_factory=list)
    method: str = "lexicon"
    warnings: List[str] = field(default_factory=list)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if left is None or right is None or len(left) != len(right) or len(left) == 0:
        return 0.0
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    left_norm = math.sqrt(sum(float(a) * float(a) for a in left))
    right_norm = math.sqrt(sum(float(b) * float(b) for b in right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _mean_vector(vectors: Sequence[Sequence[float]]) -> List[float]:
    if not vectors:
        return []
    dimension = len(vectors[0])
    return [
        sum(float(vector[index]) for vector in vectors) / len(vectors)
        for index in range(dimension)
    ]


class InputClassifier:
    def __init__(
        self,
        config: Optional[ClassifierConfig] = None,
        calibration: Optional[Calibration] = None,
        *,
        embedding_client: Any = None,
    ) -> None:
        pipeline_config = load_pipeline_config()
        self.config = config or pipeline_config.classifier
        self.calibration = calibration or load_calibration(
            config=pipeline_config
        )
        self._embedding_client = embedding_client
        self._prototype_vectors: Optional[tuple[List[float], List[float]]] = None

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def _lexicon_score(self, text: str, terms: Sequence[str]) -> tuple[float, List[str]]:
        lowered = text.lower()
        hits = [term for term in terms if term.lower() in lowered]
        score = min(1.0, len(hits) / max(1, self.config.lexicon_saturation))
        return score, hits

    async def _get_prototype_vectors(self) -> Optional[tuple[List[float], List[float]]]:
        if self._prototype_vectors is not None:
            return self._prototype_vectors
        if not self.config.macro_prototypes or not self.config.micro_prototypes:
            return None
        if self._embedding_client is None:
            try:
                from app.services.embedding_engine import embedding_engine

                self._embedding_client = embedding_engine
            except Exception:
                return None
        texts = (
            list(self.config.macro_prototypes)
            + list(self.config.micro_prototypes)
        )
        try:
            vectors = await self._embedding_client.get_embeddings_batch(
                texts, input_type="search_document"
            )
        except Exception:
            return None
        if not vectors or any(vector is None for vector in vectors):
            return None
        split = len(self.config.macro_prototypes)
        macro = _mean_vector(
            [vector for vector in vectors[:split] if vector is not None]
        )
        micro = _mean_vector(
            [vector for vector in vectors[split:] if vector is not None]
        )
        if not macro or not micro:
            return None
        self._prototype_vectors = (macro, micro)
        return self._prototype_vectors

    async def _embedding_scores(
        self, text: str
    ) -> tuple[Optional[float], Optional[float]]:
        try:
            prototypes = await self._get_prototype_vectors()
            if prototypes is None:
                return None, None
            vectors = await self._embedding_client.get_embeddings_batch(
                [text], input_type="search_document"
            )
            if not vectors or vectors[0] is None:
                return None, None
            vector = vectors[0]
            macro_proto, micro_proto = prototypes
            return _cosine(vector, macro_proto), _cosine(vector, micro_proto)
        except Exception:
            return None, None

    @staticmethod
    def _context_text(context: Optional[PersonalContext]) -> str:
        if context is None:
            return ""
        parts = [
            context.free_text,
            context.region,
            context.occupation_sector,
            context.household_notes,
            context.assets_liabilities_notes,
        ]
        return "\n".join(part for part in parts if part)

    async def score(
        self,
        text: str,
        personal_context: Optional[PersonalContext] = None,
    ) -> ClassifierScores:
        combined = f"{text}\n{self._context_text(personal_context)}".strip()
        if not combined:
            raise ClassifierError("Đầu vào phân loại rỗng.")
        combined = combined[: self.config.max_text_chars]

        macro_lex, macro_hits = self._lexicon_score(
            combined, self.config.macro_lexicon
        )
        micro_lex, micro_hits = self._lexicon_score(
            combined, self.config.micro_lexicon
        )
        macro_emb, micro_emb = await self._embedding_scores(combined)

        scores = ClassifierScores(
            macro_lexicon=round(macro_lex, 6),
            micro_lexicon=round(micro_lex, 6),
            macro_embedding=None if macro_emb is None else round(macro_emb, 6),
            micro_embedding=None if micro_emb is None else round(micro_emb, 6),
            macro_hits=macro_hits,
            micro_hits=micro_hits,
        )

        if macro_emb is not None and micro_emb is not None:
            # Map cosine [-1, 1] to [0, 1] before weighting.
            macro_norm = (macro_emb + 1.0) / 2.0
            micro_norm = (micro_emb + 1.0) / 2.0
            scores.macro_score = (
                self.config.lexicon_weight * macro_lex
                + self.config.embedding_weight * macro_norm
            )
            scores.micro_score = (
                self.config.lexicon_weight * micro_lex
                + self.config.embedding_weight * micro_norm
            )
            scores.method = "lexicon+embedding"
        else:
            scores.macro_score = macro_lex
            scores.micro_score = micro_lex
            scores.method = "lexicon"
            scores.warnings.append(
                "Không lấy được vector embedding; dùng điểm từ vựng dự phòng."
            )
        scores.macro_score = round(min(1.0, scores.macro_score), 6)
        scores.micro_score = round(min(1.0, scores.micro_score), 6)
        return scores

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------
    def _topic_tags(self, text: str) -> List[str]:
        lowered = text.lower()
        tags: List[str] = []
        for term, tag in self.config.topic_tags.items():
            if term.lower() in lowered and tag not in tags:
                tags.append(tag)
        return tags

    async def classify(
        self,
        raw_input: RawInput,
        text: Optional[str] = None,
    ) -> ClassifierResult:
        classification_text = text or raw_input.query
        scores = await self.score(classification_text, raw_input.personal_context)
        threshold = float(self.calibration.threshold)
        mixed_margin = float(self.calibration.mixed_margin)

        strong_macro = scores.macro_score >= threshold
        strong_micro = scores.micro_score >= threshold
        if strong_macro and strong_micro:
            label = "mixed"
            confidence = min(scores.macro_score, scores.micro_score)
            recommendation = "dual"
        elif strong_macro:
            label = "macro"
            confidence = scores.macro_score
            recommendation = "macro"
        elif strong_micro:
            label = "micro"
            confidence = scores.micro_score
            recommendation = "micro"
        elif abs(scores.macro_score - scores.micro_score) <= mixed_margin:
            # Weak evidence for both sides: recommend the stronger one but keep
            # confidence below threshold so auto mode asks for a mode.
            if scores.macro_score >= scores.micro_score:
                label = "macro"
                recommendation = "macro"
            else:
                label = "micro"
                recommendation = "micro"
            confidence = max(scores.macro_score, scores.micro_score)
        elif scores.macro_score > scores.micro_score:
            label = "macro"
            recommendation = "macro"
            confidence = scores.macro_score
        else:
            label = "micro"
            recommendation = "micro"
            confidence = scores.micro_score

        rationale = (
            f"Điểm vĩ mô {scores.macro_score:.3f}, vi mô {scores.micro_score:.3f}; "
            f"ngưỡng hiệu lực {threshold:.3f} từ calibration. "
            f"Từ khóa vĩ mô: {', '.join(scores.macro_hits[:8]) or 'không có'}. "
            f"Từ khóa vi mô: {', '.join(scores.micro_hits[:8]) or 'không có'}. "
            f"Phương pháp: {scores.method}."
        )
        return ClassifierResult(
            run_id=raw_input.run_id,
            label=label,  # type: ignore[arg-type]
            topic_tags=self._topic_tags(classification_text),
            confidence=round(max(0.0, min(1.0, confidence)), 6),
            rationale=rationale,
            routing_recommendation=recommendation,  # type: ignore[arg-type]
        )


__all__ = ["ClassifierScores", "InputClassifier"]
