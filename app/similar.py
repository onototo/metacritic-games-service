"""Similar games from own DB: weighted Jaccard on genres/platforms/developer + description token overlap."""
import re
from .models import Game

STOP = set("the a an and of to in is it for with on as by this that your you from are be or at will its their into game games".split())


def _tokens(text):
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in STOP}


def _j(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def score(a: Game, b: Game) -> float:
    return (0.4 * _j(set(a.genres or []), set(b.genres or []))
            + 0.2 * _j({p.platform for p in a.platforms}, {p.platform for p in b.platforms})
            + 0.15 * (1.0 if a.developer and a.developer == b.developer else 0.0)
            + 0.25 * _j(_tokens(a.description), _tokens(b.description)))


def similar_games(game: Game, all_games: list[Game], k=6):
    scored = [(score(game, g), g) for g in all_games if g.id != game.id]
    scored = [x for x in scored if x[0] > 0.02]
    scored.sort(key=lambda x: -x[0])
    return scored[:k]
