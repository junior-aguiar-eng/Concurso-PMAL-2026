import pytest

from pmal_study.live_sources import SourcePolicyError, _source_kind


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://www.itec.al.gov.br/historia", "alagoas_governo"),
        ("https://alagoas.al.gov.br/", "alagoas_governo"),
        ("https://penedo.al.gov.br/historia", "alagoas_governo"),
        ("https://pm.al.gov.br/", "pmal"),
        ("https://www.al.al.leg.br/", "aleal"),
    ],
)
def test_dominios_institucionais_de_alagoas(url: str, kind: str) -> None:
    assert _source_kind(url) == kind


@pytest.mark.parametrize("url", ["https://fakeal.gov.br/", "https://al.gov.br.evil.com/"])
def test_dominios_parecidos_sao_recusados(url: str) -> None:
    with pytest.raises(SourcePolicyError):
        _source_kind(url)
