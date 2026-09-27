"""Atribuição determinística de tópicos do edital para textos legais.

A classificação lexical (sobreposição de palavras com o título do tópico) falha na lei
seca: tópicos genéricos absorvem quase tudo e títulos como "Nulidades" ficam vazios.
Para os códigos compilados, o tópico decorre da faixa de artigos; para as leis
extravagantes oficiais, do número da lei no nome do arquivo.

As faixas seguem a estrutura de títulos e capítulos do CPM (DL 1.001/1969) e do CPPM
(DL 1.002/1969). Servem apenas para rotear a recuperação de evidências, nunca para
fundamentar gabarito; ajustes finos podem ser feitos aqui sem migração.
"""

from __future__ import annotations

import re
import unicodedata

# (primeiro artigo, último artigo, tópico). Vence a faixa mais estreita; None exclui.
ARTICLE_RANGES: dict[str, tuple[tuple[int, int, str | None], ...]] = {
    "direito_penal_militar": (
        (1, 28, "dpm.1"),
        (9, 9, "dpm.15"),
        (29, 47, "dpm.2"),
        (48, 52, "dpm.3"),
        (53, 54, "dpm.4"),
        (55, 68, "dpm.5"),
        (69, 83, "dpm.6"),
        (84, 88, "dpm.7"),
        (89, 97, "dpm.8"),
        (98, 108, "dpm.9"),
        (109, 109, "dpm.10"),
        (110, 120, "dpm.11"),
        (121, 122, "dpm.12"),
        (123, 135, "dpm.13"),
        (136, 354, "dpm.14"),
        (355, 410, None),  # crimes militares em tempo de guerra: fora do edital
    ),
    "direito_processual_penal_militar": (
        (1, 6, "dppm.1"),
        (7, 8, "dppm.2"),
        (9, 28, "dppm.3"),
        (29, 33, "dppm.4"),
        (34, 35, "dppm.5"),
        (36, 76, "dppm.6"),
        (77, 81, "dppm.7"),
        (82, 121, None),  # foro militar, competência e conflitos: sem tópico próprio
        (122, 127, "dppm.8"),
        (128, 155, "dppm.9"),
        (156, 162, "dppm.10"),
        (163, 169, "dppm.11"),
        (170, 276, "dppm.12"),
        (170, 219, "dppm.13"),
        (220, 276, "dppm.14"),
        (243, 253, "dppm.14.1"),
        (254, 261, "dppm.14.2"),
        (270, 271, "dppm.14.3"),
        (277, 293, "dppm.15"),
        (294, 383, "dppm.16"),
        (302, 306, "dppm.16.1"),
        (307, 310, "dppm.16.2"),
        (314, 346, "dppm.16.3"),
        (347, 364, "dppm.16.4"),
        (365, 367, "dppm.16.5"),
        (368, 370, "dppm.16.6"),
        (371, 381, "dppm.16.7"),
        (382, 383, "dppm.16.8"),
        (384, 497, "dppm.17"),
        (384, 450, "dppm.17.1"),
        (451, 497, "dppm.17.2"),
        (451, 462, "dppm.17.3"),
        (463, 465, "dppm.17.4"),
        (498, 498, "dppm.19.3"),
        (499, 509, "dppm.18"),
        (510, 587, "dppm.19"),
        (510, 515, "dppm.19.1"),
        (516, 525, "dppm.19.2"),
        (526, 537, "dppm.19.4"),
        (538, 549, "dppm.19.5"),
        (550, 562, "dppm.19.6"),
        (563, 567, "dppm.19.7"),
        (584, 587, "dppm.19.8"),
        (588, 718, "dppm.20"),
        (606, 642, "dppm.20.1"),
        (606, 617, "dppm.20.2"),
        (618, 642, "dppm.20.3"),
        (643, 658, "dppm.20.4"),
        (659, 674, "dppm.20.5"),
    ),
}

# Códigos compilados identificados pelo nome do arquivo (sem acentos, minúsculo).
COMPILED_CODES: dict[str, str] = {
    "del1001": "direito_penal_militar",
    "del1002": "direito_processual_penal_militar",
}

# Número da lei (sem pontos) no nome do arquivo → tópico de Legislação PMAL.
LAW_TOPICS: dict[str, str] = {
    "5346": "leg.1", "9381": "leg.1", "37042": "leg.2", "7716": "leg.3", "8072": "leg.4", "8930": "leg.4",
    "12850": "leg.5", "9455": "leg.6", "9605": "leg.7", "10826": "leg.8", "11343": "leg.9",
    "11340": "leg.10", "9503": "leg.11", "8069": "leg.12", "13869": "leg.13", "7960": "leg.14",
    "9099": "leg.15", "10259": "leg.16", "14751": "leg.17",
}

_ARTICLE = re.compile(r"(?:^|\n|\s)Art\.?\s*(\d{1,3})(?:\s*[º°o](?![a-z]))?", re.IGNORECASE)


def normalize(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value.casefold())
    return "".join(character for character in folded if not unicodedata.combining(character))


def compiled_code_discipline(relative_path: str) -> str | None:
    filename = normalize(relative_path).rsplit("/", 1)[-1]
    compact = re.sub(r"[\s.\-_]", "", filename)
    for marker, discipline in COMPILED_CODES.items():
        if marker in compact:
            return discipline
    return None


def law_topic(relative_path: str) -> str | None:
    filename = normalize(relative_path).rsplit("/", 1)[-1]
    numbers = re.findall(r"\d[\d.]*\d|\d", filename)
    for raw in numbers:
        topic = LAW_TOPICS.get(raw.replace(".", ""))
        if topic:
            return topic
    return None


def first_article(text: str) -> int | None:
    match = _ARTICLE.search(text[:600])
    return int(match.group(1)) if match else None


def topic_for_article(discipline: str, article: int) -> tuple[bool, str | None]:
    """Devolve (encontrou faixa, tópico). Faixa com tópico None exclui o trecho."""

    best: tuple[int, str | None] | None = None
    for start, end, topic in ARTICLE_RANGES.get(discipline, ()):
        if start <= article <= end and (best is None or end - start < best[0]):
            best = (end - start, topic)
    return (best is not None, best[1] if best else None)
