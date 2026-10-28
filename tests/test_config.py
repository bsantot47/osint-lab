import pytest

from core.config import CleManquante, Config, charger


def test_sans_fichier_valeurs_par_defaut(tmp_path):
    c = charger(tmp_path / "absent.toml")
    assert c == Config()


def test_lecture_fichier(tmp_path):
    f = tmp_path / "config.local.toml"
    f.write_text('[reseau]\ndelai_s = 5\n[cles]\npappers = "abc"\n', encoding="utf-8")
    c = charger(f)
    assert c.delai_s == 5.0 and c.cle("pappers") == "abc"


def test_variable_d_environnement(tmp_path, monkeypatch):
    f = tmp_path / "ailleurs.toml"
    f.write_text("[reseau]\ntimeout_s = 3\n", encoding="utf-8")
    monkeypatch.setenv("OSINT_LAB_CONFIG", str(f))
    assert charger().timeout_s == 3.0


@pytest.mark.parametrize("cles", [{}, {"hibp": "  "}])
def test_cle_manquante(cles):
    with pytest.raises(CleManquante):
        Config(cles=cles).cle("hibp")


def test_config_locale_ignoree_par_git():
    from core.config import RACINE
    assert "config.local.toml" in (RACINE / ".gitignore").read_text().splitlines()
