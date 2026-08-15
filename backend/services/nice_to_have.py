NICE_TO_HAVE_STUB_SCORE = 0.5   # neutral: same for every product, so it cannot change ordering


# LLM call #3 lands in a later phase; until then every product scores neutral
async def score(product: dict, nice_to_haves: list[str]) -> float:
    return NICE_TO_HAVE_STUB_SCORE
