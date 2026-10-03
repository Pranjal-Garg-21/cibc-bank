"""Explicitly prepare the local metric embedding cache; runtime never downloads it."""
from fastembed import TextEmbedding
from trustissues.ask.metric_matcher import CACHE_DIR, MODEL


if __name__ == "__main__":
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=MODEL, cache_dir=str(CACHE_DIR), threads=2)
    next(model.embed(["Prepare local metric matching"]))
    print(f"Prepared {MODEL} in {CACHE_DIR}")
