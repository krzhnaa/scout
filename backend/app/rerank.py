"""Local, free reranking of search snippets before they hit an LLM.
Uses sentence-transformers if installed; otherwise degrades gracefully to a
simple truncation so the agent still runs without the extra dependency."""

_model = None


def _get_model():
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer("all-MiniLM-L6-v2")
        except Exception:
            _model = False  # sentinel: unavailable, skip reranking
    return _model


def rerank_snippets(goal: str, snippets: list[dict], top_k: int = 6) -> list[dict]:
    if not snippets:
        return snippets

    model = _get_model()
    if model is False or model is None:
        return snippets[:top_k]

    import numpy as np

    texts = [f"{s.get('title', '')} {s.get('snippet', '')} {s.get('content', '')[:500]}" for s in snippets]
    goal_emb = model.encode([goal])[0]
    text_embs = model.encode(texts)

    def cos_sim(a, b):
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    scored = sorted(zip(snippets, text_embs), key=lambda p: cos_sim(goal_emb, p[1]), reverse=True)
    return [s for s, _ in scored[:top_k]]
