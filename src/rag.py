"""RAG trên quy chế chi tiêu: tách theo điều, tìm kiếm kết hợp BM25 + embedding (RRF), truy xuất bắt buộc theo rule.

Điều liên quan tới rule đã vi phạm được lấy theo metadata (logic xác định, không phụ thuộc tìm kiếm);
tìm kiếm ngữ nghĩa chỉ bổ sung các điều về ngữ cảnh (nhóm chi phí, dấu hiệu bất thường, câu hỏi tự do).
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = ROOT / 'data' / 'policies' / 'quy_che_chi_tieu.md'
CACHE_DIR = ROOT / '.cache' / 'fastembed'
DEFAULT_EMBED_MODEL = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
RRF_K = 60


@dataclass
class Chunk:
    id: str
    title: str
    chapter: str
    text: str
    rules: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return f'{self.title}\n{self.text}'


def parse_policy(path: Path = POLICY_FILE) -> list[Chunk]:
    """Mỗi '### Điều N. ...' là một chunk; dòng <!-- id: | rules: | groups: | signals: --> là metadata."""
    chunks, chapter = [], ''
    for block in re.split(r'\n(?=## |### )', Path(path).read_text(encoding='utf-8')):
        if block.startswith('## '):
            chapter = block.splitlines()[0][3:].strip()
            continue
        if not block.startswith('### '):
            continue
        lines = block.strip().splitlines()
        meta = dict(re.findall(r'(\w+):\s*([^|]*)', lines[1].replace('<!--', '').replace('-->', '')))
        split = lambda k: [x.strip() for x in meta.get(k, '').split(',') if x.strip()]
        chunks.append(Chunk(id=meta['id'].strip(), title=lines[0][4:].strip(), chapter=chapter,
                            text='\n'.join(lines[2:]).strip(),
                            rules=split('rules'), groups=split('groups'), signals=split('signals')))
    return chunks


def tokenize(text: str) -> list[str]:
    """Âm tiết + cặp âm tiết liền nhau: tiếng Việt có nhiều từ ghép hai âm tiết ("hóa đơn", "khách sạn")."""
    syl = re.findall(r'\w+', text.lower())
    return syl + [f'{a}_{b}' for a, b in zip(syl, syl[1:])]


class PolicyRetriever:
    def __init__(self, chunks: list[Chunk] | None = None, embed_model: str | None = DEFAULT_EMBED_MODEL):
        from rank_bm25 import BM25Okapi

        self.chunks = chunks or parse_policy()
        self.by_id = {c.id: c for c in self.chunks}
        self.bm25 = BM25Okapi([tokenize(c.full_text) for c in self.chunks])
        self.embedder = None
        if embed_model:
            from fastembed import TextEmbedding
            os.environ.setdefault('HF_HUB_DISABLE_SYMLINKS_WARNING', '1')
            self.embedder = TextEmbedding(embed_model, cache_dir=str(CACHE_DIR))
            self.doc_vecs = self._embed([c.full_text for c in self.chunks])

    def _embed(self, texts: list[str]) -> np.ndarray:
        v = np.array(list(self.embedder.embed(texts)))
        return v / np.linalg.norm(v, axis=1, keepdims=True)

    # ------------------------------------------------------------ các cách xếp hạng
    def rank_bm25(self, query: str) -> list[str]:
        scores = self.bm25.get_scores(tokenize(query))
        return [self.chunks[i].id for i in np.argsort(-scores)]

    def rank_dense(self, query: str) -> list[str]:
        sims = self.doc_vecs @ self._embed([query])[0]
        return [self.chunks[i].id for i in np.argsort(-sims)]

    def rank_hybrid(self, query: str) -> list[str]:
        rankings = [self.rank_bm25(query)] + ([self.rank_dense(query)] if self.embedder else [])
        score = {}
        for ranking in rankings:
            for r, cid in enumerate(ranking):
                score[cid] = score.get(cid, 0) + 1 / (RRF_K + r + 1)
        return sorted(score, key=score.get, reverse=True)

    def search(self, query: str, k: int = 4, method: str = 'hybrid') -> list[Chunk]:
        ranking = {'bm25': self.rank_bm25, 'dense': self.rank_dense, 'hybrid': self.rank_hybrid}[method](query)
        return [self.by_id[i] for i in ranking[:k]]

    # ------------------------------------------------------------ truy xuất cho một hồ sơ
    def required_for(self, rule_ids=(), signals=(), groups=()) -> list[Chunk]:
        """Điều bắt buộc phải có: gắn với rule đã vi phạm, hoặc dấu hiệu bất thường cụ thể, hoặc nhóm chi phí."""
        rule_ids, signals, groups = set(rule_ids), set(signals), set(groups)
        # Điều dành riêng cho nhóm chi phí (vd vé máy bay) chỉ áp dụng khi giao dịch thuộc đúng nhóm đó.
        fits = lambda c: not c.groups or bool(groups & set(c.groups))
        out = [c for c in self.chunks if rule_ids & set(c.rules) and fits(c)]
        out += [c for c in self.chunks if c not in out and groups & set(c.groups)]
        out += [c for c in self.chunks if c not in out and signals & set(c.signals) and fits(c) and c.id != 'D24']
        if signals:
            out.append(self.by_id['D24'])        # điều chung về dấu hiệu bất thường và mức rủi ro
        return out

    def retrieve_for_case(self, query: str, rule_ids=(), signals=(), groups=(), k_extra: int = 2,
                          max_total: int = 10) -> list[Chunk]:
        required = self.required_for(rule_ids, signals, groups)
        extra = [c for c in self.search(query, k=k_extra + len(required)) if c not in required][:k_extra]
        return (required + extra)[:max_total]
